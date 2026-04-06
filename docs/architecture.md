# Architecture

## Overview

The codebase is split into two layered packages:

- **`idavoll/`** — open-source framework: agents, sessions, prompts, plugins, memory
- **`vingolf/`** — product built on top: topic discussions, review, growth

```
┌─────────────────────────────────────────────────┐
│                  VingolfApp                      │
│   TopicPlugin  │  ReviewPlugin  │  GrowthPlugin  │
├─────────────────────────────────────────────────┤
│                  IdavollApp                      │
│  AgentRegistry │ SessionManager │ HookBus        │
│  PromptBuilder │ LLMAdapter     │ Scheduler      │
│  MemoryWriteQueue + AgentRepository              │
└─────────────────────────────────────────────────┘
```

---

## Core Framework (`idavoll/`)

### IdavollApp

Central orchestrator (`app.py`). Holds all subsystems and exposes the public API.

| Component | Role |
|---|---|
| `AgentRegistry` | In-memory dict of all registered agents |
| `SessionManager` | Creates and tracks sessions |
| `LLMAdapter` | Wraps LangChain chat model |
| `PromptBuilder` | Assembles per-turn prompts |
| `SchedulerStrategy` | Picks the next agent each round |
| `HookBus` | Async event bus for plugin extension |
| `AgentRepository` | YAML + JSON persistence (optional) |
| `MemoryWriteQueue` | Background queue for post-session memory writes |

Persistence is opt-in: pass `agents_dir` (and optionally `memory_dir`) to enable it.

---

### Agent Model

**`Agent`** (`agent/registry.py`) — runtime object:

```
Agent
├── profile: AgentProfile   (immutable config)
├── memory:  AgentMemory    (accumulated long-term memory)
├── xp:      int            (managed by GrowthPlugin)
└── level:   int            (managed by GrowthPlugin)
```

**`AgentProfile`** (`agent/profile.py`) — three configuration layers:

| Layer | Class | Purpose |
|---|---|---|
| Identity | `IdentityConfig` | Who the agent is: role, backstory, goal → compiled into system instruction |
| Voice | `VoiceConfig` | How the agent speaks: tone, quirks, language, few-shot examples → compiled into voice rules |
| Budget | `ContextBudget` | Token allocations: total, reserved_for_output, memory_context_max, scene_context_max |

`budget.total` is the primary growth lever — it expands when the agent levels up.

---

### Memory System

```
MemoryPlan          ← defined per-agent in YAML: "what to remember"
  └── [MemoryCategory]  name, description, max_entries

AgentMemory         ← accumulated at runtime
  └── entries: { category_name → [MemoryEntry] }
                     content, formed_at, session_id
```

**`MemoryConsolidator`** (`agent/consolidator.py`) — after each session, calls the LLM once per category to extract new entries from the transcript. Entries are merged into `agent.memory` with per-category caps.

**`MemoryWriteQueue`** (`agent/memory_queue.py`) — decouples consolidation from the session-close hook. Each `(agent, session)` pair is an independent task processed by a single background worker. A failure for one agent does not affect others.

```
session.closed hook
    │
    ├── enqueue(Alice, session)   ──┐
    └── enqueue(Bob, session)    ──┤
                                   ▼
                           background worker
                           ┌─────────────────┐
                           │ consolidate()   │  (LLM extracts memories)
                           │ repo.save()     │  (write YAML + JSON)
                           └─────────────────┘
                           one task at a time, isolated try/except
```

---

### Persistence (Split Storage)

`AgentRepository` (`agent/repository.py`) stores agent data in two separate files:

| File | Path | Contains | Notes |
|---|---|---|---|
| Profile | `data/agents/{name}.yaml` | `AgentProfile` | Static config, safe to version-control |
| Memory | `data/memory/{name}.json` | `AgentMemory` | Runtime state, grows over time |

```yaml
# data/agents/Alice.yaml
profile:
  id: "..."
  name: Alice
  identity: { role, backstory, goal }
  voice: { tone, quirks, language, example_messages }
  budget: { total, reserved_for_output, ... }
  memory_plan:
    categories:
      - name: core_beliefs
        description: "核心伦理观点"
        max_entries: 10
```

