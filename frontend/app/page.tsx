"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

type Project = { id: number; name: string; root: string };
type Requirement = { id: number; title: string; body: string };
type TestCase = { id: number; title: string; technique: string; expected: string };
type Run = { id: number; status: string; summary: string; output: string; started_at: string };
type IntegrationResult = Pick<Run, "status" | "summary" | "output">;
type Specialist = { id: string; name: string; status: string; tools: string[] };
type ChatMessage = { id: number; role: "user" | "assistant"; message: string; created_at: string };
type AiStatus = { provider: string; model: string | null; configured: boolean; enabled: boolean; free_tier_notice: string | null; data_sent: string };
type SecurityReport = { examined_files: number; finding_count: number; findings: Array<{ file: string; line: number; rule: string; severity: string }>; notice: string };
type Overview = { projects: Project[]; counts: Record<string, number>; specialists: Specialist[]; capabilities: { ai_provider: string; external_data_sharing: boolean } };

const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000/api";
const nav = [
  ["overview", "◫", "Resumen"], ["projects", "▦", "Proyectos"], ["chat", "✳", "Chat QA"],
  ["requirements", "⌕", "Requisitos y casos"], ["runs", "▷", "Ejecuciones"],
  ["defects", "◇", "Defectos"], ["reports", "▤", "Informes"],
  ["specialists", "✳", "Especialistas"], ["settings", "⚙", "Configuración"],
] as const;
type Page = typeof nav[number][0];

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Error ${response.status}`);
  return body as T;
}

export default function Home() {
  const [page, setPage] = useState<Page>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [requirements, setRequirements] = useState<Requirement[]>([]);
  const [cases, setCases] = useState<TestCase[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [defects, setDefects] = useState<Array<{ id: number; title: string; severity: string; status: string }>>([]);
  const [files, setFiles] = useState<string[]>([]);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [aiStatus, setAiStatus] = useState<AiStatus | null>(null);
  const [useExternalAi, setUseExternalAi] = useState(false);
  const [securityReport, setSecurityReport] = useState<SecurityReport | null>(null);
  const [integrationResults, setIntegrationResults] = useState<Record<string, IntegrationResult>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const selected = useMemo(() => overview?.projects.find((p) => p.id === projectId) || null, [overview, projectId]);

  const refresh = useCallback(async () => {
    const [result, ai] = await Promise.all([api<Overview>("/overview"), api<AiStatus>("/ai/status")]);
    setOverview(result);
    setAiStatus(ai);
    setProjectId((current) => current && result.projects.some((p) => p.id === current) ? current : result.projects[0]?.id ?? null);
  }, []);

  const refreshProject = useCallback(async (id: number) => {
    const [reqs, testCases, testRuns, foundDefects, tree, chat] = await Promise.all([
      api<Requirement[]>(`/projects/${id}/requirements`), api<TestCase[]>(`/projects/${id}/cases`),
      api<Run[]>(`/projects/${id}/runs`), api<Array<{ id: number; title: string; severity: string; status: string }>>(`/projects/${id}/defects`),
      api<{ files: string[] }>(`/projects/${id}/files`),
      api<ChatMessage[]>(`/projects/${id}/chat`),
    ]);
    setRequirements(reqs); setCases(testCases); setRuns(testRuns); setDefects(foundDefects); setFiles(tree.files); setChatMessages(chat);
  }, []);

  useEffect(() => { refresh().catch((error: Error) => setMessage(`API sin conexión: ${error.message}`)); }, [refresh]);
  useEffect(() => { if (projectId) refreshProject(projectId).catch((error: Error) => setMessage(error.message)); }, [projectId, refreshProject]);
  useEffect(() => { setUseExternalAi(window.localStorage.getItem("qa-use-external-ai") === "true"); }, []);
  useEffect(() => { window.localStorage.setItem("qa-use-external-ai", String(useExternalAi)); }, [useExternalAi]);
  useEffect(() => { if (!message) return; const timer = setTimeout(() => setMessage(""), 5000); return () => clearTimeout(timer); }, [message]);

  async function submit(event: FormEvent<HTMLFormElement>, action: (form: FormData) => Promise<void>) {
    event.preventDefault(); setBusy(true);
    const form = event.currentTarget;
    try { await action(new FormData(form)); form.reset(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "No se pudo completar la acción"); }
    finally { setBusy(false); }
  }

  async function addProject(form: FormData) {
    await api<Project>("/projects", { method: "POST", body: JSON.stringify({ name: form.get("name"), root: form.get("root") }) });
    await refresh(); setPage("projects"); setMessage("Proyecto autorizado y registrado en este equipo.");
  }

  async function addRequirement(form: FormData) {
    if (!projectId) throw new Error("Selecciona un proyecto primero.");
    await api(`/projects/${projectId}/requirements`, { method: "POST", body: JSON.stringify({ title: form.get("title"), body: form.get("body") }) });
    await refreshProject(projectId); await refresh(); setMessage("Requisito guardado.");
  }

  async function addDefect(form: FormData) {
    if (!projectId) throw new Error("Selecciona un proyecto primero.");
    await api(`/projects/${projectId}/defects`, { method: "POST", body: JSON.stringify({ title: form.get("title"), description: form.get("description"), severity: form.get("severity") }) });
    await refreshProject(projectId); await refresh(); setMessage("Defecto registrado.");
  }

  async function sendChat(form: FormData) {
    if (!projectId) throw new Error("Selecciona un proyecto primero.");
    const result = await api<{ answer: string; source: string }>(`/projects/${projectId}/chat`, { method: "POST", body: JSON.stringify({ message: form.get("message"), use_external_ai: useExternalAi }) });
    await refreshProject(projectId); setMessage(result.source === "gemini" ? "Respuesta generada por Gemini; solo se envió tu consulta." : "Respuesta generada con reglas locales; no se ejecutó ninguna acción.");
  }

  async function analyzeRequirement(requirementId: number) {
    if (!projectId) return;
    try {
      const result = await api<{ risk_count: number; risks: Array<{ level: string; finding: string; recommendation: string }>; method: string }>(`/projects/${projectId}/requirements/${requirementId}/analyze?use_external_ai=${useExternalAi}`, { method: "POST" });
      setMessage(result.risk_count ? `${result.risk_count} riesgos (${result.method}): ${result.risks.map((risk) => risk.finding).join("; ")}` : "No se detectaron riesgos; revisa igualmente el requisito.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "No se pudo analizar el requisito"); }
  }

  async function runSecurityScan() {
    if (!projectId) { setMessage("Registra o selecciona un proyecto antes de escanear."); return; }
    setBusy(true);
    try {
      const result = await api<SecurityReport>(`/projects/${projectId}/security-scan`, { method: "POST" });
      setSecurityReport(result); setMessage(`Escaneo local: ${result.finding_count} hallazgos heurísticos en ${result.examined_files} archivos.`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "No se pudo escanear el proyecto"); }
    finally { setBusy(false); }
  }

  async function runIntegration(kind: string, form: FormData) {
    if (!projectId) throw new Error("Registra o selecciona un proyecto antes de ejecutar la integración.");
    const payload = kind === "api"
      ? { url: form.get("url"), method: form.get("method"), expected_status: Number(form.get("expected_status")) }
      : kind === "sqlite"
        ? { path: form.get("path") }
        : kind === "performance"
          ? { url: form.get("url"), iterations: Number(form.get("iterations")) }
          : { server_url: form.get("server_url"), platform: form.get("platform"), device_name: form.get("device_name"), app_path: form.get("app_path") };
    const result = await api<IntegrationResult>(`/projects/${projectId}/integrations/${kind}`, {
      method: "POST", body: JSON.stringify(payload),
    });
    setIntegrationResults((current) => ({ ...current, [kind]: result }));
    await refreshProject(projectId);
    await refresh();
    setMessage(`${result.status === "passed" ? "Correcto" : "Con hallazgos"}: ${result.summary}`);
  }

  async function generate(requirementId: number) {
    if (!projectId) return; setBusy(true);
    try { const result = await api<{ generated: number; method: string }>(`/projects/${projectId}/requirements/${requirementId}/generate-cases?use_external_ai=${useExternalAi}`, { method: "POST" }); await refreshProject(projectId); await refresh(); setMessage(`${result.generated} casos propuestos por ${result.method}; requieren revisión QA.`); }
    catch (error) { setMessage(error instanceof Error ? error.message : "No se pudieron generar los casos"); }
    finally { setBusy(false); }
  }

  async function execute() {
    if (!projectId) return;
    if (!window.confirm("pytest ejecutará código del proyecto con los permisos de tu cuenta de Windows. Esta versión limita el comando y el tiempo, pero no usa un sandbox de sistema operativo. Continúa solo si confías en este repositorio.")) return;
    setBusy(true); setMessage("Ejecutando pytest en la carpeta registrada (máximo 90 segundos)…");
    try { const run = await api<Run>(`/projects/${projectId}/runs`, { method: "POST" }); await refreshProject(projectId); await refresh(); setMessage(`Ejecución real ${run.status}: ${run.summary}`); setPage("runs"); }
    catch (error) { setMessage(error instanceof Error ? error.message : "No se pudo ejecutar pytest"); }
    finally { setBusy(false); }
  }

  const title = nav.find(([id]) => id === page)?.[2] || "Resumen";
  const counts = overview?.counts;

  return <main className="shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">Q<span>+</span></div><div><b>QA Intelligence</b><small>QUALITY ENGINEERING</small></div></div>
      <div className="workspace-label">ESPACIO DE TRABAJO</div>
      <nav>{nav.map(([id, icon, label]) => <button key={id} className={`nav-item ${page === id ? "active" : ""}`} onClick={() => setPage(id)}><span className="nav-icon">{icon}</span>{label}{id === "runs" && runs.length > 0 && <i>{runs.length}</i>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="local-pill"><span className="online-dot"/> Motor local activo</div><small>v0.2 · Fase de expansión</small></div>
    </aside>

    <section className="main-area">
      <header className="topbar"><div className="breadcrumb">QA Intelligence AI <span>/</span> <b>{title}</b></div><div className="top-actions"><span className="privacy"><span>●</span> Solo local</span><select aria-label="Proyecto activo" value={projectId ?? ""} onChange={(e) => setProjectId(Number(e.target.value) || null)}><option value="">Seleccionar proyecto</option>{overview?.projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select><div className="avatar">QA</div></div></header>
      <div className="content">
        {message && <div className={`toast ${message.startsWith("API sin") ? "error" : ""}`} role="status">{message}<button onClick={() => setMessage("")}>×</button></div>}
        <div className="page-heading"><div><div className="eyebrow">CENTRO DE INGENIERÍA DE CALIDAD</div><h1>{page === "overview" ? <>Control de calidad<span className="period">.</span></> : title}</h1><p>{page === "overview" ? "Visibilidad de proyectos, cobertura y ejecuciones verificables." : selected ? `${selected.name} · ${selected.root}` : "Registra un proyecto local para comenzar."}</p></div><div className="heading-actions">{selected && <a className="button quiet" href={`${API}/projects/${selected.id}/report.html`} target="_blank" rel="noreferrer">↗ Informe</a>}<button className="button primary" disabled={!selected || busy} onClick={execute}>▷ <span>Ejecutar pruebas</span></button></div></div>

        {page === "overview" && <>
          <div className="hero-card"><div className="hero-copy"><div className="hero-tag"><span className="spark">✳</span> QA ENGINEERING WORKSPACE</div><h2>Calidad con evidencia.<br/><em>Decisiones con contexto.</em></h2><p>Convierte requisitos en cobertura accionable y ejecuta pruebas en entornos locales autorizados.</p><button className="hero-link" onClick={() => setPage(selected ? "requirements" : "projects")}>{selected ? "Explorar cobertura" : "Configurar primer proyecto"}<span>→</span></button></div><div className="hero-art"><div className="orb orb-one"/><div className="orb orb-two"/><div className="orbit orbit-one"/><div className="orbit orbit-two"/><div className="quality-core">Q<span>+</span></div><div className="float-card card-a"><b>{counts?.test_cases ?? 0}</b><small>CASOS PROPUESTOS</small><span className="mini-bars"><i/><i/><i/><i/><i/><i/><i/></span></div>{Boolean(counts?.runs) && <div className="float-card card-b"><span className="check">✓</span><div><b>{counts?.runs} ejecuciones</b><small>Evidencia almacenada</small></div></div>}</div></div>
          <div className="stat-grid">{[["Proyectos", counts?.projects ?? 0, "Espacios locales"], ["Requisitos", counts?.requirements ?? 0, "Bajo seguimiento"], ["Casos de prueba", counts?.test_cases ?? 0, "Propuestos y revisables"], ["Ejecuciones", counts?.runs ?? 0, "Con evidencia guardada"], ["Defectos abiertos", counts?.open_defects ?? 0, "Pendientes de atención"]].map(([label, value, note]) => <div className="stat-card" key={label}><div className="stat-head"><span>{label}</span><span className="stat-dot"/></div><strong>{value}</strong><small>{note}</small><div className="stat-line"/></div>)}</div>
          <div className="section-grid"><div className="panel"><div className="panel-title"><div><h3>Proyectos recientes</h3><p>Repositorios autorizados en este equipo</p></div><button className="text-button" onClick={() => setPage("projects")}>Ver proyectos →</button></div>{overview?.projects.length ? <div className="project-list">{overview.projects.slice(0, 4).map((p) => <button className={`project-row ${projectId === p.id ? "selected" : ""}`} key={p.id} onClick={() => setProjectId(p.id)}><span className="project-icon">⌘</span><span className="project-name"><b>{p.name}</b><small>{p.root}</small></span><span className="project-state"><i/> Local</span><span className="row-arrow">→</span></button>)}</div> : <Empty title="Aún no hay proyectos" text="Registra una carpeta para empezar a analizar y probar." action={() => setPage("projects")}/>}</div><div className="panel coverage-panel"><div className="panel-title"><div><h3>Especialistas QA</h3><p>Coordinador local y herramientas modulares</p></div><button className="text-button" onClick={() => setPage("specialists")}>Explorar →</button></div><div className="specialist-summary"><div className="specialist-ring"><strong>{overview?.specialists.filter((s) => s.status === "available").length ?? 0}</strong><small>listos</small></div><div className="specialist-key"><div><i className="ready-key"/> Disponible <b>{overview?.specialists.filter((s) => s.status === "available").length ?? 0}</b></div><div><i className="planned-key"/> Planificado <b>{overview?.specialists.filter((s) => s.status !== "available").length ?? 0}</b></div><small>El catálogo identifica capacidades implementadas y futuras.</small></div></div><div className="local-note">◈ <span>El análisis actual usa reglas locales. No se transmite código ni se afirma uso de IA externa.</span></div></div></div>
        </>}

        {page === "projects" && <div className="section-grid columns"><div className="panel"><div className="panel-title"><div><h3>Proyectos registrados</h3><p>Solo las carpetas agregadas aquí se habilitan para análisis y ejecución.</p></div></div>{overview?.projects.map((p) => <div className={`project-row static ${projectId === p.id ? "selected" : ""}`} key={p.id} onClick={() => setProjectId(p.id)}><span className="project-icon">⌘</span><span className="project-name"><b>{p.name}</b><small>{p.root}</small></span><span className="project-state"><i/> Local</span></div>)}{!overview?.projects.length && <p className="muted">No hay proyectos registrados.</p>}</div><div className="panel form-panel"><div className="panel-title"><div><h3>Agregar proyecto local</h3><p>Elige una carpeta del equipo a la que autorizas acceso.</p></div></div><form onSubmit={(e) => submit(e, addProject)}><label>Nombre del proyecto<input name="name" placeholder="Mi aplicación" required maxLength={120}/></label><label>Ruta absoluta de la carpeta<input name="root" placeholder="C:\\repos\\mi-aplicacion" required/><small>La API solo ejecuta pytest dentro de la ruta registrada.</small></label><button className="button primary full" disabled={busy}>+ Registrar proyecto</button></form></div><div className="panel"><div className="panel-title"><div><h3>Explorador de archivos</h3><p>{selected ? `${files.length} archivos visibles (máximo 500)` : "Selecciona un proyecto"}</p></div></div><div className="file-list">{files.slice(0, 30).map((file) => <div key={file}><span>◷</span>{file}</div>)}</div></div></div>}

        {page === "requirements" && <div className="section-grid columns"><div className="panel form-panel"><div className="panel-title"><div><h3>Capturar requisito</h3><p>Registra historias y criterios de aceptación para analizar cobertura.</p></div></div><form onSubmit={(e) => submit(e, addRequirement)}><label>Título<input name="title" placeholder="Como usuario, quiero…" required maxLength={200}/></label><label>Requisito / criterios<textarea name="body" placeholder="Describe el comportamiento esperado y sus criterios de aceptación…" required minLength={5} rows={6}/></label><button className="button primary full" disabled={busy || !selected}>Guardar requisito</button></form><div className="method-note"><b>Generación local asistida por reglas</b><span>Produce escenarios base positivos, negativos y de límites. Requieren revisión de un QA antes de usarse.</span></div></div><div className="panel"><div className="panel-title"><div><h3>Requisitos y casos</h3><p>{requirements.length} requisitos · {cases.length} casos propuestos</p></div></div>{requirements.map((req) => <div className="requirement-card" key={req.id}><div className="req-icon">R</div><div className="req-content"><b>{req.title}</b><p>{req.body}</p><button className="text-button" disabled={busy} onClick={() => analyzeRequirement(req.id)}>⌕ Analizar riesgos</button><button className="text-button" disabled={busy} onClick={() => generate(req.id)}>✳ Generar 3 escenarios</button></div></div>)}{cases.length > 0 && <div className="case-list"><h4>Casos generados</h4>{cases.map((test) => <div className="case-row" key={test.id}><span className="case-id">TC-{String(test.id).padStart(3, "0")}</span><div><b>{test.title}</b><small>{test.technique} · {test.expected}</small></div><span className="badge draft">Borrador</span></div>)}</div>}{!requirements.length && <Empty title="Sin requisitos aún" text="Agrega un requisito para habilitar el generador de escenarios."/>}</div></div>}

        {page === "chat" && <div className="panel chat-panel"><div className="panel-title"><div><h3>Coordinador QA</h3><p>{useExternalAi ? `Gemini · ${aiStatus?.model || "modelo configurado"}` : "Reglas locales · no se envía contenido"}</p></div><span className={`badge ${useExternalAi ? "planned" : "ready"}`}>{useExternalAi ? "IA externa activa" : "Local"}</span></div>{selected ? <><div className="chat-history">{chatMessages.length ? chatMessages.map((entry) => <div className={`chat-bubble ${entry.role}`} key={entry.id}><small>{entry.role === "user" ? "TÚ" : "QA COORDINATOR"}</small><p>{entry.message}</p></div>) : <div className="empty-state"><span>✳</span><b>¿En qué te ayudo con {selected.name}?</b><p>Pregunta por riesgos, requisitos, casos, ejecución o informes.</p></div>}</div><form className="chat-form" onSubmit={(e) => submit(e, sendChat)}><textarea name="message" placeholder="Escribe una consulta QA…" required maxLength={4000} rows={3}/><button className="button primary" disabled={busy || (useExternalAi && !aiStatus?.configured)}>Enviar ↑</button></form><p className="chat-disclaimer">{useExternalAi ? "Se envía a Google únicamente el texto que escribas. En la cuota gratuita, Google puede usarlo para mejorar productos." : "La conversación se guarda localmente. El chat nunca ejecuta pruebas ni inspecciona código automáticamente."}</p></> : <Empty title="Selecciona un proyecto" text="El chat guarda el historial por proyecto."/>}</div>}

        {page === "runs" && <div className="panel"><div className="panel-title"><div><h3>Historial de ejecuciones</h3><p>Resultados reales almacenados con su salida de consola.</p></div><button className="button primary" onClick={execute} disabled={!selected || busy}>▷ Ejecutar pytest</button></div>{runs.map((run) => <details className="run-card" key={run.id}><summary><span className={`run-status ${run.status}`}>{run.status === "passed" ? "✓" : "!"}</span><span><b>Run #{run.id}</b><small>{run.summary}</small></span><time>{run.started_at}</time><span className="badge">Ver evidencia⌄</span></summary><pre>{run.output || "La ejecución no produjo salida."}</pre></details>)}{!runs.length && <Empty title="Sin ejecuciones" text="Al ejecutar pytest en el proyecto seleccionado, la evidencia aparecerá aquí."/>}</div>}

        {page === "defects" && <div className="section-grid columns"><div className="panel form-panel"><div className="panel-title"><div><h3>Registrar defecto</h3><p>Documenta hallazgos vinculados al proyecto activo.</p></div></div><form onSubmit={(e) => submit(e, addDefect)}><label>Título<input name="title" placeholder="Resumen del defecto" required maxLength={200}/></label><label>Descripción<textarea name="description" rows={5} placeholder="Pasos, resultado actual y resultado esperado…" required/></label><label>Severidad<select name="severity"><option value="low">Baja</option><option value="medium">Media</option><option value="high">Alta</option><option value="critical">Crítica</option></select></label><button className="button primary full" disabled={busy || !selected}>Guardar defecto</button></form></div><div className="panel"><div className="panel-title"><div><h3>Defectos registrados</h3><p>{defects.length} hallazgos</p></div></div>{defects.map((defect) => <div className="defect-row" key={defect.id}><span className={`severity ${defect.severity}`}/><div><b>{defect.title}</b><small>{defect.severity} · {defect.status}</small></div><span className="badge">#{defect.id}</span></div>)}{!defects.length && <Empty title="No hay defectos" text="Los hallazgos que registres se mostrarán en esta lista."/>}</div></div>}

        {page === "reports" && <div className="panel"><div className="panel-title"><div><h3>Informes de calidad</h3><p>Informes HTML con casos propuestos y evidencia real de ejecución.</p></div></div>{selected ? <div className="report-card"><div className="report-icon">▤</div><div><b>Informe de {selected.name}</b><small>Casos: {cases.length} · Ejecuciones observadas: {runs.length} · Defectos: {defects.length}</small></div><a className="button primary" href={`${API}/projects/${selected.id}/report.html`} target="_blank" rel="noreferrer">Abrir informe ↗</a></div> : <Empty title="Selecciona un proyecto" text="El informe se construye a partir del proyecto activo."/>}</div>}

        {page === "specialists" && <>
          <div className="specialist-grid">{overview?.specialists.map((specialist) => {
            const resultKey = specialist.id === "database" ? "sqlite" : specialist.id;
            const integrationResult = integrationResults[resultKey];
            return <div className="panel specialist-card" key={specialist.id}>
            <div className="specialist-card-head"><span className="spec-glyph">✳</span><span className={`badge ${specialist.status === "available" ? "ready" : "planned"}`}>{specialist.status === "available" ? "Disponible" : "Planificado"}</span></div>
            <h3>{specialist.name}</h3>
            <p>{specialist.id === "mobile" ? "Requiere servidor Appium, driver y dispositivo configurados." : specialist.status === "available" ? "Capacidad conectada al motor local." : "Integración preparada para una fase futura."}</p>
            <div className="tool-tags">{specialist.tools.map((tool) => <span key={tool}>{tool}</span>)}</div>
            {specialist.id === "api" && <form className="integration-form" onSubmit={(event) => submit(event, (form) => runIntegration("api", form))}>
              <label>URL local<input name="url" type="url" defaultValue="http://127.0.0.1:8000/health" required/></label>
              <div className="integration-fields"><label>Método<select name="method"><option>GET</option><option>HEAD</option></select></label><label>Estado esperado<input name="expected_status" type="number" defaultValue="200" min="100" max="599" required/></label></div>
              <button className="button quiet full" disabled={busy || !selected}>Ejecutar prueba API</button>
            </form>}
            {specialist.id === "database" && <form className="integration-form" onSubmit={(event) => submit(event, (form) => runIntegration("sqlite", form))}>
              <label>Ruta relativa<input name="path" placeholder="data/mi-base.sqlite" required/></label>
              <small>Valida integridad SQLite sin modificarla. El archivo debe estar dentro del proyecto registrado.</small>
              <button className="button quiet full" disabled={busy || !selected}>Validar base SQLite</button>
            </form>}
            {specialist.id === "performance" && <form className="integration-form" onSubmit={(event) => submit(event, (form) => runIntegration("performance", form))}>
              <label>URL local<input name="url" type="url" defaultValue="http://127.0.0.1:8000/health" required/></label>
              <label>Solicitudes secuenciales<input name="iterations" type="number" defaultValue="5" min="1" max="10" required/></label>
              <small>Prueba ligera de latencia; no genera carga alta.</small>
              <button className="button quiet full" disabled={busy || !selected}>Medir respuesta</button>
            </form>}
            {specialist.id === "mobile" && <form className="integration-form" onSubmit={(event) => submit(event, (form) => runIntegration("mobile", form))}>
              <label>Servidor Appium<input name="server_url" type="url" defaultValue="http://127.0.0.1:4723" required/></label>
              <div className="integration-fields"><label>Plataforma<select name="platform"><option value="android">Android</option><option value="ios">iOS</option></select></label><label>Dispositivo<input name="device_name" placeholder="emulator-5554" required/></label></div>
              <label>Paquete relativo<input name="app_path" placeholder="build/app-debug.apk" required/></label>
              <small>Abre la app, lee el árbol UI y cierra la sesión. Requiere UiAutomator2 o XCUITest.</small>
              <button className="button quiet full" disabled={busy || !selected}>Ejecutar smoke test móvil</button>
            </form>}
            {specialist.id === "security" && <button className="button quiet full" disabled={busy} onClick={runSecurityScan}>⌕ Escanear patrones locales</button>}
            {integrationResult && <details className="integration-result" open>
              <summary>{integrationResult.summary}</summary>
              <pre>{integrationResult.output}</pre>
            </details>}
          </div>;
          })}</div>
          <p className="integration-safety-note">Las pruebas HTTP y de rendimiento solo aceptan servicios locales. El historial guarda resultados resumidos, sin cuerpos HTTP.</p>
          {securityReport && <div className="panel scan-results"><div className="panel-title"><div><h3>Hallazgos del escaneo</h3><p>{securityReport.examined_files} archivos revisados · no se muestran valores secretos</p></div><span className="badge">{securityReport.finding_count} patrones</span></div>{securityReport.findings.map((finding, index) => <div className="defect-row" key={`${finding.file}-${finding.line}-${index}`}><span className={`severity ${finding.severity}`}/><div><b>{finding.rule}</b><small>{finding.file}:{finding.line} · {finding.severity}</small></div></div>)}{!securityReport.findings.length && <p className="muted">No se detectaron patrones en los archivos revisados.</p>}<p className="chat-disclaimer">{securityReport.notice}</p></div>}
        </>}

        {page === "settings" && <div className="section-grid columns"><div className="panel"><div className="panel-title"><div><h3>Privacidad y modelo</h3><p>Elige local o cuota gratuita de Gemini.</p></div></div><div className="setting-row"><span><b>Coordinador activo</b><small>{aiStatus?.configured ? `${aiStatus.provider} · ${aiStatus.model}` : "Reglas locales"}</small></span><span className={`badge ${aiStatus?.configured ? "ready" : "planned"}`}>{aiStatus?.configured ? "Configurado" : "Sin clave"}</span></div><label className="ai-consent"><input type="checkbox" checked={useExternalAi} disabled={!aiStatus?.configured} onChange={(event) => setUseExternalAi(event.target.checked)}/><span><b>Permitir IA externa</b><small>Al activarla, chat y requisitos enviados a análisis/casos se transmitirán a Gemini.</small></span></label><div className="privacy-note"><b>Datos enviados</b><p>{aiStatus?.data_sent || "Solo el texto que elijas enviar. Nunca se adjuntan archivos del repositorio."}</p><p>{aiStatus?.free_tier_notice || "La cuota gratuita tiene límites variables. En ese nivel, Google puede usar el contenido para mejorar sus productos. No actives facturación para mantener el costo en cero."}</p></div><div className="setting-row"><span><b>Repositorio y archivos</b><small>No se envían al proveedor externo.</small></span><span className="badge ready">Privados</span></div></div><div className="panel"><div className="panel-title"><div><h3>Agente y ejecución local</h3><p>Restricciones que se aplican al runner actual.</p></div></div><div className="setting-row"><span><b>Carpetas autorizadas</b><small>Solo carpetas registradas desde Proyectos.</small></span><span className="badge ready">Activa</span></div><div className="setting-row"><span><b>Comando permitido</b><small>pytest, sin shell, hasta 90 segundos.</small></span><span className="badge ready">Limitado</span></div><div className="setting-row"><span><b>Base de datos</b><small>SQLite local para instalación sin servicios.</small></span><span className="badge ready">Activa</span></div></div></div>}
      </div>
      <footer className="footer"><span>QA Intelligence AI <b>·</b> Ingeniería de calidad, con evidencia</span><span>LOCAL-FIRST <i/> PRIVACIDAD ACTIVA</span></footer>
    </section>
  </main>;
}

function Empty({ title, text, action }: { title: string; text: string; action?: () => void }) {
  return <div className="empty-state"><span>⌘</span><b>{title}</b><p>{text}</p>{action && <button className="text-button" onClick={action}>Comenzar →</button>}</div>;
}
