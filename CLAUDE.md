# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests
python -m pytest

# Run a single test file
python -m pytest tests/test_observability.py -v

# Run a single test by name
python -m pytest tests/test_observability.py::TestMetricsCollector::test_increment -v

# Run the end-to-end debate example (requires API key + config.yaml)
uv run python example/debate_example.py
```

The project uses `uv` for dependency management (`uv.lock` present). Tests require no API key — `FakeLLM` in `tests/conftest.py` handles all LLM calls.

## Architecture

This repo contains two layered packages: **`idavoll/`** (open-source framework) and **`vingolf/`** (product built on top).

### idavoll/ — Core Framework

**`IdavollApp`** (`app.py`) is the central orchestrator. It holds the agent registry, session manager, LLM adapter, prompt builder, scheduler, and hook bus. Plugins install themselves by calling `app.use(plugin)`, which calls `plugin.install(app)` — giving plugins full access to register hooks and replace the scheduler.

**Agent profile** is dual-layer (`agent/profile.py`):
- `IdentityConfig` — who the agent is (role, backstory, goal); compiled into the system instruction
- `VoiceConfig` — how the agent speaks (tone, quirks, language, few-shot examples); compiled into voice rules
- `ContextBudget` — token allocation; the primary growth lever (total expands as agents level up)

**Prompt assembly** (`prompt/builder.py`) packs 6 sections into a single SystemMessage + history:
1. Identity → 2. Voice Rules + Examples → 3. Long-term Memory (≤20% of budget) → 4. Scene Context (plugin-injected, capped by `budget.scene_context_max`) → 5. Conversation History (fills remainder) → 6. Post Instructions

**`HookBus`** (`plugin/hooks.py`) is the extension point. `CORE_HOOKS` lists all framework-emitted events. Plugins call `app.hooks.on(event, handler)` or use `@app.hooks.hook(event)`. Handlers are called concurrently via `asyncio.gather`. The key inter-plugin communication channel is `session.metadata` — plugins read and write it to pass data (e.g. `scene_context`, `_langsmith_callbacks`).

**Session loop** (`app.run_session`): for each round → scheduler picks agent → `agent.before_generate` hook → `PromptBuilder.build()` → `LLMAdapter.generate()` → emit `llm.generate.after` → store `Message` → `session.message.after` hook → sleep. The LLM call forwards optional `callbacks/run_name/metadata/tags` from `session.metadata` (used by `LangSmithPlugin`).

### vingolf/ — Product Layer

**`TopicPlugin`** wraps sessions as forum topics. On install it replaces the scheduler with `TopicRelevanceStrategy` (selects agents by tag overlap). It injects topic context via the `agent.before_generate` hook and converts each `Message` into a `Post`. When a session closes it emits `vingolf.topic.review_requested`.

**`ReviewPlugin`** listens for `vingolf.topic.review_requested`. For each agent in the topic it runs three LLM reviewers in parallel (logic / creativity / social), then a moderator negotiation phase, then computes `final_score = composite * 0.5 + likes_score * 0.5`. Emits `vingolf.review.completed` when done.

### idavoll/observability/ — Observability Module

Three components:
- **`MetricsCollector`** — in-memory counters and histograms; call `.snapshot()` to read
- **`JSONFormatter` / `configure_logging()`** — structured JSON output via the `idavoll` logger
- **`ObservabilityPlugin`** — subscribes to all core hooks, logs events, populates metrics
- **`LangSmithPlugin`** — wraps each session as a LangSmith `RunTree`; stores `_langsmith_callbacks` in `session.metadata` so `LLMAdapter.generate()` attaches LLM spans as children

### Key Conventions

- All public framework extension points go through the HookBus — never monkey-patch `IdavollApp` directly.
- Plugins communicate through `session.metadata` keys. Prefix custom keys to avoid collisions (e.g. `_langsmith_*`, `scene_context`).
- Token estimation uses `len(text) // 3` (handles CJK + Latin mixed content) — see `session/context.py:_estimate_tokens`.
- `FakeLLM` in `tests/conftest.py` auto-detects the structured output schema (`_AgentProfileData`, `DimensionScore`, `NegotiatedScores`) and returns appropriate defaults. Add new schema cases there when adding structured LLM calls.
- Agent persistence is YAML via `AgentRepository`. Memory consolidation runs automatically on `session.closed` when `agents_dir` is configured.
