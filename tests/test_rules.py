from dataclasses import replace
import unittest

from liberdus_moderator.config import Config, CrosspostException, RuleSettings
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.rules import content_fingerprint, find_matches, normalize_content


TEXT = "Here is our repeated promotional message"


def config(**rule_changes):
    return Config(guild_id="1", bot_user_id="99", monitored_channel_ids=("10", "11", "12", "13"), command_channel_ids=("20",), operator_user_ids=("90",), rules=RuleSettings(**rule_changes))


def row(message_id, channel="10", author="2", content=TEXT, created=100, **changes):
    event = MessageEvent(guild_id="1", channel_id=channel, message_id=str(message_id), author_id=author, content=content, created_at=created, **changes)
    result = event.to_dict()
    result.update(version=event.version, fingerprint=content_fingerprint(content))
    return result


def by_rule(matches, rule):
    return [match for match in matches if match.rule_id == rule]


class NormalizationTests(unittest.TestCase):
    def test_case_whitespace_and_unicode_prose(self):
        self.assertEqual(normalize_content("  HELLO\tthere\n Cafe\u0301  "), "hello there café")
        self.assertEqual(content_fingerprint("HELLO  there"), content_fingerprint("hello there"))

    def test_full_urls_keep_case_paths_queries_and_encoding(self):
        original = "VISIT https://Site.invalid/Path?Token=AbC NOW"
        self.assertEqual(normalize_content(original), "visit https://Site.invalid/Path?Token=AbC now")
        for other in ("visit https://site.invalid/Path?Token=AbC now", "visit https://Site.invalid/path?Token=AbC now", "visit https://Site.invalid/Path?Token=abc now", "visit https://Site.invalid/Path?Token=%41bC now"):
            with self.subTest(other=other):
                self.assertNotEqual(content_fingerprint(original), content_fingerprint(other))


