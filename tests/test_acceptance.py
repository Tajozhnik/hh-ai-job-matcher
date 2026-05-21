import importlib
import subprocess
import sys
import unittest
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AcceptanceTests(unittest.TestCase):
    def test_modules_import(self):
        for module_name in ("scraper", "analyzer", "database", "main"):
            with self.subTest(module_name=module_name):
                importlib.import_module(module_name)

    def test_main_help_exposes_only_flag(self):
        result = subprocess.run(
            [sys.executable, "main.py", "--help"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--only", result.stdout)
        self.assertIn("{scrape,analyze,export,report}", result.stdout)

    def test_example_config_has_required_sections(self):
        config_path = PROJECT_ROOT / "config.example.yaml"
        self.assertTrue(config_path.exists(), "config.example.yaml must exist")

        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        for section in ("deepseek", "search", "proxy", "profile", "analysis"):
            with self.subTest(section=section):
                self.assertIn(section, config, f"Missing section: {section}")

        self.assertIn("api_key", config["deepseek"])
        self.assertIn("url", config["search"])
        self.assertIn("skills", config["profile"])
        self.assertIn("pet_projects", config["profile"])
        self.assertIn("output", config["analysis"])

    def test_sensitive_files_are_ignored(self):
        gitignore_path = PROJECT_ROOT / ".gitignore"
        self.assertTrue(gitignore_path.exists(), ".gitignore must exist")

        ignored = gitignore_path.read_text(encoding="utf-8").splitlines()
        for entry in ("logs/", "results/", ".env", "config.yaml", ".venv/"):
            with self.subTest(entry=entry):
                self.assertIn(entry, ignored, f"{entry} must be in .gitignore")


if __name__ == "__main__":
    unittest.main()
