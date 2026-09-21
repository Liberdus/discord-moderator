# Isolated review probes

These scripts characterize the reviewed 0.5.3 code, including known defects. A zero exit status means the observations were reproduced; it does **not** mean every observed behavior is correct. They are separate from the normal passing regression suite. The probes reuse isolated test fixtures, mock provider/Discord HTTP calls and do not access the installed profile or credentials.

See [the review report](../docs/reviews/2026-09-21-code-review.md) for findings, exact runtime and commands. Convert these scenarios into tests of the corrected behavior when implementing the fixes. Do not preserve a defect simply to keep its characterization assertion passing.
