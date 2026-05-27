"""Unit 7: txt.fyi form-POST adapter (Plan 2026-05-25-001).

Covers publish happy/draft paths, missing content, missing hidden fields,
redirect failure, anti-bot challenge propagation, and the fire-and-forget
link verification hook. All HTTP is mocked — no real network.
"""

from __future__ import annotations

from unittest import mock

import pytest

from backlink_publisher._util.errors import AntiBotChallengeError, ExternalServiceError
from backlink_publisher.config import Config
from backlink_publisher.publishing.adapters.txtfyi_api import (
    TxtfyiFormPostAdapter,
)

_ADAPTER = "backlink_publisher.publishing.adapters.txtfyi_api"

_PAYLOAD = {
    "id": "txt-1",
    "title": "Hello txt.fyi",
    "content_markdown": "# Hi\n\nA [link](https://example.com) here.\n",
    "target_url": "https://example.com",
    "main_domain": "https://example.com/",
}

_FORM_HTML_WITH_TOKENS = (
    '<form action="edit.php" method="post">'
    '<input name="nonce" type="hidden" value="a1b2c3,1234567890,def456">'
    '<input name="form_time" type="hidden" value="1234567890">'
    '<input name="url" type="url">'
    '<textarea name="txt"></textarea>'
    '<input name="go" type="submit" value="PUBLISH">'
    "</form>"
)

_FORM_HTML_MISSING_NONCE = (
    '<form action="edit.php" method="post">'
    '<input name="form_time" type="hidden" value="1234567890">'
    '<textarea name="txt"></textarea>'
    '<input name="go" type="submit" value="PUBLISH">'
    "</form>"
)


def _mock_response(*, status=200, text="", url="https://txt.fyi/"):
    """Build a requests.Response-like mock."""
    resp = mock.MagicMock()
    resp.status_code = status
    resp.text = text
    resp.url = url
    resp.headers = {"server": "cloudflare"}
    return resp


# ── happy paths ────────────────────────────────────────────────────────────


def test_publish_happy_returns_published_url():
    form_resp = _mock_response(text=_FORM_HTML_WITH_TOKENS)
    submit_resp = _mock_response(
        url="https://txt.fyi/+/abcd1234/",
    )

    with mock.patch(
        f"{_ADAPTER}.fetch_form", return_value=form_resp
    ) as mock_fetch, mock.patch(
        f"{_ADAPTER}.submit_form", return_value=submit_resp
    ) as mock_submit, mock.patch(
        f"{_ADAPTER}.attach_link_verification",
        return_value={"link_attr_verification": {"verification": "ok"}},
    ):
        res = TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="publish", config=Config())

    assert res.status == "published"
    assert res.published_url == "https://txt.fyi/+/abcd1234/"
    assert res.adapter == "txtfyi-form-post"
    assert res.platform == "txtfyi"
    assert res._provider_meta["link_attr_verification"]["verification"] == "ok"

    # Verify the form was fetched and submitted with the right data.
    mock_fetch.assert_called_once_with("https://txt.fyi/")
    assert mock_submit.call_args.args[0] == "https://txt.fyi/edit.php"
    data = mock_submit.call_args.args[1]
    assert "nonce" in data
    assert "form_time" in data
    assert "txt" in data
    assert "# Hello txt.fyi" in data["txt"]
    assert data["go"] == "PUBLISH"


def test_publish_draft_mode_returns_draft_url():
    form_resp = _mock_response(text=_FORM_HTML_WITH_TOKENS)
    submit_resp = _mock_response(
        url="https://txt.fyi/+/draft123/",
    )

    with mock.patch(f"{_ADAPTER}.fetch_form", return_value=form_resp), mock.patch(
        f"{_ADAPTER}.submit_form", return_value=submit_resp
    ) as mock_submit, mock.patch(
        f"{_ADAPTER}.attach_link_verification",
    ) as mock_verify:
        res = TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="draft", config=Config())

    assert res.status == "drafted"
    assert res.draft_url == "https://txt.fyi/+/draft123/"
    assert res.published_url == ""
    # Draft mode does not call the verify hook.
    assert res._provider_meta is None
    mock_verify.assert_not_called()


def test_publish_without_title_prepends_no_heading():
    """When title is empty, the body should NOT get a '# ' prefix."""
    form_resp = _mock_response(text=_FORM_HTML_WITH_TOKENS)
    submit_resp = _mock_response(
        url="https://txt.fyi/+/no-title/",
    )
    payload_no_title = dict(_PAYLOAD, title="")

    with mock.patch(f"{_ADAPTER}.fetch_form", return_value=form_resp), mock.patch(
        f"{_ADAPTER}.submit_form", return_value=submit_resp
    ) as mock_submit:
        TxtfyiFormPostAdapter().publish(payload_no_title, mode="publish", config=Config())

    sent_body = mock_submit.call_args.args[1]["txt"]
    assert sent_body.startswith("# Hi"), (
        "Body should not get '# ' prefix when title is empty"
    )


# ── error paths ────────────────────────────────────────────────────────────


def test_empty_content_raises_external_service_error():
    payload_empty = dict(_PAYLOAD, content_markdown="")
    with pytest.raises(ExternalServiceError, match="has no content_markdown"):
        TxtfyiFormPostAdapter().publish(payload_empty, mode="publish", config=Config())


def test_missing_hidden_fields_raises_external_service_error():
    form_resp = _mock_response(text=_FORM_HTML_MISSING_NONCE)

    with mock.patch(f"{_ADAPTER}.fetch_form", return_value=form_resp):
        with pytest.raises(ExternalServiceError, match="missing hidden fields"):
            TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="publish", config=Config())


def test_no_redirect_raises_external_service_error():
    """If submit_form returns the submit URL instead of a redirect, raise."""
    form_resp = _mock_response(text=_FORM_HTML_WITH_TOKENS)
    # .url stays at the submit endpoint — no redirect happened.
    submit_resp = _mock_response(url="https://txt.fyi/edit.php")

    with mock.patch(f"{_ADAPTER}.fetch_form", return_value=form_resp), mock.patch(
        f"{_ADAPTER}.submit_form", return_value=submit_resp
    ):
        with pytest.raises(ExternalServiceError, match="did not redirect"):
            TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="publish", config=Config())


def test_anti_bot_challenge_on_fetch_form_propagates():
    """AntiBotChallengeError from fetch_form must propagate (not catch)."""
    with mock.patch(
        f"{_ADAPTER}.fetch_form",
        side_effect=AntiBotChallengeError("bot challenge on GET txt.fyi"),
    ):
        with pytest.raises(AntiBotChallengeError):
            TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="publish", config=Config())


def test_anti_bot_challenge_on_submit_form_propagates():
    form_resp = _mock_response(text=_FORM_HTML_WITH_TOKENS)
    with mock.patch(f"{_ADAPTER}.fetch_form", return_value=form_resp), mock.patch(
        f"{_ADAPTER}.submit_form",
        side_effect=AntiBotChallengeError("bot challenge on POST txt.fyi"),
    ):
        with pytest.raises(AntiBotChallengeError):
            TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="publish", config=Config())


def test_external_service_error_from_submit_propagates():
    form_resp = _mock_response(text=_FORM_HTML_WITH_TOKENS)
    with mock.patch(f"{_ADAPTER}.fetch_form", return_value=form_resp), mock.patch(
        f"{_ADAPTER}.submit_form",
        side_effect=ExternalServiceError("HTTP 500 from txt.fyi"),
    ):
        with pytest.raises(ExternalServiceError):
            TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="publish", config=Config())
