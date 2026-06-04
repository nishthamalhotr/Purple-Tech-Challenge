"""
Store Intelligence System — FastAPI application entry point.

This module wires together all routers and middleware.
Run with: uvicorn app.main:app --reload
"""
from __future__ import annotations

import time
import uuid
import logging
import json
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.database import init_db
from app.routes.events import router as events_router
from app.routes.stores import router as stores_router

# Structured JSON logger
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if hasattr(record, "extra"):
            log.update(record.extra)
        return json.dumps(log)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("store_intelligence")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB tables on startup."""
    logger.info({"event": "startup", "message": "Initializing database"})
    await init_db()
    logger.info({"event": "startup", "message": "Database ready"})
    yield
    logger.info({"event": "shutdown"})


app = FastAPI(
    title="Store Intelligence API",
    version="1.0.0",
    description=(
        "Purplle Tech Challenge 2026 — Round 2\n\n"
        "Processes CCTV events to compute real-time store metrics, "
        "visitor sessions, conversion funnels, zone heatmaps, and anomalies."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next: Callable) -> Response:
    """Structured request/response logging with trace_id for correlation."""
    trace_id = str(uuid.uuid4())
    start = time.perf_counter()
    request.state.trace_id = trace_id

    response = await call_next(request)

    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    logger.info(json.dumps({
        "trace_id": trace_id,
        "method": request.method,
        "path": request.url.path,
        "status_code": response.status_code,
        "latency_ms": latency_ms,
        "store_id": request.path_params.get("store_id"),
        "event_count": getattr(request.state, "event_count", None),
    }))

    response.headers["X-Trace-ID"] = trace_id
    return response


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", None)
    logger.error(json.dumps({
        "event": "db_error",
        "trace_id": trace_id,
        "path": request.url.path,
        "error": str(exc),
    }))
    return JSONResponse(status_code=503, content={
        "detail": "Database unavailable",
        "status": "error",
        "trace_id": trace_id,
    })


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, HTTPException):
        raise exc

    trace_id = getattr(request.state, "trace_id", None)
    logger.error(json.dumps({
        "event": "internal_error",
        "trace_id": trace_id,
        "path": request.url.path,
        "error": str(exc),
    }), exc_info=exc)
    return JSONResponse(status_code=500, content={
        "detail": "Internal server error",
        "status": "error",
        "trace_id": trace_id,
    })


@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok", "service": "store-intelligence-api"}


app.include_router(events_router)
app.include_router(stores_router)
