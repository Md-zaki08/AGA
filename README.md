# AGA — Adaptive Goal Agent

A persistent AI agent that turns user goals into smart, self-correcting plans,
finishes them as early as possible, replans around interruptions, and never
silently drops a goal.

## Quickstart (fresh clone)

```bash
git clone <repo> && cd AGA
cp .env.example .env        # put ANY provider key in .env (see below)
pip install -e ".[dev]"
aga chat                    # onboarding wizard on first run, then REPL
```

Other commands:

```bash
aga goals                   # list goals
aga plan show --goal 1      # Rich table: version, target vs deadline, buffer, risk
aga plan history --goal 1   # all versions with changelog diffs
aga checkin --goal 1 --done 3,4 --hours 1.5 --notes "low energy"
```

## Environment variables (ANY provider works)

Generic (preferred):

| Var | Default | Meaning |
|-----|---------|---------|
| `LLM_PROVIDER` | `auto` | `anthropic\|openai\|google\|ollama\|generic\|auto` — auto-detects from model |
| `LLM_MODEL` | `claude-sonnet-4-5` | Any model id: `claude-*`, `gpt-*`, `gemini-*`, Ollama locals, custom |
| `LLM_API_KEY` | — | Any key — accepted for every provider |
| `LLM_BASE_URL` | — | Custom OpenAI-compatible endpoint (Ollama `http://localhost:11434/v1`, OpenRouter, Together…) |

Provider-specific keys — **all accepted**, use whichever you have:

`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `OPENAI_API_KEY`, `OPENAI_MODEL`,
`GOOGLE_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `TOGETHER_API_KEY`,
`OPENROUTER_API_KEY`, `MISTRAL_API_KEY`, `COHERE_API_KEY`

App settings:

| Var | Default | Meaning |
|-----|---------|---------|
| `CHECKIN_TIME` | `09:00` | Daily nudge time `HH:MM` |
| `DB_PATH` | `~/.aga/aga.db` | SQLite file (everything persists here) |

No key? The CLI still boots; the agent replies with a graceful
"model unreachable, goals are safe" message instead of crashing. Tests always
use `FakeLLM` — zero network.

## Architecture

```
                    ┌─────────────┐
                    │  aga chat   │  REPL (Rich) + APScheduler nudger thread
                    └──────┬──────┘
                           │ messages + system prompt + 12 tool schemas
              ┌────────────▼────────────┐
              │  LLM (any provider)     │  ONLY decomposes goal → milestones/
              │  anthropic/openai/      │  tasks + est_hours/is_core/deps.
              │  gemini/ollama/generic  │  NEVER dates or math.
              └────────────┬────────────┘
                           │ validated breakdown (retry once)
              ┌────────────▼────────────┐
              │  Deterministic Python   │  scheduler.py: buffer/target/finish,
              │  scheduler · velocity   │  front-load, drift, projections
              │  replanner · fallback   │  velocity, slippage, ladder
              └────────────┬────────────┘
                           │ INSERT-only versions + changelog
              ┌────────────▼────────────┐
              │  SQLite (SQLAlchemy 2.0)│  goals, plan_versions (immutable),
              │  via repository.py ONLY │  milestones, tasks, checkins,
              └─────────────────────────┘  events, memories, user_profile
```

**Critical rule:** the LLM never does date/duration math. Scheduling,
projections, drift, buffer math, and hour allocation are pure functions in
`planning/scheduler.py` — fully testable with zero API calls.

## Key behaviors

- **Persistent** — everything in SQLite; survives restarts.
- **Smart plans** — built backward from deadline, front-loaded (heavy work
  first, ~80% hours in first ~70% days), protected buffer
  `clamp(3,14,ceil(0.15*days)) × slippage`.
- **Versioned replanning** — plan rows INSERT-only; activate = transaction
  (deactivate old, insert new). One active version per goal, changelog always
  explains WHY.
- **Finish early** — `target_finish = deadline − buffer` strictly before deadline.
- **Multi-goal** — new goal triggers global replan: sort by (deadline asc,
  priority desc), greedy daily-hour allocation, ≥0.5h/day floor, conflict shown.
- **Interruptions** — blocked dates skipped entirely; buffer eaten before target moves.
- **Fallback ladder** — stretch(+20%, needs confirmation) → descope non-core →
  MVO-only → explicit negotiation (extend deadline OR reduced scope, recorded as
  Event, status `negotiating`). Never silent.
- **Learned velocity** — `mean(actual/est)` last 5 tasks; slippage +0.15 per late
  plan, cap 2.0.
- **Daily check-ins + nudges** — APScheduler at `checkin_time`; 2-day silence →
  micro-task nudge, 5 days → scope/pause proposal.
- **Memory** — one auto-line per check-in + manual `remember`/`recall`, injected
  into system prompt and planning.

## Project layout

```
src/goal_agent/{config,main,models/{goal,plan,checkin,event,user},
  db/{database,repository},llm/{client,tools,prompts},
  planning/{decomposer,scheduler,velocity,replanner,fallback},
  agent/loop,nudges/scheduler,memory/store}
tests/{test_scheduler,test_velocity,test_replanner,test_fallback,
  test_global_replan,test_plan_immutability,test_integration}
```

## Testing

```bash
python -m pytest -q   # 41 tests, FakeLLM everywhere, zero network
```

Covers scheduler math, velocity/slippage, every replanner trigger, the full
ladder, global replan, immutability, plus the 5 definition-of-done scenarios:
miss-3-days, second-goal-mid-flight, family-emergency week, impossible-goal
negotiation, early-finish celebration.
