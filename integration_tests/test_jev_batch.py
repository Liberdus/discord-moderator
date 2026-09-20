"""Real dotenv parsing and standalone packaging; no external requests."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from liberdus_moderator.jev import ProviderError
from liberdus_moderator.jev_batch import profile_key
from scripts.build_jev_batch import build

ROOT = Path(__file__).resolve().parents[1]


class BatchOwnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profile = self.root / "profiles/liberdus-mod"
        self.profile.mkdir(parents=True)
        self.env = self.profile / ".env"

    def keyfile(self, content):
        self.env.write_text(content)
        self.env.chmod(0o600)

    def test_only_private_profile_key_without_environment_fallback_or_interpolation(self):
        self.keyfile("TYPESAFE_API_KEY='synthetic-profile-key'\nDISCORD_BOT_TOKEN='unused-fixture'\n")
        with patch.dict(os.environ, TYPESAFE_API_KEY="synthetic-shell-key", BATCH_TEST_KEY="synthetic-fallback"):
            self.assertEqual(profile_key(self.profile), "synthetic-profile-key")
            self.keyfile("UNRELATED='keep'\n")
            with self.assertRaises(ProviderError):
                profile_key(self.profile)
            self.keyfile("TYPESAFE_API_KEY='" + "$" + "{BATCH_TEST_KEY}'\n")
            with self.assertRaises(ProviderError):
                profile_key(self.profile)

    def test_shared_or_symlinked_secret_file_refused(self):
        self.keyfile("TYPESAFE_API_KEY='synthetic-profile-key'\n")
        self.env.chmod(0o644)
        with self.assertRaises(ValueError):
            profile_key(self.profile)
        target = self.root / "other.env"
        self.env.rename(target)
        self.env.symlink_to(target)
        with self.assertRaises(ValueError):
            profile_key(self.profile)

    def test_packaged_preview_runs_without_profile_or_network_and_matches_source(self):
        bundle = self.root / "batch.pyz"
        build(ROOT, bundle)
        with zipfile.ZipFile(bundle) as archive:
            for source in (ROOT / "liberdus_moderator").glob("*.py"):
                self.assertEqual(archive.read(source.relative_to(ROOT).as_posix()), source.read_bytes())
            self.assertFalse(any(name.endswith(".env") or name.endswith(".toml") for name in archive.namelist()))
        runner = """import runpy, sys
from unittest.mock import patch
def audit(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
        raise RuntimeError('Network forbidden')
sys.addaudithook(audit)
bundle = sys.argv[1]
sys.argv = [bundle, 'preview']
with patch('pathlib.Path.home', side_effect=RuntimeError('Profile access forbidden')):
    runpy.run_path(bundle, run_name='__main__')
"""
        result = subprocess.run([sys.executable, "-B", "-c", runner, str(bundle)],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        preview = json.loads(result.stdout)
        self.assertEqual(preview["maximum_attempts"], 10)
        self.assertEqual(preview["maximum_reserved_microusd"], 27530)
        self.assertFalse(preview["provider_called"])


if __name__ == "__main__":
    unittest.main()
