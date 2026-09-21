# Isolated review probes

These scripts characterize the reviewed 0.5.3 code, including known defects. A zero exit status means the observations were reproduced; it does **not** mean every observed behavior is correct. They are separate from the normal passing regression suite. The probes reuse isolated test fixtures, mock provider/Discord HTTP calls and do not access the installed profile or credentials.

See [the review report](../docs/reviews/2026-09-21-code-review.md) for findings, exact runtime and commands. Convert these scenarios into tests of the corrected behavior when implementing the fixes. Do not preserve a defect simply to keep its characterization assertion passing.

The original scripts describe commit `eb41018393b75c5ad26c2c5b71a13795c73fd6a1` (0.5.3). Run them only against that historical checkout. Version 0.5.4 intentionally no longer matches their defect assertions; use `integration_tests/test_safety_controls.py`, `integration_tests/test_role_safety.py` and `tests/test_screening_action_matrix.py` for current acceptance checks.
