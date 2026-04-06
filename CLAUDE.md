# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests
python -m pytest

# Run a single test file
python -m pytest tests/test_app.py -v

# Run a single test by name
python -m pytest tests/test_observability.py::TestMetricsCollector::test_increment -v

# Run the MVP demo (requires API key in config.yaml)
uv run python example/demo.py
```

The project uses `uv` for dependency management (`uv.lock` present). Tests require no API key — `FakeLLM` in `tests/conftest.py` handles all LLM calls.

## Architecture

Two layered packages: **`idavoll/`** (open-source framework) and **`vingolf/`** (product built on top).

### idavoll/ — Core Framework

**`IdavollApp`** (`app.py`) is the central orchestrator. It holds the agent registry, session manager, LLM adapter, prompt builder, scheduler, and hook bus. Plugins install themselves by calling `app.use(plugin)`, which calls `plugin.install(app)` — giving plugins full access to register hooks and replace the scheduler.

**Agent model** (`agent/registry.py`):
- `Agent` — runtime object: `profile`, `memory`, `xp: int`, `level: int`
- `AgentRegistry` — in-memory dict; persistence is YAML via `AgentRepository`

**Agent profile** is dual-layer (`agent/profile.py`):
- `IdentityConfig` — who the agent is (role, backstory, goal); compiled into the system instruction
- `VoiceConfig` — how the agent speaks (tone, quirks, language, few-shot examples); compiled into voice rules
- `ContextBudget` — token allocation; `total` is the primary growth lever (expands as agents level up)
- `agents_md_path` — optional path to a pre-compiled Agents.md file; when set, `PromptBuilder` reads Section 1 / Section 2 from disk instead of building them from profile fields each turn

**ProfileWizard** (`agent/wizard.py`) drives an interactive multi-turn dialogue (Identity → Voice → Confirm) to create a profile without calling the LLM until the CONFIRM phase. Phases are `WizardPhase` enum values; the loop ends at `WizardPhase.DONE` when `resp.profile` is populated. `wizard.export_agents_md(path)` writes a static Agents.md compatible with `agents_md_path`.

**Prompt assembly** (`prompt/builder.py`) packs exactly 5 sections:
1. System Instruction (Identity) → 2. Voice Rules + Examples → 3. Scene Context (`memory_context` capped by `budget.memory_context_max` + plugin `scene_context` capped by `budget.scene_context_max`) → 4. Conversation History (fills remainder) → 5. Post Instructions

**`HookBus`** (`plugin/hooks.py`) is the extension point. `CORE_HOOKS` lists all framework-emitted events. Handlers registered with `bus.on(event, fn)` are called concurrently via `asyncio.gather`. The key inter-plugin communication channel is `session.metadata` — plugins read and write it to pass data (e.g. `scene_context`, `_langsmith_callbacks`).

**Session loop** (`app.run_session`): for each round → scheduler picks agent → `agent.before_generate` hook (framework memory hook writes `_memory_context`; plugins write `scene_context`) → pop `_memory_context` + read `scene_context` → `PromptBuilder.build()` → `LLMAdapter.generate()` → emit `llm.generate.after` → store `Message` → `session.message.after` hook → sleep.

**LLM providers** (`config.py`): `anthropic` (uses `langchain_anthropic.ChatAnthropic`), `openai` / `deepseek` / `kimi` (all use `langchain_openai.ChatOpenAI` with a `base_url`). Non-anthropic providers require `base_url` in config.

### vingolf/ — Product Layer

**`VingolfApp`** (`vingolf/app.py`) wraps `IdavollApp` and pre-installs TopicPlugin + ReviewPlugin + GrowthPlugin. All three are accessible as `app.topic`, `app.review`, `app.growth`. The one-shot `app.run(title, description, agents, rounds)` creates a topic, joins all agents, runs the discussion, and returns `(Topic, TopicReviewSummary | None)`.

**`TopicPlugin`** wraps sessions as forum topics (`TopicLifecycle`: OPEN → ACTIVE → CLOSED). Replaces the scheduler with `TopicRelevanceStrategy` (selects agents by tag overlap). Injects topic context via `agent.before_generate` and converts each `Message` into a `Post`. Emits `vingolf.topic.review_requested` when a topic closes.

**`ReviewPlugin`** listens for `vingolf.topic.review_requested`. For each agent it runs three LLM reviewers in parallel (logic / creativity / social), then a moderator negotiation phase, then computes `final_score = composite * 0.5 + likes_score * 0.5`. Results are `AgentReviewResult` objects collected in `TopicReviewSummary`. Emits `vingolf.review.completed`.

**`GrowthPlugin`** listens for `vingolf.review.completed`. Awards `xp_gained = int(final_score * xp_per_point)` to each agent. Levels up when `agent.xp >= base_xp_per_level * agent.level`; each level-up adds `budget_increment_per_level` tokens to `profile.budget.total`. Emits `vingolf.agent.level_up`. **Install order matters**: must come after ReviewPlugin.

### idavoll/observability/ — Observability Module

- **`MetricsCollector`** — in-memory counters and histograms; call `.snapshot()` to read
- **`JSONFormatter` / `configure_logging()`** — structured JSON output via the `idavoll` logger
- **`ObservabilityPlugin`** — subscribes to all core hooks, logs events, populates metrics
- **`LangSmithPlugin`** — wraps each session as a LangSmith `RunTree`; stores `_langsmith_callbacks` in `session.metadata` so `LLMAdapter.generate()` attaches LLM spans as children

### Key Conventions

- All public framework extension points go through the HookBus — never monkey-patch `IdavollApp` directly.
- Plugins communicate through `session.metadata` keys. Prefix custom keys to avoid collisions (e.g. `_langsmith_*`, `scene_context`). Internal (framework-only) keys are prefixed with `_`.
- Token estimation uses `len(text) // 3` (handles CJK + Latin mixed content) — see `session/context.py:_estimate_tokens`.
- `FakeLLM` in `tests/conftest.py` auto-detects the structured output schema (`_AgentProfileData`, `DimensionScore`, `NegotiatedScores`) and returns appropriate defaults. Add new schema cases there when adding structured LLM calls.
- Agent persistence is YAML via `AgentRepository`. Memory consolidation runs automatically on `session.closed` when `agents_dir` is configured.
- `Topic` uses `lifecycle` (not `status`) — values are `TopicLifecycle.OPEN / ACTIVE / CLOSED`. `TopicReviewSummary` uses `results` (not `scores`) — each item is `AgentReviewResult` with fields `logic_score`, `creativity_score`, `social_score` (not `logic`, `creativity`, `social`).
