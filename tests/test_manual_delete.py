from dataclasses import replace
import json
import unittest

import test_actions as old
from liberdus_moderator import actions, manual_delete
from liberdus_moderator.commands import CommandRequest
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.staff_review import record_assessment
from liberdus_moderator.evidence_view import units


class ManualDeleteTests(unittest.TestCase):
    setUp=old.ActionTests.setUp
    command=old.ActionTests.command

    def request(self, message_id=None):
        self.command('deletion',('on',))
        self.live.gap('reconnect')
        return manual_delete.request_delete(self.engine,self.identity,1,'98','20',message_id).request

    def events(self,request):
        return [MessageEvent.from_dict({k:v for k,v in item.items() if k not in ('version','fingerprint')})
                for item in request['evidence']]

    def prepared(self):
        request=self.request()
        payload=manual_delete.checked_refresh(self.engine,request,self.events(request))
        text=manual_delete.confirmation(self.engine,payload)
        actions.bind(self.engine,text.proposal,'700')
        return payload,text

    def test_historical_report_gets_fresh_confirmation_without_reopening_incident(self):
        request=self.request()
        saved=self.store.incident(self.identity)
        self.assertEqual(saved['revision'],2)
        self.assertEqual(saved['status'],'needs_revalidation')
        payload=manual_delete.checked_refresh(self.engine,request,self.events(request))
        text=manual_delete.confirmation(self.engine,payload)
        self.assertIn('Fetched now for staff review',text)
        self.assertLessEqual(units(text),1900)
        self.assertEqual(self.store.incident(self.identity),saved)
        self.assertFalse(actions.automatic_candidate(self.engine,saved))
        self.assertFalse(actions.history(self.engine,self.identity))

    def test_changed_content_is_shown_and_saved_in_separate_action_audit(self):
        request=self.request('100');events=self.events(request)
        events[0]=replace(events[0],content='Edited text for staff to review',edited_at=1000.)
        payload=manual_delete.checked_refresh(self.engine,request,events)
        text=manual_delete.confirmation(self.engine,payload)
        self.assertIn('CHANGED since saved report',text)
        self.assertIn('Edited text',text)
        key=actions.reserve(self.engine,payload,'100')
        record=self.store.db.execute('SELECT * FROM action_evidence_v1 WHERE action_key=?',(key,)).fetchone()
        self.assertEqual(json.loads(record['evidence_json'])[0]['content'],events[0].content)
        self.assertEqual(record['source_revision'],1)
        self.assertNotEqual(self.store.incident(self.identity)['evidence'][0]['content'],events[0].content)
        self.assertEqual(actions.history(self.engine,self.identity)[0]['automatic'],0)

    def test_confirmation_is_bound_single_use_and_invalid_after_reconnect_or_pause_cycle(self):
        payload,text=self.prepared()
        for args in (('50','20','700'),('98','10','700'),('98','20','701')):
            with self.assertRaises(actions.ActionError):actions.consume(self.engine,text.proposal['token'],*args)
        actions.consume(self.engine,text.proposal['token'],'98','20','700')
        with self.assertRaises(actions.ActionError):actions.consume(self.engine,text.proposal['token'],'98','20','700')
        self.live.gap('disconnect')
        with self.assertRaises(actions.ActionError):actions.revalidate(self.engine,payload)
        request=manual_delete.request_delete(self.engine,self.identity,1,'98','20').request
        payload=manual_delete.checked_refresh(self.engine,request,self.events(request))
        self.command('pause');self.command('resume')
        with self.assertRaises(actions.ActionError):actions.revalidate(self.engine,payload)

    def test_edited_identity_unsupported_content_and_auto_conversion_are_rejected(self):
        request=self.request('100');events=self.events(request)
        for changes in ({'author_id':'51'},{'channel_id':'11'},{'message_id':'999'},
                        {'created_at':997.},{'has_attachments':True},{'is_bot':True}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                manual_delete.checked_refresh(self.engine,request,[replace(events[0],**changes)])
        payload=manual_delete.checked_refresh(self.engine,request,events)
        for change in ({'automatic':True},{'kind':'timeout'}):
            with self.assertRaises(actions.ActionError):actions.revalidate(self.engine,{**payload,**change})

    def test_dismissal_policy_scope_timeout_and_off_switch_block_refreshed_actions(self):
        payload,text=self.prepared()
        self.now+=61
        with self.assertRaises(actions.ActionError):actions.revalidate(self.engine,payload)
        self.now-=61
        self.command('deletion',('off',))
        with self.assertRaises(actions.ActionError):actions.revalidate(self.engine,payload)
        self.command('deletion',('on',))
        record_assessment(self.engine,self.identity,'1','dismissed','98')
        with self.assertRaises(actions.ActionError):manual_delete.request_delete(self.engine,self.identity,1,'98','20')

    def test_preview_escapes_fences_mentions_unicode_and_bounds_long_messages(self):
        request=self.request('100');events=self.events(request)
        events[0]=replace(events[0],content='``` @everyone https://example.invalid/ '+ '\U0001f4a5'*1500)
        text=manual_delete.confirmation(self.engine,manual_delete.checked_refresh(self.engine,request,events))
        self.assertLessEqual(units(text),1900)
        self.assertNotIn('@everyone',text)
        self.assertNotIn('```',text)
        self.assertIn('\\`\\`\\`',text)
        self.assertIn('Text excerpts',text)

    def test_old_policy_is_not_restored_by_refresh_and_direct_command_only_requests_fetch(self):
        self.command('deletion',('on',))
        self.engine.config=replace(self.config,policy_version='new-policy')
        self.store.set_setting('policy_hash',self.engine.config.policy_hash)
        self.live.gap('reconnect')
        result=self.command('delete',(self.identity,'1'))
        self.assertTrue(result['ok'])
        self.assertIsInstance(result['data'],manual_delete.DeleteRequest)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM action_proposals_v1').fetchone()[0],0)
        self.assertNotEqual(self.store.incident(self.identity)['policy_hash'],self.engine.config.policy_hash)


if __name__=='__main__':unittest.main()