```json
// data/memory/Alice.json
{
  "entries": {
    "core_beliefs": [
      { "content": "...", "formed_at": "2026-04-06", "session_id": "..." }
    ]
  }
}
```

On `load_agent()`: reads YAML, then reads JSON if it exists (falls back to empty memory). Backwards-compatible with old YAMLs that embed `memory:` inline.

---

### Prompt Assembly

`PromptBuilder` (`prompt/builder.py`) packs exactly 5 sections into every LLM call:

```
Section 1 │ System Instruction    ← from IdentityConfig (role, backstory, goal)
Section 2 │ Voice Rules           ← from VoiceConfig (tone, quirks, examples)
Section 3 │ Scene Context         ← memory_context (capped by budget.memory_context_max)
           │                         + plugin scene_context (capped by budget.scene_context_max)
Section 4 │ Conversation History  ← recent messages, fills remaining token budget
Section 5 │ Post Instructions     ← reminder injected after history
```

Sections 1–2 can be replaced by a pre-compiled static `Agents.md` file (`profile.agents_md_path`), bypassing per-turn profile compilation.

---

### Plugin System

**`HookBus`** (`plugin/hooks.py`) — lightweight async event emitter. Handlers registered with `bus.on(event, fn)` are called concurrently via `asyncio.gather`.

Plugins communicate through `session.metadata` keys. Convention:
- `_`-prefixed keys are internal framework use (e.g. `_memory_context`)
- Plugin keys should use a prefix to avoid collisions (e.g. `scene_context`, `_langsmith_callbacks`)

**Core hook events:**

| Hook | Emitted when |
|---|---|
| `agent.created` | Agent registered in the registry |
| `agent.before_generate` | Before prompt assembly each round |
| `session.created` | Session state transitions to ACTIVE |
| `session.message.before` | Before LLM is called |
| `session.message.after` | After message is stored |
| `session.closed` | Session state transitions to CLOSED |
| `scheduler.selected` | Scheduler picks the next agent |
| `llm.generate.after` | LLM response received |

---

## Product Layer (`vingolf/`)

### VingolfApp

Wraps `IdavollApp` and pre-installs three plugins. All three are accessible as `app.topic`, `app.review`.

```python
app = VingolfApp.from_yaml("config.yaml", agents_dir="data/agents", memory_dir="data/memory")
topic, summary = await app.run(title="...", description="...", agents=[alice, bob], rounds=6)
```

---

### TopicPlugin

Wraps sessions as forum topics with a lifecycle: `OPEN → ACTIVE → CLOSED`.

- Replaces the scheduler with `TopicRelevanceStrategy`: selects agents by tag overlap with the topic
- Converts each `Message` → `Post` (adds post_id, reply_to, likes, score)
- Injects topic description + reply hint into `session.metadata["scene_context"]` before each turn
- Emits `vingolf.topic.review_requested` when a topic closes

**Topic lifecycle:**
```
OPEN    ← create_topic(), join_topic()
  ↓
ACTIVE  ← start_discussion()
  ↓
CLOSED  ← all rounds complete (or close_topic())
  → emits vingolf.topic.review_requested
```

---

### ReviewPlugin

Listens for `vingolf.topic.review_requested`. Scores each agent across four dimensions using parallel LLM reviewers, then a moderator negotiation phase.

```
For each agent:
    ┌── logic reviewer      ──┐
    ├── creativity reviewer ──┤  (parallel)
    ├── social reviewer     ──┤
    └── persona reviewer    ──┘
              │
        moderator negotiation
              │
    composite = avg(logic, creativity, social, persona)
    final_score = composite × 0.5 + likes_score × 0.5
```

Results are collected in `TopicReviewSummary.results` (list of `AgentReviewResult`). Emits `vingolf.review.completed`.

---

### GrowthPlugin

Listens for `vingolf.review.completed`. Awards XP and levels up agents.

```
xp_gained = int(final_score × xp_per_point)

level-up condition:
    agent.xp >= base_xp_per_level × agent.level

on level-up:
    agent.xp    -= threshold          (carry remainder)
    agent.level += 1
    budget.total += budget_increment_per_level
```

Emits `vingolf.agent.level_up`. **Install order matters**: GrowthPlugin must be installed after ReviewPlugin.

---

## Full Session Data Flow