class RepeatTests(unittest.TestCase):
    def test_three_distinct_channels_form_single_stable_incident(self):
        rows = [row(1, "10", created=100), row(2, "11", content=TEXT.upper(), created=110), row(3, "12", content=TEXT.replace(" ", "  "), created=120)]
        matches = find_matches(config(), rows, 130)
        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertEqual(match.rule_id, "cross_channel_repeat")
        self.assertEqual(len(match.evidence), 3)
        self.assertEqual(match.expires_at, 220)
        added = find_matches(config(), rows + [row(4, "13", created=129)], 130)[0]
        self.assertEqual(added.group_key, match.group_key)
        self.assertEqual(added.expires_at, 230)
        self.assertEqual(find_matches(config(), list(reversed(rows)), 130), matches)

    def test_same_channel_copies_do_not_meet_distinct_channel_threshold(self):
        rows = [row(index, created=100 + index) for index in range(1, 5)]
        matches = find_matches(config(), rows, 110)
        self.assertEqual(len(by_rule(matches, "cross_channel_repeat")), 0)
        self.assertEqual(len(by_rule(matches, "same_channel_repeat")), 1)
        self.assertEqual(matches[0].expires_at, 131)

    def test_actors_are_never_combined(self):
        rows = [row(1, "10", author="2"), row(2, "11", author="3"), row(3, "12", author="4")]
        self.assertEqual(find_matches(config(), rows, 110), [])

    def test_replays_do_not_increase_count_and_edit_replaces_contribution(self):
        original = row(1, "10")
        rows = [original] * 5 + [row(2, "11")]
        self.assertEqual(find_matches(config(), rows, 110), [])
        rows.append(row(3, "12"))
        self.assertEqual(len(find_matches(config(), rows, 110)), 1)
        rows.append(row(3, "12", content="Entirely different edited content", edited_at=105))
        self.assertEqual(find_matches(config(), rows, 110), [])

    def test_original_timestamps_define_half_open_window(self):
        rows = [row(1, "10", created=100, edited_at=219), row(2, "11", created=110), row(3, "12", created=120)]
        self.assertEqual(len(find_matches(config(), rows, 219)), 1)
        self.assertEqual(find_matches(config(), rows, 220), [])

    def test_latest_post_per_channel_determines_pattern_expiry(self):
        rows = [row(1, "10", created=100), row(2, "11", created=110), row(3, "12", created=120), row(4, "10", created=130)]
        self.assertEqual(find_matches(config(), rows, 140)[0].expires_at, 230)

    def test_scoped_exception_requires_author_channel_and_exact_text(self):
        exception = CrosspostException(author_ids=("2",), channel_ids=("10", "11", "12"), content=TEXT)
        conf = config(approved_crossposts=(exception,))
        rows = [row(1, "10"), row(2, "11"), row(3, "12")]
        self.assertEqual(find_matches(conf, rows, 110), [])
        other_author = [dict(item, author_id="3") for item in rows]
        self.assertEqual(len(find_matches(conf, other_author, 110)), 1)
        other_text = [dict(item, content=TEXT + " different") for item in rows]
        self.assertEqual(len(find_matches(conf, other_text, 110)), 1)
        conf = config(approved_crossposts=(replace(exception, channel_ids=("13",)),))
        self.assertEqual(len(find_matches(conf, rows, 110)), 1)

    def test_exception_does_not_disable_local_repeat_or_blocked_domain(self):
        text = TEXT + " https://scam.invalid"
        conf = config(blocked_domains=("scam.invalid",), approved_crossposts=(CrosspostException(("2",), ("10", "11", "12"), text),))
        rows = [row(i, content=text) for i in range(1, 5)] + [row(5, "11", content=text), row(6, "12", content=text)]
        matches = find_matches(conf, rows, 110)
        self.assertEqual(len(by_rule(matches, "cross_channel_repeat")), 0)
        self.assertEqual(len(by_rule(matches, "same_channel_repeat")), 1)
        self.assertEqual(len(by_rule(matches, "blocked_domain")), 6)

    def test_generic_and_url_only_text_cannot_trigger_repeats(self):
        for text in ("hello", "GM everyone", "https://very-long-domain.invalid/same-link", "<https://one.invalid> https://two.invalid"):
            rows = [row(i, channel=channel, content=text) for i, channel in enumerate(("10", "11", "12"), 1)]
            with self.subTest(text=text):
                self.assertEqual(find_matches(config(), rows, 110), [])

    def test_scope_future_rows_bots_webhooks_threads_and_off_mode(self):
        rows = [row(1, "10"), row(2, "11"), row(3, "12")]
        for change in ({"guild_id": "9"}, {"channel_id": "20"}, {"author_id": "99"}, {"is_bot": True}, {"is_webhook": True}, {"is_thread": True}, {"created_at": 111}, {"edited_at": 111}):
            with self.subTest(change=change):
                self.assertEqual(find_matches(config(), rows[:2] + [dict(rows[2], **change)], 110), [])
        self.assertEqual(find_matches(replace(config(), mode="off"), rows, 110), [])


class BlockedDomainTests(unittest.TestCase):
    def test_exact_hostname_matching_without_suffix_or_substring_matches(self):
        conf = config(blocked_domains=("scam.invalid",))
        for text in ("https://scam.invalid", "HTTPS://SCAM.INVALID/a?b=1", "(https://scam.invalid).", "https://scam.invalid:8443/a", "https://safe.invalid@scam.invalid/path", "https://scam.invalid./a"):
            with self.subTest(text=text):
                self.assertEqual(len(find_matches(conf, [row(1, content=text)], 110)), 1)
        for text in ("https://notscam.invalid", "https://scam.invalid.evil.invalid", "https://sub.scam.invalid", "https://safe.invalid/?next=https%3A%2F%2Fscam.invalid", "https://scam.invalid@safe.invalid", "scam.invalid", "ftp://scam.invalid", "https://[invalid"):
            with self.subTest(text=text):
                self.assertEqual(find_matches(conf, [row(1, content=text)], 110), [])

    def test_multiple_blocked_urls_make_one_message_incident(self):
        conf = config(blocked_domains=("scam.invalid", "other.invalid"))
        matches = find_matches(conf, [row(1, content="https://scam.invalid https://other.invalid")], 110)
        self.assertEqual(len(matches), 1)
        self.assertEqual(len(matches[0].evidence), 1)
        self.assertIn("other.invalid, scam.invalid", matches[0].reason)
        self.assertEqual(matches[0].expires_at, 100 + conf.storage.retention_seconds)


if __name__ == "__main__":
    unittest.main()
