# LLM Fusion — Sealed Multi-Model Council Runner

One prompt → **claude + codex + gemini answer independently** (sealed, no cross-talk) → answers anonymized → a **Judge** synthesizes. Two modes:

- **advise (council):** members give views → Judge → decision memo.
- **execute (fusion):** members each *plan* → Judge synthesizes one execution spec → it gets built (by you, or by a sandboxed executor) → audited.

The core value is the **seal**: in Round 1 no model sees another's answer and no prior run carries forward, so you get genuine independent multi-model judgment instead of one model's bias. Runs entirely on your local authenticated CLIs — no API keys, no OpenRouter.

## Install

```bash
cd /path/to/llm-fusion/plugins/llm-fusion
# zero runtime deps; Python 3.11+. (PyYAML optional — a bundled subset loader reads agents.yaml without it.)
python3 -m council_runner --doctor          # check all three CLIs are installed + authed
```

## Use

```bash
# Council (advise) — you (the calling session) are the Judge:
python3 -m council_runner --mode advise --judge handoff --brief "Should I do X or Y?"
#   -> runs sealed Round 1, anonymizes, exits 'awaiting-judge'. Read public/answers/*, write public/final_report.md.

# Council (advise) — fully unattended, runner judges itself:
python3 -m council_runner --mode advise --judge auto --brief "Should I do X or Y?"

# Fusion (execute) — plan with all models, then build:
python3 -m council_runner --mode execute --judge handoff --brief "Build a CSV-to-JSON converter" --workspace /path/to/repo
python3 -m council_runner --mode execute --judge auto    --brief "Build a CSV-to-JSON converter"   # sandboxed codex executor + auditor
```

From **Claude Code**, just use the skills: `/fusion-council "<question>"` and `/fusion-build "<goal>"`.

## How the seal works

| Threat | Defense |
|---|---|
| One model herds toward another's answer | Each Round-1 agent is a separate process in its own `private/<agent>/` dir; no agent ever sees a sibling's answer. |
| Prior run carries forward | No session resume; **codex always runs `--ignore-user-config`** (its memory/supermemory would otherwise carry answers). |
| Judge biased by knowing who said what | Answers shuffled to A/B/C; `mapping.json` kept at run root (never in `public/answers/`); self-references stripped. |
| Below 3 models = weak council | Preflight requires ≥3 distinct models; a runtime DIVERSITY WARNING fires if fewer survive. |
| Execute mode damages real files | Build runs in a copied, `git init`'d sandbox; the codex executor's writable roots are pinned, `/tmp`/`$TMPDIR` excluded, network off. **Proven by `tests/test_sandbox_escape.py`.** |

## Config — `agents.yaml`

3 agents, one per model (add more = one line + a `roles/*.md`). Per-agent `name/cli/model/role` (+ optional `path`). `defaults` (timeouts/retries/quorum), `judge` (handoff|auto + cli/model), `executor` (codex), `auditor` (different model).

## Layout

`council_runner/` (core, orchestrator, flows, sandbox, cli, adapters/) · `roles/*.md` (perspectives) · `prompts/*.md` (templates) · `council-runs/` (gitignored artifacts) · `.claude/skills/` · `tests/`.

## Tests

```bash
python3 -m unittest discover -s tests                       # fast, no CLI calls
COUNCIL_LIVE_TESTS=1 python3 -m unittest tests.test_sandbox_escape   # live codex escape proof
```

v2 (deferred): web dashboard, MCP tool, claude/gemini sandboxed executors.
