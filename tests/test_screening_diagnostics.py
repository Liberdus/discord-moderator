import asyncio
from copy import deepcopy
import json
import math
import ssl
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from liberdus_moderator.classifier import MODEL, RUBRIC
from liberdus_moderator.jev import ProviderError
from liberdus_moderator.screening import CONCERN, validate_screening
from liberdus_moderator.screening_diagnostics import (
    DIAGNOSTICS, DIAGNOSTIC_CODES, EXTRA_OUTCOMES, EvaluationError,
    failure_diagnostic, validate_evaluation,
)

SECRET = "SENTINEL-KEY-HEADER-RESPONSE-NOT-TO-BE-SAVED"


def response(purpose="other", concern="sensitive_request"):
    def answer(choice, rubric):
        return {"type": "choice", "choice": choice, "confidence": .99,
                "probabilities": {label: float(label == choice) for label in rubric["criteria"]}}
    return {"model": MODEL, "answers": {"context": answer(purpose, RUBRIC),
            "concern": answer(concern, CONCERN)}, "usage": {"input_tokens": 650, "output_tokens": 75}}


class ValidationDiagnosticTests(unittest.TestCase):
    def rejected(self, raw, diagnostic):
        with self.assertRaises((ValueError, OverflowError)):
            validate_screening(raw)
        with self.assertRaises(EvaluationError) as caught:
            validate_evaluation(raw)
        error = caught.exception
        self.assertEqual(error.outcome, "invalid_response")
        self.assertEqual(error.diagnostic, diagnostic)
        self.assertEqual(str(error), diagnostic)
        self.assertIn(diagnostic, DIAGNOSTIC_CODES)
        self.assertNotIn(SECRET, str(error))
        self.assertEqual(failure_diagnostic(error, "validation"), ("invalid_response", diagnostic))

    def test_all_valid_labels_are_unchanged_and_extra_fields_are_ignored(self):
        for purpose in RUBRIC["criteria"]:
            for concern in CONCERN["criteria"]:
                raw = response(purpose, concern)
                raw["unrecognized"] = {"secret": SECRET}
                raw["answers"]["context"]["extra"] = SECRET
                raw["answers"]["concern"]["extra"] = SECRET
                raw["usage"]["extra"] = SECRET
                before = deepcopy(raw)
                result = validate_evaluation(raw)
                self.assertEqual(result, validate_screening(raw))
                self.assertEqual(raw, before)
                self.assertNotIn(SECRET, json.dumps(result))

    def test_production_validator_is_the_only_acceptance_gate(self):
        accepted = {"trusted": "result"}
        with patch("liberdus_moderator.screening_diagnostics.validate_screening", return_value=accepted) as validator, \
             patch("liberdus_moderator.screening_diagnostics._diagnose_rejection", side_effect=AssertionError):
            self.assertIs(validate_evaluation(response()), accepted)
            validator.assert_called_once()
        with patch("liberdus_moderator.screening_diagnostics.validate_screening", side_effect=ValueError(SECRET)):
            with self.assertRaises(EvaluationError) as caught:
                validate_evaluation(response())
            self.assertEqual(str(caught.exception), "response.unclassified")
            self.assertTrue(caught.exception.__suppress_context__)

    def test_response_model_and_answers_shapes(self):
        for raw in (None, [], SECRET, 10):
            self.rejected(raw, "response.shape")
        for value in (None, "different-model", SECRET, False):
            raw = response(); raw["model"] = value
            self.rejected(raw, "response.model")
        for value in (None, [], SECRET):
            raw = response(); raw["answers"] = value
            self.rejected(raw, "response.answers")
        raw = response(); del raw["answers"]
        self.rejected(raw, "response.answers")

    def test_answer_shape_type_and_choice(self):
        for prefix in ("context", "concern"):
            for value in (None, [], SECRET):
                raw = response(); raw["answers"][prefix] = value
                self.rejected(raw, prefix + ".shape")
            raw = response(); del raw["answers"][prefix]
            self.rejected(raw, prefix + ".shape")
            for value in (None, "score", SECRET):
                raw = response(); raw["answers"][prefix]["type"] = value
                self.rejected(raw, prefix + ".type")
            for value in (None, [], SECRET, True):
                raw = response(); raw["answers"][prefix]["choice"] = value
                self.rejected(raw, prefix + ".choice")

    def test_confidence_rejects_boolean_nonfinite_and_out_of_range(self):
        for prefix in ("context", "concern"):
            for value in (True, False, math.nan, math.inf, -math.inf, -0.1, 1.1, 10**500, SECRET, None):
                raw = response(); raw["answers"][prefix]["confidence"] = value
                self.rejected(raw, prefix + ".confidence")
            raw = response(); del raw["answers"][prefix]["confidence"]
            self.rejected(raw, prefix + ".confidence")

    def test_probability_shapes_labels_and_values(self):
        for prefix in ("context", "concern"):
            for value in (None, [], SECRET, {"unknown": 1}, {}):
                raw = response(); raw["answers"][prefix]["probabilities"] = value
                self.rejected(raw, prefix + ".probabilities")
            for value in (True, math.nan, math.inf, -0.01, 1.01, 10**500, SECRET, None):
                raw = response(); answer = raw["answers"][prefix]
                answer["probabilities"][answer["choice"]] = value
                self.rejected(raw, prefix + ".probabilities")

    def test_probability_sums_and_choice_maximum_are_distinct(self):
        for prefix in ("context", "concern"):
            raw = response(); answer = raw["answers"][prefix]
            answer["probabilities"][answer["choice"]] = .5
            self.rejected(raw, prefix + ".probability_sum")
            raw = response(); answer = raw["answers"][prefix]
            other = next(label for label in answer["probabilities"] if label != answer["choice"])
            answer["probabilities"][answer["choice"]] = .4
            answer["probabilities"][other] = .6
            self.rejected(raw, prefix + ".choice_not_maximum")

    def test_live_probability_tolerances_and_usage_boundaries_are_preserved(self):
        for prefix in ("context", "concern"):
            raw = response(); answer = raw["answers"][prefix]
            answer["probabilities"][answer["choice"]] = .9995
            answer["confidence"] = 0
            self.assertEqual(validate_evaluation(raw), validate_screening(raw))
            raw = response(); answer = raw["answers"][prefix]
            other = next(label for label in answer["probabilities"] if label != answer["choice"])
            answer["probabilities"][answer["choice"]] = .49999975
            answer["probabilities"][other] = .50000025
            self.assertEqual(validate_evaluation(raw), validate_screening(raw))
        for value in (0, 1000000):
            raw = response(); raw["usage"] = {"input_tokens": value, "output_tokens": value}
            self.assertEqual(validate_evaluation(raw), validate_screening(raw))

    def test_usage_shape_and_counts(self):
        for value in (None, [], SECRET):
            raw = response(); raw["usage"] = value
            self.rejected(raw, "usage.shape")
        for field in ("input_tokens", "output_tokens"):
            for value in (True, False, -1, 1000001, .5, math.nan, math.inf, SECRET, None):
                raw = response(); raw["usage"][field] = value
                self.rejected(raw, "usage." + field)
            raw = response(); del raw["usage"][field]
            self.rejected(raw, "usage." + field)

    def test_evaluation_error_never_accepts_an_arbitrary_diagnostic(self):
        for value in (SECRET, None, [], {"secret": SECRET}):
            error = EvaluationError(value)
            self.assertEqual(str(error), "response.unclassified")
            self.assertNotIn(SECRET, repr(error))
        self.assertEqual(DIAGNOSTIC_CODES, frozenset(DIAGNOSTICS))
        self.assertEqual(EXTRA_OUTCOMES, {"invalid_response", "network_error", "internal_error"})
        self.assertTrue(all(type(reason) is str and reason for reason in DIAGNOSTICS.values()))
        changed = EvaluationError("context.type")
        changed.outcome, changed.diagnostic = SECRET, {"secret": SECRET}
        self.assertEqual(failure_diagnostic(changed), ("invalid_response", "response.unclassified"))


