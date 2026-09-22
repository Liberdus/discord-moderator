import copy
from dataclasses import replace
import fcntl
from pathlib import Path
import unittest
from unittest.mock import patch

import test_public_rollout as rollout_tests
from liberdus_moderator.config import Config
from liberdus_moderator.configure_jev import policy_text
from liberdus_moderator.public_deletion import APPROVED_CHANNELS, verify_deletion
from liberdus_moderator.public_rollout import (BOT, COMMAND, COMMITTERS, INCLUDED_CATEGORIES,
    MANAGE_MESSAGES, RolloutError, apply_plan)


class PublicDeletionTests(unittest.TestCase):
    channel = rollout_tests.PublicRolloutTests.channel
    get = rollout_tests.PublicRolloutTests.get
    settings = rollout_tests.PublicRolloutTests.settings

    def setUp(self):
        rollout_tests.PublicRolloutTests.setUp(self)
        self.config = replace(self.config, actions_enabled=False, allow_public_monitored_channels=True,
            monitored_channel_ids=tuple(sorted(APPROVED_CHANNELS)), included_category_ids=INCLUDED_CATEGORIES,
            excluded_category_ids=(COMMITTERS,))
        self.policy.write_text(policy_text(self.config))
        self.guild['roles'][1]['permissions'] = str(MANAGE_MESSAGES)
        self.channels += [self.channel(identity, 'approved', parent=INCLUDED_CATEGORIES[0])
                          for identity in sorted(APPROVED_CHANNELS)]

    def test_verify_is_metadata_only_exact_scope_and_no_state_changes(self):
        before, settings = self.policy.read_bytes(), self.settings()
        config, updated, chosen = verify_deletion(self.profile, self.get)
        self.assertEqual(config, self.config)
        self.assertEqual(updated.monitored_channel_ids, self.config.monitored_channel_ids)
        self.assertEqual(len(chosen['selected']), 7)
        self.assertTrue(updated.actions_enabled and updated.allow_public_deletion)
        self.assertEqual(updated.classifier, config.classifier)
        self.assertEqual(self.policy.read_bytes(), before)
        self.assertEqual(self.settings(), settings)
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all('/messages' not in path for path in self.calls))

    def test_missing_manage_messages_is_actionable_and_never_mutates(self):
        row = self.channels[-1]
        row['permission_overwrites'] = [dict(id=BOT,type=1,deny=str(MANAGE_MESSAGES),allow='0')]
        before, settings = self.policy.read_bytes(), self.settings()
        with self.assertRaisesRegex(RolloutError, row['id']):
            apply_plan(self.profile, self.get, enable_deletion=True)
        self.assertEqual(self.policy.read_bytes(), before)
        self.assertEqual(self.settings(), settings)

    def test_private_bot_mod_in_committers_keeps_exact_seven_channel_deletion_scope(self):
        next(row for row in self.channels if row['id'] == COMMAND)['parent_id'] = COMMITTERS
        before, settings = self.policy.read_bytes(), self.settings()
        _, updated, chosen = verify_deletion(self.profile, self.get)
        self.assertEqual(set(updated.monitored_channel_ids), APPROVED_CHANNELS)
        self.assertEqual({row['channel_id'] for row in chosen['selected']}, APPROVED_CHANNELS)
        self.assertEqual(updated.excluded_category_ids, (COMMITTERS,))
        self.assertEqual(self.policy.read_bytes(), before)
        self.assertEqual(self.settings(), settings)

    def test_changed_scope_or_missing_category_is_rejected(self):
        for changes in ({'monitored_channel_ids': tuple(sorted(APPROVED_CHANNELS))[:-1]},
                        {'included_category_ids': ('100',)}, {'excluded_category_ids': ()}):
            with self.subTest(changes=changes):
                self.policy.write_text(policy_text(replace(self.config, **changes)))
                with self.assertRaises(RolloutError): verify_deletion(self.profile, self.get)

    def test_audience_category_administrator_and_command_privacy_fail_closed(self):
        original = copy.deepcopy(self.channels)
        for scenario in ('moved', 'uncategorized', 'private', 'missing', 'command_public', 'admin'):
            with self.subTest(scenario=scenario):
                self.channels = copy.deepcopy(original)
                row = self.channels[-1]
                if scenario == 'moved': row['parent_id'] = COMMITTERS
                if scenario == 'uncategorized': row['parent_id'] = None
                if scenario == 'private': row['permission_overwrites'] = self.channel(row['id'],'private',private=True)['permission_overwrites']
                if scenario == 'missing': self.channels.pop()
                if scenario == 'command_public':
                    next(r for r in self.channels if r['id'] == COMMAND)['permission_overwrites'] = []
                if scenario == 'admin': self.guild['roles'][1]['permissions'] = '8'
                with self.assertRaises(RolloutError): verify_deletion(self.profile,self.get)

    def test_apply_preserves_scope_accounting_pause_and_backup_leaves_switches_off(self):
        original, settings = self.policy.read_bytes(), self.settings()
        result = apply_plan(self.profile, self.get, enable_deletion=True)
        self.assertTrue(result['public_deletion_allowed'] and result['actions_enabled'])
        self.assertFalse(result['platform_enabled'])
        self.assertEqual(Config.from_file(self.policy).monitored_channel_ids, self.config.monitored_channel_ids)
        self.assertEqual((Path(result['backup'])/'moderation.toml').read_bytes(), original)
        self.assertEqual(self.settings(Path(result['backup'])/'moderation.sqlite3'), settings)
        after = self.settings()
        for key in ('deletion_enabled','auto_delete_enabled','timeout_enabled'):
            self.assertEqual(after.pop(key),'false'); settings.pop(key)
        self.assertEqual(after.pop('action_epoch'),'4'); settings.pop('action_epoch')
        self.assertEqual(after,settings)

    def test_running_lock_and_activation_change_prevent_apply(self):
        with self.lock.open('r+') as locked:
            fcntl.flock(locked,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(RolloutError,'still running'):
                apply_plan(self.profile,self.get,enable_deletion=True)
        def changed(path):
            result=self.get(path)
            if path.endswith('/channels'):
                self.local.write_text(self.local.read_text().replace('liberdus_moderator:\n    enabled: false','liberdus_moderator:\n    enabled: true'))
            return result
        with self.assertRaisesRegex(RolloutError,'Activation or installed version'):
            apply_plan(self.profile,changed,enable_deletion=True)
        self.assertFalse(Config.from_file(self.policy).allow_public_deletion)

    def test_failed_policy_write_leaves_actions_off_and_original_policy(self):
        before=self.policy.read_bytes()
        with patch('liberdus_moderator.public_rollout.atomic_write',side_effect=OSError('disk error')):
            with self.assertRaises(OSError): apply_plan(self.profile,self.get,enable_deletion=True)
        self.assertEqual(self.policy.read_bytes(),before)
        for key in ('deletion_enabled','auto_delete_enabled','timeout_enabled'):
            self.assertEqual(self.settings()[key],'false')


if __name__ == '__main__': unittest.main()
