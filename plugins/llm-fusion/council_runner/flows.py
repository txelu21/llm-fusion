"""Advise (council) and execute (fusion) flows, plus the two judge backends:
- auto    : runner spawns a fresh judge CLI -> fully self-contained report.
- handoff : runner stops after anonymized Round 1; the calling Claude Code
            session is the Judge (the calling session judges).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .adapters import get_adapter
from .core import AgentSpec, RosterConfig, RunPaths, Status, create_run_folder, render
from .orchestrator import (
    CouncilError,
    answers_block,
    anonymize_and_write,
    check_quorum,
    now_iso,
    run_round1,
    write_meta,
)


def _seed(paths: RunPaths) -> int:
    return int.from_bytes(hashlib.sha1(paths.root.name.encode()).digest()[:4], "big")


def _models_of(ok) -> list[str]:
    return sorted({r.model for r in ok})


def _report_header(judge: str, models: list[str], notes: list[str]) -> str:
    lines = [f"> **Council run** · Judge: {judge} · Models: {', '.join(models)}"]
    for n in notes:
        lines.append(f"> {n}")
    return "\n".join(lines) + "\n\n---\n\n"


async def _judge_cli(roster: RosterConfig, login_path: str, prompt: str,
                     workdir: Path, label: str) -> "AgentResult":
    spec = AgentSpec(name=label, cli=roster.judge["cli"], model=roster.judge["model"], role="-")
    adapter = get_adapter(spec, login_path)
    return await adapter.invoke(prompt, model=spec.model, workdir=workdir,
                                timeout=roster.judge_timeout, role_text=None, role_path=None)


def _write_handoff(template: str, brief: str, answers, paths: RunPaths, mode: str) -> None:
    body = render(template, BRIEF=brief, ANSWERS=answers_block(answers))
    preamble = (
        f"# Judge instructions — {mode} mode (you are the Judge)\n\n"
        "You are the main session acting as the council Judge. Read ONLY the "
        "anonymized answers in `public/answers/` — do NOT open `mapping.json` "
        "(it would de-anonymize the council). Then follow the instruction below "
        "verbatim and write your synthesis to `public/final_report.md`.\n\n---\n\n"
    )
    (paths.root / "JUDGE_INSTRUCTIONS.md").write_text(preamble + body, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Advise
# --------------------------------------------------------------------------- #
async def run_advise(roster, project_root, login_path, prompts, brief, runs_base, backend) -> RunPaths:
    paths = create_run_folder(runs_base, "advise", brief)
    write_meta(paths, mode="advise", brief=brief, started=now_iso(), judge_backend=backend,
               roster=[asdict(a) for a in roster.advise_agents], status="running")

    results = await run_round1(roster, roster.advise_agents, project_root, login_path,
                               paths, prompts["round1_advise"], brief)
    ok, notes = check_quorum(results, roster.quorum)  # raises CouncilError below quorum
    answers = anonymize_and_write(ok, paths, _seed(paths))
    write_meta(paths, round1=[r.to_dict() for r in results], diversity_notes=notes,
               models_surviving=_models_of(ok))

    if backend == "auto":
        paths.judge.mkdir(parents=True, exist_ok=True)
        jprompt = render(prompts["judge_advise"], BRIEF=brief, ANSWERS=answers_block(answers))
        (paths.judge / "prompt.md").write_text(jprompt, encoding="utf-8")
        res = await _judge_cli(roster, login_path, jprompt, paths.judge, "judge")
        (paths.judge / "output.md").write_text(res.answer or res.error_detail, encoding="utf-8")
        (paths.judge / "raw.json").write_text(json.dumps(res.to_dict(), indent=2), encoding="utf-8")
        if not res.ok:
            write_meta(paths, status="aborted", ended=now_iso(),
                       error=f"judge failed: {res.status.value} — {res.error_detail}")
            raise CouncilError(f"judge ({res.cli}/{res.model}) failed: {res.status.value} — {res.error_detail}")
        header = _report_header(f"{res.cli}/{res.model} (auto)", _models_of(ok), notes)
        paths.final_report.write_text(header + res.answer, encoding="utf-8")
        write_meta(paths, status="complete", ended=now_iso(),
                   judge_identity=f"{res.cli}/{res.model} (auto)", final_report=str(paths.final_report))
    else:
        _write_handoff(prompts["judge_advise"], brief, answers, paths, "advise")
        write_meta(paths, status="awaiting-judge", ended=now_iso(),
                   judge_identity="main-session (handoff)")
    return paths


# --------------------------------------------------------------------------- #
# Execute
# --------------------------------------------------------------------------- #
async def run_execute(roster, project_root, login_path, prompts, brief, runs_base, backend, workspace) -> RunPaths:
    paths = create_run_folder(runs_base, "execute", brief)
    write_meta(paths, mode="execute", brief=brief, started=now_iso(), judge_backend=backend,
               roster=[asdict(a) for a in roster.execute_agents], status="running",
               workspace=str(workspace) if workspace else None)

    results = await run_round1(roster, roster.execute_agents, project_root, login_path,
                               paths, prompts["round1_execute"], brief)
    ok, notes = check_quorum(results, roster.quorum)
    answers = anonymize_and_write(ok, paths, _seed(paths))
    write_meta(paths, round1=[r.to_dict() for r in results], diversity_notes=notes,
               models_surviving=_models_of(ok))

    if backend != "auto":
        # main session synthesizes the spec AND executes it directly ("works on it")
        _write_handoff(prompts["judge_execute_spec"], brief, answers, paths, "execute")
        write_meta(paths, status="awaiting-judge", ended=now_iso(),
                   judge_identity="main-session (handoff)")
        return paths

    # auto: judge synthesizes a spec, then sandboxed executor + auditor run it.
    paths.execute.mkdir(parents=True, exist_ok=True)
    paths.judge.mkdir(parents=True, exist_ok=True)
    jprompt = render(prompts["judge_execute_spec"], BRIEF=brief, ANSWERS=answers_block(answers))
    (paths.judge / "prompt.md").write_text(jprompt, encoding="utf-8")
    jres = await _judge_cli(roster, login_path, jprompt, paths.judge, "judge")
    (paths.judge / "output.md").write_text(jres.answer or jres.error_detail, encoding="utf-8")
    if not jres.ok:
        write_meta(paths, status="aborted", ended=now_iso(),
                   error=f"spec judge failed: {jres.status.value} — {jres.error_detail}")
        raise CouncilError(f"spec judge failed: {jres.status.value} — {jres.error_detail}")
    spec_text = jres.answer
    (paths.execute / "execution_spec.md").write_text(spec_text, encoding="utf-8")

    from .sandbox import run_execution
    exec_summary, audit_text = await run_execution(
        roster, login_path, prompts, brief, spec_text, paths, workspace
    )
    header = _report_header(f"{jres.cli}/{jres.model} (auto)", _models_of(ok), notes)
    final = (header + "## Execution spec\n\n" + spec_text +
             "\n\n---\n\n## What was built\n\n" + exec_summary +
             "\n\n---\n\n## Audit\n\n" + audit_text + "\n")
    paths.final_report.write_text(final, encoding="utf-8")
    write_meta(paths, status="complete", ended=now_iso(),
               judge_identity=f"{jres.cli}/{jres.model} (auto)", final_report=str(paths.final_report))
    return paths
