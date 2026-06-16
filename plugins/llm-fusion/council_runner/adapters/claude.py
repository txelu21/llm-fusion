"""claude adapter. Round-1 / judge / auditor are read-only, role injected via
--append-system-prompt (ambient CLAUDE.md stays loaded, per the isolation model).
Fresh session every call (--no-session-persistence + fresh --session-id), never
resumed. Executor (acting) is deferred to v2 — claude has no OS-level fs sandbox,
so the autonomous executor is codex-only."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from .base import Adapter
from ..core import AgentResult, Status


class ClaudeAdapter(Adapter):
    cli_name = "claude"

    async def invoke(
        self, prompt, *, model, workdir, timeout,
        role_text=None, role_path=None, execute=False, sandbox=None,
    ) -> AgentResult:
        if not self.installed():
            return self._result(status=Status.NOT_INSTALLED, detail="claude not on PATH")
        if execute:
            return self._result(
                status=Status.ERROR,
                detail="claude executor deferred to v2 (no OS fs sandbox); use codex executor",
            )
        argv = [
            self.binary, "-p", prompt,
            "--output-format", "json",
            "--model", model,
            "--no-session-persistence",
            "--session-id", str(uuid.uuid4()),
            "--tools", "",
            "--strict-mcp-config",
        ]
        if role_text:
            argv += ["--append-system-prompt", role_text]

        rc, out, err, dur, timed = await self._run(argv, cwd=workdir, timeout=timeout)
        status, detail = self._classify(rc, out, err, timed)
        if status != Status.OK:
            return self._result(status=status, raw=out, stderr=err, duration=dur, detail=detail)

        # claude exits 0 even on API error -> must inspect the JSON.
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return self._result(status=Status.ERROR, raw=out, stderr=err, duration=dur,
                                detail="claude: unparseable JSON output")
        if data.get("is_error"):
            # re-classify from the error text
            etext = json.dumps(data)
            status, detail = self._classify(1, etext, err, False)
            if status == Status.OK:
                status = Status.ERROR
            return self._result(status=status, raw=out, stderr=err, duration=dur,
                                detail=detail or data.get("subtype", "claude is_error"))
        answer = (data.get("result") or "").strip()
        if not answer:
            return self._result(status=Status.EMPTY, raw=out, stderr=err, duration=dur,
                                detail="claude: empty result")
        return self._result(status=Status.OK, answer=answer, raw=out, stderr=err, duration=dur)

    def auth_check(self) -> tuple[bool, str]:
        if not self.installed():
            return False, "not installed"
        if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            return True, "CLAUDE_CODE_OAUTH_TOKEN set (setup-token)"
        cred = Path.home() / ".claude" / ".credentials.json"
        if cred.exists():
            return True, "credentials.json present"
        # keychain (subscription OAuth) — presence only, not contents
        if os.system('security find-generic-password -s "Claude Code-credentials" >/dev/null 2>&1') == 0:
            return True, "keychain credential present"
        return False, "no credentials.json / keychain entry (run: claude auth)"
