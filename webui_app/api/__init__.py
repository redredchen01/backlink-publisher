"""Pipeline API layer — structured wrappers for CLI invocation, draft CRUD,
and history operations.  Phase A extraction (Plan 2026-05-25-003).

Usage from routes::

    from ..api.pipeline_api import PipelineAPI, PipeResult
    api = PipelineAPI()
    result = api.plan(seed_json)
    if not result.success:
        ...
"""

from .pipeline_api import PipelineAPI, PipeResult
from .drafts_api import DraftAPI
from .history_api import HistoryAPI

__all__ = [
    "PipelineAPI",
    "PipeResult",
    "DraftAPI",
    "HistoryAPI",
]
