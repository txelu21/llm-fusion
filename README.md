# LLM Fusion — a Claude Code plugin

> *Many models in. One fused answer out.*

A **sealed multi-model LLM council** for Claude Code. One prompt → **Claude + Codex (GPT) + Gemini answer independently** (no peeking at each other) → anonymized → a judge fuses the best. Two commands:

- **`/fusion-council "<question>"`** — 6 different expert lenses across 3+ real models pressure-test a decision → you get a judged decision memo.
- **`/fusion-build "<goal>"`** — a *build-off*: all 3 models plan the same task, the judge fuses the best plan, then it gets built and audited.

This repo is both the **plugin** and its **marketplace** (the catalog Claude Code installs from).

## Install (members)

**Prerequisites — you need all three CLIs installed and logged in, plus Python 3.11+:**
- [`claude`](https://docs.claude.com/en/docs/claude-code) (Claude subscription)
- `codex` (ChatGPT/Codex subscription)
- `gemini` (Google Gemini subscription)

Then, in Claude Code:

```
/plugin marketplace add gabrieljudah/llm-fusion
/plugin install llm-fusion@fusion
```

Verify your setup from a checkout:

```
cd plugins/llm-fusion
python3 -m council_runner --doctor
```

That's it — `/fusion-council` and `/fusion-build` are now available. Run folders are written to `~/.llm-council/council-runs/` (never inside the plugin).

## Update

```
/plugin marketplace update fusion   →   /reload-plugins
```

Auto-update can be toggled in `/plugin` → Marketplaces. Each release bumps the version in `plugin.json` + `marketplace.json` and is git-tagged (`v1.2.0`, …); see [the plugin CHANGELOG](plugins/llm-fusion/CHANGELOG.md).

## What's inside

```
.claude-plugin/marketplace.json        the catalog (this repo = a marketplace)
plugins/llm-fusion/                 the plugin payload (self-contained)
  .claude-plugin/plugin.json            the plugin manifest
  skills/fusion-council/SKILL.md        /fusion-council  (decide)
  skills/fusion-build/SKILL.md          /fusion-build    (build)
  council_runner/                       the Python engine (zero runtime deps)
  roles/  prompts/  agents.yaml         the council config (editable)
  tests/                                unit + live sandbox-escape tests
```

Full usage + design: [plugins/llm-fusion/README.md](plugins/llm-fusion/README.md).

## Note on cost / access
The council spends **three** subscriptions per run (one call per model, in parallel). It's a power-user tool — make sure members know they need all three CLIs before installing.
