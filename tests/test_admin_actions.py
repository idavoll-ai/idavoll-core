from __future__ import annotations

from idavoll import IdavollApp, IdavollConfig
from vingolf import VingolfApp, VingolfConfig

import pytest


@pytest.mark.asyncio
async def test_reopen_closed_topic_allows_new_posts(fake_llm, tmp_path) -> None:
    app = VingolfApp(
        IdavollApp(
            llm=fake_llm,
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        ),
        config=VingolfConfig(db_path=str(tmp_path / "vingolf.db")),
    )
    await app.startup()
    try:
        agent = await app.create_agent("Alice", "测试 Agent")
        topic = await app.create_topic(
            title="Reopen Test",
            description="验证重开后能继续讨论",
            agents=[agent],
        )
        await app.add_user_post(topic.id, "User", "第一条消息")
        await app.close_topic(topic.id)

        reopened = await app.reopen_topic(topic.id)
        assert reopened.lifecycle.value == "open"
        assert app.get_review(topic.id) is None

        await app.add_user_post(topic.id, "User", "重开后的新消息")
        posts = app.get_posts(topic.id)
        assert [p.content for p in posts][-1] == "重开后的新消息"
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_delete_topic_removes_topic_and_session_record(fake_llm, tmp_path) -> None:
    app = VingolfApp(
        IdavollApp(
            llm=fake_llm,
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        ),
        config=VingolfConfig(db_path=str(tmp_path / "vingolf.db")),
    )
    await app.startup()
    try:
        agent = await app.create_agent("Bob", "测试 Agent")
        topic = await app.create_topic(
            title="Delete Topic",
            description="验证删除话题",
            agents=[agent],
        )
        await app.add_user_post(topic.id, "User", "topic 内容")
        await app.close_topic(topic.id)

        await app.delete_topic(topic.id)

        assert app.get_topic(topic.id) is None
        records = await app._session_repo.list_recent()  # type: ignore[union-attr]
        assert all(r.session_id != topic.session_id for r in records)
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_delete_agent_removes_workspace_progress_and_membership(fake_llm, tmp_path) -> None:
    app = VingolfApp(
        IdavollApp(
            llm=fake_llm,
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        ),
        config=VingolfConfig(db_path=str(tmp_path / "vingolf.db")),
    )
    await app.startup()
    try:
        agent = await app.create_agent("Carol", "测试 Agent")
        topic = await app.create_topic(
            title="Membership Cleanup",
            description="验证删除 Agent 后会移出话题",
            agents=[agent],
        )
        await app.close_topic(topic.id)

        workspace_path = tmp_path / "ws" / agent.id
        assert workspace_path.exists()

        await app.delete_agent(agent.id)

        assert app.agents.get(agent.id) is None
        assert app.get_progress(agent.id) is None
        assert not workspace_path.exists()
        updated_topic = app.get_topic(topic.id)
        assert updated_topic is not None
        assert agent.id not in updated_topic.memberships
    finally:
        await app.shutdown()
