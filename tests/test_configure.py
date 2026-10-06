import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tg_llm_bot.config import load_config


class ConfigureTests(unittest.TestCase):
    def test_noninteractive_setup_reuse_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            args = [sys.executable, "scripts/configure.py", str(path), "--non-interactive"]
            env = {**os.environ, "TELEGRAM_BOT_TOKEN": "fake-telegram", "LLM_API_KEY": "fake-llm"}
            result = subprocess.run(args, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("fake-telegram", result.stdout + result.stderr)
            self.assertNotIn("fake-llm", result.stdout + result.stderr)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            data = json.loads(path.read_text())
            data["requests_per_day"] = 20
            path.write_text(json.dumps(data))
            result = subprocess.run([*args[:-1], "--reuse"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(path.read_text())["requests_per_day"], 20)

    def test_missing_keys_and_paid_model_do_not_create_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            args = [sys.executable, "scripts/configure.py", str(path), "--non-interactive"]
            env = {key: value for key, value in os.environ.items()
                   if key not in {"TELEGRAM_BOT_TOKEN", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL"}}
            result = subprocess.run(args, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(path.exists())
            env.update(TELEGRAM_BOT_TOKEN="fake", LLM_API_KEY="fake", LLM_MODEL="paid/model")
            result = subprocess.run(args, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(path.exists())

    def test_relative_paths_and_environment_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"telegram_token": "fake1", "llm_api_key": "fake2",
                                        "database_path": "data/db.sqlite3", "system_prompt_path": "prompt.txt"}))
            with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "override", "LLM_API_KEY": "override2"}):
                config = load_config(path)
            self.assertEqual(config.telegram_token, "override")
            self.assertEqual(config.database_path, str((Path(directory) / "data/db.sqlite3").resolve()))
            self.assertEqual(config.system_prompt_path, str((Path(directory) / "prompt.txt").resolve()))
