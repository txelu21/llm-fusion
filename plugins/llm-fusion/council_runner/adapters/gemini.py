"""gemini adapter — fallback Google provider using the official @google/gemini-cli
(`gemini`). Drop-in for the antigravity (`agy`) provider when Antigravity isn't
available or the Gemini tier doesn't grant Antigravity quota (the documented
kill-switch fallback). Round-1 / planning are read-only advisory: headless `-p`
mode, role prepended (gemini's print mode has no system-prompt flag), YOLO OFF by
default so it never auto-accepts a tool/file action, plain-text output (gemini-cli
print mode has no --output-format flag). Stateless per process.

LATENT by default — registered in the adapter registry but NOT wired into
agents.yaml; antigravity stays the active Google seat. To activate the fallback,
swap `cli: antigravity` -> `cli: gemini` (and the model id) in agents.yaml.

Executor (acting) is NOT supported — no pinned OS fs sandbox in this runner, so
the autonomous executor stays codex-only (the only CLI with a real seatbelt)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .base import Adapter
from ..core import AgentResult, Status


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
                detail="gemini executor not supported (no pinned OS fs sandbox); use codex executor",
            )
        full_prompt = f"{role_text}\n\n{prompt}" if role_text else prompt
        # -p = headless; -m = model; YOLO stays OFF (default) so it won't auto-run
        # tool/file actions. gemini-cli print mode emits plain text (no JSON flag).
        argv = [self.binary, "-p", full_prompt, "--model", model]

        rc, out, err, dur, timed = await self._run(argv, cwd=workdir, timeout=timeout)
        status, detail = self._classify(rc, out, err, timed)

        answer = _extract_answer(out) if out.strip() else ""

        if status == Status.OK and not answer:
            return self._result(status=Status.EMPTY, raw=out, stderr=err, duration=dur,
                                detail="gemini: empty response")
        if status != Status.OK:
            return self._result(status=status, raw=out, stderr=err, duration=dur, detail=detail)
        return self._result(status=Status.OK, answer=answer, raw=out, stderr=err, duration=dur)

    def auth_check(self) -> tuple[bool, str]:
        if not self.installed():
            return False, "not installed"
        for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY"):
            if os.environ.get(var):
                return True, f"{var} set"
        if (Path.home() / ".gemini" / "oauth_creds.json").exists():
            return True, "~/.gemini/oauth_creds.json present"
        return False, "no GEMINI_API_KEY / ~/.gemini creds (run: gemini, then /auth)"


def _extract_answer(out: str) -> str:
    """gemini-cli 0.1.x prints plain text; tolerate a future JSON {"response": ...}."""
    out = out.strip()
    if not out:
        return ""
    if out[0] in "{[":
        try:
            obj = json.loads(out)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            for k in ("response", "text", "result", "output", "content"):
                v = obj.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return out
