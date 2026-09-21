import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_apply', ROOT/'scripts/apply_public_rollout.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class PublicDeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.updater=Path(self.tmp.name)/'update.pyz'; self.updater.write_bytes(b'reviewed update')
        self.rollout=Path(self.tmp.name)/'rollout.pyz'; self.rollout.write_bytes(b'reviewed scope')
        self.calls=[]; self.failure=None

    def _run(self,args):
        self.calls.append(args)
        if self.failure=='verify' and args[-1]=='verify':
            raise RuntimeError('scope changed')
        if args[-1]=='verify': return json.dumps(dict(verified=True,actions_enabled=False))
        if args[-1]=='apply':
            if self.failure=='apply': raise RuntimeError('scope changed during restart')
            return json.dumps(dict(configured=True,actions_enabled=False,action_flags_reset=True,platform_enabled=False))
        if args[-1]=='restart':
            if self.failure=='restart': return 'gateway is DEGRADED'
            return '✓ User service restarted (PID 123)\n'
        if len(args)==2 and args[1].endswith('update.pyz'):
            if self.failure=='update': return json.dumps(dict(updated=False))
            return json.dumps(dict(updated=True,version='0.5.6'))
        return ''

    def invoke(self, bad_hash=False):
        with patch.object(helper,'UPDATER',self.updater),patch.object(helper,'ROLLOUT',self.rollout),\
             patch.object(helper,'UPDATER_SHA256','bad' if bad_hash else hashlib.sha256(self.updater.read_bytes()).hexdigest()),\
             patch.object(helper,'ROLLOUT_SHA256',hashlib.sha256(self.rollout.read_bytes()).hexdigest()),\
             patch.object(helper.pwd,'getpwuid',return_value=SimpleNamespace(pw_name='hermes')),\
             patch.object(helper.shutil,'which',return_value='/bin/hermes'),\
             patch.object(helper.sys,'argv',['helper']),patch.object(helper,'run',side_effect=self._run),\
             patch('sys.stdout',new_callable=io.StringIO):
            return helper.main()

    def test_success_verifies_scope_before_stop_and_applies_before_enable(self):
        self.assertEqual(self.invoke(),0)
        self.assertEqual(self.calls[0][-1],'verify')
        tail=[args[-1] for args in self.calls]
        self.assertEqual(tail[1:3],['false','restart'])
        self.assertEqual(tail[4:],['apply','true','restart'])
        self.assertEqual(len(self.calls),7)

    def test_hash_or_plan_failure_stops_before_disabling(self):
        self.assertEqual(self.invoke(bad_hash=True),2); self.assertEqual(self.calls,[])
        self.failure='verify'
        self.assertEqual(self.invoke(),2); self.assertEqual(len(self.calls),1)

    def test_first_restart_failure_never_updates_or_enables(self):
        self.failure='restart'; self.assertEqual(self.invoke(),2)
        self.assertEqual([args[-1] for args in self.calls],['verify','false','restart'])

    def test_update_failure_never_applies_or_enables(self):
        self.failure='update'; self.assertEqual(self.invoke(),2)
        self.assertNotIn('apply',[args[-1] for args in self.calls])
        self.assertNotIn('true',[args[-1] for args in self.calls])

    def test_scope_failure_after_update_never_enables(self):
        self.failure='apply'; self.assertEqual(self.invoke(),2)
        self.assertEqual(self.calls[-1][-1],'apply')
        self.assertNotIn('true',[args[-1] for args in self.calls])


if __name__=='__main__': unittest.main()
