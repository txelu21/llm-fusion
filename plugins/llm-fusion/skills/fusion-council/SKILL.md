---
name: fusion-council
description: Run a decision, question, or tradeoff through the LLM Fusion sealed multi-model council (claude + codex + antigravity + grok answer independently, anonymized), then synthesize the verdict AS the Judge. Use when the user says "fusion-council", "fusion council", "fusion this", "sealed council", "run the fusion council", or wants real different models (not in-Claude lenses) to pressure-test a decision and hand it back for a judged verdict. For the lighter in-Claude lenses tool use /council instead; to BUILD something use /fusion-build.
---

# fusion-council — sealed multi-model council (advise mode)

You orchestrate a **sealed council** of real, different models, then you are the **Judge**. The runner enforces the seal (independent parallel processes, no cross-talk, anonymized A/B/C). You synthesize.

## Steps

1. **Run the council** (advise mode, you judge). Locate the bundled runner (works installed or from a dev checkout), then run it:
   ```bash
   PLUGIN="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$HOME"/.claude/plugins/cache/*/llm-fusion/*/ 2>/dev/null | sort -V | tail -1)}"
   PLUGIN="${PLUGIN:-$HOME/projects/llm-fusion/plugins/llm-fusion}"
   cd "$PLUGIN" && python3 -m council_runner --mode advise --judge handoff --brief "<the user's question, verbatim or lightly cleaned>"
   ```
   It fans out to the roster in `agents.yaml`, runs sealed Round 1 in parallel, anonymizes, and exits `awaiting-judge`, printing the run path (under `~/.llm-council/council-runs/`).

2. **Read ONLY the anonymized answers.** Read `<run>/JUDGE_INSTRUCTIONS.md` and every file in `<run>/public/answers/` (A.md, B.md, …). **Do NOT open `mapping.json`** — that would de-anonymize the council and defeat the seal.

3. **Judge.** Follow the structure in JUDGE_INSTRUCTIONS verbatim (Recommendation / Confidence / Where they agree / Where they disagree + your ruling / Key risks / Next actions). Weigh the arguments on merit. Write your synthesis to `<run>/public/final_report.md`, starting with the header line `> **Council run** · Judge: main-session (handoff) · Models: …` copied from `<run>/meta.json` (`models_surviving`), plus any `diversity_notes`.

4. **Show the range, then the verdict (transparency).** Because the run is a blocking subprocess the user can't watch live, surface what each member contributed BEFORE your synthesis: a one-line-per-answer summary (Answer A/B/C… → its recommendation + confidence) so they see the spread of views, not just your conclusion. Then give the verdict + run path. Confirm `final_report.md` exists and is non-empty. If `meta.json` has a DIVERSITY WARNING (fewer than 3 models survived — e.g. a CLI was unauthenticated), surface it plainly and suggest re-running after fixing auth.

The default advise council is **7 lenses across 5 models** / 4 vendors (architect, pragmatist, skeptic, first-principles, operator, user-advocate, realist). Edit `advise_agents` in `agents.yaml` to widen/narrow it.

## Notes
- **Don't answer from your own reasoning first.** The whole point is the sealed council; run it, then judge what it produced.
- **Fact-sensitive decision? Ground it first with `--context-file`.** The members reason from training knowledge (no live web) — so if the call depends on current facts (prices, recent events, competitor moves, this-week metrics), do ONE research pass up front (deep-research, `/last30days`, or your own lookup), write the verified facts to a file, and pass `--context-file <path>`. Those facts are injected into every member's brief (and the judge's) as a shared "## Shared context" block, so all members reason over the SAME grounded facts. Do NOT try to give members live browsing — one shared pass keeps the run fast, diverse, and reproducible. Research → brief → deliberate.
- A failed agent (not-authenticated / rate-limited / timeout) is handled gracefully — the council proceeds on quorum (≥2 answers from ≥2 providers). If it aborts below quorum, relay the actionable error.
- For a **fully unattended / headless** run (no main-session judging), use `--judge auto` instead — the runner spawns a fresh judge CLI and writes `final_report.md` itself.
- Doctor check before a run if unsure: `cd "$PLUGIN" && python3 -m council_runner --doctor` (verifies the member has claude + codex + agy + grok installed + authenticated).
