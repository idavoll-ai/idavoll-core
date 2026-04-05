"""Integration test: real LLM call via config.yaml.

Run with:
    pytest tests/test_llm_integration.py -v -s
"""
from __future__ import annotations

from pathlib import Path

import pytest

CONFIG_YAML = Path(__file__).parent.parent / "config.yaml"


@pytest.fixture(scope="module")
def llm():
    from idavoll.config import IdavollConfig

    cfg = IdavollConfig.from_yaml(CONFIG_YAML)
    return cfg.llm.build()


def test_config_loads():
    """Config parses without error and provider/model are set."""
    from idavoll.config import IdavollConfig

    cfg = IdavollConfig.from_yaml(CONFIG_YAML)
    assert cfg.llm.provider == "deepseek"
    assert cfg.llm.model == "deepseek-chat"
    assert cfg.llm.base_url == "https://api.deepseek.com/v1"


def test_single_turn(llm):
    """Single invoke call returns a non-empty string."""
    from langchain_core.messages import HumanMessage

    response = llm.invoke([HumanMessage(content="用一句话介绍你自己。")])
    print(f"\n[deepseek] {response.content}")
    assert isinstance(response.content, str)
    assert len(response.content) > 0


def test_multi_turn(llm):
    """Multi-turn conversation keeps context."""
    from langchain_core.messages import AIMessage, HumanMessage

    messages = [
        HumanMessage(content="我叫小明，记住我的名字。"),
        AIMessage(content="好的，我记住了，你叫小明。"),
        HumanMessage(content="我叫什么名字？"),
    ]
    response = llm.invoke(messages)
    print(f"\n[deepseek multi-turn] {response.content}")
    assert "小明" in response.content
