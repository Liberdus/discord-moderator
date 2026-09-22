import contextlib
import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import apply_review_layout as helper


class DeploymentTests(unittest.TestCase):
    def invoke(self, *, failure=None, wrong_hash=False, user='hermes'):
        calls=[]
        with tempfile.TemporaryDirectory() as temp:
            updater=Path(temp)/'update.pyz'; updater.write_bytes(b'synthetic updater')
            def run(argv):
                calls.append(argv)
                if len(calls)==failure: raise RuntimeError('synthetic failure')
                if argv[-1]=='restart': return '✓ User service restarted (PID 123)\n'
                if argv[-1].endswith('update.pyz'): return '{"updated":true,"version":"0.5.10"}'
                return ''
            with patch.object(helper,'UPDATER',updater), \
                 patch.object(helper,'UPDATER_SHA256','bad' if wrong_hash else hashlib.sha256(updater.read_bytes()).hexdigest()), \
                 patch.object(helper,'run',side_effect=run), patch.object(helper.pwd,'getpwuid',return_value=SimpleNamespace(pw_name=user)), \
                 patch.object(helper.shutil,'which',return_value='/synthetic/hermes'), \
                 patch.object(helper.sys,'argv',['helper.py']),patch.dict(helper.os.environ,{}), \
                 contextlib.redirect_stdout(io.StringIO()):
                result=helper.main()
        return result,calls

    def test_success_code_only_update_preserves_configuration(self):
        result,calls=self.invoke()
        self.assertEqual(result,0)
        self.assertEqual(len(calls),5)
        self.assertEqual(calls[0][-1],'false')
        self.assertTrue(calls[2][-1].endswith('update.pyz'))
        self.assertEqual(calls[3][-1],'true')
        self.assertEqual(calls[4][-1],'restart')
        self.assertFalse(any(call[-1] in ('actions','exempt-role') for call in calls))

    def test_any_failure_stops_later_steps(self):
        for stage in range(1,6):
            result,calls=self.invoke(failure=stage)
            self.assertEqual(result,2)
            self.assertEqual(len(calls),stage)
            if stage<=3: self.assertFalse(any(call[-1]=='true' for call in calls))

    def test_wrong_user_or_hash_never_changes_configuration(self):
        for args in ({'wrong_hash':True},{'user':'developer'}):
            result,calls=self.invoke(**args)
            self.assertEqual(result,2)
            self.assertFalse(calls)

    def test_zero_exit_without_ready_or_degraded_restart_is_failure(self):
        for output in ('Restart requested', '✓ User service restarted (PID 123)\ngateway is DEGRADED'):
            with patch.object(helper,'run',return_value=output), self.assertRaises(RuntimeError):
                helper.restart('/synthetic/hermes')
