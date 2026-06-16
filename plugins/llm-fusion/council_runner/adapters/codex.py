"""codex adapter. The ONLY autonomous executor (codex has a real OS seatbelt;
claude/gemini do not). Every call carries --ignore-user-config (R5 guard): on
codex's ~/.codex/config.toml commonly enables memories/use_memories + a supermemory
MCP, which would carry a prior council answer forward. --ignore-user-config also
drops model=gpt-5.5, so -m is always passed explicitly. exec has no
--ask-for-approval flag (0.139.0); sandbox policy governs acting.

Executor writable roots are PINNED (codex workspace-write otherwise also writes
{cwd, $TMPDIR, /tmp} — proven live in the critic panel), tmp excluded, network
off. Role for round-1 is prepended to the prompt (exec has no --system-prompt)."""
from __future__ import annotations

from pathlib import Path

from .base import Adapter
from ..core import AgentResult, Status


class CodexAdapter(Adapter):
    cli_name = "codex"

    def _base_argv(self, out_file: Path) -> list[str]:
        argv = [
            self.binary, "exec",
            "--ephemeral",
            "--ignore-user-config",   # R5: non-negotiable (memory-off)
            "--skip-git-repo-check",
            "-o", str(out_file),
        ]
        # non-regressable guard
        if "--ignore-user-config" not in argv:
            raise RuntimeError("codex argv missing --ignore-user-config (memory leak guard)")
        return argv

    async def invoke(
        self, prompt, *, model, workdir, timeout,
        role_text=None, role_path=None, execute=False, sandbox=None,
    ) -> AgentResult:
        if not self.installed():
            return self._result(status=Status.NOT_INSTALLED, detail="codex not on PATH")

        full_prompt = f"{role_text}\n\n{prompt}" if role_text else prompt

        if execute:
            if sandbox is None:
                return self._result(status=Status.ERROR, detail="codex executor requires a sandbox dir")
            sbx = Path(sandbox).resolve()
            out_file = sbx.parent / "codex_exec_last.txt"
            argv = self._base_argv(out_file) + [
                "-s", "workspace-write",
                "-C", str(sbx),
                "-m", model,
                "-c", f'sandbox_workspace_write.writable_roots=["{sbx}"]',
                "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
                "-c", "sandbox_workspace_write.network_access=false",
                full_prompt,
            ]
            cwd = sbx
        else:
            out_file = workdir / "codex_last.txt"
            argv = self._base_argv(out_file) + [
                "-s", "read-only",
                "-m", model,
                full_prompt,
            ]
            cwd = workdir

        rc, out, err, dur, timed = await self._run(argv, cwd=cwd, timeout=timeout)
        status, detail = self._classify(rc, out, err, timed)

        answer = ""
        if out_file.exists():
            answer = out_file.read_text(encoding="utf-8", errors="replace").strip()
        if not answer and rc == 0:
            answer = out.strip()  # fall back to stdout if -o file empty

        if status == Status.OK and not answer:
            return self._result(status=Status.EMPTY, raw=out, stderr=err, duration=dur,
                                detail="codex: empty output")
        if status != Status.OK:
            return self._result(status=status, raw=out, stderr=err, duration=dur, detail=detail)
        return self._result(status=Status.OK, answer=answer, raw=out, stderr=err, duration=dur)

    def auth_check(self) -> tuple[bool, str]:
        if not self.installed():
            return False, "not installed"
        # `codex login status` is cheap and does not call a model.
        import subprocess
        try:
            r = subprocess.run(
                [self.binary, "login", "status"],
                capture_output=True, text=True, timeout=20,
            )
            if r.returncode == 0:
                return True, (r.stdout.strip().splitlines() or ["logged in"])[0][:120]
            return False, (r.stderr or r.stdout or "not logged in").strip()[:120]
        except Exception as e:  # pragma: no cover
            return False, f"login status failed: {e}"
