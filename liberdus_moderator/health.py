"""Bounded, durable screening-health notices; no Discord or model I/O.

Only live screening hooks call record_success/failure. Evaluation batches do not.
A claimed notice is never automatically replayed after an uncertain delivery.
"""
import hashlib
import json
import math
import re
from uuid import uuid4

from .classifier import RESERVED_MICROUSD
from .display import panel

SETTING = 'health_monitor_v1'
WINDOW_SECONDS = 300
ALERT_INTERVAL_SECONDS = 300
FAILURE_THRESHOLD = 3
MAX_COUNT = 2**63 - 1
GAP_REASONS = frozenset({'disconnect', 'queue_full', 'worker_failure', 'unavailable_edit', 'invalid_event', 'restart_lost', 'scope_changed'})
AUTH_FAILURES = frozenset({'missing_key', 'authentication_failed', 'access_denied'})
FAILURE_LABELS = {
    'missing_key': 'API key unavailable', 'authentication_failed': 'API authentication rejected',
    'access_denied': 'API access denied', 'timeout': 'request timed out',
    'rate_limited': 'provider rate limit', 'provider_overloaded': 'provider overloaded',
    'http_error': 'provider HTTP error', 'response_too_large': 'provider response too large',
    'invalid_json': 'invalid provider JSON', 'provider_or_response_error': 'provider or response error',
    'invalid_response': 'invalid provider response', 'network_error': 'network error',
    'internal_error': 'screening error', 'uncertain': 'interrupted request',
    'stale': 'evidence changed before screening completed', 'membership_unavailable': 'membership unavailable',
    'input_limit': 'input exceeds screening limit', 'storage_limit': 'screening storage limit',
    'queue_full': 'screening queue full', 'rate_limited_locally': 'local screening rate limit',
    'clock_rollback': 'usage clock moved backward', 'usage_exceeds_reservation': 'unexpected usage',
}
FAILURE_CODES = frozenset(FAILURE_LABELS)
GAP_LABELS = {
    'disconnect': 'Discord disconnected', 'queue_full': 'Queue overflow',
    'worker_failure': 'Moderation worker stopped', 'unavailable_edit': 'Edited message unavailable',
    'invalid_event': 'Unsupported incoming event',
    'scope_changed': 'Channel scope or access changed',
    'restart_lost': 'Restarts with unfinished checks',
}
REASON_LABELS = {
    'repeated_screening_failures': 'Repeated checks failed or were skipped.',
    'jev_unavailable': 'JEV suspended after a key or access failure.',
    'daily_calls': 'Daily screening call limit reached.',
    'daily_budget': 'Daily allowance cannot reserve another check.',
    'total_calls': 'Lifetime screening call limit reached.',
    'total_budget': 'Lifetime allowance cannot reserve another check.',
    'billing_guard': 'Screening stopped by the billing guard.',
    'accounting_invalid': 'Shared usage accounting needs inspection.',
    'storage_limit': 'Screening storage limit reached.',
    'incident_capacity': 'Incident storage is at its limit.',
    'clock_rollback': 'Usage accounting is ahead of the current day.',
}
_KINDS = frozenset({'warning', 'interruption', 'recovery'})


def _count(value):
    return type(value) is int and 0 <= value <= MAX_COUNT


def _time(value):
    return type(value) in (int, float) and 0 <= value <= 253402300799 and math.isfinite(value)


def _initial():
    return dict(version=1, failure_times=[], failure_code=None, failure_active=False,
                auth_code=None, gaps={}, success_seq=0, open_success_seq=None,
                announced_reasons=[], revision=0, last_claim_at=None, attempt=None)


