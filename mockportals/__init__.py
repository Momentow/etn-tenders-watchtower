"""Local fake portals and mutated variants for tests and the demo; never production logic (ADR 0002).

    python -m mockportals              # serves every mock portal on http://localhost:8081
    from mockportals import create_app, expected_tenders, VARIANTS, ENTRY, EXPECTED_STATE
    from mockportals.testing import serve   # run the server in a thread for a test

Docs: docs/test-portals/mock-portals.md
"""

from mockportals.app import ENTRY, EXPECTED_STATE, VARIANTS, create_app
from mockportals.data import KINDS, expected_tenders

__all__ = ["ENTRY", "EXPECTED_STATE", "KINDS", "VARIANTS", "create_app", "expected_tenders"]
