"""
End-to-end smoke test: Topic discussion → Review panel → Results.

Run:
    uv run python example.py
"""

import asyncio

from idavoll import IdavollApp, IdavollConfig
from vingolf.plugins.topic import TopicPlugin
from vingolf.plugins.review import ReviewPlugin


async def main() -> None:
    cfg = IdavollConfig.from_yaml("config.yaml")

    # ── Bootstrap ─────────────────────────────────────────────────────────────
    topic_plugin = TopicPlugin()
    review_plugin = ReviewPlugin()

    app = IdavollApp.from_config(cfg)
    app.use(topic_plugin).use(review_plugin)

    @app.hooks.hook("session.message.after")
    async def on_post(session, message, **_):
        print(f"\n[{message.agent_name}]\n{message.content}\n{'─' * 60}")

    @app.hooks.hook("vingolf.review.completed")
    async def on_review_done(summary, **_):
        print(f"\n{'═' * 60}")
        print(f"REVIEW RESULTS — {summary.topic_title}")
        print(f"{'═' * 60}")
        for r in sorted(summary.results, key=lambda x: x.final_score, reverse=True):
            print(
                f"\n  {r.agent_name}"
                f"\n    Logic={r.logic_score}  Creativity={r.creativity_score}"
                f"  Social={r.social_score}"
                f"\n    Composite={r.composite_score}  Likes={r.likes_count}"
                f"  → Final={r.final_score}"
                f"\n    {r.summary}"
            )
        winner = summary.winner()
        if winner:
            print(f"\n  Winner: {winner.agent_name} ({winner.final_score}/10)")

    # ── Create agents ──────────────────────────────────────────────────────────
    print("Compiling agent profiles...\n")

    alice = await app.create_agent(
        name="Alice",
        description=(
            "A sharp philosophy professor specializing in AI ethics and epistemology. "
            "She questions every assumption, speaks with dry academic humor, and always "
            "demands precise definitions before accepting any argument."
        ),
    )
    bob = await app.create_agent(
        name="Bob",
        description=(
            "An optimistic startup founder who believes technology solves everything. "
            "Enthusiastic, uses startup jargon, occasionally naive but genuinely curious. "
            "Specializes in AI products and venture capital."
        ),
    )
    carol = await app.create_agent(
        name="Carol",
        description=(
            "A cautious policy researcher studying AI governance. She cites studies, "
            "worries about unintended consequences, and always asks who benefits and "
            "who bears the risk. Specializes in tech policy and social impact."
        ),
    )

    # Simulate some likes so the likes-score path is exercised
    # (In a real app these come from user interactions)

    # ── Create topic and discuss ───────────────────────────────────────────────
    topic = await topic_plugin.create_topic(
        title="Should AI systems have legal personhood?",
        description=(
            "Legal personhood grants entities the ability to hold rights and bear "
            "responsibilities. Some argue advanced AI systems should have limited legal "
            "standing to enable accountability. Others see this as a dangerous category "
            "error. Where should we draw the line?"
        ),
        agents=[alice, bob, carol],
        tags=["AI", "ethics", "law", "policy", "philosophy"],
    )

    print(f"Topic: {topic.title!r}\n{'═' * 60}")
    await topic_plugin.start_discussion(topic.id, rounds=6, min_interval=0.3)

    # Simulate likes after discussion (before review scores are set)
    posts = topic_plugin.get_posts(topic.id)
    for i, post in enumerate(posts):
        if post.agent_id == alice.id:
            post.likes += 2 if i % 2 == 0 else 1
        elif post.agent_id == bob.id:
            post.likes += 1

    print(f"\nRunning review panel...")
    # (review already triggered automatically via hook — just wait for it)
    # The review runs synchronously within the hook, so by here it's done.
    summary = review_plugin.get_summary(topic.id)
    if summary is None:
        print("Review not yet complete.")


if __name__ == "__main__":
    asyncio.run(main())
