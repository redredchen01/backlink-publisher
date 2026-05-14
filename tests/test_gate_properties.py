"""Property-based tests for gate primitives.

Defends against the "tautological gate" bug class documented in
`docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md`
— a gate that returns True for every input passes example-based tests
forever without anyone noticing. Hypothesis generates adversarial inputs
and asserts structural invariants: the gate MUST distinguish at least one
known-bad input from a known-good one.

Each test below pairs a positive assertion ("gate accepts X") with a
negative-shape assertion ("gate rejects Y") sourced from a fixture the
gate is contractually required to reject. If a future maintainer
accidentally re-introduces a tautological code path, hypothesis will
generate a counterexample and the negative-shape assertion fails loudly.

Gates currently under property-test coverage:
- ``verify_publish._title_in_body`` — title substring presence
- ``verify_publish._link_in_body`` — any-link substring presence
- ``anchor_metrics.normalize`` — text normalization for distribution math

Gates intentionally out of scope here (covered separately):
- ``language_check.language_matches`` — currently tautological; fix in PR
  #10 (feat/mandatory-linkcheck-lang-gate). Property tests for this gate
  will be added once that fix lands so they have something to assert
  against without xfail markers.
- ``linkcheck`` — HTTP-bound; the pure parts are too thin to property-test
  meaningfully without mocking the network.
"""

from __future__ import annotations

import string

from hypothesis import assume, given
from hypothesis import strategies as st

from backlink_publisher.anchor_metrics import normalize
from backlink_publisher.verify_publish import (
    _link_in_body,
    _title_in_body,
)


# ── verify_publish._title_in_body ────────────────────────────────────────────


@given(
    title=st.text(min_size=1, max_size=80).filter(lambda s: s.strip() != ""),
)
def test_title_in_body_positive_when_title_appears_verbatim(title):
    """Property: if a title appears in the body (case-insensitive), the gate accepts."""
    # Body contains the title prefix (up to 40 chars, matching gate logic)
    body = f"prefix some content {title} suffix more content"
    assert _title_in_body(title, body) is True


@given(
    title=st.text(min_size=10, max_size=80).filter(
        lambda s: s.strip() != "" and any(c.isalnum() for c in s)
    ),
    junk=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)),
        min_size=20,
        max_size=100,
    ),
)
def test_title_in_body_negative_when_unrelated(title, junk):
    """Property: body that has no overlap with the title is rejected.

    This is the load-bearing negative-shape assertion: if the gate were
    rewritten to return True unconditionally, this property would fail
    immediately on the first hypothesis-generated counterexample.
    """
    # Construct an unrelated body that definitely does NOT contain the title.
    # Take only ASCII letters from the title prefix to build the negative key.
    title_prefix = title[:40].strip().lower()
    assume(len(title_prefix) >= 5)  # need enough signal
    # Generate junk that does NOT contain any 5-char substring of the title.
    five_grams = {title_prefix[i:i+5] for i in range(len(title_prefix) - 4)}
    junk_lower = junk.lower()
    assume(not any(g in junk_lower for g in five_grams))
    assert _title_in_body(title, junk) is False


def test_title_in_body_empty_title_accepts():
    """Documented behavior: empty title is treated as 'no constraint' (accept)."""
    assert _title_in_body("", "any body content") is True
    assert _title_in_body("", "") is True


def test_title_in_body_known_negative_fixture():
    """Hard-coded negative: a published page that does NOT contain the title.

    This is the test that would catch the gate going tautological. If
    `_title_in_body` is ever changed to `return True` blindly, this test
    fails — no hypothesis run required.
    """
    title = "Best laptops 2026 — comprehensive buying guide"
    body = "<html><body>404 — Not Found</body></html>"
    assert _title_in_body(title, body) is False


# ── verify_publish._link_in_body ─────────────────────────────────────────────


