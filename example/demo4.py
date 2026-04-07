"""
Demo 4 — Multiple Topics with Dynamic Participation

This demo shows:
- Multiple agents (Alice, Bob, Carol, Dave, Eve) across multiple concurrent topics
- Each topic runs in its own asyncio task (真正并发)
- Agents can dynamically join / leave topics mid-discussion
- Post history is recorded and printed per topic at the end

Two-phase workflow:

  Phase 1 — compile agent profiles with the LLM and save YAML:
      uv run python example/demo4.py --setup

  Phase 2 — load agents and run the multi-topic discussion:
      uv run python example/demo4.py
"""

import argparse
import asyncio
import textwrap
from pathlib import Path

from vingolf import VingolfApp
from vingolf.config import VingolfConfig
from vingolf.plugins.review import ReviewPlugin
from vingolf.plugins.topic import TopicPlugin

CONFIG     = Path(__file__).parent.parent / "config.yaml"
AGENTS_DIR = Path(__file__).parent.parent / "data" / "agents"
MEMORY_DIR = Path(__file__).parent.parent / "data" / "memory"

AGENT_DEFS = [
    {
        "name": "Alice",
        "description": (
            "A critical-thinking AI safety researcher. "
            "She insists on rigorous alignment before deployment."
        ),
    },
    {
        "name": "Bob",
        "description": (
            "An optimistic entrepreneur building AI products. "
            "He believes iteration speed beats theoretical perfection."
        ),
    },
    {
        "name": "Carol",
        "description": (
            "A policy analyst at a think-tank. "
            "She frames arguments around regulation, incentives, and societal impact."
        ),
    },
    {
        "name": "Dave",
        "description": (
            "A philosopher specializing in ethics of technology. "
            "He approaches AI debates from a moral and ethical perspective."
        ),
    },
    {
        "name": "Eve",
        "description": (
            "A journalist covering AI developments. "
            "She asks probing questions and challenges assumptions to get to the heart of the matter."
        ),
    },
]

# Each topic specifies which agents start in it, plus optional dynamic events.
# "join_after"  : {post_count_in_topic: agent_name}  — join when N posts made
# "leave_after" : {post_count_in_topic: agent_name}  — leave when N posts made
TOPIC_SPECS = [
    dict(
        title="Open-sourcing frontier AI models: good or dangerous?",
        description=(
            "Should leading AI labs open-source their most powerful models, "
            "or keep them under wraps? Consider the implications for safety, "
            "innovation, and competition."
        ),
        tags=["AI", "open-source", "safety"],
        initial_agents=["Alice", "Bob", "Carol"],
        join_after={3: "Dave"},       # Dave joins after 3 posts
        leave_after={6: "Alice"},     # Alice leaves after 6 posts
        rounds=9,
    ),
    dict(
        title="AI regulation: proactive vs reactive approaches",
        description=(
            "What's the best way to regulate rapidly advancing AI technologies? "
            "Some advocate for proactive rules and oversight, while others prefer "
            "a reactive, case-by-case approach. Discuss the merits and risks."
        ),
        tags=["AI", "policy", "regulation"],
        initial_agents=["Carol", "Dave", "Eve"],
        join_after={2: "Bob"},        # Bob joins after 2 posts
        leave_after={},
        rounds=8,
    ),
    dict(
        title="AI and the future of work: utopia or dystopia?",
        description=(
            "Will AI lead to a utopian future of abundance and leisure, or a "
            "dystopian landscape of unemployment and inequality? Examine the "
            "economic, social, and ethical dimensions."
        ),
        tags=["AI", "economics", "society"],
        initial_agents=["Alice", "Eve"],
        join_after={2: "Carol", 4: "Bob"},
        leave_after={6: "Eve"},
        rounds=8,
    ),
]

# ── ANSI colour helpers ───────────────────────────────────────────────────────

RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
CYAN    = "\033[36m"
YELLOW  = "\033[33m"
GREEN   = "\033[32m"
MAGENTA = "\033[35m"
BLUE    = "\033[34m"
RED     = "\033[31m"

