"""sv_data: statement data, from source documents to standardised, checked numbers.

Layers (docs/財報數據與估值架構.md §2):

1. raw       — every fetched document kept as-is with its SHA-256, URL and fetch time (``raw``)
2. filed     — one record per reported number, exactly as filed (``filed``)
3. standard  — SV standard items via versioned mapping rules (alpha.3, next step)
4. checks    — versioned check rules per period (alpha.3, later step)

Rules of this library: no database, no web framework, no dependency on ``src/`` or ``backend/``.
Network access only through the HTTP function a caller passes in, so every function is testable
offline and the caller decides pacing, retries and circuit breaking.
"""
__version__ = "0.1.0"