class HealthMonitor:
    def __init__(self, engine):
        self.engine, self.store = engine, engine.store
        with self.store.transaction():
            state = self._state()
            if state['attempt'] is not None and state['attempt']['status'] == 'sending':
                state['attempt']['status'] = 'uncertain'
                state['attempt']['finished_at'] = self.engine._now()
            self._save(state)

    def _state(self):
        state = self.store.get_setting(SETTING)
        if state is None:
            return _initial()
        valid = (isinstance(state, dict) and set(state) == set(_initial())
            and type(state['version']) is int and state['version'] == 1
            and isinstance(state['failure_times'], list) and len(state['failure_times']) <= FAILURE_THRESHOLD
            and all(_time(value) for value in state['failure_times'])
            and (state['failure_code'] is None or isinstance(state['failure_code'], str) and state['failure_code'] in FAILURE_CODES)
            and type(state['failure_active']) is bool
            and (not state['failure_active'] or state['failure_code'] is not None)
            and (state['auth_code'] is None or isinstance(state['auth_code'], str) and state['auth_code'] in AUTH_FAILURES)
            and isinstance(state['gaps'], dict) and set(state['gaps']) <= GAP_REASONS
            and all(_count(value) and value > 0 for value in state['gaps'].values())
            and _count(state['success_seq']) and _count(state['revision'])
            and (state['open_success_seq'] is None or _count(state['open_success_seq']))
            and isinstance(state['announced_reasons'], list)
            and len(state['announced_reasons']) <= len(REASON_LABELS)
            and all(isinstance(value, str) and value in REASON_LABELS for value in state['announced_reasons'])
            and len(set(state['announced_reasons'])) == len(state['announced_reasons'])
            and (state['last_claim_at'] is None or _time(state['last_claim_at'])))
        if not valid:
            raise ValueError('Invalid saved moderation health state')
        attempt = state['attempt']
        if attempt is not None and not (isinstance(attempt, dict)
                and set(attempt) == {'token', 'status', 'kind', 'revision', 'started_at', 'finished_at'}
                and isinstance(attempt['token'], str) and re.fullmatch(r'[a-f0-9]{32}', attempt['token'])
                and isinstance(attempt['status'], str) and attempt['status'] in {'sending', 'sent', 'uncertain'}
                and isinstance(attempt['kind'], str) and attempt['kind'] in _KINDS
                and _count(attempt['revision']) and _time(attempt['started_at'])
                and (attempt['finished_at'] is None or _time(attempt['finished_at']))):
            raise ValueError('Invalid saved moderation health delivery')
        return state

    def _save(self, state):
        # No arbitrary provider text, evidence, user identifiers, URLs, or credentials.
        if len(json.dumps(state, separators=(',', ':'))) > 8192:
            raise ValueError('Moderation health state exceeds its bound')
        self.store.set_setting(SETTING, state)

    def _enabled(self):
        config = self.engine.config
        return (config.mode == 'report_only' and config.ai_enabled
                and config.classifier.mode == 'report_only'
                and self.store.get_setting('paused', False) is False
                and self.store.get_setting('policy_hash') == config.policy_hash)

    def record_failure(self, code):
        if not isinstance(code, str) or code not in FAILURE_CODES or not self._enabled():
            return False
        now = self.engine._now()
        with self.store.transaction():
            state = self._state()
            state['failure_times'] = [stamp for stamp in state['failure_times'] if 0 <= now - stamp <= WINDOW_SECONDS]
            state['failure_times'] = (state['failure_times'] + [now])[-FAILURE_THRESHOLD:]
            state['failure_code'] = code
            if len(state['failure_times']) >= FAILURE_THRESHOLD:
                state['failure_active'] = True  # Only actual successful live screening clears the episode.
            if code in AUTH_FAILURES:
                state['auth_code'] = code  # Worker suspends after one key/access error.
            self._save(state)
        return True

    def record_success(self):
        if not self._enabled():
            return False
        with self.store.transaction():
            state = self._state()
            state.update(failure_times=[], failure_code=None, failure_active=False, auth_code=None)
            state['success_seq'] = min(MAX_COUNT, state['success_seq'] + 1)
            self._save(state)
        return True

    def gap(self, reason):
        if not isinstance(reason, str) or reason not in GAP_REASONS or not self._enabled():
            return False
        with self.store.transaction():
            state = self._state()
            state['gaps'][reason] = min(MAX_COUNT, state['gaps'].get(reason, 0) + 1)
            self._save(state)
        return True

    def _budget_reasons(self, now):
        reasons = set()
        flag = self.store.get_setting('screening_billing_guard', False)
        if type(flag) is not bool:
            reasons.add('accounting_invalid')
        elif flag:
            reasons.add('billing_guard')
        schema = self.store.get_setting('screening_schema_version', 1)
        if type(schema) is not int or schema != 1:
            reasons.add('accounting_invalid')
        day = int(now // 86400)
        names = ('daily_calls', 'total_calls', 'daily_reserved_microusd', 'total_reserved_microusd')
        counters = {name: self.store.get_setting('screening_' + name, 0) for name in names}
        prior = self.store.get_setting('screening_budget_day', day)
        if (not all(_count(value) for value in counters.values()) or not _count(prior)
                or counters['daily_calls'] > counters['total_calls']
                or counters['daily_reserved_microusd'] > counters['total_reserved_microusd']):
            return reasons | {'accounting_invalid'}
        if prior > day:
            reasons.add('clock_rollback')
        daily_calls = counters['daily_calls'] if prior == day else 0
        daily_used = counters['daily_reserved_microusd'] if prior == day else 0
        limits = self.engine.config.classifier
        if daily_calls >= limits.max_daily_calls:
            reasons.add('daily_calls')
        if daily_used + RESERVED_MICROUSD > limits.daily_budget_microusd:
            reasons.add('daily_budget')
        if counters['total_calls'] >= limits.max_total_calls:
            reasons.add('total_calls')
        if counters['total_reserved_microusd'] + RESERVED_MICROUSD > limits.total_budget_microusd:
            reasons.add('total_budget')
        return reasons

    def _capacity_reasons(self, now):
        reasons = set()
        if self.store.db.execute('SELECT count(*) FROM incidents').fetchone()[0] >= self.engine.config.storage.max_incidents:
            reasons.add('incident_capacity')
        if self.store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='screening_attempts_v1'").fetchone():
            # Mirror the next admission's expiry rule without pruning any live rows.
            count = self.store.db.execute(
                "SELECT count(*) FROM screening_attempts_v1 WHERE created_at>=? "
                "OR outcome IN ('running','awaiting_apply')",
                (now - self.engine.config.storage.retention_seconds,)).fetchone()[0]
            if count >= self.engine.config.storage.max_messages:
                reasons.add('storage_limit')
        return reasons

    def _plan(self, state, connected, now):
        if connected is not True or not self._enabled():
            return None
        reasons = self._budget_reasons(now) | self._capacity_reasons(now)
        if state['auth_code'] is not None:
            reasons.add('jev_unavailable')
        elif state['failure_active']:
            reasons.add('repeated_screening_failures')
        reasons = sorted(reasons)
        gaps = dict(state['gaps'])
        if reasons and (reasons != state['announced_reasons'] or gaps):
            kind = 'warning'
        elif (not reasons and not state['failure_times'] and state['open_success_seq'] is not None
                and state['success_seq'] > state['open_success_seq']):
            kind = 'recovery'
        elif gaps:
            kind = 'interruption'
        else:
            return None
        if state['last_claim_at'] is not None and now - state['last_claim_at'] < ALERT_INTERVAL_SECONDS:
            return None
        content = self._content(kind, reasons, gaps, state)
        return dict(kind=kind, reasons=reasons, gaps=gaps, content=content,
                    channel_id=self.engine.config.command_channel_ids[0])

    def _content(self, kind, reasons, gaps, state):
        title = {'warning': 'Moderation coverage alert', 'interruption': 'Moderation coverage interruption',
                 'recovery': 'Moderation screening recovered'}[kind]
        lines = ['SCREENING HEALTH', '-' * 32]
        if kind == 'recovery':
            lines += ['A valid live JEV check completed.', 'No current screening blocker was detected.']
        elif kind == 'interruption':
            lines += ['Screening coverage was interrupted.']
        else:
            lines += [REASON_LABELS[reason] for reason in reasons]
            if 'jev_unavailable' in reasons:
                lines += ['Reason: ' + FAILURE_LABELS[state['auth_code']]]
            elif 'repeated_screening_failures' in reasons:
                lines += ['At least 3 checks failed or were skipped within 5 minutes.',
                          'Last reason: ' + FAILURE_LABELS[state['failure_code']]]
            lines += ['Some messages may not receive JEV screening.']
        if gaps:
            lines += ['', 'INTERRUPTIONS', '-' * 32]
            lines += [GAP_LABELS[reason] + ': ' + str(gaps[reason]) for reason in sorted(gaps)]
            lines += ['Gateway is connected now.']
        lines += ['', 'Missed checks are not backfilled.', 'Code rules are separate from JEV.',
                  'Use !mod status for current state.', 'This notice makes no AI call or moderation action.']
        return panel(title, lines)

    def sample(self, connected):
        """Inspect a possible notice without reserving delivery or changing state."""
        return self._plan(self._state(), connected, self.engine._now())

    def claim(self, connected):
        with self.store.transaction():
            state = self._state()
            now = self.engine._now()
            planned = self._plan(state, connected, now)
            if planned is None:
                return None
            token = uuid4().hex
            state['revision'] = min(MAX_COUNT, state['revision'] + 1)
            state['last_claim_at'] = now
            state['gaps'] = {}
            if planned['kind'] == 'warning':
                state['announced_reasons'] = planned['reasons']
                state['open_success_seq'] = state['success_seq']
            elif planned['kind'] == 'recovery':
                state['announced_reasons'] = []
                state['open_success_seq'] = None
            state['attempt'] = dict(token=token, status='sending', kind=planned['kind'],
                revision=state['revision'], started_at=now, finished_at=None)
            self._save(state)
            return {**planned, 'token': token, 'nonce': hashlib.sha256(('health:' + token).encode()).hexdigest()[:24],
                    'revision': state['revision']}

    def finish(self, token, success):
        if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token) or type(success) is not bool:
            raise ValueError('Invalid health delivery completion')
        with self.store.transaction():
            state = self._state()
            attempt = state['attempt']
            if attempt is None or attempt['token'] != token or attempt['status'] != 'sending':
                return False
            attempt['status'] = 'sent' if success else 'uncertain'
            attempt['finished_at'] = self.engine._now()
            self._save(state)
        return True
