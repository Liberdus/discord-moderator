import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.inspect_hermes_runtime import SOURCE_CONTRACTS, inspect_runtime, source_contracts


class RuntimeInspectionTests(unittest.TestCase):
    def test_source_contracts_detect_missing_methods(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, contracts in SOURCE_CONTRACTS.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                lines = []
                for owner, methods in contracts.items():
                    if owner:
                        lines.append(f"class {owner}:")
                    for method in methods:
                        indent = "    " if owner else ""
                        lines.append(f"{indent}def {method}(): pass")
                path.write_text("\n".join(lines))
            self.assertTrue(all(source_contracts(root).values()))
            (root / "gateway/platforms/base.py").write_text("class BasePlatformAdapter:\n    pass\n")
            self.assertFalse(source_contracts(root)["BasePlatformAdapter.connect"])

    def test_missing_installation_reports_unknown_without_import(self):
        with tempfile.TemporaryDirectory() as directory:
            report = inspect_runtime(Path(directory))
        self.assertIn("No installation virtualenv", report["runtime_probe"])
        self.assertFalse(all(report["source_interfaces"].values()))

    def test_child_receives_empty_profile_and_no_inherited_secrets(self):
        def fake_run(command, **kwargs):
            if command[0] == "git":
                return subprocess.CompletedProcess(command, 128, "", "do-not-print")
            self.assertNotIn("DISCORD_BOT_TOKEN", kwargs["env"])
            self.assertEqual(kwargs["env"]["HOME"], kwargs["env"]["HERMES_HOME"])
            self.assertTrue(Path(kwargs["env"]["HERMES_HOME"]).is_dir())
            self.assertIn("-I", command)
            self.assertIn("-B", command)
            return subprocess.CompletedProcess(command, 0, 'do-not-print\nLIBERDUS_PROBE_RESULT={"interfaces": {}}\n', "do-not-print")
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "fake-secret"}):
                with patch("scripts.inspect_hermes_runtime.subprocess.run", side_effect=fake_run):
                    report = inspect_runtime(Path(directory), "/fake/python")
        self.assertEqual(report["runtime_probe"], {"interfaces": {}})
        self.assertNotIn("do-not-print", json.dumps(report))
        self.assertNotIn("fake-secret", json.dumps(report))

    def test_failed_probe_does_not_print_raw_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("scripts.inspect_hermes_runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "fake-secret", "fake-secret")):
                report = inspect_runtime(Path(directory), "/fake/python")
        self.assertIn("did not finish", report["runtime_probe"])
        self.assertNotIn("fake-secret", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
