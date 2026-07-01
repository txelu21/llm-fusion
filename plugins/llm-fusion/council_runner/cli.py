"""Command-line entry: `python -m council_runner --mode advise --brief "..."`,
plus `--doctor` readiness. Thin — real work is in flows/orchestrator."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

from .adapters import SUPPORTED_CLIS, get_adapter
from .core import AgentSpec, Status, failures_log_path
from .flows import run_advise, run_execute
from .orchestrator import (
    CouncilError,
    get_login_path,
    load_prompts,
    load_roster,
)

# repo root = parent of this package (holds agents.yaml, roles/, prompts/).
# Self-locating: works whether run from a git checkout or an installed plugin dir.
PKG_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Persistent, writable location for runs + member config. NOT the plugin
    dir — that's read-only and wiped on plugin update. Honors ${CLAUDE_PLUGIN_DATA}
    when set (Claude Code plugin host), else ~/.llm-council."""
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    return (Path(base) if base else Path.home() / ".llm-council")


def _load_local_env(path: Path) -> None:
    """Load KEY=VALUE lines from a gitignored .env.local into os.environ
    (setdefault — never overrides an explicitly-exported value). This is how
    CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) reaches the claude child
    without exporting it in the shell. The file never leaves the machine."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="council_runner",
        description="Sealed multi-model LLM council runner (advise / execute).",
    )
    p.add_argument("--mode", choices=["advise", "execute"],
                   help="advise = decision memo; execute = plan -> spec -> build")
    p.add_argument("--brief", help="the question (advise) or goal (execute)")
    p.add_argument("--judge", choices=["handoff", "auto"], default=None,
                   help="handoff = main session judges (default); auto = self-contained CLI judge")
    p.add_argument("--workspace", default=None,
                   help="execute mode: dir copied into the sandbox as the executor's writable root")
    p.add_argument("--context-file", default=None, metavar="PATH",
                   help="file of verified/shared facts injected into EVERY member's brief (and the judge's) "
                        "as a 'Shared context' block — grounds a fact-sensitive decision without giving each "
                        "member live web access. Do one research pass up front, drop the facts here.")
    p.add_argument("--agents", default=str(PKG_ROOT / "agents.yaml"),
                   help="path to agents.yaml")
    p.add_argument("--runs-dir", default=str(data_dir() / "council-runs"),
                   help="where run folders are created (persistent, outside the plugin dir)")
    p.add_argument("--doctor", action="store_true", help="report per-CLI readiness and exit")
    p.add_argument("--ping", action="store_true",
                   help="with --doctor: fire one real round-1 call per CLI to prove the live path")
    p.add_argument("--failures", nargs="?", const=20, type=int, metavar="N",
                   help="summarize the cross-run failure log (last N entries, default 20) and exit")
    return p


def _doctor(roster, project_root: Path, login_path: str, runs_dir: Path, ping: bool) -> int:
    print("council doctor — per-CLI readiness\n")
    seen: dict[str, AgentSpec] = {}
    for a in roster.advise_agents + roster.execute_agents:
        seen.setdefault(a.cli, a)
    all_ready = True
    for cli in SUPPORTED_CLIS:
        spec = seen.get(cli) or AgentSpec(name=cli, cli=cli, model="?", role="-")
        adapter = get_adapter(spec, login_path)
        installed = adapter.installed()
        authed, detail = adapter.auth_check() if installed else (False, "not installed")
        ready = installed and authed
        in_roster = cli in seen
        mark = "✓" if ready else "✗"
        where = f" @ {adapter.binary}" if installed else ""
        roster_note = "" if in_roster else "  (not in roster)"
        print(f"  {mark} {cli:<12} installed={installed}  authed={authed}{where}")
        print(f"        {detail}{roster_note}")
        if ping and ready and in_roster:
            res = asyncio.run(_ping_one(adapter, spec, runs_dir))
            pmark = "✓" if res.status == Status.OK else "✗"
            print(f"        {pmark} ping: {res.status.value}"
                  + (f" — {res.error_detail}" if res.status != Status.OK else f" ({res.duration_s}s)"))
            if res.status != Status.OK:
                ready = False
        if in_roster and not ready:
            all_ready = False

    a_models = sorted({a.model for a in roster.advise_agents})
    e_models = sorted({a.model for a in roster.execute_agents})
    print(f"\n  advise council:  {len(roster.advise_agents)} agents / "
          f"{len(a_models)} models: {', '.join(a_models)}")
    print(f"  execute build-off: {len(roster.execute_agents)} agents / "
          f"{len(e_models)} models: {', '.join(e_models)}")
    print(f"  judge backend: {roster.judge.get('backend', 'handoff')}  "
          f"executor: {roster.executor.get('cli')}/{roster.executor.get('model')}")
    print(f"\n  overall: {'READY' if all_ready else 'NOT READY — fix the ✗ rows above'}")
    return 0 if all_ready else 1


async def _ping_one(adapter, spec: AgentSpec, runs_dir: Path):
    wd = runs_dir / ".doctor" / spec.cli
    wd.mkdir(parents=True, exist_ok=True)
    return await adapter.invoke("Reply with only the word READY.",
                                model=spec.model, workdir=wd, timeout=90)


def _failures(n: int) -> int:
    """Summarize the cross-run failure log: counts by (cli, status) so a chronic
    offender (e.g. grok/timeout) is obvious, then the last N raw entries."""
    path = failures_log_path()
    if not path.exists():
        print(f"no failure log yet — clean so far ({path})")
        return 0
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        print(f"failure log is empty ({path})")
        return 0
    by = Counter((r.get("cli"), r.get("status")) for r in rows)
    print(f"council failure log — {len(rows)} failure(s) recorded  ({path})\n")
    print("  by (cli, status):")
    for (cli, status), c in by.most_common():
        print(f"    {c:>4}  {cli or '?':<12} {status or '?'}")
    show = rows[-n:]
    print(f"\n  last {len(show)}:")
    for r in show:
        print(f"    {str(r.get('ts', '?'))[:19]}  {str(r.get('cli', '?')):<11} "
              f"{str(r.get('status', '?')):<14} {r.get('duration_s', '?')}s  "
              f"a{r.get('attempts', '?')}  {(r.get('detail') or '')[:80]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # persistent config home first (members keep their token here), then the
    # plugin/repo dir (dev convenience). setdefault => first one wins.
    _load_local_env(data_dir() / ".env.local")
    _load_local_env(PKG_ROOT / ".env.local")
    args = build_parser().parse_args(argv)
    if args.failures is not None:  # roster-independent — inspect the log even if agents.yaml is broken
        return _failures(args.failures)
    agents_yaml = Path(args.agents).expanduser().resolve()
    project_root = agents_yaml.parent
    runs_dir = Path(args.runs_dir).expanduser().resolve()
    login_path = get_login_path()

    try:
        roster = load_roster(agents_yaml)
        prompts = load_prompts(project_root)
    except CouncilError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.doctor:
        return _doctor(roster, project_root, login_path, runs_dir, args.ping)

    if not args.mode:
        print("error: --mode {advise|execute} is required (or use --doctor)", file=sys.stderr)
        return 2
    if not args.brief or not args.brief.strip():
        print("error: --brief is required", file=sys.stderr)
        return 2

    backend = args.judge or roster.judge.get("backend", "handoff")
    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else None
    runs_dir.mkdir(parents=True, exist_ok=True)

    context = None
    if args.context_file:
        cpath = Path(args.context_file).expanduser().resolve()
        if not cpath.exists():
            print(f"error: --context-file not found: {cpath}", file=sys.stderr)
            return 2
        context = cpath.read_text(encoding="utf-8")
        if not context.strip():
            print(f"warning: --context-file is empty, ignoring: {cpath}", file=sys.stderr)
            context = None
        elif len(context) > 50_000:
            print(f"warning: --context-file is large ({len(context)} chars) — it is injected into "
                  f"EVERY member prompt, multiplying token cost per run.", file=sys.stderr)

    try:
        if args.mode == "advise":
            paths = asyncio.run(run_advise(roster, project_root, login_path, prompts,
                                           args.brief, runs_dir, backend, context))
        else:
            paths = asyncio.run(run_execute(roster, project_root, login_path, prompts,
                                            args.brief, runs_dir, backend, workspace, context))
    except CouncilError as e:
        print(f"\ncouncil aborted: {e}", file=sys.stderr)
        return 1

    _print_outcome(paths, backend, args.mode)
    return 0


def _print_outcome(paths, backend: str, mode: str) -> None:
    print(f"\nrun: {paths.root}")
    if backend == "auto":
        print(f"report: {paths.final_report}")
    else:
        print(f"answers: {paths.answers}")
        print(f"judge instructions: {paths.root / 'JUDGE_INSTRUCTIONS.md'}")
        print("status: awaiting-judge — the main session now synthesizes the verdict.")
