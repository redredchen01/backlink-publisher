"""Tests for ``canonicalize_url`` — R17 dedup-key support.

Plan ref: ``docs/plans/2026-05-18-004-feat-event-substrate-corpus-plan.md`` U3 + R17.

Property-based test asserts idempotency over random URLs (Hypothesis already
in dev deps, used in ``test_gate_properties.py``).
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from backlink_publisher._util.url import canonicalize_url


# ----- Lowercase / port stripping -----


def test_lowercase_host_and_scheme() -> None:
    assert canonicalize_url("HTTPS://X.COM/Path") == "https://x.com/Path"


def test_strip_default_http_port() -> None:
    assert canonicalize_url("http://x.com:80/a") == "http://x.com/a"


def test_strip_default_https_port() -> None:
    assert canonicalize_url("https://x.com:443/a") == "https://x.com/a"


def test_keep_non_default_port() -> None:
    assert canonicalize_url("https://x.com:8443/a") == "https://x.com:8443/a"


# ----- Path trailing slash -----


def test_strip_trailing_slash_from_non_root_path() -> None:
    assert canonicalize_url("https://x.com/path/") == "https://x.com/path"


def test_preserve_root_slash() -> None:
    assert canonicalize_url("https://x.com/") == "https://x.com/"


def test_empty_path_unchanged() -> None:
    # urlunsplit will treat empty path as "" — result has no trailing /.
    assert canonicalize_url("https://x.com") == "https://x.com"


def test_strip_multiple_trailing_slashes() -> None:
    assert canonicalize_url("https://x.com/a///") == "https://x.com/a"


# ----- Query parameters -----


def test_drop_utm_source() -> None:
    assert canonicalize_url("https://x.com/a?utm_source=newsletter&id=5") == "https://x.com/a?id=5"


def test_drop_all_utm_variants() -> None:
    assert canonicalize_url(
        "https://x.com/a?utm_source=x&utm_medium=email&utm_campaign=y&utm_term=z&utm_content=w&id=1"
    ) == "https://x.com/a?id=1"


def test_pure_utm_query_becomes_empty() -> None:
    assert canonicalize_url("https://x.com/a?utm_source=x") == "https://x.com/a"


def test_sort_query_keys() -> None:
    # Insertion order zebra → alphabetical.
    assert canonicalize_url("https://x.com/a?z=1&a=2&m=3") == "https://x.com/a?a=2&m=3&z=1"


def test_preserve_duplicate_value_order_within_key() -> None:
    # Same key appears twice with different values — both kept, original order.
    assert canonicalize_url("https://x.com/a?b=first&b=second") == "https://x.com/a?b=first&b=second"


def test_keep_blank_values() -> None:
    # "b=" (explicit blank) survives.
    assert canonicalize_url("https://x.com/a?b=") == "https://x.com/a?b="


def test_utm_case_insensitive() -> None:
    # ``UTM_SOURCE`` (upper) is also dropped — comparison uses .lower().
    assert canonicalize_url("https://x.com/a?UTM_SOURCE=x&id=1") == "https://x.com/a?id=1"


# ----- Fragment -----


def test_drop_fragment() -> None:
    assert canonicalize_url("https://x.com/a#section-2") == "https://x.com/a"


def test_drop_fragment_with_query() -> None:
    assert canonicalize_url("https://x.com/a?id=1#section-2") == "https://x.com/a?id=1"


# ----- Non-http(s) passthrough -----


def test_mailto_unchanged() -> None:
    assert canonicalize_url("mailto:x@y.com") == "mailto:x@y.com"


def test_ftp_unchanged() -> None:
    assert canonicalize_url("ftp://files.example.com/path/") == "ftp://files.example.com/path/"


def test_data_url_unchanged() -> None:
    assert canonicalize_url("data:text/plain;base64,SGk=") == "data:text/plain;base64,SGk="


# ----- Edge cases -----


def test_empty_string() -> None:
    assert canonicalize_url("") == ""


def test_basic_auth_userinfo_preserved() -> None:
    """basic-auth credentials in URL are preserved here; scrubber (U6) strips them later."""
    assert canonicalize_url("https://user:pass@x.com/a/") == "https://user:pass@x.com/a"


def test_combined_transformations() -> None:
    """All rules applied at once."""
    inp = "HTTPS://EXAMPLE.COM:443/Page/?utm_source=newsletter&z=last&a=first#anchor"
    assert canonicalize_url(inp) == "https://example.com/Page?a=first&z=last"


# ----- Idempotency property -----


@settings(max_examples=100)
@given(
    scheme=st.sampled_from(["http", "https", "HTTP", "HTTPS"]),
    host=st.from_regex(r"[A-Za-z][A-Za-z0-9\.\-]{1,40}", fullmatch=True),
    path=st.from_regex(r"/[A-Za-z0-9\-_/]{0,40}", fullmatch=True),
    query=st.from_regex(r"[A-Za-z]{1,8}=[A-Za-z0-9]{0,12}", fullmatch=True),
    fragment=st.from_regex(r"[A-Za-z0-9\-]{0,20}", fullmatch=True),
)
def test_idempotent(scheme: str, host: str, path: str, query: str, fragment: str) -> None:
    """Property: canonicalize_url(canonicalize_url(u)) == canonicalize_url(u)."""
    url = f"{scheme}://{host}{path}"
    if query:
        url += f"?{query}"
    if fragment:
        url += f"#{fragment}"
    once = canonicalize_url(url)
    twice = canonicalize_url(once)
    assert once == twice


def test_idempotent_on_already_canonical() -> None:
    """Concrete idempotency check on a manually-canonical URL."""
    inp = "https://x.com/a?a=1&b=2"
    assert canonicalize_url(inp) == inp
    assert canonicalize_url(canonicalize_url(inp)) == inp


# ----- Dedup-key use case -----


def test_two_formatting_variants_collapse_to_same_key() -> None:
    """Practical R17 use: two formattings of the same URL produce same key."""
    a = "https://Medium.com/@user/post-1/?utm_source=x"
    b = "https://medium.com:443/@user/post-1#section"
    assert canonicalize_url(a) == canonicalize_url(b)


def test_different_paths_do_not_collapse() -> None:
    """Distinct paths must stay distinct (conservatism check)."""
    assert canonicalize_url("https://x.com/a") != canonicalize_url("https://x.com/b")
