"""Screening failure reasons, with no provider content or exception details.

Production response validation remains authoritative. A rejected response is
inspected only to select a fixed diagnostic code; this module cannot make a
response acceptable or change a moderation decision.
"""

import asyncio
from datetime import datetime, timezone
import math
import ssl
from types import MappingProxyType

from .classifier import MODEL, RUBRIC
from .jev import ProviderError
from .screening import CONCERN, validate_screening


_DIAGNOSTICS = {
    "response.shape": "The response was not a JSON object.",
    "response.model": "The response did not identify the pinned model.",
    "response.answers": "The response had no valid answers object.",
    "response.unclassified": "The response failed production validation; no further details were retained.",
    "usage.shape": "The response had no valid usage object.",
    "usage.reservation": "Validated input token usage exceeded the reserved input bound.",
    "usage.input_tokens": "The input token count was missing or outside the accepted integer range.",
    "usage.output_tokens": "The output token count was missing or outside the accepted integer range.",
    "request.timeout": "The request timed out; its charge may be unknown.",
    "request.interrupted": "The request was interrupted; its charge may be unknown.",
    "network.tls": "The provider connection failed its TLS or certificate checks.",
    "network.connection": "The provider connection could not be established or was interrupted.",
    "network.payload": "The provider response could not be read completely.",
    "network.client": "The HTTP client could not complete the provider request.",
    "network.io": "A network or operating-system I/O failure interrupted the request.",
    "provider.missing_key": "The profile has no usable TypeSafe API key.",
    "provider.authentication_failed": "The provider rejected API authentication.",
    "provider.access_denied": "The provider denied access to this request.",
    "provider.rate_limited": "The provider rate-limited the request.",
    "provider.provider_overloaded": "The provider reported overload.",
    "provider.http_error": "The provider returned an unexpected HTTP status.",
    "provider.response_too_large": "The provider response exceeded the accepted size limit.",
    "provider.invalid_json": "The provider response was not valid JSON.",
    "provider.unclassified": "The provider request failed without a recognized diagnostic.",
    "state.stale": "Policy or shared evaluation state changed during the request.",
    "internal.request": "An unexpected evaluation failure occurred while making the request.",
    "internal.validation": "An unexpected evaluation failure occurred while processing the response.",
    "internal.unexpected": "An unexpected evaluation failure occurred.",
}
for _prefix, _title in (("context", "Purpose"), ("concern", "Concern")):
    _DIAGNOSTICS.update({
        _prefix + ".shape": _title + " answer was missing or was not an object.",
        _prefix + ".type": _title + " answer did not use the expected choice type.",
        _prefix + ".choice": _title + " answer selected an unknown or invalid label.",
        _prefix + ".confidence": _title + " confidence was missing or was not a finite number from zero to one.",
        _prefix + ".probabilities": _title + " probabilities had invalid labels or values.",
        _prefix + ".probability_sum": _title + " probabilities did not sum to one within the accepted tolerance.",
        _prefix + ".choice_not_maximum": _title + " chosen label was not a highest-probability label within tolerance.",
    })
DIAGNOSTICS = MappingProxyType(_DIAGNOSTICS)
DIAGNOSTIC_CODES = frozenset(DIAGNOSTICS)
EXTRA_OUTCOMES = frozenset({"invalid_response", "network_error", "internal_error"})
_PROVIDER_CODES = frozenset({"missing_key", "authentication_failed", "access_denied",
    "rate_limited", "provider_overloaded", "http_error", "response_too_large", "invalid_json"})


class EvaluationError(ValueError):
    """Only a whitelisted code can appear in this exception's message."""

    outcome = "invalid_response"

    def __init__(self, diagnostic="response.unclassified"):
        self.diagnostic = (diagnostic if type(diagnostic) is str and diagnostic in DIAGNOSTIC_CODES
                           else "response.unclassified")
        super().__init__(self.diagnostic)


def _unit_number(value):
    return type(value) in (int, float) and 0 <= value <= 1 and math.isfinite(value)


def _answer_diagnostic(answer, rubric, prefix):
    if type(answer) is not dict:
        return prefix + ".shape"
    if answer.get("type") != "choice":
        return prefix + ".type"
    probabilities = answer.get("probabilities")
    if type(probabilities) is not dict or set(probabilities) != set(rubric["criteria"]):
        return prefix + ".probabilities"
    choice = answer.get("choice")
    if type(choice) is not str or choice not in probabilities:
        return prefix + ".choice"
    if any(not _unit_number(value) for value in probabilities.values()):
        return prefix + ".probabilities"
    if not _unit_number(answer.get("confidence")):
        return prefix + ".confidence"
    if abs(sum(probabilities.values()) - 1) > .001:
        return prefix + ".probability_sum"
    if probabilities[choice] + 1e-6 < max(probabilities.values()):
        return prefix + ".choice_not_maximum"
    return None


