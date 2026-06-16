"""gemini adapter. Round-1 / judge / auditor are read-only (--approval-mode
plan). Role injected via GEMINI_SYSTEM_MD (a file path). Stateless per process
(never --resume). Executor (acting) deferred to v2 — gemini has no OS-level fs
sandbox flag, so the autonomous executor is codex-only."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .base import Adapter
from ..core import AgentResult, Status

# gemini collapses several failures into exit 1; these subcodes are terminal.
_TERMINAL_EXIT = {42, 53}


class GeminiAdapter(Adapter):
    cli_name = "gemini"

    async def invoke(
        self, prompt, *, model, workdir, timeout,
        role_text=None, role_path=None, execute=False, sandbox=None,
    ) -> AgentResult:
        if not self.installed():
            return self._result(status=Status.NOT_INSTALLED, detail="gemini not on PATH")
        if execute:
            return self._result(
                status=Status.ERROR,
                detail="gemini executor deferred to v2 (no OS fs sandbox); use codex executor",
            )
        argv = [
            self.binary, "-p", prompt,
            "--output-format", "json",
            "-m", model,
            "--approval-mode", "plan",
        ]
        env = {}
        if role_path:
            env["GEMINI_SYSTEM_MD"] = str(Path(role_path).resolve())

        rc, out, err, dur, timed = await self._run(argv, cwd=workdir, timeout=timeout, env=env)
        status, detail = self._classify(rc, out, err, timed)

        # parse JSON answer
        answer = ""
        gerror = None
        if out.strip():
            try:
                data = json.loads(out)
                answer = (data.get("response") or "").strip()
                gerror = data.get("error")
            except json.JSONDecodeError:
                if rc == 0:
                    answer = out.strip()  # text fell through despite --output-format json

        if status == Status.OK and not answer:
            detail = "gemini: empty response"
            if isinstance(gerror, dict):
                detail += f" ({gerror.get('type')}: {str(gerror.get('message', ''))[:120]})"
            # empty/INVALID_STREAM is a transient model hiccup -> retryable
            return self._result(status=Status.EMPTY, raw=out, stderr=err, duration=dur, detail=detail)
        if status != Status.OK:
            return self._result(status=status, raw=out, stderr=err, duration=dur, detail=detail)
        return self._result(status=Status.OK, answer=answer, raw=out, stderr=err, duration=dur)

    def auth_check(self) -> tuple[bool, str]:
        if not self.installed():
            return False, "not installed"
        if os.environ.get("GEMINI_API_KEY"):
            return True, "GEMINI_API_KEY set"
        if (Path.home() / ".gemini" / "oauth_creds.json").exists():
            return True, "oauth_creds.json present"
        return False, "no GEMINI_API_KEY / oauth_creds.json (run: gemini, then /auth)"
