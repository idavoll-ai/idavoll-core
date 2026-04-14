from __future__ import annotations

from typing import Any

import pytest

from idavoll import IdavollApp, IdavollConfig
from idavoll.session.session import Message
from vingolf import VingolfApp, VingolfConfig
from vingolf.persistence.database import Database
from vingolf.persistence.session_repo import SessionRecordRepository, SQLiteSessionSearch


class StubSummarizer:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[tuple[list[Any], dict[str, Any]]] = []

    async def generate(self, messages: list[Any], **kwargs: Any) -> str:
        self.calls.append((messages, kwargs))
        return self.reply


@pytest.mark.asyncio
async def test_close_session_persists_raw_transcript(fake_llm, tmp_path) -> None:
    core = IdavollApp(
        llm=fake_llm,
        config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
    )
    app = VingolfApp(core, config=VingolfConfig(db_path=str(tmp_path / "vingolf.db")))
    await app.startup()

    try:
        agent = await app.create_agent("Alice", "测试 Agent")
        session = await core.create_session([agent])
        session.add_message(
            Message(
                agent_id="user:test",
                agent_name="User",
                role="user",
                content="请记录这段对话",
            )
        )
        session.add_message(
            Message(
                agent_id=agent.id,
                agent_name=agent.name,
                role="assistant",
                content="已经记录。",
            )
        )

        await core.close_session(session)

        records = await app._session_repo.list_recent()  # type: ignore[union-attr]
        assert len(records) == 1
        record = records[0]
        assert record.session_id == session.id
        assert f"[user] User: 请记录这段对话" in record.conversation
        assert f"[assistant] {agent.name}: 已经记录。" in record.conversation
        assert agent.id in record.participants.split(",")
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_topic_close_persists_raw_transcript(fake_llm, tmp_path) -> None:
    core = IdavollApp(
        llm=fake_llm,
        config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
    )
    app = VingolfApp(core, config=VingolfConfig(db_path=str(tmp_path / "vingolf.db")))
    await app.startup()

    try:
        agent = await app.create_agent("Bob", "测试 Agent")
        topic = await app.create_topic(
            title="迁移讨论",
            description="验证 topic.closed 持久化",
            agents=[agent],
        )
        await app.add_user_post(topic.id, "Tester", "这是 topic 里的第一条消息")
        await app.close_topic(topic.id)

        records = await app._session_repo.list_recent()  # type: ignore[union-attr]
        assert len(records) == 1
        record = records[0]
        assert record.session_id == topic.session_id
        assert "Tester: 这是 topic 里的第一条消息" in record.conversation
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_sqlite_session_search_summarizes_on_demand(tmp_path) -> None:
    db = Database(tmp_path / "session-search.db")
    await db.init()
    try:
        repo = SessionRecordRepository(db)
        await repo.save(
            "session-1",
            participants="agent-1,agent-2",
            conversation=(
                "[user] User: 我们要不要继续使用 SQLite 作为持久化层？\n"
                "[assistant] AgentA: 结论是保留 SQLite，全量对话先落库，需要时再检索总结。"
            ),
        )
        summarizer = StubSummarizer("结论：继续使用 SQLite 持久化原始会话，需要时按查询生成总结。")
        search = SQLiteSessionSearch(repo, agent_id="agent-1", llm=summarizer)

        result = await search.search("SQLite 持久化")

        assert "继续使用 SQLite 持久化原始会话" in result
        assert "[Session session-]" in result
        assert len(summarizer.calls) == 1
    finally:
        await db.close()
