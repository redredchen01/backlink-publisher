"""Contract tests for the Phase-1 token/OAuth-authenticated adapters (P1#12).

These adapters load credentials from a ``config.<x>_token_path`` JSON file.
The trio (availability gate, DependencyError when unconfigured,
ExternalServiceError on a 401 auth rejection) is parametrized; happy-path
response parsing differs per platform and is covered by the dedicated
per-adapter test files where it exists.
"""
from __future__ import annotations

import importlib
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import DependencyError, ExternalServiceError

# (module suffix, class, config path attr, token filename, valid token dict,
#  HTTP attr to patch for the 401 test — wordpresscom/writeas/hashnode go
#  through the shared ``http_post`` wrapper; tumblr uses ``requests.post``
#  directly because OAuth 1.0a signing needs a per-request auth object)
TOKEN_ADAPTERS = [
    ("wordpresscom_api", "WordpresscomAPIAdapter", "wordpresscom_token_path",
     "wordpresscom-token.json", {"token": "t", "site": "myblog.wordpress.com"},
     "http_post"),
    ("writeas_api", "WriteasAPIAdapter", "writeas_token_path",
     "writeas-token.json", {"token": "t"}, "http_post"),
    ("hashnode_graphql", "HashnodeGraphQLAdapter", "hashnode_token_path",
     "hashnode-token.json", {"personal_access_token": "t", "publication_id": "p"},
     "http_post"),
    ("tumblr_api", "TumblrAPIAdapter", "tumblr_credentials_path",
     "tumblr-credentials.json", {"consumer_key": "a", "consumer_secret": "b",
                                 "oauth_token": "c", "oauth_token_secret": "d",
                                 "blog_name": "myblog"}, "requests.post"),
]


def _adapter(module_suffix: str, cls_name: str):
    mod = importlib.import_module(f"backlink_publisher.publishing.adapters.{module_suffix}")
    return getattr(mod, cls_name)


def _config(tmp_path, path_attr, filename):
    cfg = MagicMock()
    cfg.config_dir = tmp_path
    setattr(cfg, path_attr, tmp_path / filename)
    return cfg


def _write_token(tmp_path, filename, token_dict):
    path = tmp_path / filename
    path.write_text(json.dumps(token_dict))
    os.chmod(path, 0o600)


def _payload():
    return {"id": "a1", "title": "T", "content_html": "<p>hi</p>",
            "content_markdown": "hi", "tags": []}


@pytest.mark.parametrize("mod,cls,attr,fname,tok,http_attr", TOKEN_ADAPTERS)
def test_available_false_without_token(mod, cls, attr, fname, tok, http_attr, tmp_path):
    assert _adapter(mod, cls).available(_config(tmp_path, attr, fname)) is False


@pytest.mark.parametrize("mod,cls,attr,fname,tok,http_attr", TOKEN_ADAPTERS)
def test_available_true_with_token(mod, cls, attr, fname, tok, http_attr, tmp_path):
    _write_token(tmp_path, fname, tok)
    assert _adapter(mod, cls).available(_config(tmp_path, attr, fname)) is True


@pytest.mark.parametrize("mod,cls,attr,fname,tok,http_attr", TOKEN_ADAPTERS)
def test_publish_without_token_raises_dependency_error(mod, cls, attr, fname, tok, http_attr, tmp_path):
    with pytest.raises(DependencyError):
        _adapter(mod, cls)().publish(_payload(), "publish", _config(tmp_path, attr, fname))


@pytest.mark.parametrize("mod,cls,attr,fname,tok,http_attr", TOKEN_ADAPTERS)
def test_publish_401_raises_external_service_error(mod, cls, attr, fname, tok, http_attr, tmp_path):
    _write_token(tmp_path, fname, tok)
    resp = MagicMock()
    resp.status_code = 401
    resp.text = "unauthorized"
    resp.json.return_value = {"errors": [{"message": "unauthorized"}]}
    with patch(
        f"backlink_publisher.publishing.adapters.{mod}.{http_attr}",
        return_value=resp,
    ):
        with pytest.raises(ExternalServiceError):
            _adapter(mod, cls)().publish(_payload(), "publish", _config(tmp_path, attr, fname))
