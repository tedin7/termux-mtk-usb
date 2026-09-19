# Validation

2026-09-19: 25 offline unit tests passed in native Termux with pinned dependencies. These cover protocol framing, partial reads and timeouts, numeric payload ACK, driver/descriptor cleanup, rejected devices, storage-write guards, GPT CRCs and backup verification failures.

Hardware results are described separately in the case study. Tests do not establish support for other devices. The public CLI intentionally omits the private one-use writer and failed DA-resume experiment.
