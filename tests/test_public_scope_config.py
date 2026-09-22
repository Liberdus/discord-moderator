import hashlib
import json
import tomllib
import unittest
from dataclasses import asdict, replace

from liberdus_moderator.config import Config, ClassifierSettings
from liberdus_moderator.configure_jev import policy_text


def policy(**changes):
    return Config('1', '99', ('10', '11'), ('20',), ('98',), **changes)


class PublicScopeConfigTests(unittest.TestCase):
    def test_defaults_preserve_exact_legacy_policy_hash_for_both_schemas(self):
        for schema in (1, 2):
            with self.subTest(schema=schema):
                config = policy(schema_version=schema)
                legacy = asdict(config)
                del legacy['rules']['review_alert_role_id']  # Absent in the captured legacy schema.
                del legacy['explicit_channel_scope']
                del legacy['allow_public_deletion'], legacy['allow_public_monitored_channels'], legacy['excluded_category_ids'], legacy['included_category_ids']
                if schema == 1:
                    del legacy['classifier']
                else:
                    del legacy['classifier']['exempt_role_ids']
                expected = hashlib.sha256(json.dumps(legacy, sort_keys=True, separators=(',', ':'),
                                                      ensure_ascii=False).encode()).hexdigest()
                self.assertEqual(config.policy_hash, expected)
                # Captured from the unmodified 0.5.16 Config implementation.
                frozen = {1: '9565c5e66e72a64cb4e621b5de4e0ba1658d906b777c6c48aace851e8a92b14e',
                          2: '80672ebc5f52fb24fdb9174c4c8adfac0c42e2de4aae266a3ddaabb6f7fe113d'}
                self.assertEqual(config.policy_hash, frozen[schema])
                self.assertEqual(Config.from_dict(tomllib.loads(policy_text(config))), config)

    def test_opt_in_and_exclusions_bind_policy_hash_and_round_trip(self):
        baseline = policy(schema_version=2)
        config = replace(baseline, allow_public_monitored_channels=True, excluded_category_ids=('200', '201'))
        encoded = tomllib.loads(policy_text(config))
        self.assertTrue(encoded['allow_public_monitored_channels'])
        self.assertEqual(encoded['scope']['excluded_category_ids'], ['200', '201'])
        self.assertNotIn('excluded_category_ids', {key: value for key, value in encoded.items() if key != 'scope'})
        self.assertEqual(Config.from_dict(encoded), config)
        self.assertNotEqual(baseline.policy_hash, config.policy_hash)
        self.assertNotEqual(config.policy_hash, replace(config, excluded_category_ids=('200',)).policy_hash)
        self.assertNotEqual(baseline.policy_hash, replace(baseline, excluded_category_ids=('200',)).policy_hash)

    def test_public_opt_in_rejects_actions_even_if_runtime_toggles_would_be_off(self):
        with self.assertRaisesRegex(ValueError, 'actions_enabled = false'):
            policy(schema_version=2, allow_public_monitored_channels=True, actions_enabled=True)
        # Existing explicitly enabled private-pilot action policies are unchanged.
        self.assertTrue(policy(schema_version=2, actions_enabled=True).actions_enabled)

    def test_included_categories_round_trip_and_bind_scope_without_changing_default_hash(self):
        base=policy(schema_version=2,allow_public_monitored_channels=True,excluded_category_ids=('200',))
        config=replace(base,included_category_ids=('100','101'))
        self.assertEqual(Config.from_dict(tomllib.loads(policy_text(config))),config)
        self.assertNotEqual(base.policy_hash,config.policy_hash)
        self.assertNotEqual(config.policy_hash,replace(config,included_category_ids=('100',)).policy_hash)
        prior=asdict(base); del prior['allow_public_deletion'], prior['included_category_ids'], prior['explicit_channel_scope']; del prior['classifier']['exempt_role_ids']
        del prior['rules']['review_alert_role_id']
        expected=hashlib.sha256(json.dumps(prior,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(base.policy_hash,expected)
        for values in (('0',),('abc',),('100','100'),('200',),[True],'100'):
            with self.subTest(values=values),self.assertRaises(ValueError):
                replace(base,included_category_ids=values)

    def test_public_deletion_requires_explicit_actions_and_category_boundaries(self):
        base = policy(schema_version=2, allow_public_monitored_channels=True,
                      included_category_ids=('100',), excluded_category_ids=('200',))
        opted = replace(base, actions_enabled=True, allow_public_deletion=True)
        self.assertEqual(Config.from_dict(tomllib.loads(policy_text(opted))), opted)
        self.assertNotEqual(base.policy_hash, opted.policy_hash)
        for changes in ({'actions_enabled': False}, {'allow_public_monitored_channels': False},
                        {'included_category_ids': ()}, {'excluded_category_ids': ()},
                        {'allow_public_deletion': 'true'}, {'allow_public_deletion': 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(opted, **changes)

    def test_public_flag_requires_exact_boolean(self):
        for value in (1, 0, 'true', None, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy(allow_public_monitored_channels=value)

    def test_category_ids_are_bounded_unique_snowflakes(self):
        for value in (('0',), ('abc',), ('200', '200'), [True], '200', tuple(map(str, range(1, 502)))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy(excluded_category_ids=value)
        self.assertEqual(policy(excluded_category_ids=['200']).excluded_category_ids, ('200',))

    def test_scope_is_explicit_no_channel_wildcards_or_automatic_membership(self):
        config = policy(allow_public_monitored_channels=True, excluded_category_ids=('200',))
        self.assertEqual(config.monitored_channel_ids, ('10', '11'))
        data = tomllib.loads(policy_text(config))
        data['scope']['include_new_channels'] = True
        with self.assertRaises(ValueError):
            Config.from_dict(data)

    def test_new_fields_supported_in_schema_one_without_enabling_jev(self):
        config = policy(allow_public_monitored_channels=True, excluded_category_ids=('200',))
        self.assertEqual(config.schema_version, 1)
        self.assertEqual(config.classifier, ClassifierSettings())
        self.assertFalse(config.ai_enabled)
        self.assertEqual(Config.from_dict(tomllib.loads(policy_text(config))), config)


if __name__ == '__main__':
    unittest.main()
