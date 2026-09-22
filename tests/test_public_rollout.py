import copy
from dataclasses import replace
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from liberdus_moderator.config import ClassifierSettings, Config, StorageSettings
from liberdus_moderator.configure_jev import policy_text
from liberdus_moderator.public_rollout import (BOT, COMMAND, COMMITTERS, GUILD, INCLUDED_CATEGORIES, PLAN_NAME,
    RolloutError, apply_plan, inventory, make_plan, selection, verify_plan)
from liberdus_moderator.preflight import ADMIN, HISTORY, SEND, VIEW


class PublicRolloutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / '.hermes'
        self.profile = self.home / 'profiles/liberdus-mod'
        (self.profile / 'state').mkdir(parents=True)
        (self.profile / 'plugins/liberdus-moderator').mkdir(parents=True)
        self.policy = self.profile / 'moderation.toml'
        self.config = Config(GUILD, BOT, ('1551249559819264030',), (COMMAND,), ('977263877391794217',),
            schema_version=2, ai_enabled=True, actions_enabled=True,
            storage=StorageSettings(database_path=str(self.profile / 'state/moderation.sqlite3')),
            classifier=ClassifierSettings(mode='report_only', max_daily_calls=10000, max_total_calls=100000,
                daily_budget_microusd=1000000, total_budget_microusd=4000000, min_interval_seconds=1))
        self.policy.write_text(policy_text(self.config))
        self.default = self.home / 'config.yaml'
        self.default.write_text('platforms:\n  discord:\n    enabled: false\n')
        self.local = self.profile / 'config.yaml'
        self.local.write_text('platforms:\n  discord:\n    enabled: false\n  liberdus_moderator:\n    enabled: false\n')
        (self.profile / 'plugins/liberdus-moderator/plugin.yaml').write_text('name: liberdus-moderator\nversion: 0.6.1\n')
        self.lock = self.profile / 'state/moderation.lock'
        self.lock.touch()
        self.database = self.profile / 'state/moderation.sqlite3'
        with sqlite3.connect(self.database) as connection:
            connection.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
            connection.executemany('INSERT INTO settings VALUES(?,?)', [
                ('deletion_enabled', 'true'), ('auto_delete_enabled', 'true'), ('timeout_enabled', 'false'),
                ('paused', 'true'), ('screening_total_calls', '60'), ('screening_total_reserved_microusd', '5031'),
                ('health_monitor_v1', '{"existing":"health"}'), ('action_epoch', '3')])
            connection.execute('CREATE TABLE saved_incidents(id TEXT)')
            connection.execute("INSERT INTO saved_incidents VALUES('keep-history')")
        self.guild = dict(id=GUILD, owner_id='2', roles=[dict(id=GUILD, permissions=str(VIEW|HISTORY|SEND)),
                                                                    dict(id='88', permissions='0')])
        self.channels = [self.channel(COMMITTERS, 'Committers', kind=4),
                         self.channel(INCLUDED_CATEGORIES[0], 'Community', kind=4), self.channel('101', 'Staff', kind=4),
                         self.channel('201', 'general', parent=INCLUDED_CATEGORIES[0]), self.channel('202', 'support', parent=INCLUDED_CATEGORIES[1]),
                         self.channel('203', 'committer-chat', parent=COMMITTERS),
                         self.channel('204', 'private-staff', parent=INCLUDED_CATEGORIES[0], private=True),
                         self.channel(COMMAND, 'bot-mod', parent='101', private=True),
                         self.channel('1551249559819264030', 'bot-test-1', parent='101', private=True),
                         self.channel('205', 'forum', kind=15), self.channel('206', 'announcements', kind=5),
                         self.channel(INCLUDED_CATEGORIES[1], 'Community two', kind=4),
                         self.channel('100', 'Other category', kind=4)]
        self.calls = []

    def channel(self, identity, name, *, parent=None, kind=0, private=False):
        return dict(id=identity, guild_id=GUILD, name=name, type=kind, parent_id=parent,
            permission_overwrites=([dict(id=GUILD, type=0, deny=str(VIEW), allow='0'),
                                  dict(id=BOT, type=1, deny='0', allow=str(VIEW|HISTORY|SEND))] if private else []))

    def get(self, path):
        self.calls.append(path)
        responses = {'/users/@me': dict(id=BOT, bot=True), '/guilds/'+GUILD: self.guild,
            '/guilds/'+GUILD+'/members/'+BOT: dict(user=dict(id=BOT), roles=['88']),
            '/guilds/'+GUILD+'/channels': self.channels}
        if path not in responses:
            raise AssertionError('Unexpected endpoint '+path)
        return copy.deepcopy(responses[path])

    def settings(self, path=None):
        with sqlite3.connect(path or self.database) as db:
            return dict(db.execute('SELECT key,value FROM settings'))

    def plan(self):
        return make_plan(self.profile, [COMMITTERS], self.get, now=1000)

    def test_inventory_metadata_only_and_selection_excludes_category_private_and_unsupported(self):
        report = inventory(self.config, self.get)
        chosen = selection(self.config, report, [COMMITTERS])
        self.assertEqual([row['channel_id'] for row in chosen['selected']], ['201','202'])
        self.assertEqual(next(row for row in chosen['omitted'] if row['channel_id']=='203')['reason'], 'excluded_category')
        self.assertEqual(next(row for row in chosen['omitted'] if row['channel_id']=='204')['reason'], 'audience_unverified_not_everyone_visible')
        self.assertEqual(len(self.calls),4)
        self.assertFalse(report['messages_read'])
        self.assertFalse(report['provider_called'])
        self.assertTrue(all('/messages' not in path for path in self.calls))

    def test_known_committers_exclusion_cannot_be_omitted(self):
        with self.assertRaisesRegex(RolloutError, 'Committers'):
            selection(self.config, inventory(self.config,self.get), ['100'])

    def test_unselected_categories_permissions_do_not_block_or_expand_plan(self):
        denied=self.channel('207','unselected',parent='100')
        denied['permission_overwrites']=[dict(id=BOT,type=1,deny=str(VIEW|HISTORY),allow='0')]
        self.channels.append(denied)
        self.channels.append(self.channel('208','uncategorized'))
        result=self.plan()
        self.assertEqual(result['included_category_ids'],list(INCLUDED_CATEGORIES))
        self.assertEqual([row['channel_id'] for row in result['selected_channels']],['201','202'])
        self.assertEqual(next(row['reason'] for row in result['omitted'] if row['channel_id']=='207'),
                         'outside_included_categories')
        self.channels.append(self.channel('209','new-outside',parent='100'))
        verify_plan(self.profile,self.get,now=1001)

    def test_missing_included_category_or_tampered_wider_plan_is_rejected(self):
        self.plan(); path=self.profile/PLAN_NAME
        original=path.read_text()
        for changes in ({'version':1}, {'included_category_ids':[]},
                        {'included_category_ids':[ *INCLUDED_CATEGORIES,'100']}):
            with self.subTest(changes=changes):
                plan=json.loads(original); plan.update(changes); path.write_text(json.dumps(plan))
                with self.assertRaises(ValueError): verify_plan(self.profile,self.get,now=1001)
        path.write_text(original)
        self.channels=[row for row in self.channels if row['id']!=INCLUDED_CATEGORIES[1] and row.get('parent_id')!=INCLUDED_CATEGORIES[1]]
        with self.assertRaisesRegex(RolloutError,'included category'): self.plan()

    def test_channel_leaving_included_categories_invalidates_plan(self):
        self.plan()
        self.channels[3]['parent_id']='100'
        with self.assertRaisesRegex(RolloutError,'scope or categories'):
            verify_plan(self.profile,self.get,now=1001)

    def test_missing_or_noncategory_exclusion_rejected(self):
        for excluded in ([COMMITTERS,'201'], [COMMITTERS,'999']):
            with self.subTest(excluded=excluded), self.assertRaisesRegex(RolloutError, 'not found'):
                selection(self.config, inventory(self.config,self.get), excluded)

    def test_wrong_bot_and_duplicate_channel_and_unknown_parent_rejected(self):
        with self.assertRaisesRegex(RolloutError, 'Bot identity'):
            inventory(self.config,lambda path: dict(id='1',bot=True))
        self.channels.append(copy.deepcopy(self.channels[3]))
        with self.assertRaises(ValueError): inventory(self.config,self.get)
        self.channels.pop()
        self.channels[3]['parent_id']='999'
        with self.assertRaisesRegex(RolloutError,'category'): inventory(self.config,self.get)

    def test_missing_text_parent_metadata_rejected(self):
        del self.channels[3]['parent_id']
        with self.assertRaisesRegex(RolloutError,'metadata'): inventory(self.config,self.get)

    def test_bot_mod_must_remain_private_but_can_be_in_excluded_category(self):
        row=next(row for row in self.channels if row['id']==COMMAND)
        row['permission_overwrites']=[]
        with self.assertRaisesRegex(RolloutError,'bot-mod'): self.plan()
        row['permission_overwrites']=self.channel(COMMAND,'bot-mod',private=True)['permission_overwrites']
        row['parent_id']=COMMITTERS
        result=self.plan()
        self.assertEqual([r['channel_id'] for r in result['selected_channels']], ['201','202'])
        omitted={r['channel_id']:r['reason'] for r in result['omitted']}
        self.assertEqual(omitted[COMMAND], 'private_commands_and_reports')
        self.assertEqual(omitted['203'], 'excluded_category')
        self.assertEqual(result['excluded_category_ids'], [COMMITTERS])

    def test_bot_mod_in_excluded_category_still_needs_private_read_write_access(self):
        row=next(row for row in self.channels if row['id']==COMMAND)
        row['parent_id']=COMMITTERS
        original=copy.deepcopy(row['permission_overwrites'])
        for denied in (VIEW, HISTORY, SEND):
            row['permission_overwrites']=original+[dict(id=BOT,type=1,allow='0',deny=str(denied))]
            with self.assertRaisesRegex(RolloutError,'bot-mod'): self.plan()

    def test_missing_bot_access_refuses_partial_plan(self):
        self.channels[3]['permission_overwrites']=[dict(id=BOT,type=1,deny=str(VIEW),allow='0')]
        with self.assertRaisesRegex(RolloutError,'Some selected'): self.plan()
        self.assertFalse((self.profile/PLAN_NAME).exists())

    def test_admin_bot_refuses_plan(self):
        self.guild['roles'][1]['permissions']=str(ADMIN)
        with self.assertRaises(RolloutError): self.plan()

    def test_plan_is_private_read_only_for_runtime_and_preserves_budgets(self):
        policy=self.policy.read_bytes(); settings=self.settings()
        result=self.plan()
        self.assertEqual(result['selected_count'],2)
        self.assertFalse(result['active_policy_changed'])
        self.assertEqual((self.profile/PLAN_NAME).stat().st_mode & 0o777,0o600)
        self.assertEqual(self.policy.read_bytes(),policy)
        self.assertEqual(self.settings(),settings)
        original,updated,_=verify_plan(self.profile,self.get,now=1001)
        self.assertEqual(original,self.config)
        self.assertTrue(updated.allow_public_monitored_channels)
        self.assertEqual(updated.excluded_category_ids,(COMMITTERS,))
        self.assertEqual(updated.included_category_ids,INCLUDED_CATEGORIES)
        self.assertFalse(updated.actions_enabled)
        self.assertEqual(updated.classifier,self.config.classifier)

    def test_expired_future_and_changed_policy_plan_rejected(self):
        self.plan()
        for now in (999,87401):
            with self.subTest(now=now), self.assertRaisesRegex(RolloutError,'stale'):
                verify_plan(self.profile,self.get,now=now)
        self.policy.write_text(policy_text(replace(self.config,policy_version='changed')))
        with self.assertRaisesRegex(RolloutError,'policy changed'): verify_plan(self.profile,self.get,now=1001)

    def test_new_channel_or_parent_move_requires_fresh_plan_but_rename_does_not(self):
        self.plan()
        self.channels[3]['name']='general-renamed'
        verify_plan(self.profile,self.get,now=1001)
        self.channels.append(self.channel('207','new-channel',parent=INCLUDED_CATEGORIES[0]))
        with self.assertRaisesRegex(RolloutError,'scope or categories'): verify_plan(self.profile,self.get,now=1001)
        self.channels.pop(); self.channels[3]['parent_id']=COMMITTERS
        with self.assertRaisesRegex(RolloutError,'scope or categories'): verify_plan(self.profile,self.get,now=1001)

    def test_audience_change_before_apply_refuses_without_changing_state(self):
        original_channels=copy.deepcopy(self.channels)
        for identity in ('201', COMMAND):
            with self.subTest(identity=identity):
                self.channels=copy.deepcopy(original_channels)
                self.plan(); before=self.settings(); policy=self.policy.read_bytes()
                row=next(row for row in self.channels if row['id']==identity)
                row['permission_overwrites']=(self.channel(identity,'private',private=True)['permission_overwrites']
                                             if identity=='201' else [])
                with self.assertRaises(RolloutError): apply_plan(self.profile,self.get,now=1001)
                self.assertEqual(self.settings(),before)
                self.assertEqual(self.policy.read_bytes(),policy)

    def test_activation_change_during_metadata_recheck_refuses_before_changes(self):
        self.plan(); before=self.settings(); policy=self.policy.read_bytes()
        def change(path):
            response=self.get(path)
            if path.endswith('/channels'):
                self.local.write_text(self.local.read_text().replace('liberdus_moderator:\n    enabled: false',
                                                                   'liberdus_moderator:\n    enabled: true'))
            return response
        with self.assertRaisesRegex(RolloutError,'Activation or installed version'):
            apply_plan(self.profile,change,now=1001)
        self.assertEqual(self.settings(),before)
        self.assertEqual(self.policy.read_bytes(),policy)

    def test_apply_requires_disabled_platform_and_released_lock(self):
        self.plan()
        original=self.local.read_text()
        self.local.write_text(original.replace('liberdus_moderator:\n    enabled: false','liberdus_moderator:\n    enabled: true'))
        with self.assertRaisesRegex(RolloutError,'Disable'): apply_plan(self.profile,self.get,now=1001)
        self.local.write_text(original)
        with self.lock.open('r+') as stream:
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(RolloutError,'still running'): apply_plan(self.profile,self.get,now=1001)

    def test_apply_disables_policy_and_flags_preserves_other_data_and_backs_up(self):
        self.plan(); before=self.settings(); policy=self.policy.read_bytes()
        result=apply_plan(self.profile,self.get,now=1001)
        self.assertTrue(result['configured'])
        after=self.settings()
        for key in ('deletion_enabled','auto_delete_enabled','timeout_enabled'):
            self.assertEqual(after.pop(key),'false'); before.pop(key)
        self.assertEqual(after.pop('action_epoch'),'4'); before.pop('action_epoch')
        self.assertEqual(after,before)
        saved=Config.from_file(self.policy)
        self.assertEqual(saved.monitored_channel_ids,('201','202'))
        self.assertFalse(saved.actions_enabled)
        self.assertTrue(saved.allow_public_monitored_channels)
        self.assertEqual(saved.included_category_ids,INCLUDED_CATEGORIES)
        self.assertEqual(saved.classifier,self.config.classifier)
        backup=Path(result['backup'])
        self.assertEqual((backup/'moderation.toml').read_bytes(),policy)
        self.assertEqual(self.settings(backup/'moderation.sqlite3')['deletion_enabled'],'true')
        self.assertEqual((backup/'moderation.sqlite3').stat().st_mode & 0o777,0o600)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute('SELECT * FROM saved_incidents').fetchone()[0],'keep-history')

    def test_failed_policy_write_keeps_platform_disabled_and_action_flags_off(self):
        self.plan(); original=self.policy.read_bytes()
        with patch('liberdus_moderator.public_rollout.atomic_write',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): apply_plan(self.profile,self.get,now=1001)
        self.assertEqual(self.policy.read_bytes(),original)
        self.assertEqual(self.settings()['auto_delete_enabled'],'false')
        self.assertIn('liberdus_moderator:\n    enabled: false',self.local.read_text())

    def test_plan_symlink_and_public_permissions_refused(self):
        self.plan(); path=self.profile/PLAN_NAME
        path.chmod(0o644)
        with self.assertRaisesRegex(RolloutError,'private'): verify_plan(self.profile,self.get,now=1001)
        path.unlink(); path.symlink_to(self.policy)
        with self.assertRaises(ValueError): verify_plan(self.profile,self.get,now=1001)


if __name__=='__main__':
    unittest.main()
