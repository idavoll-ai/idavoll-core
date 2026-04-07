"""
Demo 2 — Agent-Scoped Session 重构展示

演示本次重构引入的三个核心改动：

  1. forum.before_turn   — 框架层：写入 forum 级别共享上下文
  2. seat.before_generate — 框架层：写入 per-agent 隔离上下文（Seat.local_context）
  3. Seat 对象           — 每个 agent 独立持有参与状态，局部上下文互不干扰

两阶段工作流（同 demo1.py）：

  Phase 1 — 首次运行，用 LLM 编译 agent profile 并写入 YAML：
      uv run python example/demo2.py --setup

  Phase 2 — 之后每次运行直接从 YAML 加载，无需 LLM 编译：
      uv run python example/demo2.py
"""

import argparse
import asyncio
import textwrap
from datetime import timezone
from pathlib import Path

from idavoll import IdavollApp
from idavoll.config import IdavollConfig
from vingolf import VingolfApp
from vingolf.config import VingolfConfig
from vingolf.plugins.topic import TopicPlugin
from vingolf.plugins.review import ReviewPlugin

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

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
CYAN   = "\033[36m"
YELLOW = "\033[33m"
GREEN  = "\033[32m"
MAGENTA = "\033[35m"
BLUE   = "\033[34m"


def header(text: str) -> str:
    bar = "─" * 60
    return f"\n{BOLD}{CYAN}{bar}\n  {text}\n{bar}{RESET}"


def section(text: str) -> str:
    return f"\n{BOLD}{YELLOW}▶ {text}{RESET}"


def dim(text: str) -> str:
    return f"{DIM}{text}{RESET}"


# ── Runtime hook instrumentation ─────────────────────────────────────────────

turn_counter = 0


def install_trace_hooks(app: IdavollApp) -> None:
    """Register observers on the new hooks to print a live trace."""

    @app.hooks.hook("forum.before_turn")
    async def on_forum_before_turn(session, agent, **_):
        global turn_counter
        turn_counter += 1
        seat = session.seats.get(agent.id)
        joined = (
            seat.joined_at.astimezone(timezone.utc).strftime("%H:%M:%S")
            if seat else "?"
        )
        print(
            f"\n{BOLD}Turn {turn_counter:02d}{RESET}  "
            f"{MAGENTA}forum.before_turn{RESET}  "
            f"agent={BOLD}{agent.profile.name}{RESET}  "
            f"(joined {joined} UTC)"
        )
        # After this hook, session.metadata["scene_context"] will be set by
        # TopicPlugin with the shared topic description.
        # We'll print it after seat.before_generate runs.

    @app.hooks.hook("seat.before_generate")
    async def on_seat_before_generate(seat, session, agent, **_):
        forum_ctx = session.metadata.get("scene_context", "")
        seat_ctx  = seat.local_context.get("scene_context", "")
        reply_id  = seat.local_context.get("_reply_to_post_id")

        # Show how the per-agent scene_context differs from the forum-level one
        extra = len(seat_ctx) - len(forum_ctx)
        print(
            f"           {BLUE}seat.before_generate{RESET}  "
            f"agent={BOLD}{agent.profile.name}{RESET}  "
            f"forum_ctx={len(forum_ctx)}chars  "
            f"seat_ctx={len(seat_ctx)}chars  "
            f"(+{extra} reply-hint)"
            + (f"  reply_to_post={reply_id[:8]}…" if reply_id else "")
        )

    @app.hooks.hook("session.message.after")
    async def on_message(session, message, **_):
        snippet = message.content[:80].replace("\n", " ")
        print(f"           {GREEN}✓ posted{RESET}  [{message.agent_name}] {dim(snippet)}…")


# ── Phase 1: create and persist agents ───────────────────────────────────────

async def setup() -> None:
    """Compile agent profiles with the LLM and write them to YAML files."""
    base_app = VingolfApp.from_yaml(CONFIG, agents_dir=AGENTS_DIR, memory_dir=MEMORY_DIR)

    print(header("Demo 2 — Setup: compiling agent profiles"))
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


