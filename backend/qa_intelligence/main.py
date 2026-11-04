"""Punto de entrada de la API de QA Intelligence AI."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from qa_intelligence.config import settings
from qa_intelligence.api import router
from qa_intelligence.store import initialize

APP_NAME = settings.app_name
LOG_LEVEL = settings.log_level.upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize()
    yield


app = FastAPI(
    title=APP_NAME,
    description="API inicial para una plataforma de ingeniería de calidad de software.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(router)


@app.get("/health", tags=["Sistema"], summary="Estado del servicio")
def health() -> dict[str, str]:
    """Devuelve una respuesta verificable de disponibilidad de la API."""
    logger.info("Se consultó el estado del servicio")
    return {"status": "ok", "service": APP_NAME}
