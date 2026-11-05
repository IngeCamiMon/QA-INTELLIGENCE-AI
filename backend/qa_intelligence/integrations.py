"""Integraciones de QA locales con límites explícitos y evidencia resumida."""

from __future__ import annotations

import json
import sqlite3
import statistics
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import HTTPException


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(_NoRedirect())
_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}


def validate_loopback_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in _LOOPBACK_NAMES:
        raise HTTPException(422, "Por seguridad, la URL debe usar http(s)://localhost, 127.0.0.1 o [::1].")
    if parsed.username or parsed.password or not parsed.netloc or parsed.query or parsed.fragment:
        raise HTTPException(422, "La URL no debe incluir credenciales, query ni fragmento.")
    try:
        parsed.port
    except ValueError as exc:
        raise HTTPException(422, "El puerto de la URL no es válido.") from exc
    return value.strip().rstrip("/")


def request_local(url: str, method: str = "GET", timeout: float = 5.0) -> tuple[int, str, int]:
    """Request loopback sin seguir redirects y sin guardar cuerpos potencialmente sensibles."""
    safe_url = validate_loopback_url(url)
    request = Request(safe_url, method=method, headers={"User-Agent": "QA-Intelligence-AI/0.2"})
    started = time.perf_counter()
    try:
        response = _OPENER.open(request, timeout=timeout)
    except HTTPError as exc:
        # HTTP errors siguen siendo una respuesta válida para poder compararla con lo esperado.
        status, content_type = exc.code, exc.headers.get("Content-Type", "")
        exc.close()
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(502, f"No se pudo conectar con el servicio local ({type(exc).__name__}).") from exc
    else:
        status, content_type = response.status, response.headers.get("Content-Type", "")
        response.close()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return status, content_type, elapsed_ms


def api_smoke(url: str, method: str, expected_status: int) -> dict:
    status, content_type, elapsed_ms = request_local(url, method=method, timeout=8)
    passed = status == expected_status
    return {
        "status": "passed" if passed else "failed",
        "summary": f"{method} respondió HTTP {status}; esperado HTTP {expected_status}.",
        "output": json.dumps({"url": validate_loopback_url(url), "method": method,
                              "http_status": status, "expected_status": expected_status,
                              "content_type": content_type, "duration_ms": elapsed_ms}, ensure_ascii=False),
    }


