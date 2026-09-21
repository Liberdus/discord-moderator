"""Isolated evaluation attempts sharing the live screening allowance, never its results.

The runner holds its own process lock while initializing/recovering this ledger.
Every reservation and settlement is one SQLite write transaction across all clients.
"""
import math
import re

from .classifier import MODEL, RESERVED_INPUT_TOKENS, RESERVED_MICROUSD, encoded
from .jev_batch import ERRORS, Ledger, run_name
from .screening import SCREENING_HASH, validated_saved
from .screening_diagnostics import DIAGNOSTIC_CODES, EXTRA_OUTCOMES

TABLE = 'screening_eval_attempts_v1'
DIAGNOSTICS_TABLE = 'screening_eval_diagnostics_v1'
RETRY_TABLE = 'screening_eval_retries_v1'
MAX_RECORDS = 1000
MAX_RESULT_BYTES = 8192
OUTCOMES = ERRORS | EXTRA_OUTCOMES | {'ok'}
_COLUMNS = ('run_id', 'request_hash', 'suite', 'case_name', 'fixture_hash', 'policy_hash',
            'model', 'rubric_hash', 'day', 'started_at', 'finished_at', 'outcome',
            'result_json', 'latency_ms')
_DIAGNOSTIC_COLUMNS = ('run_id', 'request_hash', 'code')
_RETRY_COLUMNS = ('retry_run_id', 'source_run_id', 'case_name', 'request_hash',
                  'fixture_hash', 'suite', 'model', 'rubric_hash')


def _identity(run_id, digest):
    if not isinstance(run_id, str):
        raise ValueError('Invalid evaluation run name')
    run_name(run_id)
    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('Expected a SHA-256 request hash')


def _timestamp(value):
    if type(value) not in (int, float) or not 0 < value <= 253402300799 or not math.isfinite(value):
        raise ValueError('Invalid evaluation timestamp')
    return float(value)


