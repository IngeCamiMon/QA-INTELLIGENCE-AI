# QA Intelligence AI

Plataforma local-first para ingeniería de calidad. Incluye API FastAPI, dashboard Next.js/TypeScript en español, persistencia SQLite, análisis heurístico local de requisitos, generador de escenarios revisables, ejecución opcional de pytest, evidencia JUnit XML, registro de auditoría e informes HTML.

El coordinador actual utiliza reglas locales. No hay proveedor externo de IA configurado, no se transmite el código del repositorio y no se presentan recomendaciones como ejecuciones. Los casos generados son borradores que requieren revisión QA.

## Requisitos

- Windows 11, Python 3.11, Node.js 22 y npm.
- Git para historial del proyecto.
- Docker no es necesario: la persistencia local usa SQLite.

## Instalación en PowerShell

En la raíz del proyecto:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
Set-Location frontend
npm ci
Copy-Item .env.example .env.local
```

## Iniciar

Abre dos terminales en la raíz del proyecto.

Terminal 1, API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn qa_intelligence.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Terminal 2, dashboard:

```powershell
Set-Location frontend
npm run dev
```

Abre `http://localhost:3000`. La documentación de la API queda en `http://127.0.0.1:8000/docs`; el endpoint de salud es `/health`. Detén cada proceso con `Ctrl+C` en su terminal.

## Activar IA externa gratuita (opcional)

La aplicación integra Gemini API mediante Google AI Studio. Los modelos y cuotas elegibles tienen un nivel gratuito limitado; los límites pueden cambiar. En el nivel gratuito Google indica que puede usar el contenido de las solicitudes para mejorar sus productos. No envíes datos personales, secretos ni requisitos confidenciales si no aceptas esas condiciones. Para evitar cargos, no configures una cuenta de facturación de Google Cloud.

1. Copia .env.example como .env en la raíz del proyecto.
2. Crea una clave en [Google AI Studio](https://aistudio.google.com/apikey) y guárdala únicamente en .env como GEMINI_API_KEY=.... Nunca la pegues en el chat, en el frontend ni en Git.
3. Conserva AI_PROVIDER=gemini y GEMINI_MODEL=gemini-3.8-flash (modelo mostrado con cuota gratuita en la documentación actual). Reinicia la API.
4. En **Configuración**, activa **Permitir IA externa**. Sin esta selección la aplicación usa reglas locales.

Solo se envía a Gemini el mensaje que escribes o el texto del requisito al pulsar analizar/generar; nunca se adjuntan archivos del repositorio. La respuesta externa queda guardada en el historial local. Si la cuota se agota, se muestra un error y no se cambia silenciosamente a otro proveedor. El nivel gratuito depende de disponibilidad, modelo, región y cuenta; revisa siempre el panel de uso de AI Studio.

## Operación local segura

1. En **Proyectos**, registra manualmente una carpeta que tengas autorización para revisar.
2. Carga requisitos; el análisis y los casos usan reglas locales o Gemini si configuraste la clave y activaste el envío en **Configuración**. Revisa siempre las sugerencias.
3. Al ejecutar pytest, confirma que confías en el código de prueba. Los tests de un repositorio se ejecutan con los permisos de tu cuenta de Windows; hay límite de 90 segundos y no se usa shell, pero esta versión no crea un sandbox de sistema operativo. No ejecutes código de proyectos no confiables.
4. En **Especialistas**, puedes ejecutar un escaneo local de patrones de secretos y llamadas inseguras. Devuelve ubicaciones sin exponer valores; es heurístico, limitado a archivos de texto admitidos y puede generar falsos positivos.
5. Consulta la salida real, JUnit XML, auditoría e informe HTML.

La API solo escucha en loopback por defecto y CORS solo permite el dashboard local. Esta versión aún no implementa cuentas ni autenticación para el servidor; no expongas la API a una red pública. El explorador lee archivos dentro de la carpeta registrada, omite `.git`, `node_modules` y entornos virtuales, y limita la lista a 500 archivos. No guarda secretos en el repositorio.

## Verificación

```powershell
.\.venv\Scripts\python.exe -m pytest
Set-Location frontend
npm ci
npm run build
```

GitHub Actions ejecuta los tests del backend y el build del frontend en cada push y pull request. Este pipeline está configurado; su ejecución remota depende del repositorio en GitHub.

## Estado de las fases

| Fase | Estado implementado |
| --- | --- |
| 1. Fundación | API, entorno virtual, Git, logging, `/health`, pruebas y documentación. |
| 2. Cerebro IA | Coordinador local o Gemini API de cuota gratuita con selección explícita, análisis, casos QA y memoria conversacional SQLite por proyecto. |
| 3. Especialistas QA | Análisis funcional, casos positivos/negativos/límites, ejecución pytest y escaneo de patrones de seguridad. API, bases de datos especializadas, carga y mobile siguen planificados. |
| 4. Ejecución local | Registro explícito de carpetas, explorador limitado, pytest con timeout, salida, JUnit XML y auditoría. Aislamiento de sistema operativo pendiente. |
| 5. Plataforma web | Dashboard en español para proyectos, chat QA, requisitos, casos, ejecuciones, defectos, informes, especialistas y privacidad. |
| 6. Integraciones | GitHub Actions y proveedor Gemini API gratuito opcional configurados. Playwright, Selenium, Cypress, Appium, k6/JMeter, SonarQube y Jenkins pendientes. |
| 7. Validación integral | Pruebas de API/flujo local, comprobación HTML/secretos, ejecución JUnit y build TypeScript. E2E de navegador, sandbox, seguridad avanzada y rendimiento pendientes. |

No se configuran ni activan servicios pagos. Gemini requiere clave local de AI Studio y puede operar dentro de la cuota gratuita disponible; los servicios de base de datos servidor, despliegue web, herramientas móviles, carga y escáneres de seguridad especializados siguen planificados.

## Estructura

```text
backend/qa_intelligence/   API, configuración, coordinador local y SQLite
backend/tests/             Pruebas de salud, API, ejecución y reportes
frontend/app/              Dashboard Next.js/TypeScript
.github/workflows/         Quality gates de backend y frontend
data/                      Base SQLite (generada en la primera ejecución)
```