def sqlite_integrity(root: Path, relative_path: str) -> dict:
    try:
        candidate = (root / relative_path).resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(422, "No se encontró el archivo SQLite dentro del proyecto.") from exc
    if not candidate.is_relative_to(root.resolve()):
        raise HTTPException(422, "La base debe estar dentro de la carpeta del proyecto registrado.")
    if not candidate.is_file() or candidate.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise HTTPException(422, "Selecciona un archivo .db, .sqlite o .sqlite3.")
    if candidate.stat().st_size > 100 * 1024 * 1024:
        raise HTTPException(413, "La base SQLite supera el límite de 100 MB.")

    connection: sqlite3.Connection | None = None
    started = time.perf_counter()
    try:
        db_uri = f"file:{quote(candidate.as_posix(), safe='/:')}?mode=ro"
        connection = sqlite3.connect(db_uri, uri=True, timeout=3)
        connection.execute("PRAGMA query_only=ON")
        # Interrumpe trabajo excesivo en bases dañadas o adversarialmente complejas.
        progress_deadline = time.monotonic() + 5
        connection.set_progress_handler(lambda: int(time.monotonic() > progress_deadline), 10_000)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 100"
        )]
    except (sqlite3.Error, OSError) as exc:
        return {"status": "failed", "summary": "No se pudo validar la base SQLite.",
                "output": json.dumps({"file": relative_path, "error": str(exc)[:300]}, ensure_ascii=False)}
    finally:
        if connection is not None:
            connection.close()

    passed = integrity == "ok"
    return {
        "status": "passed" if passed else "failed",
        "summary": "SQLite: integridad correcta." if passed else "SQLite reportó problemas de integridad.",
        "output": json.dumps({"file": relative_path, "integrity": integrity,
                              "tables": tables, "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                              "read_only": True}, ensure_ascii=False),
    }


def performance_smoke(url: str, iterations: int) -> dict:
    safe_url = validate_loopback_url(url)
    durations: list[float] = []
    statuses: dict[str, int] = {}
    failures = 0
    for _ in range(iterations):
        try:
            status, _, elapsed = request_local(safe_url, method="GET", timeout=4)
            durations.append(elapsed)
            statuses[str(status)] = statuses.get(str(status), 0) + 1
        except HTTPException:
            failures += 1
    ordered = sorted(durations)
    p95_index = max(0, int(len(ordered) * 0.95 + 0.999) - 1) if ordered else 0
    passed = failures == 0 and len(statuses) == 1 and "200" in statuses
    return {
        "status": "passed" if passed else "failed",
        "summary": f"{iterations} solicitudes secuenciales: {failures} errores de conexión; promedio {statistics.mean(durations):.1f} ms." if durations else f"No hubo respuestas; {failures} errores de conexión.",
        "output": json.dumps({"url": safe_url, "iterations": iterations, "sequential": True,
                              "http_statuses": statuses, "connection_errors": failures,
                              "average_ms": round(statistics.mean(durations), 2) if durations else None,
                              "p95_ms": ordered[p95_index] if ordered else None,
                              "max_ms": max(durations) if durations else None,
                              "notice": "Smoke test local de baja carga; no sustituye una prueba de estrés con k6/JMeter."}, ensure_ascii=False),
    }


def appium_smoke(root: Path, server_url: str, platform: str, device_name: str, app_path: str) -> dict:
    safe_server = validate_loopback_url(server_url)
    try:
        app = (root / app_path).resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(422, "No se encontró el paquete móvil dentro del proyecto.") from exc
    if not app.is_relative_to(root.resolve()):
        raise HTTPException(422, "La aplicación móvil debe estar dentro de la carpeta del proyecto registrado.")
    platform_key = platform.lower()
    if platform_key not in {"android", "ios"}:
        raise HTTPException(422, "Plataforma permitida: Android o iOS.")
    allowed = {".apk"} if platform_key == "android" else {".ipa", ".app"}
    if (not app.is_file() and not (platform_key == "ios" and app.is_dir())) or app.suffix.lower() not in allowed:
        raise HTTPException(422, f"Selecciona un paquete {', '.join(sorted(allowed))} válido.")

    status_code, _, _ = request_local(f"{safe_server}/status", timeout=5)
    if status_code != 200:
        raise HTTPException(502, f"El servidor Appium respondió HTTP {status_code} en /status.")

    automation = "UiAutomator2" if platform_key == "android" else "XCUITest"
    capabilities = {
        "platformName": "Android" if platform_key == "android" else "iOS",
        "appium:automationName": automation,
        "appium:deviceName": device_name.strip(),
        "appium:app": str(app),
        "appium:newCommandTimeout": 30,
    }
    body = json.dumps({"capabilities": {"alwaysMatch": capabilities, "firstMatch": [{}]}}).encode()
    request = Request(f"{safe_server}/session", data=body,
                      headers={"Content-Type": "application/json", "User-Agent": "QA-Intelligence-AI/0.2"}, method="POST")
    session_id = None
    source_length = 0
    error = ""
    session_closed = False
    try:
        with _OPENER.open(request, timeout=90) as response:
            payload = json.loads(response.read(200_000).decode("utf-8", errors="replace"))
        value = payload.get("value", {}) if isinstance(payload, dict) else {}
        session_id = payload.get("sessionId") or value.get("sessionId")
        if not session_id:
            error = str(value.get("message", "Appium no devolvió una sesión."))[:400]
        else:
            source_request = Request(f"{safe_server}/session/{session_id}/source", method="GET")
            with _OPENER.open(source_request, timeout=30) as response:
                source_payload = json.loads(response.read(1_000_000).decode("utf-8", errors="replace"))
            source = source_payload.get("value", "") if isinstance(source_payload, dict) else ""
            source_length = len(source) if isinstance(source, str) else 0
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read(20_000).decode("utf-8", errors="replace"))
            value = payload.get("value", {}) if isinstance(payload, dict) else {}
            message = value.get("message", payload.get("message", "")) if isinstance(value, dict) else ""
            error = f"Appium HTTP {exc.code}: {str(message or exc.reason)[:300]}"
        except (ValueError, OSError):
            error = f"Appium HTTP {exc.code}: {str(exc.reason)[:300]}"
        finally:
            exc.close()
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        error = f"{type(exc).__name__}: {str(exc)[:350]}"
    finally:
        if session_id:
            delete = Request(f"{safe_server}/session/{session_id}", method="DELETE")
            try:
                with _OPENER.open(delete, timeout=10):
                    session_closed = True
            except Exception:
                error = error or "No se pudo cerrar la sesión Appium."

    passed = bool(session_id) and not error and source_length > 0 and session_closed
    return {
        "status": "passed" if passed else "failed",
        "summary": "Appium inició la app y obtuvo el árbol de interfaz." if passed else "No se completó el smoke test móvil.",
        "output": json.dumps({"platform": platform_key, "device": device_name.strip(),
                              "app": app_path, "session_created": bool(session_id),
                              "ui_source_characters": source_length, "session_closed": session_closed,
                              "error": error or None}, ensure_ascii=False),
    }
