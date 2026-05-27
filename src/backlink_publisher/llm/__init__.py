"""Publish-free LLM primitives.

This package is deliberately decoupled from ``publishing/`` — importing it must
**not** boot the adapter registry, the platform ``*_api`` modules, or the
browser/CDP publish stack. It holds the hardened endpoint guard + bounded POST
helper (lifted from ``webui_app/routes/llm.py``) and the prompt-input
sanitiser / log redactor (lifted from
``publishing/adapters/llm_anchor_provider.py``), plus ``generate_link_text``
used by the opt-in ``generate-backlink-text`` CLI verb.

The import-isolation invariant is enforced by ``tests/test_llm_client.py``.
Keep this module free of any ``backlink_publisher.publishing`` import.
"""

from __future__ import annotations