TOPIC_COLORS = [CYAN, MAGENTA, BLUE]   # one colour per topic


def header(text: str) -> str:
    bar = "─" * 60
    return f"\n{BOLD}{CYAN}{bar}\n  {text}\n{bar}{RESET}"


def section(text: str) -> str:
    return f"\n{BOLD}{YELLOW}▶ {text}{RESET}"


def dim(text: str) -> str:
    return f"{DIM}{text}{RESET}"


def event_line(icon: str, color: str, label: str, detail: str = "") -> None:
    print(f"  {color}{icon}  {BOLD}{label}{RESET}  {DIM}{detail}{RESET}")


# ── Phase 1: create and persist agents ───────────────────────────────────────

async def setup() -> None:
    app = VingolfApp.from_yaml(CONFIG, agents_dir=AGENTS_DIR, memory_dir=MEMORY_DIR)

    print(header("Demo 4 — Setup: compiling agent profiles"))
    print(f"  Saving to {AGENTS_DIR}/\n")

    for defn in AGENT_DEFS:
        yaml_path = AGENTS_DIR / f"{defn['name']}.yaml"
        if yaml_path.exists():
            print(f"  {defn['name']:6s} already exists, skipping  ({yaml_path})")
            continue
        agent = await app.create_agent(defn["name"], defn["description"])
        print(f"  {BOLD}{agent.profile.name:6s}{RESET}  {agent.profile.identity.role}")
        print(f"         saved → {yaml_path}")

    print(f"\n{DIM}Done. Run without --setup to start the discussion.{RESET}\n")


# ── Phase 2: multi-topic concurrent discussion ────────────────────────────────

async def run_topic(
    *,
    spec: dict,
    agents: dict,
    topic_plugin: TopicPlugin,
    color: str,
    lock: asyncio.Lock,
) -> None:
    """
    Create one topic, wire dynamic join/leave hooks, run the discussion,
    and print the post history.  Designed to run concurrently with other
    instances via asyncio.gather.
    """
    title        = spec["title"]
    initial      = [agents[n] for n in spec["initial_agents"]]
    join_after   = {k: agents[v] for k, v in spec.get("join_after", {}).items()}
    leave_after  = {k: agents[v] for k, v in spec.get("leave_after", {}).items()}
    rounds       = spec.get("rounds", 8)

    topic = await topic_plugin.create_topic(
        title=title,
        description=spec["description"],
        tags=spec.get("tags"),
        agents=initial,
    )

    # Per-topic post counter used by the message hook below
    post_count = 0

    # We need a reference to the underlying IdavollApp hooks
    app: "IdavollApp" = topic_plugin._require_app()  # type: ignore[attr-defined]

    @app.hooks.hook("session.message.after")
    async def _on_message(session, message, **_):
        nonlocal post_count
        # Only handle messages that belong to *this* topic's session
        if session.id != topic.session_id:
            return

        post_count += 1
        snippet = message.content[:80].replace("\n", " ")

        async with lock:
            print(
                f"  {color}[{title[:30]:30s}]{RESET}  "
                f"{BOLD}{message.agent_name:6s}{RESET}  "
                f"{DIM}#{post_count:02d}{RESET}  {snippet}…"
            )

        # Dynamic join
        if post_count in join_after:
            agent_to_join = join_after[post_count]
            await topic_plugin.join_topic(topic.id, agent_to_join)
            async with lock:
                event_line(
                    "→", GREEN,
                    f"{agent_to_join.profile.name} joined [{title[:30]}]",
                    f"participants: {[p.profile.name for p in session.participants]}",
                )

        # Dynamic leave
        if post_count in leave_after:
            agent_to_leave = leave_after[post_count]
            await topic_plugin.leave_topic(topic.id, agent_to_leave)
            async with lock:
                event_line(
                    "←", RED,
                    f"{agent_to_leave.profile.name} left [{title[:30]}]",
                    f"participants: {[p.profile.name for p in session.participants]}",
                )

    await topic_plugin.start_discussion(topic.id, rounds=rounds)
    return topic


