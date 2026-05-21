"""LLM endpoint host allowlist (Plan 2026-05-21-006 Unit 3.1).

WebUI's `/settings/test-llm-connection` accepts an `endpoint` URL from the
operator and forwards their `Authorization: Bearer <api_key>` to it. SSRF
gating (via `_util.net_safety`) blocks internal IP classes but cannot stop
an attacker-controlled public host (e.g., `https://evil.example.com`) from
receiving the operator's API key.

This allowlist enumerates the canonical LLM providers the operator
realistically uses. An `endpoint` whose host is not in the allowlist is
rejected with `host_not_allowlisted` unless the operator sets
`BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST=1` (explicit opt-in: "I know my
API key will be sent to a non-canonical host").

Extending the allowlist:
    Add the bare hostname (no scheme, no port, no path). Subdomains are
    matched only if listed explicitly — `api.openai.com` does NOT match
    `evil.api.openai.com`. PR comments should explain the source.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

_LLM_HOST_ALLOWLIST: frozenset[str] = frozenset({
    # OpenAI + canonical OpenAI-compatible providers
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "api.deepseek.com",
    "api.siliconflow.cn",
    "api.moonshot.cn",
    "api.together.xyz",
    "api.groq.com",
    "api.fireworks.ai",
    "openrouter.ai",
    "api.mistral.ai",
    "api.cohere.ai",
    "api.perplexity.ai",
    "api.x.ai",
    # Local development convenience — Ollama / LM Studio default loopback.
    # Operators running on localhost separately need to opt-in via
    # BACKLINK_PUBLISHER_LLM_ALLOW_LOOPBACK=1 because SSRF gate rejects
    # 127.0.0.0/8 by default.
    "localhost",
    "127.0.0.1",
    "::1",
})


def is_allowlisted(url: str) -> bool:
    """Return True if the URL's host is in the LLM allowlist, OR if the
    operator opted out of allowlisting via env.

    Bare hostname comparison — subdomains are NOT matched.
    """
    if os.environ.get("BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST") == "1":
        return True
    host = urlparse(url).hostname
    if not host:
        return False
    return host.lower() in _LLM_HOST_ALLOWLIST


def known_hosts() -> frozenset[str]:
    """Return the set of allowlisted hosts. Useful for error-message
    rendering (so the operator can see what's accepted)."""
    return _LLM_HOST_ALLOWLIST
