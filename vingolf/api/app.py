"""FastAPI application factory for Vingolf."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vingolf.api import state
from vingolf.api.routers import agents, topics


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

        resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        vingolf = VingolfApp.from_yaml(
            config_path,
            api_key=resolved_key,
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

    app.include_router(agents.router)
    app.include_router(topics.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, Any]:
        vingolf = state.get_app()
        return {
            "status": "ok",
            "agents": len(vingolf.agents.all()),
            "topics": len(vingolf.all_topics()),
        }

    return app
