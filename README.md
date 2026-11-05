# QA Intelligence AI

Plataforma local-first para ingeniería de calidad. Incluye API FastAPI, dashboard Next.js/TypeScript en español, persistencia SQLite, análisis heurístico local de requisitos, generador de escenarios revisables, ejecución opcional de pytest, pruebas de servicios HTTP locales, verificación SQLite de solo lectura, smoke test de baja carga, integración Appium para móvil, evidencia de ejecuciones, registro de auditoría e informes HTML.

El coordinador funciona con reglas locales por defecto o Gemini API si se configura una clave local y se activa el envío externo. No transmite el código del repositorio y distingue recomendaciones de ejecuciones. Los casos generados son borradores que requieren revisión QA.

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

Desde PowerShell, en la raíz del proyecto, inicia los dos servicios con un comando:

```powershell
.\start.ps1
```

El script abre una ventana para la API y otra para el dashboard, y reutiliza los servicios si ya responden en sus puertos esperados. Para reiniciar instancias que abrió el propio script usa `.\start.ps1 -Restart`. Detén cada servidor con `Ctrl+C` en su ventana.

También puedes iniciarlos manualmente en dos terminales.

Terminal 1, API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn qa_intelligence.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Terminal 2, dashboard:

```powershell
Set-Location frontend
npm run dev
```

Abre `http://127.0.0.1:3000`. La documentación de la API queda en `http://127.0.0.1:8000/docs`; el endpoint de salud es `/health`.

## Activar IA externa gratuita (opcional)

La aplicación integra Gemini API mediante Google AI Studio. Los modelos y cuotas elegibles tienen un nivel gratuito limitado; los límites pueden cambiar. En el nivel gratuito Google indica que puede usar el contenido de las solicitudes para mejorar sus productos. No envíes datos personales, secretos ni requisitos confidenciales si no aceptas esas condiciones. Para evitar cargos, no configures una cuenta de facturación de Google Cloud.

