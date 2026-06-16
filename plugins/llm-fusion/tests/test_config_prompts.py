"""Roster validation + prompt-template contract tests."""
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from council_runner.orchestrator import CouncilError, load_prompts, load_roster  # noqa: E402


class TestRoster(unittest.TestCase):
    def test_loads_shipped_roster(self):
        roster = load_roster(ROOT / "agents.yaml")
        self.assertEqual(len(roster.advise_agents), 6)        # 6 diverse lenses
        self.assertEqual(len(roster.execute_agents), 3)       # one builder per model
        self.assertEqual({a.cli for a in roster.execute_agents}, {"claude", "codex", "gemini"})
        # execute agents all wear the SAME role (the build-off invariant)
        self.assertEqual({a.role for a in roster.execute_agents}, {"roles/builder.md"})
        self.assertEqual(roster.quorum, 2)
        self.assertEqual(roster.judge["backend"], "handoff")

    def _write(self, body: str) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / "roles").mkdir()
        for r in ("a", "b", "c", "builder"):
            (d / "roles" / f"{r}.md").write_text("role")
        (d / "agents.yaml").write_text(body)
        return d / "agents.yaml"

    def test_advise_rejects_fewer_than_3_models(self):
        y = self._write(
            "advise_agents:\n"
            "  - { name: x, cli: claude, model: opus, role: roles/a.md }\n"
            "  - { name: y, cli: gemini, model: opus, role: roles/b.md }\n"
        )
        with self.assertRaises(CouncilError) as ctx:
            load_roster(y)
        self.assertIn(">=3 distinct models", str(ctx.exception))

    def test_rejects_unknown_cli(self):
        y = self._write(
            "advise_agents:\n"
            "  - { name: x, cli: claude, model: opus, role: roles/a.md }\n"
            "  - { name: y, cli: codex, model: gpt-5.5, role: roles/b.md }\n"
            "  - { name: z, cli: llama, model: big, role: roles/c.md }\n"
        )
        with self.assertRaises(CouncilError):
            load_roster(y)

    def test_rejects_missing_role_file(self):
        d = Path(tempfile.mkdtemp())
        (d / "agents.yaml").write_text(
            "advise_agents:\n"
            "  - { name: x, cli: claude, model: opus, role: roles/missing.md }\n"
            "  - { name: y, cli: codex, model: gpt-5.5, role: roles/missing.md }\n"
            "  - { name: z, cli: gemini, model: pro, role: roles/missing.md }\n"
        )
        with self.assertRaises(CouncilError):
            load_roster(d / "agents.yaml")

    def test_execute_rejects_duplicate_models(self):
        y = self._write(
            "advise_agents:\n"
            "  - { name: x, cli: claude, model: opus, role: roles/a.md }\n"
            "  - { name: y, cli: codex, model: gpt-5.5, role: roles/b.md }\n"
            "  - { name: z, cli: gemini, model: pro, role: roles/c.md }\n"
            "execute_agents:\n"
            "  - { name: e1, cli: claude, model: opus, role: roles/builder.md }\n"
            "  - { name: e2, cli: gemini, model: pro, role: roles/builder.md }\n"
            "  - { name: e3, cli: gemini, model: pro, role: roles/builder.md }\n"
        )
        with self.assertRaises(CouncilError) as ctx:
            load_roster(y)
        self.assertIn("distinct models", str(ctx.exception))

    def test_execute_derived_when_absent(self):
        # advise-only config: execute roster auto-derives one builder per model
        y = self._write(
            "advise_agents:\n"
            "  - { name: x, cli: claude, model: opus, role: roles/a.md }\n"
            "  - { name: y, cli: codex, model: gpt-5.5, role: roles/b.md }\n"
            "  - { name: z, cli: gemini, model: pro, role: roles/c.md }\n"
        )
        roster = load_roster(y)
        self.assertEqual(len(roster.execute_agents), 3)
        self.assertEqual({a.role for a in roster.execute_agents}, {"roles/builder.md"})


class TestPrompts(unittest.TestCase):
    def test_loads_all_templates(self):
        p = load_prompts(ROOT)
        self.assertEqual(
            set(p),
            {"round1_advise", "round1_execute", "judge_advise",
             "judge_execute_spec", "executor", "auditor"},
        )

    def test_round1_has_anonymity_line(self):
        p = load_prompts(ROOT)
        for key in ("round1_advise", "round1_execute"):
            self.assertIn("Do not identify yourself, your model", p[key],
                          f"{key} missing the mandatory anonymity instruction")

    def test_templates_have_expected_vars(self):
        p = load_prompts(ROOT)
        self.assertIn("{{BRIEF}}", p["round1_advise"])
        self.assertIn("{{ANSWERS}}", p["judge_advise"])
        self.assertIn("{{ANSWERS}}", p["judge_execute_spec"])
        self.assertIn("{{SPEC}}", p["executor"])
        self.assertIn("{{DELIVERABLE}}", p["auditor"])

    def test_round1_templates_do_not_inline_roles(self):
        p = load_prompts(ROOT)
        for key in ("round1_advise", "round1_execute"):
            self.assertNotIn(
                "{{ROLE}}", p[key],
                f"{key} should rely on adapter role injection, not duplicate the role in the user prompt",
            )


class TestPackaging(unittest.TestCase):
    def test_pyproject_does_not_advertise_incomplete_console_script(self):
        with (ROOT / "pyproject.toml").open("rb") as f:
            data = tomllib.load(f)
        self.assertNotIn("scripts", data.get("project", {}))


if __name__ == "__main__":
    unittest.main()
