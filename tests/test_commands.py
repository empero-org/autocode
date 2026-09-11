"""Unit tests for the REPL's slash commands: /help, /config, /model and /reset."""
import builtins
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runner import RUNNER, FakeServer  # noqa: E402


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        shutil.copy(RUNNER, self.dir / "runner.py")
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("AUTOCODE_", "OPENAI_"))}
        patch = mock.patch.dict(os.environ, clean, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        spec = importlib.util.spec_from_file_location("runner_under_test", self.dir / "runner.py")
        self.r = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.r)
        self.r.GLOBAL_CONFIG = self.dir / "global.json"  # never touch the real ~/.config
        self.err = io.StringIO()
        redirect = contextlib.redirect_stderr(self.err)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        self.addCleanup(shutil.rmtree, self.dir)

    def test_settings_come_from_the_right_layer(self):
        self.r.GLOBAL_CONFIG.write_text(json.dumps({"model": "global-model", "base_url": "http://g/v1"}))
        self.r.save_setting(self.r.PROJECT_CONFIG, "model", "project-model")
        os.environ["AUTOCODE_TEMPERATURE"] = "0.5"
        cfg = self.r.load_config()
        self.assertEqual((cfg["model"], cfg["base_url"], cfg["temperature"]), ("project-model", "http://g/v1", 0.5))
        self.assertEqual(self.r.effective("model"), (".autocode/config.json", "project-model"))
        self.assertEqual(self.r.effective("temperature")[0], "AUTOCODE_* env")
        self.assertEqual(self.r.effective("timeout"), ("default", 600))
        self.r.configure(cfg, "")
        self.assertIn("project-model", self.err.getvalue())

    def test_config_saves_masks_and_unsets(self):
        cfg = self.r.load_config()
        self.r.configure(cfg, "temperature 0.2")
        self.assertEqual(json.loads(self.r.PROJECT_CONFIG.read_text())["temperature"], 0.2)
        self.assertEqual(cfg["temperature"], 0.2)
        self.r.configure(cfg, "api_key sk-abcdefghijklmnop")
        self.r.configure(cfg, "api_key")
        self.assertIn("sk-…mnop", self.err.getvalue())
        self.assertNotIn("sk-abcdefghijklmnop", self.err.getvalue())
        self.assertEqual(self.r.PROJECT_CONFIG.stat().st_mode & 0o777, 0o600)
        self.r.configure(cfg, "unset temperature")
        self.assertIsNone(cfg["temperature"])
        self.r.configure(cfg, "tempurature 1")
        self.r.configure(cfg, "timeout soon")
        self.assertIn("unknown setting 'tempurature'", self.err.getvalue())
        self.assertIn("takes a JSON value", self.err.getvalue())

    def test_model_lists_filters_and_switches(self):
        server = FakeServer([], models=["coder-small", "coder-large", "chat-mini"])
        self.addCleanup(server.close)
        self.r.save_setting(self.r.PROJECT_CONFIG, "base_url", server.url)
        cfg = self.r.load_config()
        self.r.switch_model(cfg, "")
        self.assertEqual(self.r.listed, ["coder-small", "coder-large", "chat-mini"])
        with mock.patch.object(builtins, "input", return_value=""):  # the default: this session only
            self.r.switch_model(cfg, "2")
        self.assertEqual(cfg["model"], "coder-large")
        self.assertEqual(self.r.OVERRIDES["model"], "coder-large")
        self.assertNotIn("model", json.loads(self.r.PROJECT_CONFIG.read_text()))
        self.r.switch_model(cfg, "coder")  # ambiguous: shows the matches instead
        self.assertEqual(self.r.listed, ["coder-small", "coder-large"])
        with mock.patch.object(builtins, "input", return_value="p"):
            self.r.switch_model(cfg, "mini")  # a unique match, kept for the project
        self.assertEqual(cfg["model"], "chat-mini")
        self.assertEqual(json.loads(self.r.PROJECT_CONFIG.read_text())["model"], "chat-mini")
        self.assertNotIn("model", self.r.OVERRIDES)

    def test_reset_updates_runner_from_the_package(self):
        runner = self.dir / "runner.py"
        runner.write_text(runner.read_text() + "# a local edit\n")
        reloads = []
        agent = types.SimpleNamespace(reload=lambda: reloads.append(True))
        self.r.update_runner(agent)
        self.assertEqual(runner.read_text(), RUNNER.read_text())
        self.assertTrue((self.dir / ".autocode" / "runner.prev.py").read_text().endswith("# a local edit\n"))
        self.assertEqual(reloads, [True])
        self.r.update_runner(agent)  # nothing newer: no reload
        self.assertEqual(reloads, [True])
        self.assertIn("already the newest", self.err.getvalue())

    def test_help_and_non_commands(self):
        agent = object()
        self.assertIs(self.r.slash(agent, "/help"), agent)
        self.assertIn("/model", self.err.getvalue())
        self.assertIsNone(self.r.slash(agent, "/etc/hosts looks wrong"))  # goes to the model, not a command


if __name__ == "__main__":
    unittest.main()
