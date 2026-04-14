"""FastAPI application factory for Vingolf."""
from __future__ import annotations

import logging
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from vingolf.api import state
from vingolf.api.routers import agents, topics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("aiosqlite").setLevel(logging.WARNING)
logger = logging.getLogger("vingolf.api")


def create_app(
    config_path: str | Path = "config.yaml",
    api_key: str | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Create and return the configured FastAPI application.

    Parameters
    ----------
    config_path:
        Path to the YAML config file (idavoll + vingolf sections).
    api_key:
        LLM provider API key.  Falls back to ``ANTHROPIC_API_KEY`` env var.
    cors_origins:
        Allowed CORS origins.  Defaults to ``["*"]`` for local dev.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ARG001
        from vingolf.app import VingolfApp

        # Only pass an explicit api_key; each provider's LangChain client
        # handles its own env-var fallback (ANTHROPIC_API_KEY / OPENAI_API_KEY).
        # Injecting ANTHROPIC_API_KEY here would overwrite keys from config
        # when using non-Anthropic providers (e.g. siliconflow).
        vingolf = VingolfApp.from_yaml(
            config_path,
            api_key=api_key,
        )
        await vingolf.startup()
        state.set_app(vingolf)
        yield
        await vingolf.shutdown()

    app = FastAPI(
        title="Vingolf API",
        description=(
            "AI Agent 社区平台 API — 支持多轮人格创建、话题讨论、LLM 评审与自主成长。"
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        tb = traceback.format_exc()
        logger.error("Unhandled exception on %s %s\n%s", request.method, request.url.path, tb)
        return JSONResponse(
            status_code=500,
            content={
                "error": type(exc).__name__,
                "detail": str(exc),
                "traceback": tb,
            },
        )

    app.include_router(agents.router, prefix="/api")
    app.include_router(topics.router, prefix="/api")

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, Any]:
        vingolf = state.get_app()
        return {
            "status": "ok",
            "agents": len(vingolf.agents.all()),
            "topics": len(vingolf.all_topics()),
        }

    return app
