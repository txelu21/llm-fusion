# Changelog

All notable changes to LLM Fusion.

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