@given(
    link=st.from_regex(r"https://[a-z]{3,15}\.com/[a-z]{3,15}", fullmatch=True),
)
def test_link_in_body_positive_when_link_appears(link):
    """Property: if any required link appears in the body, the gate accepts."""
    body = f"prefix <a href='{link}'>anchor</a> suffix"
    assert _link_in_body([link], body) is True


@given(
    link=st.from_regex(r"https://[a-z]{3,15}\.com/[a-z]{3,15}", fullmatch=True),
    body_text=st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Zs")),
        min_size=10,
        max_size=100,
    ),
)
def test_link_in_body_negative_when_unrelated(link, body_text):
    """Property: body without the link rejects."""
    assume(link not in body_text)
    # Also assume no substring overlap that could spuriously match
    assume(link[:20] not in body_text)
    assert _link_in_body([link], body_text) is False


def test_link_in_body_empty_list_accepts():
    """Documented behavior: empty required-links list is 'no constraint'."""
    assert _link_in_body([], "any body") is True
    assert _link_in_body([], "") is True


def test_link_in_body_known_negative_fixture():
    """Hard-coded negative: published page that does NOT link to the target."""
    required = ["https://target-site.example/money-page"]
    body = "<html><body>Some unrelated content with no outbound links.</body></html>"
    assert _link_in_body(required, body) is False


def test_link_in_body_partial_match_accepts():
    """Documented behavior: substring match is enough (no exact URL parsing)."""
    required = ["https://example.com/page"]
    # Body contains the URL with a query string suffix — still matches
    body = '<a href="https://example.com/page?ref=campaign">link</a>'
    assert _link_in_body(required, body) is True


# ── anchor_metrics.normalize ─────────────────────────────────────────────────


@given(text=st.text(min_size=0, max_size=200))
def test_normalize_idempotent(text):
    """Property: normalize(normalize(x)) == normalize(x).

    Idempotency is a structural invariant of any text-normalization function.
    Violating it means the function has hidden state or non-deterministic
    behavior — both are bug-class signals.
    """
    once = normalize(text)
    twice = normalize(once)
    assert once == twice


@given(text=st.text(min_size=1, max_size=80))
def test_normalize_case_invariant(text):
    """Property: normalize(text.upper()) == normalize(text.lower())."""
    assert normalize(text.upper()) == normalize(text.lower())


@given(
    text=st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
        min_size=1,
        max_size=50,
    ),
)
def test_normalize_preserves_alphanumerics(text):
    """Property: alphanumeric characters survive normalization (after casefold).

    The gate explicitly does NOT strip punctuation or fold diacritics —
    only collapses whitespace and casefolds. Alphanumerics MUST survive.
    """
    result = normalize(text)
    # Every alphanumeric char in input (after casefold) should appear in output
    for ch in text.casefold():
        if ch.isalnum():
            assert ch in result, f"alphanumeric {ch!r} dropped from normalize({text!r}) = {result!r}"


def test_normalize_brand_variants_remain_distinct():
    """Load-bearing negative-shape: 'Lyft, Inc.' and 'Lyft Inc' must NOT collapse.

    This is the property that the document-review flagged (F4 in PR #11
    rev-2 review). If a future maintainer adds punctuation-stripping to
    normalize, this test fails — preserving the false-positive defense.
    """
    assert normalize("Lyft, Inc.") != normalize("Lyft Inc.")
    assert normalize("O'Reilly") != normalize("OReilly")
    assert normalize("Yahoo!") != normalize("Yahoo")


def test_normalize_whitespace_collapses():
    """Documented behavior: internal whitespace runs collapse to one space."""
    assert normalize("a   b") == "a b"
    assert normalize("a\t\nb") == "a b"
    assert normalize("  leading and trailing  ") == "leading and trailing"


def test_normalize_empty_string_stays_empty():
    assert normalize("") == ""
    assert normalize("   ") == ""
    assert normalize("\t\n") == ""