def _diagnose_rejection(raw):
    """Mirror the live validator's checks without returning accepted results."""
    if type(raw) is not dict:
        return "response.shape"
    if raw.get("model") != MODEL:
        return "response.model"
    answers = raw.get("answers")
    if type(answers) is not dict:
        return "response.answers"
    context = _answer_diagnostic(answers.get("context"), RUBRIC, "context")
    if context:
        return context
    # validate_screening first validates context and usage via validate_response.
    usage = raw.get("usage")
    if type(usage) is not dict:
        return "usage.shape"
    for field in ("input_tokens", "output_tokens"):
        value = usage.get(field)
        if type(value) is not int or not 0 <= value <= 1000000:
            return "usage." + field
    return _answer_diagnostic(answers.get("concern"), CONCERN, "concern") or "response.unclassified"


def validate_evaluation(raw):
    """Use exactly the production acceptance rules; add safe rejection detail."""
    try:
        return validate_screening(raw)
    except (ValueError, OverflowError):
        # Very large JSON integers can overflow math.isfinite in the live
        # validator; they remain rejected and receive a numeric diagnostic.
        try:
            diagnostic = _diagnose_rejection(raw)
        except Exception:
            diagnostic = "response.unclassified"
        raise EvaluationError(diagnostic) from None


def _aiohttp_diagnostic(error):
    # Preview and standard-library-only tests must not require aiohttp.
    try:
        import aiohttp
    except ImportError:
        return None
    groups = (
        ("network.tls", ("ClientConnectorCertificateError", "ClientConnectorSSLError",
                         "ClientSSLError", "ServerFingerprintMismatch")),
        ("network.payload", ("ClientPayloadError",)),
        ("network.connection", ("ClientConnectionError",)),
        ("network.client", ("ClientError",)),
    )
    for diagnostic, names in groups:
        classes = tuple(candidate for name in names
                        if isinstance((candidate := getattr(aiohttp, name, None)), type))
        if classes and isinstance(error, classes):
            return diagnostic
    return None


def failure_diagnostic(error, phase="request"):
    """Return only fixed outcome/code pairs; never retain exception content."""
    if isinstance(error, EvaluationError):
        code = getattr(error, "diagnostic", None)
        return "invalid_response", (code if type(code) is str and code in DIAGNOSTIC_CODES
                                     else "response.unclassified")
    if isinstance(error, asyncio.CancelledError):
        return "uncertain", "request.interrupted"
    if isinstance(error, TimeoutError):
        return "timeout", "request.timeout"
    if isinstance(error, ProviderError):
        code = error.args[0] if len(error.args) == 1 and type(error.args[0]) is str else None
        if code in _PROVIDER_CODES:
            return code, "provider." + code
        if code == "stale":
            return "stale", "state.stale"
        if code == "usage_exceeds_reservation":
            return "usage_exceeds_reservation", "usage.reservation"
        return "provider_or_response_error", "provider.unclassified"
    if phase == "validation":
        return "internal_error", "internal.validation"
    if phase != "request":
        return "internal_error", "internal.unexpected"
    if isinstance(error, ssl.SSLError):
        return "network_error", "network.tls"
    diagnostic = _aiohttp_diagnostic(error)
    if diagnostic:
        return "network_error", diagnostic
    if isinstance(error, OSError):
        return "network_error", "network.io"
    return "internal_error", "internal.request"


def live_failure_lines(store):
    """Read the last retained request failure; never reinterpret old generic errors."""
    tables = {row[0] for row in store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('screening_attempts_v1','screening_failures_v1')")}
    if "screening_attempts_v1" not in tables:
        return []
    has_details = "screening_failures_v1" in tables
    detail = "d.diagnostic" if has_details else "NULL"
    join = " LEFT JOIN screening_failures_v1 d ON d.attempt_key=a.key" if has_details else ""
    outcomes = tuple(sorted(_PROVIDER_CODES | {"provider_or_response_error", "timeout", "uncertain",
                                             "usage_exceeds_reservation"}))
    row = store.db.execute("SELECT a.finished_at," + detail + " AS diagnostic FROM screening_attempts_v1 a" +
        join + " WHERE a.outcome IN (" + ",".join("?" for _ in outcomes) +
        ") ORDER BY a.started_at DESC LIMIT 1", outcomes).fetchone()
    if row is None:
        return []
    stamp, code = row[0], row[1]
    if type(stamp) not in (int, float) or not math.isfinite(stamp) or not 0 < stamp <= 253402300799:
        return []
    lines = ["", "LAST FAILED AI CHECK (UTC)", datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")]
    if type(code) is str and code in DIAGNOSTIC_CODES:
        lines += [code, DIAGNOSTICS[code]]
    else:
        lines += ["Details were not recorded."]
    return lines
