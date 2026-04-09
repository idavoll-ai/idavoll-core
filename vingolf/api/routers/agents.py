from __future__ import annotations

from fastapi import APIRouter, HTTPException

from vingolf.api import state
from vingolf.api.schemas import (
    AgentOut,
    AgentProgressOut,
    CreateAgentRequest,
    RefineSoulRequest,
    SoulPreviewOut,
)

router = APIRouter(prefix="/agents", tags=["agents"])


def _agent_out(app, agent) -> AgentOut:
    progress = app.get_progress(agent.id)
    return AgentOut(
        id=agent.id,
        name=agent.name,
        description=agent.profile.description,
        level=progress.level if progress else 1,
        xp=progress.xp if progress else 0,
        context_budget=agent.profile.budget.total,
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(body: CreateAgentRequest) -> AgentOut:
    """创建个性化 Agent，自动生成 SOUL.md。"""
    app = state.get_app()
    agent = await app.create_agent(body.name, body.description)
    return _agent_out(app, agent)


@router.get("", response_model=list[AgentOut])
def list_agents() -> list[AgentOut]:
    """列出所有已注册 Agent。"""
    app = state.get_app()
    return [_agent_out(app, a) for a in app.agents.all()]


@router.get("/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: str) -> AgentOut:
    app = state.get_app()
    agent = app.agents.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    return _agent_out(app, agent)


# ---------------------------------------------------------------------------
# Multi-turn SOUL.md creation
# ---------------------------------------------------------------------------

@router.get("/{agent_id}/soul", response_model=SoulPreviewOut)
def preview_soul(agent_id: str) -> SoulPreviewOut:
    """返回当前 SOUL.md 原文，供前端展示给用户预览。"""
    app = state.get_app()
    agent = app.agents.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    return SoulPreviewOut(soul=app.preview_soul(agent))


@router.post("/{agent_id}/soul/refine", response_model=SoulPreviewOut)
async def refine_soul(agent_id: str, body: RefineSoulRequest) -> SoulPreviewOut:
    """根据用户反馈更新 SOUL.md（多轮对话创建人格）。

    前端流程：
      1. `GET /agents/{id}/soul`   → 展示当前草稿
      2. 用户输入修改意见
      3. `POST /agents/{id}/soul/refine` → 返回更新后草稿
      4. 重复直到用户满意
    """
    app = state.get_app()
    agent = app.agents.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    updated = await app.refine_soul(agent, body.feedback)
    return SoulPreviewOut(soul=updated)


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

@router.get("/{agent_id}/progress", response_model=AgentProgressOut)
def get_progress(agent_id: str) -> AgentProgressOut:
    """返回 Agent 的当前等级和经验值。"""
    app = state.get_app()
    agent = app.agents.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    progress = app.get_progress(agent_id)
    if progress is None:
        return AgentProgressOut(agent_id=agent_id, xp=0, level=1)
    return AgentProgressOut(agent_id=agent_id, xp=progress.xp, level=progress.level)
