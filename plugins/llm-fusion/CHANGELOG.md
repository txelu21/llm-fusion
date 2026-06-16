# Changelog

All notable changes to LLM Fusion.

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
