"""API de proyectos, análisis QA, ejecución local, defectos y reportes."""

import asyncio
import html
import json
import re
import tempfile
import sys
from pathlib import Path
from contextlib import suppress
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from qa_intelligence import store
from qa_intelligence import ai_provider
from qa_intelligence import integrations
from qa_intelligence import web_automation

router = APIRouter(prefix="/api")

SPECIALISTS = [
    {"id": "functional", "name": "QA funcional", "status": "available", "tools": ["requisitos", "riesgos", "casos", "trazabilidad"]},
    {"id": "automation", "name": "Automatización", "status": "available", "tools": ["pytest", "Selenium", "Serenity BDD", "Cucumber", "JUnit 4"]},
    {"id": "api", "name": "API Testing", "status": "available", "tools": ["HTTP GET/HEAD/POST/PUT/PATCH/DELETE", "estado esperado", "JSON local"]},
    {"id": "database", "name": "Bases de datos", "status": "available", "tools": ["SQLite", "integridad", "solo lectura"]},
    {"id": "performance", "name": "Rendimiento", "status": "available", "tools": ["latencia", "promedio y p95", "máximo 10 solicitudes"]},
    {"id": "security", "name": "Seguridad autorizada", "status": "available", "tools": ["detección local de secretos", "patrones inseguros"]},
    {"id": "mobile", "name": "Mobile", "status": "available", "tools": ["Appium", "Android/iOS", "smoke test de interfaz"]},
    {"id": "devops", "name": "DevOps y calidad", "status": "planned", "tools": ["JUnit XML", "CI/CD"]},
]


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    root: str = Field(min_length=1, max_length=1000)


class RequirementInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=5, max_length=20000)


class DefectInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    severity: str = "medium"


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    use_external_ai: bool = False