1. Copia .env.example como .env en la raíz del proyecto.
2. Crea una clave en [Google AI Studio](https://aistudio.google.com/apikey) y guárdala únicamente en .env como GEMINI_API_KEY=.... Nunca la pegues en el chat, en el frontend ni en Git.
3. Conserva AI_PROVIDER=gemini y el modelo gratuito de cuota disponible en tu región, por ejemplo `GEMINI_MODEL=gemini-3.5-flash`. Reinicia la API.
4. En **Configuración**, activa **Permitir IA externa**. Sin esta selección la aplicación usa reglas locales.

Solo se envía a Gemini el mensaje que escribes o el texto del requisito al pulsar analizar/generar; nunca se adjuntan archivos del repositorio. La respuesta externa queda guardada en el historial local. Si la cuota se agota, se muestra un error y no se cambia silenciosamente a otro proveedor. El nivel gratuito depende de disponibilidad, modelo, región y cuenta; revisa siempre el panel de uso de AI Studio.

## Operación local segura

1. En **Proyectos**, registra manualmente una carpeta que tengas autorización para revisar.
2. Carga requisitos; el análisis y los casos usan reglas locales o Gemini si configuraste la clave y activaste el envío en **Configuración**. Revisa siempre las sugerencias.
3. Al ejecutar pytest, confirma que confías en el código de prueba. Los tests de un repositorio se ejecutan con los permisos de tu cuenta de Windows; hay límite de 90 segundos y no se usa shell, pero esta versión no crea un sandbox de sistema operativo. No ejecutes código de proyectos no confiables.
4. En **Especialistas**, puedes ejecutar un escaneo local de patrones de secretos y llamadas inseguras. Devuelve ubicaciones sin exponer valores; es heurístico, limitado a archivos de texto admitidos y puede generar falsos positivos.
5. Consulta la salida real, JUnit XML, auditoría e informe HTML.

La API solo escucha en loopback por defecto y CORS solo permite el dashboard local. Esta versión aún no implementa cuentas ni autenticación para el servidor; no expongas la API a una red pública. El explorador lee archivos dentro de la carpeta registrada, omite `.git`, `node_modules` y entornos virtuales, y limita la lista a 500 archivos. No guarda secretos en el repositorio.

## Integraciones de QA

En **Especialistas** están disponibles:

- **API Testing:** ejecuta solicitudes `GET`, `HEAD`, `POST`, `PUT`, `PATCH` o `DELETE`, compara el estado esperado y opcionalmente valida una ruta JSON. Solo admite `localhost`, `127.0.0.1` o `::1`; no sigue redirecciones ni guarda cuerpos o valores de respuesta.
- **Bases de datos:** ejecuta `PRAGMA integrity_check` en un archivo `.db`, `.sqlite` o `.sqlite3` dentro del proyecto seleccionado. La conexión abre en solo lectura; los archivos mayores de 100 MB se rechazan.
- **Rendimiento:** ejecuta hasta 100 solicitudes `GET` concurrentes con máximo 10 conexiones a una URL local y resume latencia promedio, p95, máximo, throughput y estados HTTP. Es una prueba acotada para cargas pequeñas, no sustituye k6/JMeter en estrés o carga sostenida.
- **Mobile:** si Appium está instalado, configurado con el driver adecuado y tiene emulador/dispositivo disponible, inicia la app Android/iOS, comprueba que pueda leer el árbol UI y cierra la sesión. El APK/IPA debe estar dentro del proyecto registrado. Android requiere UiAutomator2; iOS requiere XCUITest y entorno macOS compatible.

### Automatización web Selenium + Serenity

En **Especialistas → Automatización**, pega la URL de la aplicación (localhost o un sitio público HTTPS) y describe el flujo en lenguaje natural. Por ejemplo, para `https://sauce-demo.myshopify.com/`, puedes pedir comprobar la carga de la tienda, navegar productos y verificar el carrito usando datos demo. Para mejorar los selectores, QA Intelligence lee la página inicial y Gemini recibe la URL, descripción y un resumen limitado de botones, campos y etiquetas; no se envía el HTML completo ni archivos del repositorio. Revisa el escenario y confirma antes de ejecutarlo; prueba únicamente sitios autorizados, sin completar compras ni introducir datos personales.

En **Proyectos**, registra una carpeta local donde quieras guardar la suite; puede ser un repositorio de pruebas vacío y no necesita contener el código del sitio remoto. QA Intelligence creará el proyecto Maven dentro de `.qa-automations/` en esa carpeta.

La suite usa Selenium WebDriver, Serenity BDD, Cucumber en español y JUnit 4; se guarda dentro de `.qa-automations/` en la carpeta registrada. La ejecución requiere Java 17, Maven, Chrome y conexión a Maven Central para descargar dependencias; estas bibliotecas son gratuitas y de código abierto. El informe Serenity se abre desde la pantalla. Para sitios externos exige HTTPS y DNS con direcciones públicas, y restringe las acciones automatizadas al mismo origen para bloquear enlaces y formularios a otros dominios.

La integración API admite `GET`, `HEAD`, `POST`, `PUT`, `PATCH` y `DELETE`, estado esperado, cuerpo JSON opcional y una aserción opcional sobre una ruta JSON (por ejemplo `data.estado` igual a `"activo"`). Métodos que pueden modificar datos piden confirmación y se deben ejecutar contra datos locales de prueba. No se persiste el cuerpo enviado, la respuesta ni los valores comparados.

La integración de rendimiento permite configurar hasta 100 solicitudes y 10 conexiones simultáneas a localhost. Es una carga pequeña y limitada, no una prueba de estrés o carga sostenida; k6/JMeter aún no están integrados.

Cada ejecución se guarda en **Ejecuciones** y en la auditoría local. Las integraciones API y rendimiento solo contactan servicios en loopback; Selenium puede abrir localhost o el sitio HTTPS público que escribiste.

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
| 3. Especialistas QA | Análisis funcional, casos positivos/negativos/límites, ejecución pytest, escaneo de patrones de seguridad, smoke HTTP, integridad SQLite, medición local secuencial e integración de smoke Appium. |
| 4. Ejecución local | Registro explícito de carpetas, explorador limitado, pytest con timeout, salida, JUnit XML y auditoría. Aislamiento de sistema operativo pendiente. |
| 5. Plataforma web | Dashboard en español para proyectos, chat QA, requisitos, casos, ejecuciones, defectos, informes, especialistas y privacidad. |
| 6. Integraciones | GitHub Actions, Gemini opcional, API HTTP local, SQLite, smoke Appium y borradores web Selenium/Serenity generados desde URL + descripción. k6/JMeter, Playwright, Cypress, SonarQube y Jenkins siguen pendientes. |
| 7. Validación integral | pytest existente, integración de suites web Serenity, historial JUnit, informe HTML, auditoría y CI. La suite web nueva aún requiere ejecutar su primera validación en la máquina objetivo; sandbox de sistema operativo, seguridad avanzada y carga sostenida pendientes. |

No se configuran ni activan servicios pagos. Gemini requiere clave local de AI Studio y puede operar dentro de la cuota gratuita disponible; los servicios de base de datos servidor, despliegue web, herramientas móviles, carga y escáneres de seguridad especializados siguen planificados.

## Estructura

```text
backend/qa_intelligence/   API, configuración, coordinador local y SQLite
backend/tests/             Pruebas de salud, API, ejecución y reportes
frontend/app/              Dashboard Next.js/TypeScript
.github/workflows/         Quality gates de backend y frontend
data/                      Base SQLite (generada en la primera ejecución)
```