class ScreeningEvalLedger(Ledger):
    """Separate durable rows, shared screening calls/rate/spend, no live-worker lifecycle."""

    def initialize(self):
        with self.transaction():
            self.db.execute(f'''CREATE TABLE IF NOT EXISTS {TABLE}(
                run_id TEXT NOT NULL, request_hash TEXT NOT NULL, suite TEXT NOT NULL,
                case_name TEXT NOT NULL, fixture_hash TEXT NOT NULL, policy_hash TEXT NOT NULL,
                model TEXT NOT NULL, rubric_hash TEXT NOT NULL, day INTEGER NOT NULL,
                started_at REAL NOT NULL, finished_at REAL, outcome TEXT NOT NULL,
                result_json TEXT, latency_ms INTEGER,
                PRIMARY KEY(run_id, request_hash))''')
            if tuple(row['name'] for row in self.db.execute(f'PRAGMA table_info({TABLE})')) != _COLUMNS:
                raise ValueError('Unsupported screening evaluation schema')
            self._initialize_side_tables()
            # Caller owns the evaluation lock. Never recover the live worker's rows.
            self.db.execute(f"UPDATE {TABLE} SET outcome='uncertain' WHERE outcome='running'")

    def _initialize_side_tables(self):
        """Add optional metadata without changing the original attempts schema."""
        self.db.execute(f'''CREATE TABLE IF NOT EXISTS {DIAGNOSTICS_TABLE}(
            run_id TEXT NOT NULL, request_hash TEXT NOT NULL, code TEXT NOT NULL,
            PRIMARY KEY(run_id, request_hash))''')
        self.db.execute(f'''CREATE TABLE IF NOT EXISTS {RETRY_TABLE}(
            retry_run_id TEXT PRIMARY KEY, source_run_id TEXT NOT NULL, case_name TEXT NOT NULL,
            request_hash TEXT NOT NULL, fixture_hash TEXT NOT NULL, suite TEXT NOT NULL,
            model TEXT NOT NULL, rubric_hash TEXT NOT NULL)''')
        for table, columns in ((DIAGNOSTICS_TABLE, _DIAGNOSTIC_COLUMNS), (RETRY_TABLE, _RETRY_COLUMNS)):
            if tuple(row['name'] for row in self.db.execute(f'PRAGMA table_info({table})')) != columns:
                raise ValueError('Unsupported evaluation metadata schema')

    def diagnostic(self, run_id, digest):
        """Return a reviewed static code only; historical rows need no side table."""
        _identity(run_id, digest)
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (DIAGNOSTICS_TABLE,)).fetchone():
            return None
        row = self.db.execute(f"SELECT CASE WHEN typeof(code)='text' AND length(code)<=128 THEN code ELSE NULL END "
                              f'FROM {DIAGNOSTICS_TABLE} WHERE run_id=? AND request_hash=?', (run_id, digest)).fetchone()
        return row[0] if row and row[0] in DIAGNOSTIC_CODES else None

    def retry_binding(self, run_id):
        from .screening_cases import SUITE
        _identity(run_id, '0' * 64)
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (RETRY_TABLE,)).fetchone():
            return None
        row = self.db.execute(f'SELECT * FROM {RETRY_TABLE} WHERE retry_run_id=?', (run_id,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        _identity(value['source_run_id'], value['request_hash'])
        if (value['retry_run_id'] != run_id or value['source_run_id'] == run_id
                or not isinstance(value['case_name'], str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', value['case_name'])
                or not isinstance(value['fixture_hash'], str) or not re.fullmatch(r'[a-f0-9]{64}', value['fixture_hash'])
                or value['suite'] != SUITE or value['model'] != MODEL or value['rubric_hash'] != SCREENING_HASH):
            raise ValueError('Invalid evaluation retry binding')
        return value

    @staticmethod
    def _fixture(case):
        if (not isinstance(case.name, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', case.name)
                or not isinstance(case.fixture_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', case.fixture_hash)):
            raise ValueError('Invalid evaluation fixture metadata')

    def bind_retry(self, source_run_id, retry_run_id, case, digest):
        """Bind an explicitly named, single-case retry before reserving any credits."""
        from .screening_cases import SUITE
        _identity(source_run_id, digest)
        _identity(retry_run_id, digest)
        self._fixture(case)
        if source_run_id == retry_run_id:
            raise ValueError('Retry run must differ from its source')
        expected = dict(retry_run_id=retry_run_id, source_run_id=source_run_id, case_name=case.name,
                        request_hash=digest, fixture_hash=case.fixture_hash, suite=SUITE,
                        model=MODEL, rubric_hash=SCREENING_HASH)
        with self.transaction():
            source = self.row(source_run_id, digest)
            if (source is None or source['outcome'] not in OUTCOMES - {'ok'}
                    or any(source[name] != expected[name] for name in
                           ('case_name', 'fixture_hash', 'suite', 'model', 'rubric_hash'))):
                raise ValueError('Retry requires a matching saved failed evaluation')
            self._initialize_side_tables()
            # A failed retry may be explicitly retried under another new name,
            # but a baseline cannot be rebound as its own descendant.
            ancestor, seen = source_run_id, set()
            while ancestor is not None:
                if ancestor == retry_run_id or ancestor in seen or len(seen) >= MAX_RECORDS:
                    raise ValueError('Invalid cyclic retry ancestry')
                seen.add(ancestor)
                prior = self.retry_binding(ancestor)
                ancestor = prior['source_run_id'] if prior else None
            existing = self.retry_binding(retry_run_id)
            if existing is not None and existing != expected:
                raise ValueError('Retry run is already bound to a different source or case')
            rows = self.db.execute(f'SELECT request_hash,case_name,fixture_hash,suite,model,rubric_hash FROM {TABLE} WHERE run_id=?',
                                   (retry_run_id,)).fetchall()
            if existing is None and rows:
                raise ValueError('Retry run must be new or already bound to this failed case')
            if any(any(row[name] != expected[name] for name in row.keys()) for row in rows):
                raise ValueError('Retry run already contains unrelated attempts')
            if existing is not None:
                return existing
            if self.db.execute(f'SELECT count(*) FROM {RETRY_TABLE}').fetchone()[0] >= MAX_RECORDS:
                raise ValueError('Retry binding history is full')
            self.db.execute(f'INSERT INTO {RETRY_TABLE} VALUES(?,?,?,?,?,?,?,?)',
                            tuple(expected[name] for name in _RETRY_COLUMNS))
            return expected

    def row(self, run_id, digest):
        _identity(run_id, digest)
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone():
            return None
        return self.db.execute(
            f'SELECT run_id,request_hash,suite,case_name,fixture_hash,policy_hash,model,rubric_hash,day,'
            f'started_at,finished_at,outcome,latency_ms,CASE WHEN typeof(result_json)=\'text\' '
            f'AND length(CAST(result_json AS BLOB))<={MAX_RESULT_BYTES} THEN result_json ELSE NULL END AS result_json '
            f'FROM {TABLE} WHERE run_id=? AND request_hash=?', (run_id, digest)).fetchone()

    def _counter(self, name, default=0):
        try:
            value = self.number(name, default)
        except (OverflowError, TypeError) as error:
            raise ValueError('Invalid shared budget accounting') from error
        if type(value) is not int or value > 2**63 - 1:
            raise ValueError('Invalid shared budget accounting')
        return value

    def _accounting(self, default_day):
        values = {name: self._counter('screening_' + name) for name in
                  ('daily_calls', 'total_calls', 'daily_reserved_microusd', 'total_reserved_microusd')}
        values['day'] = self._counter('screening_budget_day', default_day)
        if (values['daily_calls'] > values['total_calls']
                or values['daily_reserved_microusd'] > values['total_reserved_microusd']):
            raise ValueError('Invalid shared budget accounting')
        return values

    def gate(self, config):
        if not config.ai_enabled or config.classifier.mode != 'report_only':
            return 'report_only_required'
        if self.get_setting('policy_hash') != config.policy_hash:
            return 'policy_changed'
        schema = self.get_setting('screening_schema_version')
        if type(schema) is not int or schema != 1:
            return 'unsupported_screening_schema'
        for setting, reason in (('paused', 'moderation_paused'), ('screening_billing_guard', 'billing_guard')):
            value = self.get_setting(setting, False)
            if type(value) is not bool:
                return 'invalid_shared_state'
            if value:
                return reason
        return None

    def reserve(self, run_id, case, digest, config, now):
        from .screening_cases import SUITE
        _identity(run_id, digest)
        now = _timestamp(now)
        self._fixture(case)
        with self.transaction():
            binding = self.retry_binding(run_id)
            if binding is not None and (binding['case_name'] != case.name or binding['request_hash'] != digest
                                        or binding['fixture_hash'] != case.fixture_hash):
                raise ValueError('Retry run is restricted to its bound failed case')
            if self.row(run_id, digest) is not None:
                return 'cached'
            reason = self.gate(config)
            if reason:
                return reason
            day = int(now // 86400)
            values = self._accounting(day)
            try:
                last = self.number('screening_last_attempt_at')
            except (OverflowError, TypeError) as error:
                raise ValueError('Invalid shared budget accounting') from error
            if last > 253402300799:
                raise ValueError('Invalid shared budget accounting')
            if day < values['day'] or now < last:
                return 'clock_rollback'
            if now < last + config.classifier.min_interval_seconds:
                return 'wait'
            if self.db.execute(f'SELECT count(*) FROM {TABLE}').fetchone()[0] >= MAX_RECORDS:
                return 'history_full'
            daily = values['daily_calls'] if day == values['day'] else 0
            daily_reserved = values['daily_reserved_microusd'] if day == values['day'] else 0
            settings = config.classifier
            if (daily >= settings.max_daily_calls or values['total_calls'] >= settings.max_total_calls
                    or daily_reserved + RESERVED_MICROUSD > settings.daily_budget_microusd
                    or values['total_reserved_microusd'] + RESERVED_MICROUSD > settings.total_budget_microusd):
                return 'budget_exhausted'
            self.db.execute(f"INSERT INTO {TABLE} VALUES(?,?,?,?,?,?,?,?,?,?,NULL,'running',NULL,NULL)",
                (run_id, digest, SUITE, case.name, case.fixture_hash, config.policy_hash,
                 MODEL, SCREENING_HASH, day, now))
            for name, value in (
                ('budget_day', day), ('daily_calls', daily + 1),
                ('daily_reserved_microusd', daily_reserved + RESERVED_MICROUSD),
                ('total_calls', values['total_calls'] + 1),
                ('total_reserved_microusd', values['total_reserved_microusd'] + RESERVED_MICROUSD),
                ('last_attempt_at', now),
            ):
                self.set_setting('screening_' + name, value)
        return 'reserved'

    def finish(self, run_id, digest, outcome, now, latency, result=None, *, diagnostic=None):
        _identity(run_id, digest)
        now = _timestamp(now)
        if outcome not in OUTCOMES:
            raise ValueError('Invalid evaluation outcome')
        if type(latency) is not int or not 0 <= latency <= 86400000:
            raise ValueError('Invalid evaluation latency')
        with self.transaction():
            row = self.row(run_id, digest)
            if row is None or row['outcome'] != 'running':
                return  # Duplicate/crash completion can never settle a second time.
            if diagnostic is not None and (not isinstance(diagnostic, str) or diagnostic not in DIAGNOSTIC_CODES):
                raise ValueError('Invalid evaluation diagnostic code')
            valid, amount = None, None
            if result is not None:
                try:
                    valid = validated_saved(result)
                except (KeyError, TypeError, ValueError, RecursionError) as error:
                    raise ValueError('Invalid saved screening result') from error
                amount = (valid['input_tokens'] * 42 + 999) // 1000
                valid['estimated_microusd'] = amount
                if valid['input_tokens'] > RESERVED_INPUT_TOKENS:
                    outcome = 'usage_exceeds_reservation'
            elif outcome == 'ok':
                raise ValueError('A successful evaluation requires a validated result')
            result_json = encoded(valid).decode() if valid is not None else None
            if result_json is not None and len(result_json.encode('utf-8')) > MAX_RESULT_BYTES:
                raise ValueError('Saved screening result is too large')
            if valid is not None:
                values = self._accounting(row['day'])
                if (values['total_reserved_microusd'] < RESERVED_MICROUSD
                        or (values['day'] == row['day'] and values['daily_reserved_microusd'] < RESERVED_MICROUSD)):
                    raise ValueError('Shared reservation accounting is inconsistent')
                adjustment = amount - RESERVED_MICROUSD
                self.set_setting('screening_total_reserved_microusd', values['total_reserved_microusd'] + adjustment)
                if values['day'] == row['day']:
                    self.set_setting('screening_daily_reserved_microusd', values['daily_reserved_microusd'] + adjustment)
            self.db.execute(f'UPDATE {TABLE} SET finished_at=?,outcome=?,result_json=?,latency_ms=? '
                            "WHERE run_id=? AND request_hash=? AND outcome='running'",
                            (now, outcome, result_json, latency, run_id, digest))
            if diagnostic is not None:
                self._initialize_side_tables()
                self.db.execute(f'INSERT INTO {DIAGNOSTICS_TABLE} VALUES(?,?,?)', (run_id, digest, diagnostic))
            if outcome == 'usage_exceeds_reservation':
                self.set_setting('screening_billing_guard', True)