class ApiCheckInput(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    expected_status: int = Field(default=200, ge=100, le=599)
    body: str = Field(default="", max_length=10000)
    expected_json_path: str = Field(default="", max_length=200)
    expected_json_value: str = Field(default="", max_length=1000)


class SqliteCheckInput(BaseModel):
    path: str = Field(min_length=1, max_length=500)


class PerformanceCheckInput(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    iterations: int = Field(default=25, ge=1, le=100)
    concurrency: int = Field(default=5, ge=1, le=10)


class MobileCheckInput(BaseModel):
    server_url: str = Field(default="http://127.0.0.1:4723", min_length=8, max_length=2048)
    platform: Literal["android", "ios"]
    device_name: str = Field(min_length=1, max_length=120)
    app_path: str = Field(min_length=1, max_length=500)


class WebAutomationInput(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    description: str = Field(min_length=5, max_length=3000)
    allow_external_ai: bool = False


class WebAutomationRunInput(BaseModel):
    suite: str = Field(min_length=1, max_length=500)


def project_or_404(project_id: int) -> dict:
    project = store.one("SELECT * FROM projects WHERE id = ?", (project_id,))
    if not project:
        raise HTTPException(404, "Proyecto no encontrado")
    root = Path(project["root"]).resolve()
    if not root.is_dir():
        raise HTTPException(409, "La carpeta registrada ya no está disponible")
    return {**project, "resolved_root": root}


async def _collect_capped_output(reader: asyncio.StreamReader, limit: int = 20_000) -> str:
    """Lee una salida de proceso sin guardar más de `limit` caracteres en memoria."""
    chunks: list[str] = []
    size = 0
    while chunk := await reader.read(4096):
        text = chunk.decode("utf-8", errors="replace")
        chunks.append(text)
        size += len(text)
        if size > limit:
            joined = "".join(chunks)
            chunks = [joined[-limit:]]
            size = limit
    return "".join(chunks)


@router.get("/overview")
def overview() -> dict:
    ai = ai_provider.status()
    return {
        "projects": store.rows("SELECT * FROM projects ORDER BY id DESC"),
        "counts": {
            "projects": store.one("SELECT COUNT(*) AS n FROM projects")["n"],
            "requirements": store.one("SELECT COUNT(*) AS n FROM requirements")["n"],
            "test_cases": store.one("SELECT COUNT(*) AS n FROM test_cases")["n"],
            "runs": store.one("SELECT COUNT(*) AS n FROM runs")["n"],
            "open_defects": store.one("SELECT COUNT(*) AS n FROM defects WHERE status='open'")["n"],
        },
        "specialists": SPECIALISTS,
        "capabilities": {"ai_provider": ai["provider"], "external_data_sharing": ai["configured"],
                         "external_data_sharing_requires_explicit_consent": True,
                         "external_ai_configured": ai["configured"],
                         "execution_evidence_required": True},
    }


@router.get("/ai/status")
def ai_status() -> dict:
    return ai_provider.status()


def _save_integration_run(project_id: int, integration_id: str, result: dict) -> dict:
    run_id = store.create(
        "INSERT INTO runs(project_id, status, command, summary, output) VALUES (?, ?, ?, ?, ?)",
        (project_id, result["status"], f"qa-integration:{integration_id}", result["summary"], result["output"]),
    )
    store.audit("integration.executed", project_id, {"run_id": run_id, "integration": integration_id,
                                                       "status": result["status"]})
    return store.one("SELECT * FROM runs WHERE id=?", (run_id,))


def _execute_integration(project_id: int, integration_id: str, action) -> dict:
    try:
        result = action()
    except HTTPException as exc:
        if exc.status_code in {413, 422}:
            raise
        result = {"status": "failed", "summary": str(exc.detail),
                  "output": json.dumps({"error": str(exc.detail)}, ensure_ascii=False)}
    return _save_integration_run(project_id, integration_id, result)


@router.post("/projects/{project_id}/integrations/api", status_code=201)
def run_api_check(project_id: int, payload: ApiCheckInput) -> dict:
    project_or_404(project_id)
    return _execute_integration(project_id, "api",
                                lambda: integrations.api_smoke(payload.url, payload.method, payload.expected_status,
                                                               payload.body, payload.expected_json_path,
                                                               payload.expected_json_value))


@router.post("/projects/{project_id}/integrations/sqlite", status_code=201)
def run_sqlite_check(project_id: int, payload: SqliteCheckInput) -> dict:
    project = project_or_404(project_id)
    return _execute_integration(project_id, "sqlite",
                                lambda: integrations.sqlite_integrity(project["resolved_root"], payload.path))


@router.post("/projects/{project_id}/integrations/performance", status_code=201)
def run_performance_check(project_id: int, payload: PerformanceCheckInput) -> dict:
    project_or_404(project_id)
    return _execute_integration(project_id, "performance",
                                lambda: integrations.performance_smoke(payload.url, payload.iterations, payload.concurrency))


@router.post("/projects/{project_id}/integrations/mobile", status_code=201)
def run_mobile_check(project_id: int, payload: MobileCheckInput) -> dict:
    project = project_or_404(project_id)
    return _execute_integration(project_id, "mobile", lambda: integrations.appium_smoke(
        project["resolved_root"], payload.server_url, payload.platform, payload.device_name, payload.app_path))


@router.post("/projects/{project_id}/automations/web/generate", status_code=201)
def generate_web_automation(project_id: int, payload: WebAutomationInput) -> dict:
    project = project_or_404(project_id)
    generated = web_automation.create_suite(project["resolved_root"], project_id, payload.url,
                                               payload.description.strip(), allow_external_ai=payload.allow_external_ai)
    store.audit("automation.web.generated", project_id, {"suite": generated["suite"], "steps": generated["steps"]})
    return generated


@router.post("/projects/{project_id}/automations/web/run", status_code=201)
def run_web_automation(project_id: int, payload: WebAutomationRunInput) -> dict:
    project = project_or_404(project_id)
    result = web_automation.run_suite(project["resolved_root"], payload.suite)
    run_id = store.create(
        "INSERT INTO runs(project_id, status, command, summary, output) VALUES (?, ?, ?, ?, ?)",
        (project_id, result["status"], f"serenity:{payload.suite}", result["summary"],
         json.dumps({"output": result.get("output", ""), "report": result.get("report")}, ensure_ascii=False)),
    )
    store.audit("automation.web.executed", project_id, {"run_id": run_id, "status": result["status"]})
    return {**store.one("SELECT * FROM runs WHERE id=?", (run_id,)), "report": result.get("report")}


@router.get("/projects/{project_id}/automations/web/report")
def web_automation_report(project_id: int, suite: str):
    root = project_or_404(project_id)["resolved_root"]
    suite_path = (root / suite).resolve()
    automation_root = (root / ".qa-automations").resolve()
    report = suite_path / "target" / "site" / "serenity" / "index.html"
    if not suite_path.is_relative_to(automation_root) or not report.is_file():
        raise HTTPException(404, "No se encontró el informe Serenity para esta suite.")
    return FileResponse(report, media_type="text/html")


@router.get("/specialists")
def specialists() -> list[dict]:
    return SPECIALISTS


@router.get("/projects/{project_id}/chat")
def chat_history(project_id: int) -> list[dict]:
    project_or_404(project_id)
    return store.rows("SELECT id, role, message, created_at FROM chat_messages WHERE project_id=? ORDER BY id", (project_id,))


@router.post("/projects/{project_id}/chat", status_code=201)
def chat(project_id: int, payload: ChatInput) -> dict:
    project_or_404(project_id)
    question = payload.message.strip()
    if payload.use_external_ai:
        answer = ai_provider.generate_text(
            question,
            system_instruction=(
                "Eres un asistente senior de aseguramiento de calidad de software. "
                "Responde en español, con pasos concretos, criterios verificables y límites claros. "
                "Nunca afirmes que ejecutaste pruebas, inspeccionaste archivos o cambiaste el proyecto. "
                "Solo recibes la consulta escrita; no tienes acceso al repositorio."
            ),
        )
        store.create("INSERT INTO chat_messages(project_id, role, message) VALUES (?, 'user', ?)", (project_id, question))
        store.create("INSERT INTO chat_messages(project_id, role, message) VALUES (?, 'assistant', ?)", (project_id, answer))
        store.audit("chat.message", project_id, {"source": "gemini", "external_text_sent": True})
        return {"answer": answer, "source": "gemini", "executed": False, "external_text_sent": True}
    lowered = question.lower()
    if any(word in lowered for word in ("ejecuta", "ejecutar", "corre las pruebas", "run", "pytest")):
        answer = "No he ejecutado ninguna prueba desde este mensaje. Para obtener evidencia real, revisa que confías en el repositorio y usa el botón «Ejecutar pruebas»; solo se lanzará pytest con un límite de 90 segundos."
    elif any(word in lowered for word in ("caso", "escenario", "test", "automat")):
        answer = "Puedo proponer escenarios desde los requisitos registrados. Abre «Requisitos y casos», elige un requisito y genera los escenarios base; aparecerán como borradores locales pendientes de revisión QA."
    elif any(word in lowered for word in ("riesgo", "analiza", "análisis", "requisito")):
        answer = "El análisis heurístico local busca criterios ambiguos, descripciones breves y ausencia de límites o escenarios negativos. Abre un requisito y usa «Analizar» para ver hallazgos; esto es una recomendación, no una ejecución."
    elif any(word in lowered for word in ("informe", "reporte", "resultado")):
        answer = "El informe del proyecto reúne los casos propuestos y las salidas almacenadas de pytest. Los escenarios sugeridos se distinguen de las ejecuciones observadas. Abre «Informes» para consultarlo."
    else:
        answer = "Soy el coordinador QA local. Puedo orientarte sobre requisitos, riesgos, escenarios de prueba, ejecuciones verificables e informes. Esta respuesta usa reglas locales; no consulta un modelo externo ni inspecciona archivos automáticamente."
    store.create("INSERT INTO chat_messages(project_id, role, message) VALUES (?, 'user', ?)", (project_id, question))
    store.create("INSERT INTO chat_messages(project_id, role, message) VALUES (?, 'assistant', ?)", (project_id, answer))
    store.audit("chat.message", project_id, {"source": "local-rules"})
    return {"answer": answer, "source": "local-rules", "executed": False}


@router.post("/projects", status_code=201)
def add_project(payload: ProjectInput) -> dict:
    root = Path(payload.root).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(400, "Selecciona una carpeta existente en este equipo")
    try:
        project_id = store.create("INSERT INTO projects(name, root) VALUES (?, ?)",
                                  (payload.name.strip(), str(root)))
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(409, "Esta carpeta ya está registrada") from exc
        raise
    store.audit("project.created", project_id, {"name": payload.name, "root": str(root)})
    return store.one("SELECT * FROM projects WHERE id = ?", (project_id,))


@router.get("/projects/{project_id}")
def get_project(project_id: int) -> dict:
    project = project_or_404(project_id)
    return {key: value for key, value in project.items() if key != "resolved_root"}


@router.get("/projects/{project_id}/files")
def project_files(project_id: int) -> dict:
    root = project_or_404(project_id)["resolved_root"]
    excluded = {".git", ".venv", "venv", "node_modules", "__pycache__", ".next"}
    files = []
    for path in root.rglob("*"):
        if len(files) >= 500:
            break
        if path.is_file() and not any(part in excluded for part in path.parts):
            try:
                files.append(str(path.relative_to(root)))
            except ValueError:
                continue
    return {"root": str(root), "files": files, "truncated": len(files) >= 500}


@router.post("/projects/{project_id}/security-scan")
def security_scan(project_id: int) -> dict:
    """Escaneo local de patrones: devuelve ubicaciones, nunca valores sensibles."""
    root = project_or_404(project_id)["resolved_root"]
    excluded = {".git", ".venv", "venv", "node_modules", "__pycache__", ".next", ".pytest_cache"}
    allowed = {".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yaml", ".yml", ".toml", ".sql", ".ini", ".cfg"}
    patterns = [
        ("private-key", "high", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
        ("aws-access-key", "critical", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
        ("credential-assignment", "high", re.compile(r"(?i)\b(?:api[_-]?key|secret(?:[_-]?key)?|password|passwd|token)\b\s*[:=]\s*['\"]?([A-Za-z0-9_+/=-]{16,})")),
        ("dynamic-evaluation", "medium", re.compile(r"\b(?:eval|exec)\s*\(")),
        ("unsafe-html-insertion", "medium", re.compile(r"(?:dangerouslySetInnerHTML|\.innerHTML\s*=)")),
        ("shell-enabled", "high", re.compile(r"\bshell\s*=\s*True\b")),
    ]
    findings, examined, byte_count, truncated = [], 0, 0, False
    for path in root.rglob("*"):
        if any(part in excluded for part in path.parts) or path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() not in allowed and not path.name.startswith(".env"):
            continue
        if examined >= 3000 or byte_count >= 20 * 1024 * 1024:
            truncated = True
            break
        try:
            if path.stat().st_size > 1024 * 1024:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        examined += 1
        byte_count += len(content.encode("utf-8", errors="replace"))
        for line_number, line in enumerate(content.splitlines(), 1):
            for rule, severity, pattern in patterns:
                match = pattern.search(line)
                if not match:
                    continue
                if rule == "credential-assignment" and match.group(1).lower().startswith(("change-me", "your-", "example", "placeholder", "test-")):
                    continue
                findings.append({"file": str(path.relative_to(root)), "line": line_number,
                                 "rule": rule, "severity": severity})
                break
            if len(findings) >= 500:
                truncated = True
                break
        if truncated:
            break
    store.audit("security.scan", project_id, {"examined_files": examined, "finding_count": len(findings)})
    return {"method": "local-patterns", "executed": False, "examined_files": examined,
            "findings": findings, "finding_count": len(findings), "truncated": truncated,
            "notice": "Revisión heurística de patrones; puede producir falsos positivos y no sustituye un análisis de seguridad especializado.",
            "secret_values_returned": False}


@router.post("/projects/{project_id}/requirements", status_code=201)
def add_requirement(project_id: int, payload: RequirementInput) -> dict:
    project_or_404(project_id)
    requirement_id = store.create(
        "INSERT INTO requirements(project_id, title, body) VALUES (?, ?, ?)",
        (project_id, payload.title.strip(), payload.body.strip()))
    store.audit("requirement.created", project_id, {"requirement_id": requirement_id})
    return store.one("SELECT * FROM requirements WHERE id = ?", (requirement_id,))


@router.get("/projects/{project_id}/requirements")
def requirements(project_id: int) -> list[dict]:
    project_or_404(project_id)
    return store.rows("SELECT * FROM requirements WHERE project_id=? ORDER BY id DESC", (project_id,))


@router.post("/projects/{project_id}/requirements/{requirement_id}/generate-cases", status_code=201)
def generate_cases(project_id: int, requirement_id: int, use_external_ai: bool = False) -> dict:
    project_or_404(project_id)
    req = store.one("SELECT * FROM requirements WHERE id=? AND project_id=?", (requirement_id, project_id))
    if not req:
        raise HTTPException(404, "Requisito no encontrado")
    if use_external_ai:
        generated = ai_provider.generate_json(
            f"Genera exactamente tres casos QA para este requisito.\nTítulo: {req['title']}\nDescripción: {req['body']}",
            system_instruction=(
                "Actúa como QA senior. Devuelve escenarios positivo, negativo y de límites o transiciones. "
                "No inventes detalles: expresa lo desconocido como supuesto. Cada caso debe ser verificable."
            ),
            response_schema={
                "type": "object",
                "properties": {
                    "cases": {"type": "array", "minItems": 3, "maxItems": 3, "items": {
                        "type": "object", "properties": {
                            "title": {"type": "string"}, "preconditions": {"type": "string"},
                            "steps": {"type": "string"}, "expected": {"type": "string"},
                            "technique": {"type": "string"},
                        }, "required": ["title", "preconditions", "steps", "expected", "technique"],
                    }},
                },
                "required": ["cases"],
            },
        )
        items = generated.get("cases")
        if not isinstance(items, list) or len(items) != 3 or any(
            not isinstance(item, dict) or any(not isinstance(item.get(field), str) or not item[field].strip()
                for field in ("title", "preconditions", "steps", "expected", "technique"))
            for item in items
        ):
            raise HTTPException(502, "La IA no devolvió tres casos completos; no se guardaron resultados.")
        ids = []
        for item in items:
            ids.append(store.create(
                "INSERT INTO test_cases(project_id, requirement_id, title, preconditions, steps, expected, technique) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_id, requirement_id, f"{req['title']}: {item['title']}", item["preconditions"],
                 item["steps"], item["expected"], item["technique"]),
            ))
        store.audit("cases.generated", project_id, {"requirement_id": requirement_id, "case_ids": ids, "method": "gemini"})
        return {"generated": len(ids), "method": "gemini", "cases": store.rows(
            "SELECT * FROM test_cases WHERE id IN (" + ",".join("?" for _ in ids) + ")", tuple(ids))}
    scenarios = [
        ("Flujo principal", "Ingresar datos válidos según el requisito.", "La operación finaliza correctamente y cumple los criterios de aceptación.", "Partición de equivalencia"),
        ("Entrada inválida", "Omitir o alterar un dato obligatorio.", "El sistema rechaza la entrada e informa el error sin guardar datos inválidos.", "Prueba negativa"),
        ("Límites y transiciones", "Probar valores mínimos, máximos y estados inmediatamente anterior y posterior.", "Los límites se respetan y cada transición permitida produce el estado esperado.", "Valores límite y transición de estados"),
    ]
    ids = []
    for title, steps, expected, technique in scenarios:
        ids.append(store.create("""INSERT INTO test_cases(project_id, requirement_id, title, preconditions, steps, expected, technique)
                              VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (project_id, requirement_id, f"{req['title']}: {title}", "Entorno de prueba disponible; requisito cargado.", steps, expected, technique)))
    store.audit("cases.generated", project_id, {"requirement_id": requirement_id, "case_ids": ids, "method": "local-rules"})
    return {"generated": len(ids), "method": "local-rules", "cases": store.rows(
        "SELECT * FROM test_cases WHERE id IN (" + ",".join("?" for _ in ids) + ")", tuple(ids))}


@router.post("/projects/{project_id}/requirements/{requirement_id}/analyze")
def analyze_requirement(project_id: int, requirement_id: int, use_external_ai: bool = False) -> dict:
    project_or_404(project_id)
    req = store.one("SELECT * FROM requirements WHERE id=? AND project_id=?", (requirement_id, project_id))
    if not req:
        raise HTTPException(404, "Requisito no encontrado")
    if use_external_ai:
        analysis = ai_provider.generate_json(
            f"Evalúa este requisito.\nTítulo: {req['title']}\nDescripción: {req['body']}",
            system_instruction=(
                "Evalúa claridad, aceptación, casos negativos, límites, estados y riesgos de seguridad. "
                "Da recomendaciones concretas. Usa niveles high, medium o low."
            ),
            response_schema={
                "type": "object",
                "properties": {
                    "risks": {"type": "array", "items": {"type": "object", "properties": {
                        "level": {"type": "string", "enum": ["high", "medium", "low"]},
                        "finding": {"type": "string"}, "recommendation": {"type": "string"},
                    }, "required": ["level", "finding", "recommendation"]}},
                    "strategy": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["risks", "strategy"],
            },
        )
        risks, strategy = analysis.get("risks"), analysis.get("strategy")
        if not isinstance(risks, list) or not isinstance(strategy, list):
            raise HTTPException(502, "La IA devolvió un análisis incompleto.")
        store.audit("requirement.analyzed", project_id, {"requirement_id": requirement_id, "method": "gemini"})
        return {"method": "gemini", "executed": False, "risk_count": len(risks), "risks": risks,
                "strategy": strategy,
                "notice": "Análisis generado por Gemini a partir del texto del requisito. Requiere revisión QA; no ejecuta pruebas."}
    text = f"{req['title']} {req['body']}".lower()
    risks = []
    if not any(term in text for term in ("debe", "cuando", "si ", "criterio", "entonces", "aceptación", "aceptacion")):
        risks.append({"level": "high", "finding": "Criterios de aceptación poco explícitos", "recommendation": "Definir resultados observables para cada escenario."})
    if len(req["body"].split()) < 12:
        risks.append({"level": "medium", "finding": "Descripción breve", "recommendation": "Aclarar actores, datos, reglas y excepciones."})
    if not any(term in text for term in ("error", "inválid", "invalid", "rechaz", "límite", "limite", "vacío", "vacio")):
        risks.append({"level": "medium", "finding": "No se describen comportamientos negativos o límites", "recommendation": "Especificar entradas inválidas, valores límite y mensajes esperados."})
    return {"method": "local-rules", "executed": False, "risk_count": len(risks), "risks": risks,
            "strategy": ["Validar el flujo principal con particiones de equivalencia.",
                         "Probar datos obligatorios, valores límite y transiciones.",
                         "Revisar trazabilidad con los criterios de aceptación."],
            "notice": "Análisis heurístico local; revisar con un QA. No constituye ejecución de pruebas."}


@router.get("/projects/{project_id}/cases")
def cases(project_id: int) -> list[dict]:
    project_or_404(project_id)
    return store.rows("SELECT * FROM test_cases WHERE project_id=? ORDER BY id DESC", (project_id,))


@router.post("/projects/{project_id}/runs", status_code=201)
async def run_pytest(project_id: int) -> dict:
    """Ejecuta solo pytest, sin shell, dentro de la carpeta registrada por el usuario."""
    project = project_or_404(project_id)
    root: Path = project["resolved_root"]
    xml_file = tempfile.NamedTemporaryFile(prefix="qa-intelligence-", suffix=".xml", delete=False)
    xml_path = Path(xml_file.name)
    xml_file.close()
    command = [sys.executable, "-m", "pytest", "-q", "--tb=short", f"--junitxml={xml_path}"]
    try:
        process = await asyncio.create_subprocess_exec(
            *command, cwd=root, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
        async def capture() -> tuple[str, int | None]:
            assert process.stdout is not None
            captured = await _collect_capped_output(process.stdout)
            await process.wait()
            return captured, process.returncode

        output, return_code = await asyncio.wait_for(capture(), timeout=90)
        status = "passed" if return_code == 0 else "failed"
        summary = f"pytest terminó con código {return_code}"
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        output, status, summary = "Tiempo máximo excedido (90 s).", "timeout", "Ejecución detenida por límite de tiempo"
    except FileNotFoundError as exc:
        output, status, summary = str(exc), "error", "No fue posible iniciar pytest"
    junit_xml = xml_path.read_text(encoding="utf-8", errors="replace") if xml_path.exists() else ""
    with suppress(OSError):
        xml_path.unlink()
    run_id = store.create("INSERT INTO runs(project_id, status, command, summary, output, junit_xml) VALUES (?, ?, ?, ?, ?, ?)",
                          (project_id, status, "python -m pytest -q --tb=short --junitxml=<temporal>", summary, output, junit_xml))
    store.audit("tests.executed", project_id, {"run_id": run_id, "status": status})
    return store.one("SELECT * FROM runs WHERE id=?", (run_id,))


@router.get("/projects/{project_id}/runs/{run_id}/junit.xml")
def junit_report(project_id: int, run_id: int):
    from fastapi.responses import Response
    project_or_404(project_id)
    run = store.one("SELECT junit_xml FROM runs WHERE id=? AND project_id=?", (run_id, project_id))
    if not run:
        raise HTTPException(404, "Ejecución no encontrada")
    if not run["junit_xml"]:
        raise HTTPException(404, "Esta ejecución no generó un informe JUnit")
    return Response(run["junit_xml"], media_type="application/xml",
                    headers={"Content-Disposition": f"attachment; filename=qa-run-{run_id}.xml"})


@router.get("/projects/{project_id}/runs")
def runs(project_id: int) -> list[dict]:
    project_or_404(project_id)
    return store.rows("SELECT * FROM runs WHERE project_id=? ORDER BY id DESC", (project_id,))


@router.post("/projects/{project_id}/defects", status_code=201)
def add_defect(project_id: int, payload: DefectInput) -> dict:
    project_or_404(project_id)
    if payload.severity not in {"low", "medium", "high", "critical"}:
        raise HTTPException(422, "Severidad permitida: low, medium, high o critical")
    defect_id = store.create("INSERT INTO defects(project_id, title, description, severity) VALUES (?, ?, ?, ?)",
                             (project_id, payload.title.strip(), payload.description.strip(), payload.severity))
    store.audit("defect.created", project_id, {"defect_id": defect_id})
    return store.one("SELECT * FROM defects WHERE id=?", (defect_id,))


@router.get("/projects/{project_id}/defects")
def defects(project_id: int) -> list[dict]:
    project_or_404(project_id)
    return store.rows("SELECT * FROM defects WHERE project_id=? ORDER BY id DESC", (project_id,))


@router.get("/projects/{project_id}/report")
def report(project_id: int) -> dict:
    project = project_or_404(project_id)
    requirements_list = requirements(project_id)
    case_list = cases(project_id)
    run_list = runs(project_id)
    defect_list = defects(project_id)
    latest = run_list[0] if run_list else None
    return {"project": project["name"], "requirements": requirements_list,
            "cases": case_list, "runs": run_list, "defects": defect_list,
            "latest_run_status": latest["status"] if latest else "not_run",
            "evidence_note": "Solo las ejecuciones registradas incluyen resultados observados."}


@router.get("/projects/{project_id}/report.html", response_class=HTMLResponse)
def report_html(project_id: int):
    data = report(project_id)
    title = html.escape(data["project"])
    run_rows = "".join(f"<tr><td>{html.escape(r['started_at'])}</td><td>{html.escape(r['status'])}</td><td><pre>{html.escape(r['output'])}</pre></td></tr>" for r in data["runs"])
    case_rows = "".join(f"<tr><td>{html.escape(c['title'])}</td><td>{html.escape(c['technique'])}</td><td>{html.escape(c['expected'])}</td></tr>" for c in data["cases"])
    body = f"<!doctype html><html lang='es'><meta charset='utf-8'><title>Informe QA · {title}</title><style>body{{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;color:#17212b}}table{{border-collapse:collapse;width:100%;margin:16px 0}}td,th{{border:1px solid #ccd4dd;padding:10px;text-align:left;vertical-align:top}}pre{{white-space:pre-wrap}}.note{{padding:12px;background:#edf5ff}}</style><h1>Informe de calidad · {title}</h1><p class='note'>{html.escape(data['evidence_note'])}</p><h2>Casos ({len(data['cases'])})</h2><table><tr><th>Caso</th><th>Técnica</th><th>Resultado esperado</th></tr>{case_rows}</table><h2>Ejecuciones observadas ({len(data['runs'])})</h2><table><tr><th>Fecha</th><th>Estado</th><th>Evidencia</th></tr>{run_rows}</table></html>"
    return HTMLResponse(body)


@router.get("/audit")
def audit_events() -> list[dict]:
    return store.rows("SELECT * FROM audit_events ORDER BY id DESC LIMIT 200")
