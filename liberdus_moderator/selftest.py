"""Bounded smoke checks with fixed fixtures and disposable in-memory state.

No live policy, database, Discord client, credentials, or provider is accepted.
These checks exercise code behavior, not live Discord delivery or a real restart.
"""

from dataclasses import replace
import json

from .config import Config, StorageSettings
from .engine import Engine
from .models import MessageEvent
from .storage import Store


def _require(condition):
    # Keep checks active under python -O as well.
    if not condition:
        raise ValueError("Self-test expectation failed")


class _Fixture:
    def __init__(self):
        from .live import LiveSession

        self.now = 1000.0
        self.config = Config(
            "1", "99", ("10", "11", "12"), ("20",), ("98",),
            storage=StorageSettings(database_path=":memory:"), schema_version=2,
        )
        self.store = Store(":memory:")
        try:
            self.engine = Engine(self.config, self.store, clock=lambda: self.now)
            self.live = LiveSession(self.engine)
        except BaseException:
            self.store.close()
            raise

    def event(self, identity=100, channel="10"):
        return MessageEvent("1", channel, str(identity), "50",
                            "Synthetic moderation self-test repeated message.", self.now)

    def pattern(self):
        events = [self.event(100 + i, channel) for i, channel in enumerate(("10", "11", "12"))]
        return events, [self.engine.process(event) for event in events]

    def incident(self, rule):
        incidents = self.store.incidents()
        _require(len(incidents) == 1 and incidents[0]["rule_id"] == rule)
        return incidents[0]


def _cross_channel(fixture):
    _, results = fixture.pattern()
    incident = fixture.incident("cross_channel_repeat")
    _require(incident["status"] == "open" and len(incident["evidence"]) == 3)
    _require(len(results[-1]["new_incident_ids"]) == 1)
    _require(len(fixture.store.reports()) == 1)


def _same_channel(fixture):
    for identity in range(100, 104):
        fixture.engine.process(fixture.event(identity))
    incident = fixture.incident("same_channel_repeat")
    _require(incident["status"] == "open" and len(incident["evidence"]) == 4)


def _edit(fixture):
    events, _ = fixture.pattern()
    fixture.now += 1
    fixture.engine.process(replace(events[-1], content="This synthetic message was corrected.",
                                   edited_at=fixture.now))
    incident = fixture.incident("cross_channel_repeat")
    _require(incident["status"] == "withdrawn" and incident["revision"] == 2)
    _require(len(incident["evidence"]) == 3 and not fixture.store.reports())


def _deletion_reset(fixture):
    fixture.pattern()
    # Invoke the same state transition used after a Discord delete callback.
    # Receiving that callback over Discord remains a separate live check.
    fixture.live.gap("deleted_message")
    status = fixture.live.status(False)
    _require(status["message_count"] == 0 and status["pending_reports"] == 0)
    _require(status["coverage_gaps"] == 1
             and status["last_coverage_gap"]["reason"] == "deleted_message")
    _require(fixture.incident("cross_channel_repeat")["status"] == "needs_revalidation")


def _pause_resume(fixture):
    fixture.engine.set_paused(True)
    _, results = fixture.pattern()
    _require(all(result["reason"] == "moderation_paused" for result in results))
    _require(fixture.engine.status()["message_count"] == 0 and not fixture.store.incidents())
    fixture.now += 1
    fixture.engine.set_paused(False)
    fixture.pattern()
    _require(fixture.incident("cross_channel_repeat")["status"] == "open")


def _authorization(fixture):
    from .commands import CommandRequest

    request = CommandRequest("1", "20", "98", "pause")
    for wrong in (replace(request, guild_id="2"), replace(request, channel_id="10"),
                  replace(request, user_id="50")):
        _require(fixture.live.command(wrong, "500", False) is None)
        _require(not fixture.engine.status()["paused"])
    _require(fixture.live.command(request, "500", False) is not None)
    _require(fixture.engine.status()["paused"])
    _require(fixture.live.command(replace(request, command="resume"), "500", False) is None)
    _require(fixture.engine.status()["paused"])


def _exclusions(fixture):
    event = fixture.event()
    for change in ({"author_id": "99"}, {"is_bot": True}, {"is_webhook": True},
                   {"channel_id": "20"}, {"channel_id": "77"}, {"guild_id": "2"}):
        _require(fixture.engine.process(replace(event, **change))["disposition"] == "ignored")
    _require(fixture.engine.status()["message_count"] == 0 and not fixture.store.incidents())


def _duplicate_delivery(fixture):
    events, _ = fixture.pattern()
    report = fixture.live.claim_report()
    _require(report is not None and report["payload"]["channel_id"] == "20")
    fixture.live.finish_report(report["id"], "800")
    for event in events:
        _require(fixture.engine.process(event)["reason"] == "duplicate_event")
    _require(fixture.live.claim_report() is None and len(fixture.store.incidents()) == 1)
    _require(fixture.store.db.execute("SELECT count(*) FROM deliveries").fetchone()[0] == 1)


def _session_recovery(fixture):
    from .live import LiveSession

    fixture.pattern()
    _require(fixture.live.claim_report() is not None)
    fixture.engine.set_paused(True)
    # Recreate the session on retained in-memory state; no gateway restart.
    recovered = LiveSession(Engine(fixture.config, fixture.store, clock=lambda: fixture.now))
    status = recovered.status(False)
    _require(status["paused"] and status["message_count"] == 0 and status["uncertain_reports"] == 1)
    _require(recovered.claim_report() is None)


_CHECKS = (
    ("Cross-channel repetition", _cross_channel),
    ("Same-channel repetition", _same_channel),
    ("Edit withdrawal", _edit),
    ("Deletion reset logic", _deletion_reset),
    ("Pause/resume logic", _pause_resume),
    ("Command authorization", _authorization),
    ("Bot and scope exclusions", _exclusions),
    ("Duplicate delivery prevention", _duplicate_delivery),
    ("Session recovery (simulated)", _session_recovery),
)


def run_selftest():
    checks = []
    for name, check in _CHECKS:
        fixture = None
        passed = False
        try:
            fixture = _Fixture()
            check(fixture)
            passed = True
        except Exception:
            # Only fixed labels leave the runner; never expose exception data.
            pass
        finally:
            if fixture is not None:
                try:
                    fixture.store.close()
                except Exception:
                    passed = False
        checks.append({"name": name, "passed": passed})
    return {"scope": "synthetic_default_policy", "passed": all(item["passed"] for item in checks),
            "checks": checks, "ai_calls": 0, "public_actions": []}


def format_summary(result):
    passed = sum(item["passed"] for item in result["checks"])
    lines = [f"Liberdus self-test: {passed}/{len(result['checks'])} passed",
             "Synthetic fixtures; default test policy; separate in-memory state."]
    lines += [f"{'PASS' if item['passed'] else 'FAIL'}: {item['name']}" for item in result["checks"]]
    lines += ["Live Discord events, permissions and actual restart: not tested.",
              "Self-test AI calls: 0 | Public actions: 0"]
    return "\n".join(lines)


if __name__ == "__main__":
    result = run_selftest()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
