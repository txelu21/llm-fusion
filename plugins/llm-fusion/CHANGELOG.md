# Changelog

All notable changes to LLM Fusion.

## [1.3.3] — 2026-07-01 (fork: txelu21 — shared-context grounding)

### Added
- **`--context-file <PATH>`** — inject a file of verified/shared facts into **every** member's round-1 prompt (and the judge's) as a `## Shared context` block. This is how you **ground a fact-sensitive decision without giving each member live web access**: do one research pass up front (e.g. deep-research / last30days), drop the verified facts in a file, and every member reasons over the **same** facts on equal footing. Deliberately preferred over per-member browsing, which would (a) slow every run to browse latency, (b) **converge** answers on the same search results (killing the diversity that is the council's value), and (c) make runs non-reproducible. The `{{CONTEXT}}` template slot collapses to nothing when the flag is absent (backward compatible). Facts copied to `<run>/context.md`; `context_chars` recorded in `meta.json`; warns if the file exceeds 50k chars (it multiplies per-member token cost). Pattern: **research → brief → deliberate** (keep `/fusion-council` = judgment, `deep-research` = web facts; chain them, don't merge them).

## [1.3.2] — 2026-07-01 (fork: txelu21 — Grok reliability)

### Fixed
- **Grok no longer times out and gets silently dropped every run.** Root cause: headless `-p` ran grok as a full agent (web-search + subagents + multi-turn loops on by default), so `grok-composer-2.5-fast` streamed 40KB+ of reasoning past the 300s budget; `--output-format json` buffered it all and **lost it on the kill** (empty stdout); and `TIMEOUT` was non-retryable with no fallback. Grok is now pinned to a **single-shot reasoner** matching its peers: `--output-format streaming-json` + `--max-turns 1` + `--no-subagents` + `--disable-web-search` (grok.py). Live: `grok-realist → ok (26s)`, was a 300s timeout.
- **streaming-json extractor** (`_from_events`) now reads grok's actual event shape — text is in the `data` key of `{"type":"text","data":…}`; `{"type":"thought",…}` reasoning is excluded from the answer (was leaking raw NDJSON into the answer after the format switch).

### Added
- **Partial-output salvage.** `Adapter._run` now streams stdout/stderr into buffers so partial output SURVIVES a timeout kill (`communicate()` discarded it). Grok promotes a streamed-but-complete answer (closing `RECOMMENDATION` line present) to OK instead of losing it.
- **Self-healing timeout retry.** A `TIMEOUT` on an adapter that opts in (`SUPPORTS_DEGRADED_RETRY`, currently grok) gets ONE fast degraded retry — `--effort low`, half the budget — before being dropped (orchestrator `_run_one_agent`). Other adapters' timeout handling is unchanged.
- **Cross-run failure log + `--failures` CLI.** Every non-OK agent, across all runs, is appended to `~/.llm-council/council-failures.jsonl` (`core.append_failure`); `python -m council_runner --failures [N]` summarizes counts by (cli, status) plus the last N entries, so a chronic offender is obvious instead of vanishing into per-run dirs.

> Note: disabling grok's live web-search makes it single-shot like the other members (the round-1 prompt is analysis-only, and no other member web-searches). Re-enable by raising `--max-turns` if the live-web lens is wanted back. Flags confirmed against grok 0.2.77.

## [1.3.1] — 2026-06-30 (fork: txelu21)

### Changed
- Sonnet seat upgraded to **Sonnet 5** (`claude-firstprinciples` model `sonnet` → `claude-sonnet-5`, released 2026-06-30, narrows the gap with Opus 4.8). Roster unchanged otherwise — still 7 lenses / 5 models / 4 vendors. Verified the `claude` CLI accepts `--model claude-sonnet-5` live.

## [1.3.0] — 2026-06-30 (fork: txelu21 — Grok)

