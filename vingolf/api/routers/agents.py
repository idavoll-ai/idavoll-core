from __future__ import annotations

from fastapi import APIRouter, HTTPException

from vingolf.api import state
from vingolf.api.schemas import (
    AgentOut,
    AgentProgressOut,
    AgentTopicOut,
    BootstrapChatRequest,
    BootstrapChatResponse,
    CreateAgentRequest,
    MembershipOut,
    RefineSoulRequest,
    RefineSoulTextRequest,
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
# Bootstrap conversation (对话式创建 SOUL.md，Agent 创建前调用)
# ---------------------------------------------------------------------------

@router.post("/bootstrap/chat", response_model=BootstrapChatResponse)
async def bootstrap_chat(body: BootstrapChatRequest) -> BootstrapChatResponse:
    """对话式设计 SOUL.md。

    前端流程：
      1. 用户在对话框中描述 Agent 人格
      2. 每次用户发送消息，前端把完整对话历史发送到此接口
      3. 后端 AI 回复（reply），当信息充足时同时返回 soul（SOUL.md 文本）
      4. 前端收到 soul 后切换到预览+确认页面
      5. 用户确认后调用 POST /agents 正式创建
    """
    app = state.get_app()
    reply, soul = await app.bootstrap_chat(
        body.name,
        [m.model_dump() for m in body.messages],
    )
    return BootstrapChatResponse(reply=reply, soul=soul)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.post("/soul/refine", response_model=SoulPreviewOut)
async def refine_soul_text(body: RefineSoulTextRequest) -> SoulPreviewOut:
    """无状态 SOUL.md 调整 — 不需要已有 Agent，预览阶段专用。"""
    from idavoll.agent.profile import AgentProfile
    from idavoll.agent.workspace import ProfileWorkspaceManager
    app = state.get_app()
    soul_spec = await app._app.profile_service.refine(
        body.name, body.current_soul, body.feedback
    )
    tmp_profile = AgentProfile(name=body.name)
    rendered = ProfileWorkspaceManager.render_soul(tmp_profile, soul_spec)
    return SoulPreviewOut(soul=rendered)


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(body: CreateAgentRequest) -> AgentOut:
    """创建个性化 Agent。

    - 仅传 ``description``：后端会重新生成 SOUL.md
    - 同时传 ``soul``：后端会直接持久化确认后的 SOUL.md
    """
    app = state.get_app()
    if body.soul:
        agent = await app.create_agent_from_soul(
            body.name,
            body.description,
            body.soul,
        )
    else:
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
# Topics joined by this agent
# ---------------------------------------------------------------------------

@router.get("/{agent_id}/topics", response_model=list[AgentTopicOut])
def get_agent_topics(agent_id: str) -> list[AgentTopicOut]:
    """返回该 Agent 已加入的所有话题及其参与状态。"""
    app = state.get_app()
    if app.agents.get(agent_id) is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    pairs = app.get_agent_topics(agent_id)
    result = []
    for topic, membership in pairs:
        result.append(AgentTopicOut(
            id=topic.id,
            title=topic.title,
            description=topic.description,
            tags=topic.tags,
            lifecycle=topic.lifecycle.value,
            member_count=topic.member_count,
            membership=MembershipOut(
                joined_at=membership.joined_at.isoformat(),
                initiative_posts=membership.initiative_posts,
                reply_posts=membership.reply_posts,
                last_post_at=membership.last_post_at.isoformat() if membership.last_post_at else None,
            ),
        ))
    return result


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
