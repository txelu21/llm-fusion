"""grok adapter (xAI Grok Build CLI, installed via x.ai/cli/install.sh, binary
`grok`). Round-1 / planning are read-only advisory through headless `-p` ask
mode. Role text is prepended because grok's headless mode has no separate
system-prompt flag. Stateless per process. Executor (acting) is NOT supported —
grok has no proven OS-level fs sandbox flag in this runner, so the autonomous
executor stays codex-only (the only CLI with a real seatbelt). Grok's value here
is the realist lens: live market/world reality, timing, and data-grounded risk.

Flags confirmed against `grok --help` (Grok Build TUI, 2026-06-30): `-p/--single`
(headless single-turn → stdout), `--model`, `--output-format {plain,json,
streaming-json}`, and `--permission-mode {default,plan,…}` where `plan` is the
read-only mode (reads/searches but never edits or executes — keeps grok's live
web-search edge, its realist differentiator, while blocking writes). The JSON
parse falls back to raw stdout, so a stray output shape degrades, not corrupts."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .base import Adapter
from ..core import AgentResult, Status


class GrokAdapter(Adapter):
    cli_name = "grok"

    # Centralized so a correction is a one-line edit, not a hunt through invoke().
    PERMISSION_MODE = "plan"       # grok --permission-mode {default,plan,…}; plan = read-only
    OUTPUT_FORMAT = "json"         # grok --output-format {plain,json,streaming-json}

    async def invoke(
        self, prompt, *, model, workdir, timeout,
        role_text=None, role_path=None, execute=False, sandbox=None,
    ) -> AgentResult:
        if not self.installed():
            return self._result(status=Status.NOT_INSTALLED, detail="grok not on PATH")
        if execute:
            return self._result(
                status=Status.ERROR,
                detail="grok executor not supported (no OS fs sandbox); use codex executor",
            )
        full_prompt = f"{role_text}\n\n{prompt}" if role_text else prompt
        argv = [
            self.binary,
            "-p", full_prompt,
            "--model", model,
            "--output-format", self.OUTPUT_FORMAT,
            "--permission-mode", self.PERMISSION_MODE,
        ]

        rc, out, err, dur, timed = await self._run(argv, cwd=workdir, timeout=timeout)
        status, detail = self._classify(rc, out, err, timed)

        answer = _extract_answer(out) if out.strip() else ""

        if status == Status.OK and not answer:
            # ran clean but produced nothing parseable -> transient hiccup, retryable
            return self._result(status=Status.EMPTY, raw=out, stderr=err, duration=dur,
                                detail="grok: empty/unparseable response")
        if status != Status.OK:
            return self._result(status=status, raw=out, stderr=err, duration=dur, detail=detail)
        return self._result(status=Status.OK, answer=answer, raw=out, stderr=err, duration=dur)

    def auth_check(self) -> tuple[bool, str]:
        if not self.installed():
            return False, "not installed"
        # env keys first (headless / enterprise path). GROK_DEPLOYMENT_KEY takes
        # precedence per the official installer; XAI_API_KEY for the public API.
        for var in ("GROK_DEPLOYMENT_KEY", "XAI_API_KEY", "GROK_API_KEY", "GROK_CODE_XAI_API_KEY"):
            if os.environ.get(var):
                return True, f"{var} set"
        # `grok login` (browser OAuth) writes ~/.grok/auth.json (confirmed from the
        # official install.sh). Presence only, never contents.
        gdir = Path.home() / ".grok"
        for fname in ("auth.json", "oauth_creds.json", "credentials.json", "user-settings.json"):
            if (gdir / fname).exists():
                return True, f"~/.grok/{fname} present"
        return False, "no XAI_API_KEY / ~/.grok/auth.json (run: grok login, or export XAI_API_KEY)"


# --------------------------------------------------------------------------- #
# Answer extraction — defensive across grok's json / streaming-json / text out.
# --------------------------------------------------------------------------- #
_ANSWER_KEYS = ("response", "result", "output", "text", "content", "answer", "message", "final")


def _from_obj(obj) -> str:
    """Pull the assistant text from one parsed JSON value (dict/list/str)."""
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, dict):
        # OpenAI-ish shape: {"choices":[{"message":{"content": ...}}]}
        choices = obj.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return msg["content"].strip()
        for k in _ANSWER_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, dict):  # one level of nesting, e.g. {"result":{"text":...}}
                nested = _from_obj(v)
                if nested:
                    return nested
        return ""
    return ""


def _from_events(events: list) -> str:
    """Accumulate text from a streaming-json event list. Prefer a terminal event
    that carries the whole answer; else concatenate incremental text/delta chunks."""
    final = ""
    chunks: list[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type") or ev.get("event") or ""
        # terminal/full-answer events
        if etype in ("step_finish", "message", "result", "final", "completion"):
            full = _from_obj(ev) or (ev.get("delta") if isinstance(ev.get("delta"), str) else "")
            if full:
                final = full
        # incremental text
        if etype in ("text", "delta", "token", "content"):
            for k in ("text", "delta", "content"):
                v = ev.get(k)
                if isinstance(v, str):
                    chunks.append(v)
    return (final or "".join(chunks)).strip()


def _extract_answer(out: str) -> str:
    out = out.strip()
    if not out:
        return ""
    # 1) whole blob is one JSON value
    try:
        obj = json.loads(out)
    except json.JSONDecodeError:
        obj = None
    if obj is not None:
        if isinstance(obj, list):
            ans = _from_events(obj)
            if ans:
                return ans
        ans = _from_obj(obj)
        if ans:
            return ans
    # 2) NDJSON / streaming-json: one JSON object per line
    events = []
    parsed_any = False
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
            parsed_any = True
        except json.JSONDecodeError:
            parsed_any = False
            break
    if parsed_any and events:
        ans = _from_events(events)
        if ans:
            return ans
        # last object might just be the answer dict
        ans = _from_obj(events[-1])
        if ans:
            return ans
    # 3) plain text fallback (e.g. --output-format text, or unrecognized shape)
    return out
