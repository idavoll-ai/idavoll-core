"""
Demo 1 — Agent persistence via YAML + load_agent

Two-phase workflow:
  Phase 1 (setup): Create agents via natural-language description, compile
                   their profiles with the LLM, and save them as YAML files
                   under agents/.
  Phase 2 (run):   Load agents straight from the YAML files (no LLM call
                   needed), then run the same forum discussion as demo.py.

Run Phase 1 once to create the YAML files:
    uv run python example/demo1.py --setup

Then run Phase 2 as many times as you like:
    uv run python example/demo1.py
"""

import argparse
import asyncio
from pathlib import Path

from vingolf import VingolfApp

CONFIG = Path(__file__).parent.parent / "config.yaml"
AGENTS_DIR = Path(__file__).parent.parent / "data" / "agents"
MEMORY_DIR = Path(__file__).parent.parent / "data" / "memory"

# Agent definitions — edit these to customise who participates.
AGENT_DEFS = [
    {
        "name": "Alice",
        "description": (
            "A sharp philosophy professor specialising in AI ethics. "
            "She argues from first principles, citing Kant and Rawls."
        ),
    },
    {
        "name": "Bob",
        "description": (
            "An optimistic startup founder who believes technology solves everything. "
            "He favours pragmatic, market-driven arguments."
        ),
    },
]

TOPIC = dict(
    title="Should AI have legal personhood?",
    description=(
        "Explore whether artificial intelligence systems should be granted "
        "legal personhood — with rights and responsibilities similar to corporations."
    ),
    tags=["AI", "ethics", "law"],
    rounds=4,
)


# ── Phase 1: create and persist agents ───────────────────────────────────────

async def setup() -> None:
    """Compile agent profiles with the LLM and write them to YAML files."""
    app = VingolfApp.from_yaml(CONFIG, agents_dir=AGENTS_DIR, memory_dir=MEMORY_DIR)

    print(f"Creating agents → {AGENTS_DIR}/\n")
    for defn in AGENT_DEFS:
        yaml_path = AGENTS_DIR / f"{defn['name']}.yaml"
        if yaml_path.exists():
            print(f"  {defn['name']:8s} already exists, skipping  ({yaml_path})")
            continue

        agent = await app.create_agent(defn["name"], defn["description"])
        print(f"  {agent.profile.name:8s} {agent.profile.identity.role}")
        print(f"           saved → {yaml_path}")

    print("\nDone. Run without --setup to start the discussion.")


# ── Phase 2: load agents and run discussion ───────────────────────────────────

async def run() -> None:
    """Load agents from YAML and run the forum discussion."""
    # Verify YAML files exist before creating the app
    missing = [
        AGENTS_DIR / f"{d['name']}.yaml"
        for d in AGENT_DEFS
        if not (AGENTS_DIR / f"{d['name']}.yaml").exists()
    ]
    if missing:
        paths = ", ".join(str(p) for p in missing)
        raise FileNotFoundError(
            f"Agent YAML file(s) not found: {paths}\n"
            "Run  uv run python example/demo1.py --setup  first."
        )

    app = VingolfApp.from_yaml(CONFIG, agents_dir=AGENTS_DIR, memory_dir=MEMORY_DIR)

    print("Loading agents from YAML…")
    agents = []
    for defn in AGENT_DEFS:
        yaml_path = AGENTS_DIR / f"{defn['name']}.yaml"
        agent = app.load_agent(yaml_path)
        mem_path = app._app.repo.memory_path_for_name(defn["name"])
        total_memories = sum(len(v) for v in agent.memory.entries.values())
        mem_note = f"  ({total_memories} memories from {mem_path.name})" if total_memories else ""
        print(f"  {agent.profile.name:8s} {agent.profile.identity.role}{mem_note}")
        agents.append(agent)

    print("\nRunning discussion…")
    topic, summary = await app.run(agents=agents, **TOPIC)

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


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Create agent YAML files (calls the LLM to compile profiles).",
    )
    args = parser.parse_args()

    asyncio.run(setup() if args.setup else run())
