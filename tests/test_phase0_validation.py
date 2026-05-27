"""Tests for backlink_publisher.phase0.validation — Unit 2.

Per `inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14`:
every negative assertion is paired with a positive complement so a future
fix that flips polarity is loud, not silent.

Per `tests-coupled-to-operator-config-state-2026-05-18`: gh CLI mocks return
explicit JSON dicts, never empty success.
"""

from __future__ import annotations


import pytest

from backlink_publisher.phase0 import validation as V


# ---------------------------------------------------------------------------
# Marker regex
# ---------------------------------------------------------------------------


def test_marker_regex_matches_canonical_form() -> None:
    body = "G1 Pass.\n\n<!-- phase0-verdict: result=pass run_id=trig-01-fire-42 -->\n"
    m = V.MARKER_RE.search(body)
    assert m is not None  # positive
    assert m.group(1) == "trig-01-fire-42"


def test_marker_regex_rejects_missing_marker() -> None:
    body = "G1 Pass. (Operator: I forgot the marker.)"
    assert V.MARKER_RE.search(body) is None  # negative
    # PAIRED positive: same body WITH marker matches.
    body_with = body + "\n<!-- phase0-verdict: result=pass run_id=x -->"
    assert V.MARKER_RE.search(body_with) is not None


def test_marker_regex_rejects_fail_result() -> None:
    body = "G1 Fail.\n<!-- phase0-verdict: result=fail run_id=trig-01 -->\n"
    assert V.MARKER_RE.search(body) is None
    # PAIRED positive: switching to result=pass matches.
    body_pass = body.replace("result=fail", "result=pass")
    assert V.MARKER_RE.search(body_pass) is not None


# ---------------------------------------------------------------------------
# normalize_body
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_str,expected",
    [
        ("a\nb\nc", "a\nb\nc"),         # already LF; idempotent
        ("a\r\nb\r\nc", "a\nb\nc"),     # CRLF -> LF
        ("a\rb\rc", "a\nb\nc"),         # bare CR -> LF
        ("", ""),
        ("\r\n", "\n"),
    ],
)
def test_normalize_body_lf_only(input_str: str, expected: str) -> None:
    assert V.normalize_body(input_str) == expected


def test_normalize_body_is_idempotent() -> None:
    raw = "a\r\nb\rc\nd"
    once = V.normalize_body(raw)
    twice = V.normalize_body(once)
    assert once == twice


def test_sha256_hex_crlf_and_lf_match() -> None:
    """Body sha must be over LF-normalized bytes — CRLF and LF input agree."""
    crlf = "line1\r\nline2\r\n"
    lf = "line1\nline2\n"
    assert V.sha256_hex(crlf) == V.sha256_hex(lf)


# ---------------------------------------------------------------------------
# validate_seal_schema — strict positive
# ---------------------------------------------------------------------------


def _valid_seal_routine() -> dict:
    return {
        "unit": "unit2",
        "branch": "local/telegraph-unit2-staged",
        "main_sha": "a" * 40,
        "sealed_at": "2026-06-01T10:00:00Z",
        "last_resealed_at": None,
        "sealed_by": "operator:init",
        "verdict_ref": {
            "kind": "routine_comment",
            "pr": 36,
            "comment_url": "https://github.com/x/y/issues/comments/1",
            "comment_id": 1,
            "comment_author": "telegraph-routine-bot[bot]",
            "comment_created_at": "2026-06-01T09:55:00Z",
            "comment_updated_at": "2026-06-01T09:55:00Z",
            "comment_body_sha256": "b" * 64,
        },
    }


def _valid_seal_manual() -> dict:
    s = _valid_seal_routine()
    s["verdict_ref"] = {
        "kind": "manual",
        "evidence_path": "scripts/telegraph_spike/manual-verdicts/2026-06-01.json",
        "evidence_sha256": "c" * 64,
    }
    return s


def test_validate_seal_schema_accepts_routine_comment() -> None:
    V.validate_seal_schema(_valid_seal_routine())  # does not raise


def test_validate_seal_schema_accepts_manual() -> None:
    V.validate_seal_schema(_valid_seal_manual())  # does not raise


@pytest.mark.parametrize("missing", V._REQUIRED_SEAL_FIELDS)
def test_validate_seal_schema_rejects_missing_top_level_field(missing: str) -> None:
    s = _valid_seal_routine()
    del s[missing]
    with pytest.raises(V.SealValidationError, match=f"missing field: {missing}"):
        V.validate_seal_schema(s)
    # PAIRED positive: putting it back makes the schema pass.
    s = _valid_seal_routine()
    V.validate_seal_schema(s)


@pytest.mark.parametrize("missing", V._REQUIRED_VERDICT_REF_ROUTINE)
def test_validate_seal_schema_rejects_missing_verdict_ref_routine_field(missing: str) -> None:
    s = _valid_seal_routine()
    del s["verdict_ref"][missing]
    with pytest.raises(V.SealValidationError, match=f"verdict_ref.{missing}"):
        V.validate_seal_schema(s)


@pytest.mark.parametrize("missing", V._REQUIRED_VERDICT_REF_MANUAL)
def test_validate_seal_schema_rejects_missing_verdict_ref_manual_field(missing: str) -> None:
    s = _valid_seal_manual()
    del s["verdict_ref"][missing]
    with pytest.raises(V.SealValidationError, match=f"verdict_ref.{missing}"):
        V.validate_seal_schema(s)


def test_validate_seal_schema_rejects_bad_sha_format() -> None:
    s = _valid_seal_routine()
    s["main_sha"] = "not-a-sha"
    with pytest.raises(V.SealValidationError, match="main_sha"):
        V.validate_seal_schema(s)
    # PAIRED positive
    s["main_sha"] = "a" * 40
    V.validate_seal_schema(s)


