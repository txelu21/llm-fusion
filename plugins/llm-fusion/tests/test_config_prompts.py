"""Roster validation + prompt-template contract tests."""
import asyncio
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from council_runner.adapters import SUPPORTED_CLIS, get_adapter  # noqa: E402
from council_runner.core import AgentSpec, Status  # noqa: E402
from council_runner.orchestrator import CouncilError, load_prompts, load_roster  # noqa: E402


class TestRoster(unittest.TestCase):
    def test_loads_shipped_roster(self):
        roster = load_roster(ROOT / "agents.yaml")
        self.assertEqual(len(roster.advise_agents), 7)        # 7 diverse lenses
        self.assertEqual(len(roster.execute_agents), 4)       # one builder per model
        self.assertEqual({a.cli for a in roster.execute_agents}, {"claude", "codex", "antigravity", "grok"})
        # execute agents all wear the SAME role (the build-off invariant)
        self.assertEqual({a.role for a in roster.execute_agents}, {"roles/builder.md"})
        self.assertEqual(roster.quorum, 2)
        self.assertEqual(roster.judge["backend"], "handoff")

    def test_antigravity_cli_is_supported_provider(self):
        self.assertIn("antigravity", SUPPORTED_CLIS)
        spec = AgentSpec(
            name="antigravity-skeptic",
            cli="antigravity",
            model="gemini-3.1-pro-preview",
            role="roles/skeptic.md",
        )
        adapter = get_adapter(spec)
        self.assertEqual(adapter.cli_name, "antigravity")

    def test_antigravity_provider_uses_agy_binary(self):
        d = Path(tempfile.mkdtemp())
        agy = d / "agy"
        agy.write_text("#!/bin/sh\nexit 0\n")
        agy.chmod(0o755)
        spec = AgentSpec(
            name="antigravity-skeptic",
            cli="antigravity",
            model="gemini-3.1-pro-preview",
            role="roles/skeptic.md",
        )

        adapter = get_adapter(spec, login_path=str(d))

        self.assertTrue(adapter.installed())
        self.assertEqual(Path(adapter.binary).name, "agy")

    def test_antigravity_invoke_uses_agy_print_mode(self):
        d = Path(tempfile.mkdtemp())
        args_file = d / "args.txt"
        agy = d / "agy"
        agy.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{args_file}'\nprintf 'READY\\n'\n")
        agy.chmod(0o755)
        workdir = d / "work"
        workdir.mkdir()
        spec = AgentSpec(
            name="antigravity-skeptic",
            cli="antigravity",
            model="gemini-3.1-pro-preview",
            role="roles/skeptic.md",
        )
        adapter = get_adapter(spec, login_path=str(d))

        result = asyncio.run(adapter.invoke(
            "Answer the brief.",
            model="gemini-3.1-pro-preview",
            workdir=workdir,
            timeout=5,
            role_text="You are the skeptic.",
        ))

        args_text = args_file.read_text()
        args = args_text.splitlines()
        self.assertEqual(result.status, Status.OK)
        self.assertEqual(result.answer, "READY")
        self.assertEqual(args[0], "--print")
        self.assertIn("You are the skeptic.", args_text)
        self.assertIn("Answer the brief.", args_text)
        self.assertIn("--model", args)
        self.assertIn("gemini-3.1-pro-preview", args)
        self.assertIn("--sandbox", args)
        self.assertNotIn("--output-format", args)
        self.assertNotIn("--approval-mode", args)
        self.assertNotIn("-m", args)

    def test_grok_cli_is_supported_provider(self):
        self.assertIn("grok", SUPPORTED_CLIS)
        spec = AgentSpec(name="grok-realist", cli="grok", model="grok-4", role="roles/realist.md")
        self.assertEqual(get_adapter(spec).cli_name, "grok")

    def test_grok_invoke_uses_headless_readonly_mode(self):
        d = Path(tempfile.mkdtemp())
        args_file = d / "args.txt"
        grok = d / "grok"
        # mock grok: capture argv, emit a json answer object on stdout
        grok.write_text(
            f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{args_file}'\n"
            "printf '{\"response\": \"READY\"}\\n'\n"
        )
        grok.chmod(0o755)
        workdir = d / "work"
        workdir.mkdir()
        spec = AgentSpec(name="grok-realist", cli="grok", model="grok-4", role="roles/realist.md")
        adapter = get_adapter(spec, login_path=str(d))

        result = asyncio.run(adapter.invoke(
            "Answer the brief.",
            model="grok-4",
            workdir=workdir,
            timeout=5,
            role_text="You are the realist.",
        ))

        args_text = args_file.read_text()
        args = args_text.splitlines()
        self.assertEqual(result.status, Status.OK)
        self.assertEqual(result.answer, "READY")       # json {"response": ...} parsed
        self.assertEqual(args[0], "-p")                # headless prompt mode
        self.assertIn("You are the realist.", args_text)  # role prepended to prompt
        self.assertIn("Answer the brief.", args_text)
        self.assertIn("--model", args)
        self.assertIn("grok-4", args)
        self.assertIn("--output-format", args)
        self.assertIn("--permission-mode", args)
        self.assertIn("plan", args)                    # read-only mode (no file edits)
        self.assertNotIn("--mode", args)               # the non-existent flag must be gone

    def test_gemini_fallback_is_registered_but_latent(self):
        # registered as a provider...
        self.assertIn("gemini", SUPPORTED_CLIS)
        # ...but NOT wired into the shipped roster (antigravity stays the Google seat).
        roster = load_roster(ROOT / "agents.yaml")
        used = {a.cli for a in roster.advise_agents + roster.execute_agents}
        self.assertNotIn("gemini", used)

    def test_gemini_invoke_uses_headless_plaintext(self):
        d = Path(tempfile.mkdtemp())
        args_file = d / "args.txt"
        gem = d / "gemini"
        gem.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{args_file}'\nprintf 'READY\\n'\n")
        gem.chmod(0o755)
        workdir = d / "work"
        workdir.mkdir()
        spec = AgentSpec(name="gemini-skeptic", cli="gemini", model="gemini-2.5-pro", role="roles/skeptic.md")
        adapter = get_adapter(spec, login_path=str(d))

        result = asyncio.run(adapter.invoke(
            "Answer the brief.", model="gemini-2.5-pro", workdir=workdir,
            timeout=5, role_text="You are the skeptic.",
        ))

        args_text = args_file.read_text()
        args = args_text.splitlines()
        self.assertEqual(result.status, Status.OK)
        self.assertEqual(result.answer, "READY")        # plain-text stdout
        self.assertEqual(args[0], "-p")                 # headless prompt mode
        self.assertIn("You are the skeptic.", args_text)  # role prepended
        self.assertIn("--model", args)
        self.assertIn("gemini-2.5-pro", args)
        self.assertNotIn("-y", args)                    # YOLO must stay OFF (read-only)
        self.assertNotIn("--yolo", args)

    def test_grok_executor_is_refused(self):
        # grok must NEVER be the autonomous executor (codex-only by design).
        spec = AgentSpec(name="grok-builder", cli="grok", model="grok-4", role="roles/builder.md")
        adapter = get_adapter(spec)
        # installed() may be False in CI; force the executor-guard path regardless.
        adapter.binary = "/bin/true"
        result = asyncio.run(adapter.invoke(
            "build it", model="grok-4", workdir=Path(tempfile.mkdtemp()),
            timeout=5, execute=True, sandbox=Path(tempfile.mkdtemp()),
        ))
        self.assertEqual(result.status, Status.ERROR)
        self.assertIn("executor", result.error_detail)

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
            "  - { name: y, cli: antigravity, model: opus, role: roles/b.md }\n"
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
            "  - { name: z, cli: antigravity, model: pro, role: roles/missing.md }\n"
        )
        with self.assertRaises(CouncilError):
            load_roster(d / "agents.yaml")

    def test_execute_rejects_duplicate_models(self):
        y = self._write(
            "advise_agents:\n"
            "  - { name: x, cli: claude, model: opus, role: roles/a.md }\n"
            "  - { name: y, cli: codex, model: gpt-5.5, role: roles/b.md }\n"
            "  - { name: z, cli: antigravity, model: pro, role: roles/c.md }\n"
            "execute_agents:\n"
            "  - { name: e1, cli: claude, model: opus, role: roles/builder.md }\n"
            "  - { name: e2, cli: antigravity, model: pro, role: roles/builder.md }\n"
            "  - { name: e3, cli: antigravity, model: pro, role: roles/builder.md }\n"
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
            "  - { name: z, cli: antigravity, model: pro, role: roles/c.md }\n"
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
