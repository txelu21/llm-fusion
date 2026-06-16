"""Orchestrator: roster loading + preflight, sealed parallel Round 1 (with
corrected retry/quorum/diversity logic), anonymization, and the two judge
backends (auto = self-contained; handoff = main session judges)."""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .adapters import SUPPORTED_CLIS, get_adapter
from .core import (
    AgentResult,
    AgentSpec,
    RosterConfig,
    RunPaths,
    Status,
    anonymize,
    create_run_folder,
    load_yaml,
    render,
    strip_self_refs,
)

_RETRY_AFTER_RE = re.compile(r"(?:retry after|reset in|try again in)\s+(\d+)", re.IGNORECASE)
_BACKOFF_CAP = 30
_AGENT_FIELDS = {"name", "cli", "model", "role", "path"}


class CouncilError(Exception):
    """Actionable, user-facing failure (printed cleanly, not a traceback)."""


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #
def get_login_path() -> str:
    """The login-shell PATH (codex/gemini may be absent from a bare PATH)."""
    try:
        r = subprocess.run(["bash", "-lc", 'printf %s "$PATH"'],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return os.environ.get("PATH", "")


# --------------------------------------------------------------------------- #
# Roster loading + preflight
# --------------------------------------------------------------------------- #
def _parse_agents(raw: list, label: str) -> list[AgentSpec]:
    out: list[AgentSpec] = []
    for a in raw:
        missing = {"name", "cli", "model", "role"} - set(a)
        if missing:
            raise CouncilError(f"{label} agent {a!r} missing fields: {', '.join(sorted(missing))}")
        out.append(AgentSpec(**{k: v for k, v in a.items() if k in _AGENT_FIELDS}))
    return out


def _derive_execute(advise: list[AgentSpec]) -> list[AgentSpec]:
    """Fallback when execute_agents isn't given: one builder per distinct model."""
    seen: dict[str, AgentSpec] = {}
    for a in advise:
        if a.model not in seen:
            seen[a.model] = AgentSpec(name=f"{a.cli}-builder", cli=a.cli,
                                      model=a.model, role="roles/builder.md", path=a.path)
    return list(seen.values())


def load_roster(agents_yaml: Path) -> RosterConfig:
    if not agents_yaml.exists():
        raise CouncilError(f"agents.yaml not found at {agents_yaml}")
    data = load_yaml(agents_yaml)
    if not isinstance(data, dict):
        raise CouncilError(f"{agents_yaml} is not a valid mapping")
    advise_raw = data.get("advise_agents") or data.get("agents")  # back-compat
    if not advise_raw:
        raise CouncilError(f"{agents_yaml} has no 'advise_agents:' (or 'agents:') list")
    advise = _parse_agents(advise_raw, "advise")
    execute_raw = data.get("execute_agents")
    execute = _parse_agents(execute_raw, "execute") if execute_raw else _derive_execute(advise)

    roster = RosterConfig(
        defaults=data.get("defaults") or {},
        judge=data.get("judge") or {"backend": "handoff", "cli": "claude", "model": "opus"},
        executor=data.get("executor") or {"cli": "codex", "model": "gpt-5.5"},
        auditor=data.get("auditor") or {"cli": "gemini", "model": "gemini-2.5-pro"},
        advise_agents=advise,
        execute_agents=execute,
    )
    _validate_roster(roster, agents_yaml.parent)
    return roster


def _validate_roster(roster: RosterConfig, project_root: Path) -> None:
    # advise wants >=3 distinct models (perspective diversity across real models);
    # execute wants >=2 distinct models for a real build-off.
    for label, agents, min_models in (
        ("advise", roster.advise_agents, 3),
        ("execute", roster.execute_agents, 2),
    ):
        if not agents:
            raise CouncilError(f"{label}_agents is empty")
        for a in agents:
            if a.cli not in SUPPORTED_CLIS:
                raise CouncilError(f"{label} agent {a.name}: unsupported cli {a.cli!r} "
                                   f"(supported: {', '.join(SUPPORTED_CLIS)})")
            if not (project_root / a.role).exists():
                raise CouncilError(f"{label} agent {a.name}: role file not found: {a.role}")
        distinct = {a.model for a in agents}
        if len(distinct) < min_models:
            raise CouncilError(
                f"{label} council needs >={min_models} distinct models; "
                f"has {len(distinct)}: {', '.join(sorted(distinct))}"
            )
    # execute: same builder role + same model = pure redundancy, so models must be unique
    emodels = [a.model for a in roster.execute_agents]
    if len(set(emodels)) != len(emodels):
        dup = sorted({m for m in emodels if emodels.count(m) > 1})
        raise CouncilError(
            f"execute_agents must use distinct models (same builder role on the same "
            f"model is redundant); duplicated: {', '.join(dup)}"
        )


def load_prompts(project_root: Path) -> dict[str, str]:
    pdir = project_root / "prompts"
    if not pdir.is_dir():
        raise CouncilError(f"prompts/ directory not found at {pdir}")
    out: dict[str, str] = {}
    for name in ("round1_advise", "round1_execute", "judge_advise",
                 "judge_execute_spec", "executor", "auditor"):
        f = pdir / f"{name}.md"
        if not f.exists():
            raise CouncilError(f"missing prompt template: prompts/{name}.md")
        out[name] = f.read_text(encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
# Round 1 (sealed, parallel)
# --------------------------------------------------------------------------- #
def _parse_retry_after(text: str) -> int | None:
    m = _RETRY_AFTER_RE.search(text or "")
    return int(m.group(1)) if m else None


async def _run_one_agent(adapter, *, prompt, model, workdir, timeout, retries,
                         role_text, role_path) -> AgentResult:
    attempts = 0
    last: AgentResult | None = None
    while True:
        attempts += 1
        res = await adapter.invoke(
            prompt, model=model, workdir=workdir, timeout=timeout,
            role_text=role_text, role_path=role_path,
        )
        res.attempts = attempts
        last = res
        if res.status == Status.OK:
            return res
        if attempts > retries or not res.status.is_retryable:
            return res
        # retryable (rate_limited): honor server reset if present, else backoff+jitter
        wait = _parse_retry_after(res.stderr + res.raw)
        if wait is None:
            wait = min(2 ** attempts + random.uniform(0, 1.5), _BACKOFF_CAP)
        await asyncio.sleep(min(wait, _BACKOFF_CAP))
    return last  # unreachable


async def run_round1(roster: RosterConfig, agents: list[AgentSpec], project_root: Path,
                     login_path: str, paths: RunPaths, template: str, brief: str) -> list[AgentResult]:
    models = ", ".join(sorted({a.model for a in agents}))
    print(f"  fanning out to {len(agents)} agents in parallel across [{models}]...",
          file=sys.stderr, flush=True)

    async def one(agent: AgentSpec) -> AgentResult:
        adapter = get_adapter(agent, login_path)
        role_path = project_root / agent.role
        role_text = role_path.read_text(encoding="utf-8")
        agent_dir = paths.agent_dir(agent.name)
        agent_dir.mkdir(parents=True, exist_ok=True)
        prompt = render(template, ROLE=role_text, BRIEF=brief)
        (agent_dir / "role.md").write_text(role_text, encoding="utf-8")
        (agent_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        res = await _run_one_agent(
            adapter, prompt=prompt, model=agent.model, workdir=agent_dir,
            timeout=roster.round1_timeout, retries=roster.retries,
            role_text=role_text, role_path=role_path,
        )
        _write_agent_artifacts(agent_dir, res)
        mark = "✓" if res.ok else "✗"
        print(f"  {mark} {agent.name} ({agent.cli}/{agent.model}) → {res.status.value} "
              f"({res.duration_s}s)", file=sys.stderr, flush=True)
        return res

    return await asyncio.gather(*[one(a) for a in agents])


def _write_agent_artifacts(agent_dir: Path, res: AgentResult) -> None:
    (agent_dir / "stdout.txt").write_text(res.raw, encoding="utf-8")
    (agent_dir / "stderr.txt").write_text(res.stderr, encoding="utf-8")
    (agent_dir / "result.json").write_text(json.dumps(res.to_dict(), indent=2), encoding="utf-8")
    if res.answer:
        (agent_dir / "answer.md").write_text(res.answer, encoding="utf-8")
        _, hits = strip_self_refs(res.answer)
        if hits:
            (agent_dir / "anon_strip.log").write_text(
                "self-reference hits stripped before judging:\n- " + "\n- ".join(hits) + "\n",
                encoding="utf-8",
            )


# --------------------------------------------------------------------------- #
# Quorum + diversity
# --------------------------------------------------------------------------- #
def check_quorum(results: list[AgentResult], quorum: int) -> tuple[list[AgentResult], list[str]]:
    ok = [r for r in results if r.ok]
    notes: list[str] = []
    providers = {r.cli for r in ok}
    models = {r.model for r in ok}
    if len(ok) < quorum or len(providers) < 2:
        failed = [f"{r.name} ({r.cli}/{r.model}): {r.status.value} — {r.error_detail or 'see result.json'}"
                  for r in results if not r.ok]
        raise CouncilError(
            "below council quorum — need >=" + str(quorum) +
            " answers from >=2 providers, got " + f"{len(ok)} from {len(providers)} provider(s).\n  " +
            "\n  ".join(failed)
        )
    if len(results) > len(ok):
        notes.append(f"{len(results) - len(ok)} of {len(results)} agents failed; "
                     "council proceeded with the rest.")
    if len(models) < 3:
        notes.append(f"DIVERSITY WARNING: only {len(models)} distinct model(s) survived "
                     f"({', '.join(sorted(models))}); the council's value depends on >=3.")
    return ok, notes


# --------------------------------------------------------------------------- #
# Anonymize + write
# --------------------------------------------------------------------------- #
def anonymize_and_write(ok: list[AgentResult], paths: RunPaths, seed: int) -> dict[str, str]:
    answers, mapping = anonymize(ok, seed)
    for letter, text in answers.items():
        (paths.answers / f"{letter}.md").write_text(text, encoding="utf-8")
    paths.mapping_file.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    return answers


def answers_block(answers: dict[str, str]) -> str:
    return "\n\n".join(f"### Answer {letter}\n\n{text}" for letter, text in answers.items())


# --------------------------------------------------------------------------- #
# Meta
# --------------------------------------------------------------------------- #
def write_meta(paths: RunPaths, **fields) -> None:
    base = {}
    if paths.meta_file.exists():
        try:
            base = json.loads(paths.meta_file.read_text(encoding="utf-8"))
        except Exception:
            base = {}
    base.update(fields)
    paths.meta_file.write_text(json.dumps(base, indent=2), encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
