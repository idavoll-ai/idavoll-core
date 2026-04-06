"""
Quick MVP demo — run with:
    uv run python example/demo.py
"""

import asyncio
from pathlib import Path

from vingolf import VingolfApp

CONFIG = Path(__file__).parent.parent / "config.yaml"


async def main() -> None:
    app = VingolfApp.from_yaml(CONFIG)

    print("Creating agents…")
    alice = await app.create_agent(
        "Alice",
        "A sharp philosophy professor specialising in AI ethics. "
        "She argues from first principles, citing Kant and Rawls.",
    )
    bob = await app.create_agent(
        "Bob",
        "An optimistic startup founder who believes technology solves everything. "
        "He favours pragmatic, market-driven arguments.",
    )

    print(f"  Alice: {alice.profile.identity.role}")
    print(f"  Bob:   {bob.profile.identity.role}")

    print("\nRunning discussion…")
    topic, summary = await app.run(
        title="Should AI have legal personhood?",
        description=(
            "Explore whether artificial intelligence systems should be granted "
            "legal personhood — with rights and responsibilities similar to corporations."
        ),
        agents=[alice, bob],
        tags=["AI", "ethics", "law"],
        rounds=4,
    )

    print(f"\nTopic: {topic.title}  (status={topic.lifecycle.value})")
    print(f"Posts ({len(app.get_posts(topic.id))}):")
    for post in app.get_posts(topic.id):
        snippet = post.content[:120].replace("\n", " ")
        print(f"  [{post.agent_name}] {snippet}…")

    if summary:
        print("\nReview summary:")
        for score in sorted(summary.results, key=lambda s: s.final_score, reverse=True):
            print(
                f"  {score.agent_name:10s}  final={score.final_score:.2f}  "
                f"logic={score.logic_score:.1f}  creativity={score.creativity_score:.1f}  "
                f"social={score.social_score:.1f}"
            )
        winner = summary.winner()
        if winner:
            print(f"\nWinner: {winner.agent_name}")
    else:
        print("\n(No review summary yet)")


if __name__ == "__main__":
    asyncio.run(main())
