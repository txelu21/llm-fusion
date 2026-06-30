"""Core plumbing tests — run with: python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from council_runner.core import (  # noqa: E402
    AgentResult, Status, _mini_yaml, anonymize, create_run_folder,
    load_yaml, render, strip_self_refs,
)


class TestMiniYaml(unittest.TestCase):
    def test_parses_shipped_agents_yaml(self):
        data = _mini_yaml((ROOT / "agents.yaml").read_text())
        self.assertEqual(data["defaults"]["retries"], 1)
        self.assertEqual(data["defaults"]["quorum"], 2)
        self.assertEqual(data["judge"]["backend"], "handoff")
        self.assertEqual(data["executor"]["cli"], "codex")
        self.assertEqual(len(data["advise_agents"]), 7)
        self.assertEqual(len(data["execute_agents"]), 4)
        names = {a["name"] for a in data["advise_agents"]}
        self.assertIn("claude-architect", names)
        self.assertIn("grok-realist", names)  # fork: 4th provider (xAI)
        # execute agents share the builder role
        self.assertEqual({a["role"] for a in data["execute_agents"]}, {"roles/builder.md"})
        # types preserved
        self.assertIsInstance(data["defaults"]["round1_timeout_sec"], int)

    def test_matches_pyyaml_if_available(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        text = (ROOT / "agents.yaml").read_text()
        self.assertEqual(_mini_yaml(text), yaml.safe_load(text))

    def test_scalars(self):
        d = _mini_yaml("a:\n  i: 5\n  f: 1.5\n  t: true\n  n: null\n  s: hello\n")
        self.assertEqual(d["a"], {"i": 5, "f": 1.5, "t": True, "n": None, "s": "hello"})


class TestRender(unittest.TestCase):
    def test_substitution(self):
        self.assertEqual(render("{{A}}-{{B}}", A="x", B="y"), "x-y")
        self.assertEqual(render("no vars", A="x"), "no vars")


class TestAnonymize(unittest.TestCase):
    def _mk(self, name, cli, model, answer):
        return AgentResult(name=name, cli=cli, model=model, status=Status.OK, answer=answer)

    def test_shuffle_is_seed_deterministic_and_lossless(self):
        results = [
            self._mk("claude-architect", "claude", "opus", "Claude here, I think X."),
            self._mk("codex-pragmatist", "codex", "gpt-5.5", "Ship the small thing."),
            self._mk("antigravity-skeptic", "antigravity", "gemini-3.1-pro-preview", "As Gemini, this fails."),
        ]
        a1, m1 = anonymize(results, seed=42)
        a2, m2 = anonymize(results, seed=42)
        self.assertEqual(m1, m2)  # deterministic
        self.assertEqual(set(a1), {"A", "B", "C"})
        # every original agent appears exactly once in the mapping
        mapped = {v["name"] for v in m1.values()}
        self.assertEqual(mapped, {"claude-architect", "codex-pragmatist", "antigravity-skeptic"})

    def test_self_refs_stripped_from_answers(self):
        results = [self._mk("antigravity-skeptic", "antigravity", "gemini-3.1-pro-preview", "As Gemini, I doubt it.")]
        answers, _ = anonymize(results, seed=1)
        text = answers["A"]
        self.assertNotIn("Gemini", text)

    def test_antigravity_self_refs_stripped_from_answers(self):
        cleaned, hits = strip_self_refs("As Antigravity, I would use Gemini.")
        self.assertTrue(hits)
        self.assertNotIn("Antigravity", cleaned)

    def test_strip_self_refs_reports_hits(self):
        cleaned, hits = strip_self_refs("As Claude, I am Claude and I run GPT-4.")
        self.assertTrue(hits)
        self.assertNotIn("Claude", cleaned)

    def test_grok_self_refs_stripped_from_answers(self):
        cleaned, hits = strip_self_refs("As Grok from xAI, I'd ship it.")
        self.assertTrue(hits)
        self.assertNotIn("Grok", cleaned)
        self.assertNotIn("xAI", cleaned)


class TestRunFolder(unittest.TestCase):
    def test_creates_layout(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            paths = create_run_folder(Path(tmp), "advise", "Should I do X?")
            self.assertTrue(paths.answers.is_dir())
            self.assertTrue(paths.private.is_dir())
            self.assertTrue((paths.root / "original_prompt.md").exists())
            self.assertEqual((paths.root / "mode.txt").read_text().strip(), "advise")
            self.assertIn("advise", paths.root.name)


class TestStatus(unittest.TestCase):
    def test_retryable_statuses(self):
        for s in (Status.RATE_LIMITED, Status.EMPTY):
            self.assertTrue(s.is_retryable, f"{s} should retry")
        for s in (Status.TIMEOUT, Status.ERROR, Status.NOT_AUTHENTICATED, Status.NOT_INSTALLED):
            self.assertFalse(s.is_retryable, f"{s} should not retry")


if __name__ == "__main__":
    unittest.main()
