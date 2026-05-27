"""Pure validate-backlinks engine package.

The CLI shell (``cli/validate_backlinks.py``) and the in-process WebUI bridge
(``webui_app/api/pipeline_api.py``) both call :func:`validate.engine.validate_rows`
so their data + typed-error behavior match by construction (thin-WebUI Phase 2
Unit 6, plan ``2026-05-27-004``). See ``ledger.aggregate.build_ledger`` for the
engine/shell/in-process precedent this follows.
"""

from __future__ import annotations

from .engine import ValidateOutcome, load_config_tolerant, validate_rows

__all__ = ["ValidateOutcome", "validate_rows", "load_config_tolerant"]
