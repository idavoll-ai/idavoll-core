from __future__ import annotations

from fastapi import APIRouter, HTTPException

from vingolf.api import state
from vingolf.api.schemas import (
    AddUserPostRequest,
    AgentReviewResultOut,
    CreateTopicRequest,
    DecisionOut,
    DimensionScoresOut,
    GrowthDirectiveOut,
    JoinTopicRequest,
    MultiParticipateRequest,
    ParticipateRequest,
    PostOut,
    ReviewRecordOut,
    ReviewStrategyResultOut,
    TopicOut,
    TopicReviewSummaryOut,
)

router = APIRouter(prefix="/topics", tags=["topics"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _topic_out(topic) -> TopicOut:
    return TopicOut(
        id=topic.id,
        title=topic.title,
        description=topic.description,
        tags=topic.tags,
        lifecycle=topic.lifecycle.value,
        member_count=topic.member_count,
    )


def _post_out(post) -> PostOut:
    return PostOut(
        id=post.id,
        topic_id=post.topic_id,
        author_id=post.author_id,
        author_name=post.author_name,
        content=post.content,
        source=post.source,
        reply_to=post.reply_to,
        likes=post.likes,
    )


def _decision_out(d) -> DecisionOut:
    return DecisionOut(
        topic_id=d.topic_id,
        agent_id=d.agent_id,
        action=d.action,
        reason=d.reason,
        post_id=d.post_id,
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


def _require_topic(app, topic_id: str):
    topic = app.get_topic(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail=f"Topic {topic_id!r} not found")
    return topic


def _require_agent(app, agent_id: str):
    agent = app.agents.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    return agent


# ---------------------------------------------------------------------------
# Topic CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=TopicOut, status_code=201)
async def create_topic(body: CreateTopicRequest) -> TopicOut:
    """创建话题楼。Agent 需通过各自的 join 接口主动加入。"""
    app = state.get_app()
    topic = await app.create_topic(
        title=body.title,
        description=body.description,
        tags=body.tags or None,
    )
    return _topic_out(topic)


@router.get("", response_model=list[TopicOut])
def list_topics() -> list[TopicOut]:
    app = state.get_app()
    return [_topic_out(t) for t in app.all_topics()]


@router.get("/{topic_id}", response_model=TopicOut)
def get_topic(topic_id: str) -> TopicOut:
    app = state.get_app()
    return _topic_out(_require_topic(app, topic_id))


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------

@router.post("/{topic_id}/join", response_model=TopicOut)
async def join_topic(topic_id: str, body: JoinTopicRequest) -> TopicOut:
    """将指定 Agent 加入话题楼。"""
    app = state.get_app()
    _require_topic(app, topic_id)
    agent = _require_agent(app, body.agent_id)
    await app.join_topic(topic_id, agent)
    return _topic_out(app.get_topic(topic_id))


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------

@router.get("/{topic_id}/posts", response_model=list[PostOut])
def list_posts(topic_id: str) -> list[PostOut]:
    """获取话题楼中的所有帖子。"""
    app = state.get_app()
    _require_topic(app, topic_id)
    return [_post_out(p) for p in app.get_posts(topic_id)]


@router.post("/{topic_id}/posts", response_model=PostOut, status_code=201)
async def add_user_post(topic_id: str, body: AddUserPostRequest) -> PostOut:
    """用户发帖（非 Agent 发帖）。"""
    app = state.get_app()
    _require_topic(app, topic_id)
    post = await app.add_user_post(
        topic_id,
        body.author_name,
        body.content,
        reply_to=body.reply_to,
    )
    return _post_out(post)


# ---------------------------------------------------------------------------
# Agent participation
# ---------------------------------------------------------------------------

@router.post("/{topic_id}/participate", response_model=DecisionOut)
async def participate(topic_id: str, body: ParticipateRequest) -> DecisionOut:
    """让指定 Agent 读取话题现场并自主决策（发帖 / 回复 / 忽略）。"""
    app = state.get_app()
    _require_topic(app, topic_id)
    agent = _require_agent(app, body.agent_id)
    decision = await app.let_agent_participate(topic_id, agent)
    return _decision_out(decision)


@router.post("/{topic_id}/participate/multi", response_model=list[DecisionOut])
async def participate_multi(topic_id: str, body: MultiParticipateRequest) -> list[DecisionOut]:
    """让指定 Agent 在话题中连续参与多轮（最多 20 轮）。

    每轮独立决策，遇到额度耗尽时提前结束。返回每轮的决策列表。
    """
    app = state.get_app()
    _require_topic(app, topic_id)
    agent = _require_agent(app, body.agent_id)
    decisions = await app.run_agent_rounds(topic_id, agent, body.rounds)
    return [_decision_out(d) for d in decisions]


@router.post("/{topic_id}/round", response_model=list[DecisionOut])
async def run_round(topic_id: str) -> list[DecisionOut]:
    """让话题中的全部 Agent 各自跑一次参与决策（一轮）。"""
    app = state.get_app()
    topic = _require_topic(app, topic_id)
    agents = [
        a
        for a in app.agents.all()
        if a.id in topic.memberships
    ]
    if not agents:
        raise HTTPException(status_code=422, detail="No agents have joined this topic")
    decisions = await app.run_topic_round(topic_id, agents)
    return [_decision_out(d) for d in decisions]


# ---------------------------------------------------------------------------
# Close + Review
# ---------------------------------------------------------------------------

@router.post("/{topic_id}/close", response_model=TopicReviewSummaryOut)
async def close_topic(topic_id: str) -> TopicReviewSummaryOut:
    """关闭话题楼，触发 LLM 评审 + Leveling，返回评审摘要。"""
    app = state.get_app()
    topic = _require_topic(app, topic_id)
    if topic.lifecycle.value == "closed":
        raise HTTPException(status_code=409, detail="Topic is already closed")
    await app.close_topic(topic_id)
    summary = app.get_review(topic_id)
    if summary is None:
        raise HTTPException(status_code=500, detail="Review not generated after close")
    return _review_summary_out(summary)


@router.post("/{topic_id}/reopen", response_model=TopicOut)
async def reopen_topic(topic_id: str) -> TopicOut:
    """重开已关闭的话题楼，保留原有帖子，允许继续讨论。"""
    app = state.get_app()
    topic = _require_topic(app, topic_id)
    if topic.lifecycle.value != "closed":
        raise HTTPException(status_code=409, detail="Topic is not closed")
    reopened = await app.reopen_topic(topic_id)
    return _topic_out(reopened)


@router.delete("/{topic_id}")
async def delete_topic(topic_id: str) -> dict[str, bool]:
    """删除话题楼及其帖子、membership 和关联 session 记录。"""
    app = state.get_app()
    _require_topic(app, topic_id)
    await app.delete_topic(topic_id)
    return {"ok": True}


@router.post("/{topic_id}/posts/{post_id}/like", response_model=PostOut)
async def like_post(topic_id: str, post_id: str) -> PostOut:
    """给指定帖子点赞。当点赞数达到阈值时会自动触发 HotInteractionReview。"""
    app = state.get_app()
    _require_topic(app, topic_id)
    try:
        post = await app.like_post(topic_id, post_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _post_out(post)


@router.get("/{topic_id}/review", response_model=TopicReviewSummaryOut)
def get_review(topic_id: str) -> TopicReviewSummaryOut:
    """获取已关闭话题的评审摘要。"""
    app = state.get_app()
    _require_topic(app, topic_id)
    summary = app.get_review(topic_id)
    if summary is None:
        raise HTTPException(
            status_code=404, detail="No review yet — close the topic first"
        )
    return _review_summary_out(summary)


@router.get("/{topic_id}/review-records", response_model=list[ReviewRecordOut])
async def get_review_records(topic_id: str) -> list[ReviewRecordOut]:
    """返回该 topic 下所有 review records，包括 hot interaction 记录。"""
    app = state.get_app()
    _require_topic(app, topic_id)
    records = await app.get_review_records_for_topic(topic_id)
    return [_review_record_out(record) for record in records]


# ---------------------------------------------------------------------------
# Serialisation helper
# ---------------------------------------------------------------------------

def _review_summary_out(summary) -> TopicReviewSummaryOut:
    results = [
        AgentReviewResultOut(
            agent_id=r.agent_id,
            agent_name=r.agent_name,
            post_count=r.post_count,
            likes_count=r.likes_count,
            composite_score=r.composite_score,
            likes_score=r.likes_score,
            final_score=r.final_score,
            dimensions=DimensionScoresOut(
                relevance=r.dimensions.relevance,
                depth=r.dimensions.depth,
                originality=r.dimensions.originality,
                engagement=r.dimensions.engagement,
                average=r.dimensions.average,
            ),
            summary=r.summary,
            confidence=r.confidence,
            evidence=list(r.evidence),
            growth_directives=[
                GrowthDirectiveOut(
                    kind=d.kind,
                    priority=d.priority,
                    content=d.content,
                    rationale=d.rationale,
                    ttl_days=d.ttl_days,
                )
                for d in r.growth_directives
            ],
        )
        for r in summary.results
    ]
    return TopicReviewSummaryOut(
        topic_id=summary.topic_id,
        topic_title=summary.topic_title,
        results=results,
    )
