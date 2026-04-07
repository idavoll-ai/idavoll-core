"""
Demo 3 — Dynamic Join / Leave

演示在讨论进行中 agent 动态加入和退出：

  Phase 1 (rounds 1–3)  — Alice + Bob 开场
  Phase 2 (rounds 4–6)  — Carol 中途加入
  Phase 3 (rounds 7–9)  — Alice 离席，Bob + Carol 收尾

两阶段工作流：

  Phase 1 — 首次运行，用 LLM 编译 agent profile 并写入 YAML：
      uv run python example/demo3.py --setup

  Phase 2 — 之后每次运行直接从 YAML 加载：
      uv run python example/demo3.py
"""

import argparse
import asyncio
import textwrap
from pathlib import Path

from idavoll import IdavollApp
from idavoll.config import IdavollConfig
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
]

TOPIC = dict(
    title="Open-sourcing frontier AI models: good or dangerous?",
    description=(
        "Should frontier AI labs release their weights publicly? "
        "Discuss the trade-offs between democratising access, "
        "accelerating innovation, and the risk of misuse."
    ),
    tags=["AI", "open-source", "policy", "safety"],
)

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
    base_app = VingolfApp.from_yaml(CONFIG, agents_dir=AGENTS_DIR, memory_dir=MEMORY_DIR)

    print(header("Demo 3 — Setup: compiling agent profiles"))
    print(f"  Saving to {AGENTS_DIR}/\n")

    for defn in AGENT_DEFS:
        yaml_path = AGENTS_DIR / f"{defn['name']}.yaml"
        if yaml_path.exists():
            print(f"  {defn['name']:6s} already exists, skipping  ({yaml_path})")
            continue
        agent = await base_app.create_agent(defn["name"], defn["description"])
        print(f"  {BOLD}{agent.profile.name:6s}{RESET}  {agent.profile.identity.role}")
        print(f"         saved → {yaml_path}")

    print(f"\n{DIM}Done. Run without --setup to start the discussion.{RESET}\n")


# ── Phase 2: dynamic join/leave discussion ────────────────────────────────────

async def run() -> None:
    missing = [
        AGENTS_DIR / f"{d['name']}.yaml"
        for d in AGENT_DEFS
        if not (AGENTS_DIR / f"{d['name']}.yaml").exists()
    ]
    if missing:
        paths = ", ".join(str(p) for p in missing)
        raise FileNotFoundError(
            f"Agent YAML file(s) not found: {paths}\n"
            "Run  uv run python example/demo3.py --setup  first."
        )

    vingolf_cfg = VingolfConfig.from_yaml(CONFIG)
    vingolf_cfg.topic.default_rounds = 9
    vingolf_cfg.topic.min_interval = 0.3

    topic_plugin  = TopicPlugin(config=vingolf_cfg.topic)
    review_plugin = ReviewPlugin(config=vingolf_cfg.review)

    vingolf_app = VingolfApp.from_yaml(
        CONFIG,
        agents_dir=AGENTS_DIR,
        memory_dir=MEMORY_DIR,
        plugins=[topic_plugin, review_plugin],
    )
    base_app: IdavollApp = vingolf_app._app

    # ── Load agents ────────────────────────────────────────────────────────────
    print(header("Demo 3 — Dynamic Join / Leave"))
    print(section("Loading agents from YAML…"))

    agents = {}
    for defn in AGENT_DEFS:
        yaml_path = AGENTS_DIR / f"{defn['name']}.yaml"
        agent = vingolf_app.load_agent(yaml_path)
        agents[defn["name"]] = agent
        print(f"  {BOLD}{agent.profile.name:6s}{RESET}  {agent.profile.identity.role}")

    alice = agents["Alice"]
    bob   = agents["Bob"]
    carol = agents["Carol"]

    # ── Create topic — only Alice and Bob join initially ───────────────────────
    print(section("Creating topic with Alice + Bob (Carol joins later)…"))
    topic = await topic_plugin.create_topic(**TOPIC, agents=[alice, bob])

    session = base_app.sessions.get_or_raise(topic.session_id)
    print(f"\n  Topic   : {topic.title}")
    print(f"  Session : {session.id[:16]}…")
    print(f"  Seats   : {[s.agent.profile.name for s in session.seats.values()]}")

    # ── Wire dynamic join / leave via hooks ───────────────────────────────────
    post_count = 0
    carol_joined = False
    alice_left   = False

    @base_app.hooks.hook("session.message.after")
    async def on_message(session, message, **_):
        nonlocal post_count, carol_joined, alice_left

        post_count += 1
        snippet = message.content[:72].replace("\n", " ")
        print(
            f"\n  {BOLD}[{message.agent_name}]{RESET}  "
            f"{DIM}post #{post_count}{RESET}\n"
            f"  {snippet}…"
        )

        # ── After post 3: Carol joins ─────────────────────────────────────────
        if post_count == 3 and not carol_joined:
            carol_joined = True
            await topic_plugin.join_topic(topic.id, carol)
            event_line(
                "→", GREEN,
                "Carol joined the discussion",
                f"participants now: {[p.profile.name for p in session.participants]}",
            )

        # ── After post 6: Alice leaves ────────────────────────────────────────
        if post_count == 6 and not alice_left:
            alice_left = True
            await topic_plugin.leave_topic(topic.id, alice)
            event_line(
                "←", RED,
                "Alice left the discussion",
                f"participants now: {[p.profile.name for p in session.participants]}",
            )

    # ── Run discussion ─────────────────────────────────────────────────────────
    print(section("Running discussion (9 rounds)…"))
    print(dim("  rounds 1–3: Alice + Bob  |  round 4+: Carol joins  |  round 7+: Alice leaves"))
    await topic_plugin.start_discussion(topic.id)

    # ── Final seat states ──────────────────────────────────────────────────────
    print(header("Final seat states"))
    for seat in session.seats.values():
        joined_str = seat.joined_at.strftime("%H:%M:%S UTC")
        print(
            f"  {BOLD}{seat.agent.profile.name:6s}{RESET}  "
            f"state={seat.state.value}  "
            f"schedulable={seat.is_schedulable}  "
            f"joined={joined_str}"
        )

    # ── Full post list ─────────────────────────────────────────────────────────
    posts = topic_plugin.get_posts(topic.id)
    print(header(f"All posts ({len(posts)})"))
    for i, post in enumerate(posts, 1):
        wrapped = textwrap.fill(
            post.content, width=72,
            initial_indent="    ", subsequent_indent="    ",
        )
        reply_note = dim(f"  ↩ {post.reply_to[:8]}…") if post.reply_to else ""
        print(f"\n  {BOLD}#{i:02d}  [{post.agent_name}]{RESET}{reply_note}")
        print(wrapped)

    # ── Review scores ──────────────────────────────────────────────────────────
    summary = review_plugin.get_summary(topic.id)
    if summary:
        print(header("Review scores"))
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
            print(f"\n  {BOLD}{GREEN}Winner: {winner.agent_name}{RESET}")

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
