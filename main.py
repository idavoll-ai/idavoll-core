"""Vingolf API server entry point.

Usage:
    uv run main.py
    # or
    uvicorn main:app --reload

Environment:
    ANTHROPIC_API_KEY  — required for Anthropic models
    CONFIG_PATH        — path to config YAML, default: config.yaml
"""
import os

from vingolf.api.app import create_app

app = create_app(
    config_path=os.getenv("CONFIG_PATH", "config.yaml"),
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
