"""Integración opcional con el nivel gratuito de Gemini API.

Solo transmite el texto entregado explícitamente por el usuario a esta función.
No inspecciona ni adjunta archivos del proyecto.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException

from qa_intelligence.config import settings

INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
API_REVISION = "2026-05-20"


def configured() -> bool:
    return settings.ai_provider.lower() == "gemini" and bool(settings.gemini_api_key.strip())


def status() -> dict:
    enabled = settings.ai_provider.lower() == "gemini"
    return {
        "provider": "gemini" if enabled else "local-rules",
        "model": settings.gemini_model if enabled else None,
        "configured": configured(),
        "enabled": enabled,
        "free_tier_notice": (
            "Google AI Studio ofrece una cuota gratuita limitada para modelos compatibles. "
            "Las solicitudes de esa cuota pueden usarse para mejorar productos de Google. "
            "No configures facturación si quieres mantener el costo en cero."
        ) if enabled else None,
        "data_sent": "Solo el mensaje o requisito que envías; para automatización web, la URL, descripción y resumen de controles HTML de la página elegida. Nunca se envían archivos del repositorio ni el HTML completo.",
    }


def generate_text(prompt: str, *, system_instruction: str, response_schema: dict | None = None) -> str:
    if settings.ai_provider.lower() != "gemini":
        raise HTTPException(503, "La IA externa no está habilitada. Configura AI_PROVIDER=gemini en .env.")
    if not settings.gemini_api_key.strip():
        raise HTTPException(503, "Falta GEMINI_API_KEY. Agrégala en .env local; no la compartas en el chat.")

    request_body: dict = {
        "model": settings.gemini_model,
        "input": f"{system_instruction.strip()}\n\nSolicitud del usuario:\n{prompt.strip()}",
    }
    if response_schema:
        request_body["response_format"] = {
            "type": "text",
            "mime_type": "application/json",
            "schema": response_schema,
        }
    request = Request(
        INTERACTIONS_URL,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": settings.gemini_api_key.strip(),
            "Api-Revision": API_REVISION,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 429:
            raise HTTPException(503, "Gemini alcanzó el límite gratuito temporal. Espera y vuelve a intentar.") from exc
        if exc.code in (401, 403):
            raise HTTPException(503, "Gemini rechazó la clave o el acceso al nivel gratuito. Revisa la clave en AI Studio.") from exc
        raise HTTPException(502, f"El proveedor Gemini respondió con error HTTP {exc.code}.") from exc
    except (TimeoutError, URLError, OSError) as exc:
        raise HTTPException(502, "No se pudo conectar con Gemini. Verifica la conexión y vuelve a intentar.") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(502, "Gemini devolvió una respuesta que no se pudo interpretar.") from exc

    text = payload.get("output_text")
    if not isinstance(text, str):
        # The REST response exposes model output in steps; output_text is an SDK convenience property.
        chunks = [
            block.get("text", "")
            for step in payload.get("steps", [])
            if step.get("type") == "model_output"
            for block in step.get("content", [])
            if block.get("type") == "text"
        ]
        text = "".join(chunks)
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(502, "Gemini no devolvió texto utilizable.")
    return text.strip()


def generate_json(prompt: str, *, system_instruction: str, response_schema: dict) -> dict:
    text = generate_text(prompt, system_instruction=system_instruction, response_schema=response_schema)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(502, "Gemini devolvió JSON inválido; no se guardaron resultados.") from exc
    if not isinstance(value, dict):
        raise HTTPException(502, "Gemini devolvió un formato inesperado; no se guardaron resultados.")
    return value
