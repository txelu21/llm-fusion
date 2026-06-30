"""Core plumbing for the sealed council runner: data models, a zero-dependency
YAML-subset loader, {{VAR}} rendering, run-folder creation, and anonymization.

Kept deliberately small and dependency-free (minimal-code rule: nothing speculative).
PyYAML is used if importable; otherwise a minimal subset loader handles the
shipped agents.yaml shape (top-level maps + a list of inline flow mappings).
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

try:  # optional; we degrade to the subset loader if absent
    import yaml as _pyyaml  # type: ignore
    _HAS_YAML = True
except Exception:  # pragma: no cover - environment dependent
    _HAS_YAML = False


# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #
class Status(str, Enum):
    OK = "ok"
    NOT_INSTALLED = "not_installed"
    NOT_AUTHENTICATED = "not_authenticated"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    EMPTY = "empty"            # ran fine but returned no answer (stochastic hiccup)
    ERROR = "error"

    @property
    def is_retryable(self) -> bool:
        # Never auto-retry a timeout (it already ate the whole budget) or a
        # terminal error. Retry rate limits and empty/malformed-stream responses
        # (e.g. antigravity/gemini INVALID_STREAM — usually transient).
        return self in (Status.RATE_LIMITED, Status.EMPTY)


@dataclass
class AgentResult:
    name: str
    cli: str
    model: str
    status: Status = Status.ERROR
    answer: str = ""          # clean extracted answer text
    raw: str = ""             # raw stdout (json or text)
    stderr: str = ""
    duration_s: float = 0.0
    attempts: int = 1
    error_detail: str = ""    # human-readable reason on failure

    @property
    def ok(self) -> bool:
        return self.status == Status.OK and bool(self.answer.strip())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class AgentSpec:
    name: str
    cli: str
    model: str
    role: str            # path (relative to project root) to the role .md
    path: str | None = None   # explicit binary path override (PATH resolution)


@dataclass
class RosterConfig:
    defaults: dict
    judge: dict
    executor: dict
    auditor: dict
    advise_agents: list[AgentSpec]    # diverse lenses (advise mode)
    execute_agents: list[AgentSpec]   # same builder role, one per model (execute)

    def agents_for(self, mode: str) -> list[AgentSpec]:
        return self.execute_agents if mode == "execute" else self.advise_agents

    # ---- typed convenience accessors with sane fallbacks ----
    def d(self, key: str, default: Any) -> Any:
        return self.defaults.get(key, default)

    @property
    def round1_timeout(self) -> int:
        return int(self.d("round1_timeout_sec", 240))

    @property
    def judge_timeout(self) -> int:
        return int(self.d("judge_timeout_sec", 300))

    @property
    def executor_timeout(self) -> int:
        return int(self.d("executor_timeout_sec", 600))

    @property
    def retries(self) -> int:
        return int(self.d("retries", 1))

    @property
    def quorum(self) -> int:
        return int(self.d("quorum", 2))


# --------------------------------------------------------------------------- #
# YAML subset loader (zero-dep fallback)
# --------------------------------------------------------------------------- #
def _strip_comment(line: str) -> str:
    """Drop a trailing/leading comment. Our config has no '#' inside values."""
    s = line
    if s.lstrip().startswith("#"):
        return ""
    # cut at first ' #' (space-hash) not inside braces/quotes — config is flat
    in_qs = in_qd = False
    depth = 0
    for i, ch in enumerate(s):
        if ch == "'" and not in_qd:
            in_qs = not in_qs
        elif ch == '"' and not in_qs:
            in_qd = not in_qd
        elif ch == "{" and not (in_qs or in_qd):
            depth += 1
        elif ch == "}" and not (in_qs or in_qd):
            depth -= 1
        elif ch == "#" and not (in_qs or in_qd) and depth == 0 and i > 0 and s[i - 1] == " ":
            return s[:i].rstrip()
    return s


def _parse_scalar(s: str) -> Any:
    s = s.strip()
    if s == "" or s.lower() in ("null", "~", "none"):
        return None
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _split_top_commas(inner: str) -> list[str]:
    out, buf = [], []
    in_qs = in_qd = False
    for ch in inner:
        if ch == "'" and not in_qd:
            in_qs = not in_qs
        elif ch == '"' and not in_qs:
            in_qd = not in_qd
        if ch == "," and not (in_qs or in_qd):
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _parse_flow_mapping(s: str) -> dict:
    s = s.strip()
    if not (s.startswith("{") and s.endswith("}")):
        raise ValueError(f"expected inline mapping, got: {s!r}")
    inner = s[1:-1].strip()
    out: dict = {}
    if not inner:
        return out
    for part in _split_top_commas(inner):
        key, sep, val = part.partition(":")
        if not sep:
            raise ValueError(f"bad inline mapping entry: {part!r}")
        out[key.strip()] = _parse_scalar(val)
    return out


def _mini_yaml(text: str) -> dict:
    """Parse the documented agents.yaml subset: top-level keys whose values are
    either a scalar, a nested map of scalars, or a list of inline flow mappings.
    Two levels deep only — deeper structures require PyYAML."""
    root: dict = {}
    top: str | None = None
    for raw in text.splitlines():
        line = _strip_comment(raw.rstrip())
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if indent == 0:
            key, sep, rest = content.partition(":")
            if not sep:
                raise ValueError(f"bad top-level line: {content!r}")
            key, rest = key.strip(), rest.strip()
            if rest == "":
                root[key] = None  # block (map or list) follows
                top = key
            else:
                root[key] = _parse_scalar(rest)
                top = None
        else:
            if top is None:
                raise ValueError(f"indented line with no parent: {content!r}")
            if content.startswith("- "):
                item = content[2:].strip()
                if not isinstance(root.get(top), list):
                    root[top] = []
                root[top].append(
                    _parse_flow_mapping(item) if item.startswith("{") else _parse_scalar(item)
                )
            else:
                key, sep, rest = content.partition(":")
                if not sep:
                    raise ValueError(f"bad nested line: {content!r}")
                if not isinstance(root.get(top), dict):
                    root[top] = {}
                root[top][key.strip()] = _parse_scalar(rest)
    return root


def load_yaml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if _HAS_YAML:
        return _pyyaml.safe_load(text) or {}
    return _mini_yaml(text)


# --------------------------------------------------------------------------- #
# Template rendering ({{VAR}})
# --------------------------------------------------------------------------- #
def render(template: str, **vars: str) -> str:
    out = template
    for key, val in vars.items():
        out = out.replace("{{" + key + "}}", val)
    return out


# --------------------------------------------------------------------------- #
# Run folder
# --------------------------------------------------------------------------- #
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str, limit: int = 40) -> str:
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    return (s[:limit].rstrip("-")) or "run"


@dataclass
class RunPaths:
    root: Path

    @property
    def private(self) -> Path:
        return self.root / "private"

    @property
    def public(self) -> Path:
        return self.root / "public"

    @property
    def answers(self) -> Path:
        return self.public / "answers"

    @property
    def judge(self) -> Path:
        return self.root / "judge"

    @property
    def execute(self) -> Path:
        return self.root / "execute"

    @property
    def sandbox(self) -> Path:
        return self.execute / "sandbox"

    @property
    def audit(self) -> Path:
        return self.root / "audit"

    @property
    def mapping_file(self) -> Path:
        # run root, NOT inside public/answers — the judge is only ever pointed
        # at public/answers/, so letters->names stays out of its view.
        return self.root / "mapping.json"

    @property
    def meta_file(self) -> Path:
        return self.root / "meta.json"

    @property
    def final_report(self) -> Path:
        return self.public / "final_report.md"

    def agent_dir(self, name: str) -> Path:
        return self.private / name


def create_run_folder(base: Path, mode: str, brief: str) -> RunPaths:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = base / f"{stamp}-{mode}-{_slug(brief)}"
    paths = RunPaths(run_dir)
    paths.answers.mkdir(parents=True, exist_ok=True)
    paths.private.mkdir(parents=True, exist_ok=True)
    (run_dir / "original_prompt.md").write_text(brief.strip() + "\n", encoding="utf-8")
    (run_dir / "mode.txt").write_text(mode + "\n", encoding="utf-8")
    return paths


# --------------------------------------------------------------------------- #
# Anonymization
# --------------------------------------------------------------------------- #
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# self-reference patterns stripped as a *backup* defense (the primary defense is
# the round-1 "do not identify yourself" instruction). Every hit is logged.
_SELF_REF_PATTERNS = [
    r"\bas (an? )?(Claude|GPT|ChatGPT|OpenAI|Gemini|Google|Anthropic|Codex|Antigravity|Grok|xAI)\b[^.,;:\n]*",
    r"\bI(?:'m| am) (Claude|GPT|ChatGPT|Gemini|Codex|Antigravity|Grok|an? \w+ model)\b",
    r"\b(Claude|GPT-?\d?\.?\d?|Gemini|Codex|Antigravity|Grok|xAI)\b",
    r"\bas the (architect|pragmatist|skeptic|operator|user[- ]advocate|first[- ]principles|realist)\b",
]


def strip_self_refs(text: str) -> tuple[str, list[str]]:
    hits: list[str] = []
    out = text
    for pat in _SELF_REF_PATTERNS:
        for m in re.finditer(pat, out, flags=re.IGNORECASE):
            hits.append(m.group(0))
        out = re.sub(pat, "[redacted]", out, flags=re.IGNORECASE)
    return out, hits


def anonymize(ok_results: list[AgentResult], seed: int) -> tuple[dict[str, str], dict[str, dict]]:
    """Shuffle ok answers to letters; return (answers_by_letter, mapping_by_letter).
    Self-references are stripped from the *answers* (judge-facing) but the mapping
    keeps the true identity for the final report."""
    order = list(range(len(ok_results)))
    random.Random(seed).shuffle(order)
    answers: dict[str, str] = {}
    mapping: dict[str, dict] = {}
    for letter_idx, res_idx in enumerate(order):
        letter = _LETTERS[letter_idx]
        res = ok_results[res_idx]
        cleaned, _ = strip_self_refs(res.answer)
        answers[letter] = cleaned
        mapping[letter] = {"name": res.name, "cli": res.cli, "model": res.model}
    return answers, mapping
