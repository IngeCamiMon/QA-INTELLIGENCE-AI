"""Genera y ejecuta un proyecto Serenity/Cucumber aislado para una app local."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException

from qa_intelligence import ai_provider

_ACTIONS = {"navigate", "click", "type", "assert_text", "assert_url"}


def validate_local_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(422, "Por seguridad, Selenium solo acepta URLs locales: localhost, 127.0.0.1 o [::1].")
    if parsed.username or parsed.password or not parsed.netloc or parsed.query or parsed.fragment:
        raise HTTPException(422, "La URL no debe incluir credenciales, parámetros de consulta ni fragmentos.")
    try:
        parsed.port
    except ValueError as exc:
        raise HTTPException(422, "El puerto de la URL no es válido.") from exc
    return value.strip()


def create_suite(root: Path, project_id: int, url: str, request: str, *, allow_external_ai: bool) -> dict:
    url = validate_local_url(url)
    if not allow_external_ai:
        raise HTTPException(403, "Activa explícitamente Gemini en Configuración para enviar la URL y descripción del caso.")
    if not ai_provider.configured():
        raise HTTPException(503, "Para convertir una descripción libre en pasos, configura Gemini y activa su cuota gratuita.")
    generated = ai_provider.generate_json(
        f"URL de inicio: {url}\nFlujo solicitado: {request}",
        system_instruction=(
            "Convierte la petición QA en un plan de navegador determinista para la URL local dada. "
            "Devuelve entre 1 y 12 pasos. actions permitidas: navigate, click, type, assert_text, assert_url. "
            "Usa selectores CSS en target para click/type; type lleva el texto en value. assert_text usa "
            "value como texto visible esperado. assert_url usa value como fragmento esperado de URL. "
            "Incluye navigate solo para abrir la URL de inicio; nunca uses otra URL, código, JavaScript, "
            "credenciales ni operaciones de escritura/compra/borrado. Expresa supuestos mediante pasos de aserción."
        ),
        response_schema={"type": "object", "properties": {"steps": {"type": "array", "minItems": 1, "maxItems": 12,
            "items": {"type": "object", "properties": {"action": {"type": "string", "enum": sorted(_ACTIONS)},
                "target": {"type": "string"}, "value": {"type": "string"}}, "required": ["action", "target", "value"]}}},
            "required": ["steps"]},
    )
    steps = generated.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
        raise HTTPException(502, "La IA devolvió pasos inválidos.")
    for step in steps:
        if not isinstance(step, dict) or step.get("action") not in _ACTIONS or any(
            not isinstance(step.get(key), str) or len(step[key]) > 500 for key in ("target", "value")
        ):
            raise HTTPException(502, "La IA devolvió una acción inválida; no se generó el proyecto.")
        if step["action"] == "navigate" and step["value"] != url:
            raise HTTPException(502, "La IA intentó navegar fuera de la URL local aprobada.")
        if step["action"] in {"click", "type"} and not step["target"].strip():
            raise HTTPException(502, "La IA devolvió un selector vacío.")
        if step["action"] in {"assert_text", "assert_url"} and not step["value"].strip():
            raise HTTPException(502, "La IA devolvió una comprobación vacía.")

    automation_root = (root / ".qa-automations").resolve()
    automation_root.mkdir(parents=True, exist_ok=True)
    suite = automation_root / f"web-{uuid.uuid4().hex[:10]}"
    feature_dir = suite / "src" / "test" / "resources" / "features"
    java_dir = suite / "src" / "test" / "java" / "local" / "qa"
    feature_dir.mkdir(parents=True)
    java_dir.mkdir(parents=True)
    feature = ["# language: es", "Característica: Flujo web automatizado", "  Escenario: " + request[:160].replace("\n", " ")]
    for step in steps:
        action, target, value = step["action"], _gherkin(step["target"]), _gherkin(step["value"])
        if action == "navigate": feature.append('    Dado que abro la aplicación')
        elif action == "click": feature.append(f'    Cuando hago clic en el elemento CSS "{target}"')
        elif action == "type": feature.append(f'    Cuando escribo "{value}" en el elemento CSS "{target}"')
        elif action == "assert_text": feature.append(f'    Entonces veo el texto "{value}"')
        else: feature.append(f'    Entonces la URL contiene "{value}"')
    (feature_dir / "web.feature").write_text("\n".join(feature) + "\n", encoding="utf-8")
    (suite / "pom.xml").write_text(_POM, encoding="utf-8")
    (java_dir / "WebSteps.java").write_text(_JAVA_STEPS.replace("__START_URL__", json.dumps(url, ensure_ascii=False)), encoding="utf-8")
    (java_dir / "CucumberTest.java").write_text(_JAVA_RUNNER, encoding="utf-8")
    (suite / "serenity.properties").write_text(f"webdriver.base.url={url}\nwebdriver.driver=chrome\n", encoding="utf-8")
    (suite / "plan.json").write_text(json.dumps({"url": url, "request": request, "steps": steps}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"suite": str(suite.relative_to(root)), "url": url, "steps": len(steps),
            "files": ["pom.xml", "serenity.properties", "src/test/resources/features/web.feature",
                      "src/test/java/local/qa/CucumberTest.java", "src/test/java/local/qa/WebSteps.java"],
            "feature": "\n".join(feature)}


def _gherkin(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def run_suite(root: Path, suite_path: str) -> dict:
    suite = (root / suite_path).resolve()
    automation_root = (root / ".qa-automations").resolve()
    if not suite.is_relative_to(automation_root) or not (suite / "pom.xml").is_file():
        raise HTTPException(422, "No se encontró un proyecto de automatización web generado por QA Intelligence.")
    try:
        plan = json.loads((suite / "plan.json").read_text(encoding="utf-8"))
        url = validate_local_url(plan["url"])
        intact = (
            (suite / "pom.xml").read_text(encoding="utf-8") == _POM
            and (suite / "src/test/java/local/qa/CucumberTest.java").read_text(encoding="utf-8") == _JAVA_RUNNER
            and (suite / "src/test/java/local/qa/WebSteps.java").read_text(encoding="utf-8")
                == _JAVA_STEPS.replace("__START_URL__", json.dumps(url, ensure_ascii=False))
        )
    except (OSError, KeyError, ValueError, TypeError):
        intact = False
    if not intact:
        raise HTTPException(409, "El código de la suite cambió desde que se generó. Genera un borrador nuevo antes de ejecutarlo.")
    mvn = shutil.which("mvn") or shutil.which("mvn.cmd")
    if not mvn:
        raise HTTPException(503, "Maven no está instalado o no está disponible en PATH.")
    started = time.monotonic()
    try:
        command = [mvn, "-B", "-q", "verify"]
        if os.name == "nt" and mvn.lower().endswith((".cmd", ".bat")):
            command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", f'"{mvn}" -B -q verify']
        completed = subprocess.run(command, cwd=suite, capture_output=True, text=True, timeout=180, shell=False)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "summary": "Serenity superó el límite de 180 segundos.", "output": ""}
    output = (completed.stdout + "\n" + completed.stderr)[-20000:]
    status = "passed" if completed.returncode == 0 else "failed"
    report = suite / "target" / "site" / "serenity" / "index.html"
    return {"status": status, "summary": f"Serenity terminó con código {completed.returncode} en {time.monotonic()-started:.1f}s.",
            "output": output, "report": str(report.relative_to(root)) if report.is_file() else None}


_POM = """<project xmlns=\"http://maven.apache.org/POM/4.0.0\" xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\" xsi:schemaLocation=\"http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd\"><modelVersion>4.0.0</modelVersion><groupId>local.qa</groupId><artifactId>generated-web-automation</artifactId><version>1.0-SNAPSHOT</version><properties><maven.compiler.release>17</maven.compiler.release><project.build.sourceEncoding>UTF-8</project.build.sourceEncoding><serenity.version>4.2.34</serenity.version><cucumber.version>7.20.1</cucumber.version></properties><dependencies><dependency><groupId>net.serenity-bdd</groupId><artifactId>serenity-core</artifactId><version>${serenity.version}</version></dependency><dependency><groupId>net.serenity-bdd</groupId><artifactId>serenity-cucumber</artifactId><version>${serenity.version}</version></dependency><dependency><groupId>io.cucumber</groupId><artifactId>cucumber-junit</artifactId><version>${cucumber.version}</version><scope>test</scope></dependency><dependency><groupId>junit</groupId><artifactId>junit</artifactId><version>4.13.2</version><scope>test</scope></dependency></dependencies><build><plugins><plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-surefire-plugin</artifactId><version>3.5.2</version></plugin><plugin><groupId>net.serenity-bdd.maven.plugins</groupId><artifactId>serenity-maven-plugin</artifactId><version>${serenity.version}</version><executions><execution><id>serenity-reports</id><phase>post-integration-test</phase><goals><goal>aggregate</goal></goals></execution></executions></plugin></plugins></build></project>"""
_POM = _POM.replace(
    "<plugins>",
    "<plugins><plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId>"
    "<version>3.13.0</version><configuration><release>${maven.compiler.release}</release></configuration></plugin>",
)

