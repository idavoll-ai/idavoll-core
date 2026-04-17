from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from vingolf.api import state
from vingolf.api.schemas import (
    AgentOut,
    AgentProgressOut,
    GrowthDirectiveOut,
    AgentTopicOut,
    BootstrapChatRequest,
    BootstrapChatResponse,
    CreateAgentRequest,
    MembershipOut,
    RefineSoulRequest,
    RefineSoulTextRequest,
    ReviewRecordOut,
    ReviewStrategyResultOut,
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


def _review_record_out(record: dict) -> ReviewRecordOut:
    return ReviewRecordOut(
        id=record["id"],
        trigger_type=record["trigger_type"],
        topic_id=record["topic_id"],
        session_id=record.get("session_id"),
        target_type=record["target_type"],
        target_id=record["target_id"],
        agent_id=record["agent_id"],
        agent_name=record["agent_name"],
        quality_score=record["quality_score"],
        confidence=record["confidence"],
        summary=record["summary"],
        growth_priority=record["growth_priority"],
        status=record["status"],
        error_message=record.get("error_message"),
        created_at=record["created_at"],
        strategy_results=[
            ReviewStrategyResultOut(
                reviewer_name=item["reviewer_name"],
                status=item["status"],
                dimension=item["dimension"],
                score=item["score"],
                confidence=item["confidence"],
                evidence=list(item.get("evidence", [])),
                concerns=list(item.get("concerns", [])),
                parse_failed=bool(item.get("parse_failed", False)),
                summary=item["summary"],
                raw_output=item.get("raw_output", ""),
            )
            for item in record.get("strategy_results", [])
        ],
        growth_directives=[
            GrowthDirectiveOut(
                kind=item["kind"],
                priority=item["priority"],
                content=item["content"],
                rationale=item["rationale"],
                agent_decision=item.get("agent_decision"),
                decision_rationale=item.get("decision_rationale"),
                final_content=item.get("final_content"),
                decided_at=item.get("decided_at"),
                ttl_days=item.get("ttl_days"),
            )
            for item in record.get("growth_directives", [])
        ],
    )


# ---------------------------------------------------------------------------
# Bootstrap conversation (对话式创建 SOUL.md，Agent 创建前调用)
# ---------------------------------------------------------------------------

@router.post("/bootstrap/stream")
async def bootstrap_chat_stream(body: BootstrapChatRequest) -> StreamingResponse:
    """对话式设计 SOUL.md — SSE 流式版本。

    每个事件格式：``data: {"type": "token"|"soul"|"error"|"done", ...}``
    """
    app = state.get_app()

    async def _gen():
        async for line in app.bootstrap_chat_stream(
            body.name,
            [m.model_dump() for m in body.messages],
        ):
            yield line

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    from idavoll.agent.profile import ProfileManager
    app = state.get_app()
    soul_spec = await app._app.refine_soul_stateless(
        body.name, body.current_soul, body.feedback
    )
    tmp_profile = AgentProfile(name=body.name)
    rendered = ProfileManager.render_soul(tmp_profile, soul_spec)
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


@router.get("/{agent_id}/reviews", response_model=list[ReviewRecordOut])
async def get_agent_reviews(agent_id: str) -> list[ReviewRecordOut]:
    """返回该 Agent 的最近 review records，包括 hot interactions。"""
    app = state.get_app()
    if app.agents.get(agent_id) is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    records = await app.get_review_records_for_agent(agent_id)
    return [_review_record_out(record) for record in records]


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


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str) -> dict[str, bool]:
    """删除 Agent，同时移除其 workspace、progress 和 topic membership。"""
    app = state.get_app()
    agent = app.agents.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    await app.delete_agent(agent_id)
    return {"ok": True}


@router.post("/{agent_id}/consolidate")
async def consolidate_agent(agent_id: str) -> dict[str, int]:
    """将该 Agent 的 pending GrowthDirectives 批量提升：
    memory_candidate → 写入长期 memory；reflection_candidate → 触发 hook。
    返回 {"applied": N}。
    """
    app = state.get_app()
    if app.agents.get(agent_id) is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    if app.consolidation is None:
        raise HTTPException(status_code=503, detail="ConsolidationService not available (call startup first)")
    applied = await app.consolidation.consolidate(agent_id)
    return {"applied": applied}


@router.post("/consolidate/all")
async def consolidate_all() -> dict[str, int]:
    """对所有 Agent 执行一次 GrowthDirective 合并。返回 {agent_id: applied_count}。"""
    app = state.get_app()
    if app.consolidation is None:
        raise HTTPException(status_code=503, detail="ConsolidationService not available")
    result = await app.consolidation.consolidate_all()
    return result