### Added
- **Fourth provider: Grok (xAI).** New `GrokAdapter` (`council_runner/adapters/grok.py`) drives the xAI Grok Build CLI (`grok`) in headless read-only mode (`-p … --output-format json --mode ask`), with a defensive answer extractor that handles json / streaming-json / plain-text output. Registered in the adapter registry (`SUPPORTED_CLIS` now 4).
- **`roles/realist.md`** — Grok's lens: live market/world reality, timing, and data-grounded risk.
- Roster: `grok-realist` added to `advise_agents` (now **7 lenses across 5 models**) and `grok-builder` to `execute_agents` (now **4 models**). Grok is an advisor/planner only — it **never** executes; the autonomous executor stays codex-only (the only CLI with a real OS seatbelt).
- Seal hardened: anonymization patterns + round-1 prompts now strip Grok/xAI self-references too.
- **Latent `gemini` fallback adapter** (`council_runner/adapters/gemini.py`) — the documented kill-switch for the Google seat: a drop-in for `agy`/Antigravity using the official `@google/gemini-cli` (`gemini -p … --model …`, plain-text, YOLO off / read-only). Registered but NOT wired into `agents.yaml`; activate by swapping `cli: antigravity` -> `cli: gemini`. Use only if Antigravity is unavailable (note: a `GEMINI_API_KEY` path is metered API, against the "local CLIs, no keys" design — prefer subscription auth).
- Tests: Grok + Gemini provider/argv/executor-guard/self-ref coverage; updated roster-count assertions.

> Grok flags + model CONFIRMED LIVE against grok 0.2.77 (`--permission-mode plan` for read-only, `--output-format json`, `-p/--single`, `--model`; grok.com tier model = `grok-composer-2.5-fast`). `--doctor --ping` makes a real round-1 grok call and passes.

### Changed
- Switched the Google provider surface from `gemini` CLI to `antigravity` CLI while keeping Gemini model names in the roster.
- Pointed the Antigravity provider at the installed `agy` executable.

## [1.2.0] — 2026-06-16

### Added
- **Packaged as a self-contained Claude Code plugin + marketplace.** The repo is now a marketplace (`.claude-plugin/marketplace.json`) hosting the `llm-fusion` plugin (`plugins/llm-fusion/`) — members `/plugin marketplace add` + `/plugin install`, no separate clone needed.
- Runs + member config moved to a **persistent home** (`~/.llm-council/`, or `${CLAUDE_PLUGIN_DATA}`), never the read-only plugin dir; skills self-locate the bundled runner via `${CLAUDE_PLUGIN_ROOT}` with a cache-glob fallback.

## [1.1.0] — 2026-06-16

### Added
- **Auth via `claude setup-token`** — runner auto-loads `CLAUDE_CODE_OAUTH_TOKEN` from a gitignored `.env.local`, so the claude seat works even when the macOS keychain token is stale.
- **Two rosters** in `agents.yaml`: `advise_agents` (6 diverse lenses across 4 models) and `execute_agents` (a build-off — one shared `builder` role per distinct model).
- **`roles/builder.md`** — the shared execution role for the execute build-off.
- **Live progress output** — per-agent fan-out + `✓/✗` completion lines to stderr.
- Retryable **`EMPTY`** status — gemini `INVALID_STREAM` / empty responses now retry instead of dropping a model.

### Changed
- Skills renamed: `/council-advise` → **`/fusion-council`**, `/council-execute` → **`/fusion-build`**.
- gemini model bumped `gemini-2.5-pro` → **`gemini-3.1-pro-preview`** (current flagship on the CLI).
- Execute mode is now a **model build-off** (same builder role, different models, judge fuses the best) instead of reusing the advise lenses.

## [1.0.0] — 2026-06-14

### Added
- Sealed multi-model council runner: `claude` + `codex` + `gemini` answer independently in parallel, anonymized A/B/C, fresh judge synthesizes.
- **Advise** mode (decision memo) and **execute** mode (plan → spec → sandboxed build → audit).
- Two judge backends: `handoff` (calling session judges) and `auto` (self-contained CLI judge).
- Hardened **codex sandbox executor** (pinned writable roots, tmp excluded, network off) — escape-proof, proven by `tests/test_sandbox_escape.py`.
- `--doctor` / `--doctor --ping` readiness checks; graceful degradation (quorum + diversity warning); zero runtime deps.