# ── Phase 2: load agents and run discussion ───────────────────────────────────

async def run() -> None:
    """Load agents from YAML and run the discussion with hook tracing."""
    missing = [
        AGENTS_DIR / f"{d['name']}.yaml"
        for d in AGENT_DEFS
        if not (AGENTS_DIR / f"{d['name']}.yaml").exists()
    ]
    if missing:
        paths = ", ".join(str(p) for p in missing)
        raise FileNotFoundError(
            f"Agent YAML file(s) not found: {paths}\n"
            "Run  uv run python example/demo2.py --setup  first."
        )

    vingolf_cfg = VingolfConfig.from_yaml(CONFIG)
    vingolf_cfg.topic.default_rounds = 6
    vingolf_cfg.topic.min_interval = 0.3

    topic_plugin = TopicPlugin(config=vingolf_cfg.topic)
    review_plugin = ReviewPlugin(config=vingolf_cfg.review)

    vingolf_app = VingolfApp.from_yaml(
        CONFIG,
        agents_dir=AGENTS_DIR,
        memory_dir=MEMORY_DIR,
        plugins=[topic_plugin, review_plugin],
    )
    base_app = vingolf_app._app
    install_trace_hooks(base_app)

    # ── Load agents ────────────────────────────────────────────────────────────
    print(header("Demo 2 — Agent-Scoped Session (forum / seat hooks)"))
    print(section("Loading agents from YAML…"))
    agents = []
    for defn in AGENT_DEFS:
        yaml_path = AGENTS_DIR / f"{defn['name']}.yaml"
        agent = vingolf_app.load_agent(yaml_path)
        total_memories = sum(len(v) for v in agent.memory.entries.values())
        mem_note = f"  ({total_memories} memories)" if total_memories else ""
        print(f"  {BOLD}{agent.profile.name:6s}{RESET}  {agent.profile.identity.role}{mem_note}")
        agents.append(agent)

    # ── Create topic ───────────────────────────────────────────────────────────
    print(section("Creating topic and joining agents…"))
    topic = await topic_plugin.create_topic(**TOPIC, agents=agents)

    session = base_app.sessions.get_or_raise(topic.session_id)
    print(f"\n  Forum ID  : {session.id[:16]}…")
    print(f"  Seats     : {[session.seats[a.id].agent.profile.name for a in agents]}")
    print(f"  Topic     : {topic.title}")

    # ── Run discussion ─────────────────────────────────────────────────────────
    print(section("Running discussion (watch forum vs seat hooks)…"))
    await topic_plugin.start_discussion(topic.id)

    # ── Post-discussion: inspect Seat state ────────────────────────────────────
    print(header("Seat state after discussion"))
    for seat in session.seats.values():
        ctx_keys = list(seat.local_context.keys())
        joined_str = seat.joined_at.astimezone(timezone.utc).strftime("%H:%M:%S")
        print(
            f"  {BOLD}{seat.agent.profile.name:6s}{RESET}  "
            f"state={seat.state.value}  "
            f"schedulable={seat.is_schedulable}  "
            f"joined={joined_str} UTC  "
            f"local_context_keys={ctx_keys or '[]'}"
        )

    # ── Posts ──────────────────────────────────────────────────────────────────
    posts = topic_plugin.get_posts(topic.id)
    print(section(f"Posts ({len(posts)})"))
    for post in posts:
        wrapped = textwrap.fill(
            post.content, width=72,
            initial_indent="    ", subsequent_indent="    ",
        )
        reply_note = dim(f"  ↩ reply to {post.reply_to[:8]}…") if post.reply_to else ""
        print(f"\n  {BOLD}[{post.agent_name}]{RESET}{reply_note}")
        print(wrapped)

    # ── Review ─────────────────────────────────────────────────────────────────
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