_JAVA_RUNNER = """package local.qa;\nimport net.serenitybdd.cucumber.CucumberWithSerenity;\nimport io.cucumber.junit.CucumberOptions;\nimport org.junit.runner.RunWith;\n@RunWith(CucumberWithSerenity.class)\n@CucumberOptions(features=\"src/test/resources/features\", glue=\"local.qa\", plugin={\"pretty\"})\npublic class CucumberTest {}\n"""

_JAVA_STEPS = r'''package local.qa;
import io.cucumber.java.es.*;
import net.serenitybdd.annotations.Managed;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.support.ui.WebDriverWait;
import java.time.Duration;
import static org.junit.Assert.assertTrue;
public class WebSteps {
 @Managed(driver="chrome") WebDriver driver;
 @Dado("que abro la aplicación") public void abrir(){ driver.get(__START_URL__); }
 @Cuando("hago clic en el elemento CSS {string}") public void click(String css){ new WebDriverWait(driver, Duration.ofSeconds(10)).until(d->d.findElement(org.openqa.selenium.By.cssSelector(css)).isDisplayed()).click(); }
 @Cuando("escribo {string} en el elemento CSS {string}") public void escribir(String texto,String css){ var e=new WebDriverWait(driver,Duration.ofSeconds(10)).until(d->d.findElement(org.openqa.selenium.By.cssSelector(css))); e.clear(); e.sendKeys(texto); }
 @Entonces("veo el texto {string}") public void texto(String texto){ assertTrue("No se encontró texto visible: "+texto, new WebDriverWait(driver,Duration.ofSeconds(10)).until(d->d.getPageSource().contains(texto))); }
 @Entonces("la URL contiene {string}") public void url(String fragmento){ assertTrue("La URL no contiene: "+fragmento, driver.getCurrentUrl().contains(fragmento)); }
}
'''
