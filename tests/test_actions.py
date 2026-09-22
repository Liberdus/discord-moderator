from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from liberdus_moderator import actions
from liberdus_moderator.commands import CommandRequest, handle_command
from liberdus_moderator.config import Config, ClassifierSettings
from liberdus_moderator.engine import Engine
from liberdus_moderator.live import LiveSession
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.storage import Store
from liberdus_moderator.staff_review import pending_page


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.config = Config('1','99',('10','11','12'),('20',),('98',),schema_version=2,actions_enabled=True)
        self.engine = Engine(self.config,self.store,clock=lambda:self.now)
        self.live = LiveSession(self.engine)
        for i,ch in enumerate(('10','11','12')):
            self.engine.process(MessageEvent('1',ch,str(100+i),'50','Repeated sufficient content.',998+i))
        self.incident = self.store.incidents()[0]
        self.identity = self.incident['id']

    def command(self,name,args=(),user='98',channel='20',guild='1'):
        return handle_command(self.engine,CommandRequest(guild,channel,user,name,arguments=args))

    def proposal(self,kind='delete'):
        self.command('deletion' if kind=='delete' else 'timeout',('on',))
        return actions.propose(self.engine,self.identity,1,kind,'98','20')

    def test_flags_fail_closed_authorization_and_persistence(self):
        self.assertFalse(actions.enabled(self.engine,'timeout'))
        self.assertFalse(self.command('timeout',('on',),user='50')['authorized'])
        self.assertFalse(self.command('timeout',('on',),channel='10')['authorized'])
        self.assertFalse(self.command('timeout',('on',),guild='2')['authorized'])
        self.assertTrue(self.command('timeout',('on',))['ok'])
        self.live=LiveSession(Engine(self.config,self.store,clock=lambda:self.now))
        self.assertTrue(actions.enabled(self.engine,'timeout'))
        self.assertFalse(self.command('timeout',('yes',))['ok'])
        self.command('timeout',('off',))
        self.assertFalse(actions.enabled(self.engine,'timeout'))
        self.engine.config=replace(self.config,actions_enabled=False)
        self.store.set_setting('policy_hash',self.engine.config.policy_hash)
        self.assertFalse(self.command('timeout',('on',))['ok'])

    def test_public_deletion_commands_and_timeout_policy_override_persisted_flags(self):
        self.engine.config = replace(self.config, allow_public_monitored_channels=True,
            allow_public_deletion=True, included_category_ids=('100',), excluded_category_ids=('200',))
        self.store.set_setting('policy_hash', self.engine.config.policy_hash)
        for name in ('deletion', 'auto-delete'):
            self.assertTrue(self.command(name, ('on',))['ok'])
        self.assertTrue(self.engine.status()['deletion_enabled'])
        self.assertTrue(self.engine.status()['auto_delete_enabled'])
        text = self.live.command(CommandRequest('1', '20', '98', 'deletion'), '900', True)
        self.assertIn('Policy: deletion only', text)
        from liberdus_moderator.classification_view import format_incident, incident_view
        text = format_incident(incident_view(self.engine, self.incident))
        self.assertIn('Timeout is disabled by policy', text)
        self.store.set_setting('timeout_enabled', True)  # Stale/externally changed flag cannot bypass policy.
        self.assertFalse(self.command('timeout', ('on',))['ok'])
        self.assertFalse(actions.enabled(self.engine, 'timeout'))
        self.assertFalse(self.engine.status()['timeout_enabled'])
        with self.assertRaises(actions.ActionError):
            actions.plan(self.engine, self.identity, 1, 'timeout', '98', '20')
        self.assertTrue(self.command('timeout', ('off',))['ok'])
        self.assertFalse(self.command('deletion', ('on',), channel='10')['authorized'])
        self.command('deletion', ('off',))
        self.assertFalse(actions.enabled(self.engine, 'deletion'))

    def test_confirmation_has_exact_links_is_bound_and_one_use(self):
        text=self.proposal()
        self.assertIn('Delete 3 message(s)',text)
        for i in range(3): self.assertIn('/'+str(100+i)+'>',text)
        self.assertLess(len(text),1900)
        p=text.proposal
        actions.bind(self.engine,p,'700')
        for user,ch,msg in [('50','20','700'),('98','10','700'),('98','20','701')]:
            with self.assertRaises(actions.ActionError): actions.consume(self.engine,p['token'],user,ch,msg)
        payload=actions.consume(self.engine,p['token'],'98','20','700')
        self.assertEqual(payload['author_id'],'50')
        with self.assertRaises(actions.ActionError): actions.consume(self.engine,p['token'],'98','20','700')

    def test_expired_restart_cancel_and_setting_changes_cancel_proposals(self):
        for mode in ('expire','restart','cancel','toggle','pause'):
            with self.subTest(mode=mode):
                self.engine.set_paused(False)
                # Restart coverage does not happen here, so replace terminal test incidents as needed.
                if self.store.incident(self.identity)['status']!='open':
                    continue
                p=self.proposal().proposal; actions.bind(self.engine,p,'700')
                if mode=='expire': self.now+=61
                elif mode=='restart': self.live=LiveSession(self.engine)
                elif mode=='cancel': actions.consume(self.engine,p['token'],'98','20','700',cancel=True)
                elif mode=='toggle': self.command('deletion',('off',));self.command('deletion',('on',))
                else: self.engine.set_paused(True)
                with self.assertRaises(actions.ActionError): actions.consume(self.engine,p['token'],'98','20','700')
                self.assertFalse(actions.history(self.engine,self.identity))

    def test_action_ledger_dedup_and_restart_marks_uncertain(self):
        p=self.proposal().proposal;actions.bind(self.engine,p,'700')
        payload=actions.consume(self.engine,p['token'],'98','20','700')
        key=actions.reserve(self.engine,payload,'100')
        self.assertIsNotNone(key)
        self.assertIsNone(actions.reserve(self.engine,payload,'100'))
        self.live=LiveSession(self.engine)
        self.assertEqual(actions.history(self.engine,self.identity)[0]['outcome'],'uncertain')
        self.assertIsNone(actions.reserve(self.engine,payload,'100'))

    def test_timeout_one_per_incident_and_member_cooldown(self):
        p=self.proposal('timeout').proposal;actions.bind(self.engine,p,'700')
        payload=actions.consume(self.engine,p['token'],'98','20','700')
        key=actions.reserve(self.engine,payload,'50'); actions.finish(self.engine,key,'done')
        self.assertIsNone(actions.reserve(self.engine,payload,'50'))
        other={**payload,'incident_id':'f'*32}
        with self.assertRaisesRegex(actions.ActionError,'recent timeout'): actions.reserve(self.engine,other,'50')

    def test_dismiss_closes_review_without_action_and_blocks_pending_plan(self):
        p=self.proposal().proposal;actions.bind(self.engine,p,'700')
        self.assertTrue(self.command('dismiss',(self.identity,'1'))['ok'])
        self.assertEqual(pending_page(self.engine)['total'],0)
        with self.assertRaises(actions.ActionError): actions.consume(self.engine,p['token'],'98','20','700')
        self.assertFalse(actions.history(self.engine,self.identity))

    def test_stale_or_changed_evidence_refused(self):
        self.command('deletion',('on',))
        self.assertFalse(self.command('delete',(self.identity,'2'))['ok'])
        self.assertFalse(self.command('delete',(self.identity,'1','999'))['ok'])
        p=self.proposal().proposal;actions.bind(self.engine,p,'700')
        event=MessageEvent('1','10','100','50','Completely changed replacement.',998,edited_at=1000)
        self.engine.process(event)
        with self.assertRaises(actions.ActionError): actions.consume(self.engine,p['token'],'98','20','700')

    def test_auto_rule_strict_threshold_context_scope_and_flags(self):
        self.command('deletion',('on',));self.command('auto-delete',('on',))
        incident={**self.incident,'rule_id':'jev_message','evidence':self.incident['evidence'][:1]}
        baseline=dict(evidence_state='current',choice='sensitive_request',confidence=.91,purpose='other',age_seconds=0)
        for change,expected in [({},True),({'confidence':.90},False),({'choice':'impersonation'},False),
                                ({'purpose':'quoted_warning'},False),({'purpose':'unclear'},False),
                                ({'evidence_state':'historical'},False),({'age_seconds':61},False)]:
            with patch('liberdus_moderator.screening.saved_screening',return_value={**baseline,**change}):
                self.assertEqual(actions.automatic_candidate(self.engine,incident),expected)
        self.command('deletion',('off',))
        with patch('liberdus_moderator.screening.saved_screening',return_value=baseline):
            self.assertFalse(actions.automatic_candidate(self.engine,incident))

    def test_pause_no_action_and_single_message_selection(self):
        self.command('deletion',('on',))
        text=actions.propose(self.engine,self.identity,1,'delete','98','20','101')
        self.assertIn('Delete 1 message(s)',text)
        self.assertNotIn('/100>',text)
        self.engine.set_paused(True)
        self.assertFalse(self.command('delete',(self.identity,'2'))['ok'])

    def test_current_action_history_is_authorized_bounded_and_unchanged_by_reads(self):
        p=self.proposal().proposal;actions.bind(self.engine,p,'700')
        payload=actions.consume(self.engine,p['token'],'98','20','700')
        key=actions.reserve(self.engine,payload,'100');actions.finish(self.engine,key,'done')
        self.assertFalse(self.command('actions',(self.identity,),user='50')['authorized'])
        self.assertFalse(self.command('actions',('f'*32,))['ok'])
        before=actions.history(self.engine,self.identity)
        text=self.live.command(CommandRequest('1','20','98','actions',arguments=(self.identity,)),'900',True)
        self.assertIn('Staff delete: done',text)
        self.assertIn('By: 98',text)
        self.assertEqual(actions.history(self.engine,self.identity),before)

    def test_status_help_and_action_request_have_no_network_effects(self):
        for i,name in enumerate(('help','status','timeout','auto-delete','deletion')):
            text=self.live.command(CommandRequest('1','20','98',name),str(800+i),True)
            self.assertIn('```',text)
            self.assertLess(len(text),1900)
        self.command('deletion',('on',))
        text=self.live.command(CommandRequest('1','20','98','delete',arguments=(self.identity,'1')),'900',True)
        from liberdus_moderator.manual_delete import DeleteRequest
        self.assertIsInstance(text, DeleteRequest)
        self.assertFalse(actions.history(self.engine,self.identity))


class MessageComparisonTests(unittest.TestCase):
    def test_missing_or_changed_roles_are_not_message_edits(self):
        event = MessageEvent('1','10','100','50','Original text.',1000,author_role_ids=('77',))
        evidence = {**event.to_dict(), 'version': event.version, 'fingerprint': 'saved'}
        for roles in ((), ('88',), ('77','88')):
            fetched = replace(event, author_role_ids=roles)
            self.assertNotEqual(event.version, fetched.version)
            self.assertEqual(actions.message_changes(evidence, fetched), ())

    def test_every_message_field_still_participates_in_comparison(self):
        event = MessageEvent('1','10','100','50','Original text.',1000,author_role_ids=('77',))
        changes = dict(guild_id='2',channel_id='11',message_id='101',author_id='51',
                       content='Edited replacement.',created_at=1001,edited_at=1002,
                       is_bot=True,is_webhook=True,is_thread=True,has_attachments=True)
        for field, value in changes.items():
            with self.subTest(field=field):
                result = actions.message_changes(event.to_dict(), replace(event, **{field:value}))
                self.assertEqual(result, (field,))
                self.assertNotIn('Edited replacement', str(result))
