"""Execute-mode sandbox: copy the workspace into the run's own dir, git-init it
so model git-ops stay local, run the hardened codex executor, then a read-only
auditor. File safety lives in the codex adapter's pinned writable_roots + the
escape test (tests/test_sandbox_escape.py). Real project files are never touched."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .adapters import get_adapter
from .core import AgentSpec, RosterConfig, RunPaths, Status, render

_COPY_IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "venv", "node_modules", "council-runs", "__pycache__", "*.pyc"
)
_DELIVERABLE_CAP = 24_000  # chars handed to the auditor


def _prepare_sandbox(paths: RunPaths, workspace: Path | None) -> Path:
    sbx = paths.sandbox
    if workspace:
        src = Path(workspace).expanduser().resolve()
        if not src.is_dir():
            raise FileNotFoundError(f"--workspace not a directory: {src}")
        shutil.copytree(src, sbx, ignore=_COPY_IGNORE)
    else:
        sbx.mkdir(parents=True, exist_ok=True)
    # the sandbox is its own throwaway git repo -> model git ops can't reach the parent
    subprocess.run(["git", "init", "-q"], cwd=sbx, capture_output=True)
    return sbx


def _summarize_sandbox(sbx: Path) -> str:
    parts: list[str] = ["### Files in sandbox\n"]
    files = sorted(p for p in sbx.rglob("*") if p.is_file() and ".git/" not in str(p.relative_to(sbx)))
    for p in files:
        parts.append(f"- {p.relative_to(sbx)} ({p.stat().st_size} bytes)")
    changes = sbx / "CHANGES.md"
    if changes.exists():
        parts.append("\n### CHANGES.md\n\n" + changes.read_text(encoding="utf-8", errors="replace"))
    body = "\n".join(parts)
    # include text-file contents up to the cap
    budget = _DELIVERABLE_CAP - len(body)
    for p in files:
        if budget <= 0:
            break
        if p.name == "CHANGES.md":
            continue
        try:
            txt = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        chunk = f"\n\n### {p.relative_to(sbx)}\n\n```\n{txt}\n```"
        body += chunk[:budget]
        budget -= len(chunk)
    return body


async def run_execution(roster: RosterConfig, login_path: str, prompts: dict,
                        brief: str, spec_text: str, paths: RunPaths,
                        workspace: Path | None) -> tuple[str, str]:
    sbx = _prepare_sandbox(paths, workspace)

    # --- executor (codex, sandboxed) ---
    exec_spec = AgentSpec(name="executor", cli=roster.executor["cli"],
                          model=roster.executor["model"], role="-")
    adapter = get_adapter(exec_spec, login_path)
    exec_prompt = render(prompts["executor"], BRIEF=brief, SPEC=spec_text)
    eres = await adapter.invoke(
        exec_prompt, model=exec_spec.model, workdir=sbx,
        timeout=roster.executor_timeout, execute=True, sandbox=sbx,
    )
    (paths.execute / "executor_result.json").write_text(json.dumps(eres.to_dict(), indent=2), encoding="utf-8")
    if eres.status != Status.OK:
        return (f"_Executor ({eres.cli}/{eres.model}) failed: {eres.status.value} — "
                f"{eres.error_detail}_", "_Skipped — no deliverable to audit._")

    deliverable = _summarize_sandbox(sbx)

    # --- auditor (read-only, different model) ---
    paths.audit.mkdir(parents=True, exist_ok=True)
    aud_spec = AgentSpec(name="auditor", cli=roster.auditor["cli"],
                         model=roster.auditor["model"], role="-")
    aud_adapter = get_adapter(aud_spec, login_path)
    aud_prompt = render(prompts["auditor"], BRIEF=brief, SPEC=spec_text, DELIVERABLE=deliverable)
    (paths.audit / "prompt.md").write_text(aud_prompt, encoding="utf-8")
    # cwd = the sandbox so the auditor's "workspace" IS the real deliverable it can
    # read directly (read-only), not an empty dir — otherwise file-existence checks
    # in the spec's checklist falsely FAIL.
    ares = await aud_adapter.invoke(
        aud_prompt, model=aud_spec.model, workdir=sbx, timeout=roster.judge_timeout,
    )
    audit_text = ares.answer if ares.ok else f"_Auditor failed: {ares.status.value} — {ares.error_detail}_"
    (paths.audit / "output.md").write_text(audit_text, encoding="utf-8")
    return deliverable, audit_text