class TransportDiagnosticTests(unittest.TestCase):
    def checked(self, error, expected, phase="request"):
        result = failure_diagnostic(error, phase)
        self.assertEqual(result, expected)
        self.assertIn(result[1], DIAGNOSTIC_CODES)
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn(SECRET, DIAGNOSTICS[result[1]])
        return result

    def test_timeout_interruption_and_validation_are_fixed(self):
        self.checked(TimeoutError(SECRET), ("timeout", "request.timeout"))
        self.checked(asyncio.CancelledError(SECRET), ("uncertain", "request.interrupted"))
        self.checked(EvaluationError("context.confidence"), ("invalid_response", "context.confidence"), "validation")
        self.checked(ValueError(SECRET), ("internal_error", "internal.validation"), "validation")
        self.checked(RuntimeError(SECRET), ("internal_error", "internal.unexpected"), SECRET)

    def test_fixed_provider_failures_preserve_outcome_without_retaining_details(self):
        for code in ("missing_key", "authentication_failed", "access_denied", "rate_limited",
                     "provider_overloaded", "http_error", "response_too_large", "invalid_json"):
            self.checked(ProviderError(code), (code, "provider." + code))
        self.checked(ProviderError("stale"), ("stale", "state.stale"))
        self.checked(ProviderError("usage_exceeds_reservation"),
                     ("usage_exceeds_reservation", "usage.reservation"))
        self.assertEqual(DIAGNOSTICS["usage.reservation"],
                         "Validated input token usage exceeded the reserved input bound.")
        for error in (ProviderError(SECRET), ProviderError("http_error", SECRET), ProviderError()):
            self.checked(error, ("provider_or_response_error", "provider.unclassified"))

    def test_aiohttp_is_optional_and_never_needed_for_validation(self):
        with patch.dict("sys.modules", aiohttp=None):
            self.checked(OSError(SECRET), ("network_error", "network.io"))
            self.checked(RuntimeError(SECRET), ("internal_error", "internal.request"))
            self.checked(ssl.SSLError(SECRET), ("network_error", "network.tls"))
            self.assertEqual(validate_evaluation(response()), validate_screening(response()))

    def test_aiohttp_class_groups_use_fixed_codes_without_exception_strings(self):
        class ClientError(Exception):
            def __str__(self):
                raise AssertionError("Exception text must never be read")
        class ClientConnectionError(ClientError): pass
        class ClientSSLError(ClientConnectionError): pass
        class ClientConnectorCertificateError(ClientSSLError): pass
        class ServerFingerprintMismatch(ClientConnectionError): pass
        class ClientPayloadError(ClientError): pass
        class ServerTimeoutError(ClientConnectionError, TimeoutError): pass
        module = SimpleNamespace(ClientError=ClientError, ClientConnectionError=ClientConnectionError,
            ClientSSLError=ClientSSLError, ClientConnectorCertificateError=ClientConnectorCertificateError,
            ServerFingerprintMismatch=ServerFingerprintMismatch, ClientPayloadError=ClientPayloadError)
        with patch.dict("sys.modules", aiohttp=module):
            for cls, code in ((ClientError, "network.client"), (ClientConnectionError, "network.connection"),
                              (ClientSSLError, "network.tls"), (ClientConnectorCertificateError, "network.tls"),
                              (ServerFingerprintMismatch, "network.tls"), (ClientPayloadError, "network.payload")):
                self.checked(cls(SECRET), ("network_error", code))
            self.checked(ServerTimeoutError(SECRET), ("timeout", "request.timeout"))

    def test_actual_aiohttp_classes_when_available(self):
        try:
            import aiohttp
        except ImportError:
            self.skipTest("aiohttp is an optional runtime dependency")
        cases = (
            (aiohttp.ClientConnectionError(SECRET), "network_error", "network.connection"),
            (aiohttp.ClientPayloadError(SECRET), "network_error", "network.payload"),
            (aiohttp.ClientError(SECRET), "network_error", "network.client"),
            (aiohttp.ServerTimeoutError(SECRET), "timeout", "request.timeout"),
            (aiohttp.ServerFingerprintMismatch(b'a', b'b', SECRET, 443), "network_error", "network.tls"),
            (aiohttp.ClientConnectorCertificateError(None, ssl.CertificateError(SECRET)), "network_error", "network.tls"),
        )
        for error, outcome, diagnostic in cases:
            self.checked(error, (outcome, diagnostic))


if __name__ == "__main__":
    unittest.main()