async def run() -> None:
    missing = [
        AGENTS_DIR / f"{d['name']}.yaml"
        for d in AGENT_DEFS
        if not (AGENTS_DIR / f"{d['name']}.yaml").exists()
    ]
    if missing:
        print(f"{RED}Error: missing agent YAML files:{RESET}")
        for path in missing:
            print(f"  {path}")
        print(f"\n{DIM}Run with --setup to create them.{RESET}\n")
        return

    vingolf_cfg = VingolfConfig.from_yaml(CONFIG)
    vingolf_cfg.topic.default_rounds = 8
    vingolf_cfg.topic.min_interval   = 0.3

    topic_plugin  = TopicPlugin(config=vingolf_cfg.topic)
    review_plugin = ReviewPlugin(config=vingolf_cfg.review)

    vingolf_app = VingolfApp.from_yaml(
        CONFIG,
        agents_dir=AGENTS_DIR,
        memory_dir=MEMORY_DIR,
        plugins=[topic_plugin, review_plugin],
    )

    # ── Load agents ────────────────────────────────────────────────────────────
    print(header("Demo 4 — Multiple Topics with Dynamic Participation"))
    print(section("Loading agents…"))

    agents: dict = {}
    for defn in AGENT_DEFS:
        agent = vingolf_app.load_agent(AGENTS_DIR / f"{defn['name']}.yaml")
        agents[defn["name"]] = agent
        print(f"  {BOLD}{agent.profile.name:6s}{RESET}  {agent.profile.identity.role}")

    # ── Launch topics concurrently ─────────────────────────────────────────────
    print(section("Launching topics concurrently…"))
    for i, spec in enumerate(TOPIC_SPECS):
        color = TOPIC_COLORS[i % len(TOPIC_COLORS)]
        print(f"  {color}[{spec['title'][:50]}]{RESET}")
        print(f"     starters: {spec['initial_agents']}")

    print()

    lock = asyncio.Lock()

    topic_tasks = [
        run_topic(
            spec=spec,
            agents=agents,
            topic_plugin=topic_plugin,
            color=TOPIC_COLORS[i % len(TOPIC_COLORS)],
            lock=lock,
        )
        for i, spec in enumerate(TOPIC_SPECS)
    ]

    completed_topics = await asyncio.gather(*topic_tasks)

    # ── Per-topic post history ─────────────────────────────────────────────────
    for i, topic in enumerate(completed_topics):
        color = TOPIC_COLORS[i % len(TOPIC_COLORS)]
        posts = topic_plugin.get_posts(topic.id)
        print(header(f"Post History — {topic.title}  ({len(posts)} posts)"))
        for j, post in enumerate(posts, 1):
            wrapped = textwrap.fill(
                post.content, width=72,
                initial_indent="    ", subsequent_indent="    ",
            )
            reply_note = dim(f"  ↩ {post.reply_to[:8]}…") if post.reply_to else ""
            print(f"\n  {color}{BOLD}#{j:02d}  [{post.agent_name}]{RESET}{reply_note}")
            print(wrapped)

    # ── Review scores per topic ────────────────────────────────────────────────
    for i, topic in enumerate(completed_topics):
        color = TOPIC_COLORS[i % len(TOPIC_COLORS)]
        summary = review_plugin.get_summary(topic.id)
        if not summary:
            continue
        print(header(f"Review — {topic.title}"))
        for r in sorted(summary.results, key=lambda x: x.final_score, reverse=True):
            bar = "█" * int(r.final_score)
            print(
                f"  {BOLD}{r.agent_name:6s}{RESET}  "
                f"final={GREEN}{r.final_score:.2f}{RESET}  "
                f"logic={r.logic_score:.1f}  "
                f"creativity={r.creativity_score:.1f}  "
                f"social={r.social_score:.1f}  "
                f"{DIM}{bar}{RESET}"
            )
        winner = summary.winner()
        if winner:
            print(f"\n  {color}{BOLD}Winner: {winner.agent_name}{RESET}")

    print(f"\n{DIM}Done.{RESET}\n")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Compile agent profiles with the LLM and save YAML files.",
    )
    args = parser.parse_args()
    asyncio.run(setup() if args.setup else run())