def test_validate_seal_schema_rejects_unknown_sealed_by() -> None:
    s = _valid_seal_routine()
    s["sealed_by"] = "routine:trig_01"
    with pytest.raises(V.SealValidationError, match="sealed_by"):
        V.validate_seal_schema(s)
    # PAIRED positive
    s["sealed_by"] = "operator:init"
    V.validate_seal_schema(s)


def test_validate_seal_schema_rejects_unknown_verdict_kind() -> None:
    s = _valid_seal_routine()
    s["verdict_ref"]["kind"] = "automatic"
    with pytest.raises(V.SealValidationError, match="verdict_ref.kind"):
        V.validate_seal_schema(s)


def test_validate_seal_schema_rejects_bad_body_sha_hex_length() -> None:
    s = _valid_seal_routine()
    s["verdict_ref"]["comment_body_sha256"] = "b" * 63  # one short
    with pytest.raises(V.SealValidationError, match="comment_body_sha256"):
        V.validate_seal_schema(s)


# ---------------------------------------------------------------------------
# validate_verdict_comment
# ---------------------------------------------------------------------------


def _allowlist_with(login: str = "telegraph-routine-bot[bot]") -> dict:
    return {
        "schema_version": 1,
        "authorized_authors": [{"login": login, "routine_id": "trig_01"}],
        "_path": "/tmp/allowlist.yaml",
        "_logins": frozenset({login}),
    }


def _valid_comment(login: str = "telegraph-routine-bot[bot]", pr: int = 36) -> dict:
    return {
        "id": 12345,
        "url": "https://api.github.com/repos/x/y/issues/comments/12345",
        "html_url": "https://github.com/x/y/pull/36#issuecomment-12345",
        "issue_url": f"https://api.github.com/repos/x/y/issues/{pr}",
        "user": {"login": login},
        "body": "G1 Pass!\n<!-- phase0-verdict: result=pass run_id=trig-01-fire-42 -->\n",
        "created_at": "2026-06-01T09:55:00Z",
        "updated_at": "2026-06-01T09:55:00Z",
    }


def test_validate_verdict_comment_accepts_valid() -> None:
    result = V.validate_verdict_comment(
        _valid_comment(), expected_pr=36, allowlist=_allowlist_with(),
    )
    assert result["run_id"] == "trig-01-fire-42"
    assert result["user_login"] == "telegraph-routine-bot[bot]"
    assert result["body_sha256"] == V.sha256_hex(_valid_comment()["body"])


def test_validate_verdict_comment_rejects_unknown_author() -> None:
    c = _valid_comment(login="attacker[bot]")
    with pytest.raises(V.SealValidationError, match="NOT in authorized-routine-bots allowlist"):
        V.validate_verdict_comment(c, expected_pr=36, allowlist=_allowlist_with())
    # PAIRED positive: allowlist that includes attacker passes.
    V.validate_verdict_comment(c, expected_pr=36, allowlist=_allowlist_with("attacker[bot]"))


def test_validate_verdict_comment_rejects_wrong_pr() -> None:
    c = _valid_comment(pr=99)  # issue_url ends /issues/99
    with pytest.raises(V.SealValidationError, match="does not target PR #36"):
        V.validate_verdict_comment(c, expected_pr=36, allowlist=_allowlist_with())
    # PAIRED positive: expected_pr=99 matches.
    V.validate_verdict_comment(c, expected_pr=99, allowlist=_allowlist_with())


def test_validate_verdict_comment_rejects_missing_marker() -> None:
    c = _valid_comment()
    c["body"] = "G1 Pass! (no marker)"
    with pytest.raises(V.SealValidationError, match="routine marker"):
        V.validate_verdict_comment(c, expected_pr=36, allowlist=_allowlist_with())
    # PAIRED positive: body with marker passes.
    c["body"] = "G1 Pass!\n<!-- phase0-verdict: result=pass run_id=x -->\n"
    V.validate_verdict_comment(c, expected_pr=36, allowlist=_allowlist_with())


def test_validate_verdict_comment_rejects_missing_user() -> None:
    c = _valid_comment()
    del c["user"]
    with pytest.raises(V.SealValidationError, match="comment.user"):
        V.validate_verdict_comment(c, expected_pr=36, allowlist=_allowlist_with())


# ---------------------------------------------------------------------------
# _run_gh — monkeypatch the subprocess.run dependency at module level
# ---------------------------------------------------------------------------


def test_run_gh_returns_parsed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProc:
        returncode = 0
        stdout = '{"id": 42, "login": "bot"}'
        stderr = ""

    def fake_run(cmd, **kwargs):
        assert cmd[0:2] == ["gh", "api"]
        return FakeProc()

    monkeypatch.setattr(V.subprocess, "run", fake_run)
    out = V._run_gh("repos/x/y/issues/comments/42")
    assert out == {"id": 42, "login": "bot"}


def test_run_gh_raises_on_auth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProc:
        returncode = 4
        stdout = ""
        stderr = "error: authentication required"

    monkeypatch.setattr(V.subprocess, "run", lambda *a, **kw: FakeProc())
    with pytest.raises(V.GhAuthError):
        V._run_gh("repos/x/y/issues/comments/42")


def test_run_gh_raises_on_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **kw):
        raise FileNotFoundError("gh not found")

    monkeypatch.setattr(V.subprocess, "run", boom)
    with pytest.raises(V.GhNotInstalledError):
        V._run_gh("repos/x/y/issues/comments/42")
