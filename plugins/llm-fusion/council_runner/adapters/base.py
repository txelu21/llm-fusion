"""Adapter base: async subprocess runner, outcome classification, binary
resolution. Each CLI adapter subclasses this and owns its own argv + parsing."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path

from ..core import AgentResult, AgentSpec, Status

# stderr/stdout signatures, checked in priority order.
_AUTH_RE = re.compile(
    r"\b(401|403|unauthorized|not logged in|please log in|login required|"
    r"permission_denied|invalid api key|authentication)\b",
    re.IGNORECASE,
)
_RATE_RE = re.compile(
    r"\b(429|rate limit|rate-limit|quota|resource_exhausted|overloaded|"
    r"too many requests|usage limit)\b",
    re.IGNORECASE,
)
# deterministic, non-retryable bad-request failures
_TERMINAL_RE = re.compile(
    r"\b(invalid_request|model_not_found|unknown model|max_output_tokens|"
    r"context_length|bad request|unsupported)\b",
    re.IGNORECASE,
)


def resolve_binary(name: str, explicit: str | None, login_path: str | None) -> str | None:
    """Resolve a CLI binary: explicit path wins, then the login-shell PATH
    (passed in), then the current PATH. Returns absolute path or None."""
    if explicit:
        p = Path(explicit).expanduser()
        return str(p) if p.exists() else None
    if login_path:
        hit = shutil.which(name, path=login_path)
        if hit:
            return hit
    return shutil.which(name)


class Adapter(ABC):
    cli_name: str = "base"

    def __init__(self, spec: AgentSpec, login_path: str | None = None):
        self.spec = spec
        self.login_path = login_path
        self.binary = resolve_binary(self.cli_name, spec.path, login_path)

    # ---- subprocess plumbing -------------------------------------------- #
    async def _run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout: int,
        env: dict | None = None,
        stdin_devnull: bool = True,
    ) -> tuple[int | None, str, str, float, bool]:
        """Run argv. Returns (returncode, stdout, stderr, duration_s, timed_out)."""
        full_env = dict(os.environ)
        if self.login_path:
            full_env["PATH"] = self.login_path + os.pathsep + full_env.get("PATH", "")
        # never hand the Anthropic setup-token to the OpenAI/Google CLIs
        if self.cli_name != "claude":
            full_env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        if env:
            full_env.update(env)
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL if stdin_devnull else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
                env=full_env,
            )
        except FileNotFoundError:
            return 127, "", f"{self.cli_name}: binary not found", time.monotonic() - start, False
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await asyncio.gather(proc.wait(), return_exceptions=True)
            return None, "", f"{self.cli_name}: timed out after {timeout}s", time.monotonic() - start, True
        dur = time.monotonic() - start
        return proc.returncode, out_b.decode("utf-8", "replace"), err_b.decode("utf-8", "replace"), dur, False

    # ---- classification ------------------------------------------------- #
    def _classify(self, rc: int | None, stdout: str, stderr: str, timed_out: bool) -> tuple[Status, str]:
        if timed_out:
            return Status.TIMEOUT, stderr
        # not_installed only from the precise spawn-time sentinel — NOT from the
        # model's own output (an executor's sandboxed shell can legitimately emit
        # "command not found" while still succeeding at the build).
        if rc == 127 or "binary not found" in stderr:
            return Status.NOT_INSTALLED, stderr or "not installed"
        if rc in (0, None):
            return Status.OK, ""
        # the run failed (rc != 0): now it's safe to read the failure signature.
        blob = f"{stderr}\n{stdout}"
        if _AUTH_RE.search(blob):
            return Status.NOT_AUTHENTICATED, _first_line(blob)
        if _RATE_RE.search(blob):
            return Status.RATE_LIMITED, _first_line(blob)
        return Status.ERROR, _first_line(blob) or f"exit {rc}"

    def _result(self, *, status, answer="", raw="", stderr="", duration=0.0, attempts=1, detail="") -> AgentResult:
        return AgentResult(
            name=self.spec.name, cli=self.cli_name, model=self.spec.model,
            status=status, answer=answer.strip(), raw=raw, stderr=stderr,
            duration_s=round(duration, 2), attempts=attempts, error_detail=detail,
        )

    def installed(self) -> bool:
        return self.binary is not None

    # ---- interface ------------------------------------------------------ #
    @abstractmethod
    async def invoke(
        self,
        prompt: str,
        *,
        model: str,
        workdir: Path,
        timeout: int,
        role_text: str | None = None,
        role_path: Path | None = None,
        execute: bool = False,
        sandbox: Path | None = None,
    ) -> AgentResult:
        ...

    @abstractmethod
    def auth_check(self) -> tuple[bool, str]:
        """Cheap, no-model auth probe. Returns (authed, detail)."""
        ...


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:300]
    return ""