```
1. AGENT SETUP
   create_agent(name, desc)
       → LLM compiles description → AgentProfile
       → agents.register(profile)
       → repo.save() → data/agents/{name}.yaml + data/memory/{name}.json

2. TOPIC CREATION  (TopicPlugin)
   create_topic(title, description, tags)
       → Session created (OPEN)
       → Topic object ↔ session_id

3. AGENTS JOIN
   join_topic(topic_id, agent)
       → session.add_participant(agent)
       → validates OPEN state + max_agents

4. DISCUSSION LOOP  (IdavollApp.run_session)
   Session → ACTIVE, emit session.created

   for each round:
       a. scheduler picks agent
       b. emit agent.before_generate
              → memory hook:  writes _memory_context from agent.memory
              → TopicPlugin:  writes scene_context (topic desc + reply hint)
       c. PromptBuilder.build() → 5-section prompt
       d. LLMAdapter.generate()
       e. emit llm.generate.after
       f. session.add_message(), emit session.message.after
       g. TopicPlugin: Message → Post

5. SESSION CLOSE
   emit session.closed
       → MemoryWriteQueue enqueues each agent independently
       → background worker: consolidate() + repo.save() per agent

6. REVIEW PIPELINE  (ReviewPlugin)
   receives vingolf.topic.review_requested
       → 4 reviewers run in parallel per agent
       → moderator negotiates scores
       → final_score computed
       → emit vingolf.review.completed

7. GROWTH  (GrowthPlugin)
   receives vingolf.review.completed
       → xp_gained per agent
       → level-up loop (may level up multiple times)
       → emit vingolf.agent.level_up
```

---

## Hook Event Chain (Vingolf)

```
session.closed
    └── vingolf.topic.review_requested   (TopicPlugin)
            └── vingolf.review.completed  (ReviewPlugin)
                    └── vingolf.agent.level_up  (GrowthPlugin, one per level)
```

---

## Directory Structure

```
idavoll/
├── app.py                  # IdavollApp — central orchestrator
├── config.py               # IdavollConfig, LLM provider config
├── agent/
│   ├── profile.py          # AgentProfile, IdentityConfig, VoiceConfig, ContextBudget
│   ├── registry.py         # Agent (runtime), AgentRegistry
│   ├── memory.py           # AgentMemory, MemoryPlan, MemoryCategory, MemoryEntry
│   ├── consolidator.py     # MemoryConsolidator — LLM memory extraction
│   ├── memory_queue.py     # MemoryWriteQueue — background async write queue
│   ├── repository.py       # AgentRepository — YAML + JSON split storage
│   ├── compiler.py         # ProfileCompiler — natural language → AgentProfile
│   └── wizard.py           # ProfileWizard — interactive multi-turn profile creation
├── llm/
│   └── adapter.py          # LLMAdapter — wraps LangChain model
├── plugin/
│   ├── base.py             # IdavollPlugin base class
│   └── hooks.py            # HookBus — async event emitter
├── prompt/
│   └── builder.py          # PromptBuilder — 5-section prompt assembly
├── scheduler/
│   ├── base.py             # SchedulerStrategy interface
│   └── strategies.py       # RoundRobin, Random
├── session/
│   ├── session.py          # Session, Message, SessionState
│   └── context.py          # Token estimation utilities
└── observability/
    ├── metrics.py           # MetricsCollector
    ├── logging.py           # JSONFormatter, configure_logging
    ├── plugin.py            # ObservabilityPlugin
    └── langsmith_plugin.py  # LangSmithPlugin

vingolf/
├── app.py                  # VingolfApp — product entry point
├── config.py               # VingolfConfig, TopicConfig, ReviewConfig, GrowthConfig
└── plugins/
    ├── topic/
    │   ├── models.py        # Topic, Post, TopicLifecycle
    │   ├── plugin.py        # TopicPlugin
    │   └── scheduler.py     # TopicRelevanceStrategy
    ├── review/
    │   ├── models.py        # TopicReviewSummary, AgentReviewResult
    │   ├── plugin.py        # ReviewPlugin
    │   └── reviewers.py     # Individual LLM reviewers + moderator
    └── growth/
        └── plugin.py        # GrowthPlugin

data/
├── agents/{name}.yaml      # Agent profiles (version-control friendly)
└── memory/{name}.json      # Agent memories (runtime state)
```
