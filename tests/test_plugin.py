import pytest
import sys
import os
import json
from unittest import mock
from unittest.mock import Mock
from requests.models import Response

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from mkdocs_confluence_plugin.plugin import ConfluencePlugin
from mkdocs.config import load_config
from mkdocs.structure.pages import Page
from mkdocs.structure.files import Files
from mkdocs.structure.files import File
from mkdocs.structure.nav import Navigation
import inspect


@pytest.fixture
def mock_put(monkeypatch):
    mock = Mock()
    mock.return_value.status_code = 200
    mock.return_value.text = "OK"
    monkeypatch.setattr("requests.put", mock)
    return mock

@pytest.fixture
def plugin():
    return ConfluencePlugin()


def test_plugin_instantiation():
    plugin = ConfluencePlugin()
    assert isinstance(plugin, ConfluencePlugin)


def test_on_config_with_env(monkeypatch, plugin):
    monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")
    monkeypatch.setenv("ATLASSIAN_USER", "testuser")
    monkeypatch.setenv("ATLASSIAN_TOKEN", "secrettoken")

    config = {
        "confluence": {
            "space": "SPACE",
            "host_url": "https://example.atlassian.net/wiki/rest/api/content",
            "username": "testuser",
            "password": "secrettoken",
            "debug": False,
            "dryrun": True
        }
    }

    plugin.config = config["confluence"]
    result = plugin.on_config(config)

    assert plugin.enabled is True
    assert plugin.confluence.url == "https://example.atlassian.net/wiki"
    assert plugin.confluence.username == "testuser"
    assert plugin.confluence.password == "secrettoken"
    assert plugin.default_labels == ["cpe", "mkdocs"]


def test_on_config_missing_space_key(plugin):
    config = {"confluence": {}}
    with pytest.raises(ValueError, match="Missing required config keys:"):
        plugin.on_config(config)


def test_on_config_no_env(monkeypatch, plugin):
    monkeypatch.delenv("ATLASSIAN_URL", raising=False)
    monkeypatch.delenv("ATLASSIAN_USER", raising=False)
    monkeypatch.delenv("ATLASSIAN_TOKEN", raising=False)

    config = {"confluence": {"space": "SPACE"}}

    with pytest.raises(ValueError, match="Missing required config keys: host_url, username, password"):
        plugin.on_config(config)


def test_on_post_page_does_not_modify_output(plugin):
    plugin.enabled = True
    plugin.page_attachments = {"Test Page": []}
    plugin.config = {"dryrun": False}

    page = type("Page", (), {
        "markdown": "# Hello\nThis is a **test**",
        "title": "Test Page"
    })()

    output = "original output"
    result = plugin.on_post_page(output, page, {"site_dir": "."})

    assert result == output


def test_on_post_page_empty(plugin):
    plugin.enabled = True
    plugin.page_attachments = {"": []}
    plugin.config = {"dryrun": False}

    page = type("Page", (), {
        "markdown": "",
        "title": ""
    })()

    html = plugin.on_post_page("", page, {"site_dir": "."})
    assert html == ""


@mock.patch("requests.get")
def test_on_nav_fetch_page_id(mock_get, plugin):
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "results": [{"id": "456", "version": {"number": 4}}]
    }

    plugin.enabled = True
    plugin.curl_url = "https://example.atlassian.net/wiki/rest/api/content/"
    plugin.auth = ("user", "token")
    config = {"site_name": "Test Docs", "confluence": {"space": "SPACE"}}

    file = File(path="welcome.md", src_dir="docs", dest_dir="site", use_directory_urls=True)
    page = Page(title="Welcome", file=file, config=config)
    nav = Navigation(items=[page], pages=[page])

    if not hasattr(plugin, "page_ids"):
        plugin.page_ids = {}

    plugin.on_nav(nav, config=config, files=[])
    plugin.page_ids["Welcome"] = "456"
    assert plugin.page_ids["Welcome"] == "456"


def test_on_post_build_updates_existing_page(mock_put, plugin):
    plugin.enabled = True
    plugin.auth = ("user", "token")
    plugin.curl_url = "https://example.atlassian.net/wiki/rest/api/content/"
    plugin.page_ids = {"Home": "789"}
    plugin.page_versions = {"Home": 1}
    plugin.pages = [{"title": "Home", "body": "<p>Updated</p>"}]

    plugin.on_post_build(config={}, files=[])

    mock_put.assert_called_once()
    call_args = mock_put.call_args[1]
    assert call_args["auth"] == ("user", "token")
    assert call_args["json"]["version"]["number"] == 2


@mock.patch("requests.post")
def test_on_post_build_creates_new_page(mock_post, plugin):
    plugin.enabled = True
    plugin.auth = ("user", "token")
    plugin.curl_url = "https://example.atlassian.net/wiki/rest/api/content/"
    plugin.page_ids = {}
    plugin.pages = [{"title": "New Page", "body": "<p>Fresh</p>"}]
    plugin.space = "SPACE"
    plugin.parent_id = None

    mock_post.return_value.status_code = 201
    plugin.on_post_build(config={"confluence": {"space": "SPACE"}}, files=[])

    assert mock_post.called


@mock.patch("requests.put")
def test_on_post_build_handles_api_error(mock_put, plugin, caplog):
    plugin.enabled = True
    plugin.auth = ("user", "token")
    plugin.curl_url = "https://example.atlassian.net/wiki/rest/api/content/"
    plugin.page_ids = {"Broken": "999"}
    plugin.page_versions = {"Broken": 2}
    plugin.pages = [{"title": "Broken", "body": "<p>Fail</p>"}]

    mock_put.return_value.status_code = 500
    mock_put.return_value.text = "Internal Server Error"

    with caplog.at_level("ERROR"):
        plugin.on_post_build(config={}, files=[])


def test_on_post_page_has_method(plugin):
    assert hasattr(plugin, "on_post_page")


def test_on_post_build_no_pages(plugin):
    plugin.pages = []
    plugin.page_ids = {}
    plugin.page_versions = {}
    plugin.enabled = True
    plugin.auth = ("user", "token")
    plugin.curl_url = "https://example.atlassian.net/wiki/rest/api/content/"

    with mock.patch("requests.put") as mock_put, mock.patch("requests.post") as mock_post:
        plugin.on_post_build(config={"confluence": {"space": "SPACE"}}, files=[])
        mock_put.assert_not_called()
        mock_post.assert_not_called()


def test_on_post_build_creates_new_page_with_parent(plugin):
    plugin.pages = [{"title": "Child Page", "body": "<p>Content</p>"}]
    plugin.page_ids = {}
    plugin.page_versions = {}
    plugin.enabled = True
    plugin.auth = ("user", "token")
    plugin.curl_url = "https://example.atlassian.net/wiki/rest/api/content/"
    plugin.space = "SPACE"
    plugin.parent_id = "12345"

    with mock.patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        plugin.on_post_build(config={}, files=[])
        args, kwargs = mock_post.call_args
        data_sent = kwargs["data"]
        assert '"ancestors": [{"id": "12345"}]' in data_sent


def test_on_post_build_missing_space_key_logs_error(plugin, caplog):
    plugin.pages = [{"title": "New Page", "body": "<p>Content</p>"}]
    plugin.page_ids = {}
    plugin.page_versions = {}
    plugin.enabled = True
    plugin.auth = ("user", "token")
    plugin.curl_url = "https://example.atlassian.net/wiki/rest/api/content/"
    plugin.space = None

    with mock.patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 400
        mock_post.return_value.text = "Bad Request"
        plugin.on_post_build(config={}, files=[])

    assert any("Failed to create page" in r.message for r in caplog.records)


@mock.patch("requests.put")
def test_on_post_build_updates_page_success(mock_put, plugin):
    plugin.enabled = True
    plugin.auth = ("user", "token")
    plugin.curl_url = "https://example.atlassian.net/wiki/rest/api/content/"
    plugin.page_ids = {"Page1": "123"}
    plugin.page_versions = {"Page1": 1}
    plugin.pages = [{"title": "Page1", "body": "<p>Updated</p>"}]

    mock_put.return_value.status_code = 200
    plugin.on_post_build(config={}, files=[])
    mock_put.assert_called_once()


@mock.patch("requests.put")
def test_on_post_build_updates_page_failure(mock_put, plugin, caplog):
    plugin.enabled = True
    plugin.auth = ("user", "token")
    plugin.curl_url = "https://example.atlassian.net/wiki/rest/api/content/"
    plugin.page_ids = {"Page2": "124"}
    plugin.page_versions = {"Page2": 2}
    plugin.pages = [{"title": "Page2", "body": "<p>Updated</p>"}]

    mock_put.return_value.status_code = 500
    mock_put.return_value.text = "Internal Error"

    plugin.on_post_build(config={}, files=[])
    assert "Failed to update page" in caplog.text
    mock_put.assert_called_once()


@mock.patch("requests.post")
def test_on_post_build_creates_page_with_parent(mock_post, plugin):
    plugin.enabled = True
    plugin.auth = ("user", "token")
    plugin.curl_url = "https://example.atlassian.net/wiki/rest/api/content/"
    plugin.space = "SPACE"
    plugin.parent_id = "456"
    plugin.page_ids = {}
    plugin.pages = [{"title": "Child Page", "body": "<p>Child</p>"}]

    mock_post.return_value.status_code = 201

    plugin.on_post_build(config={}, files=[])
    called_data = mock_post.call_args[1]["data"]
    assert '"ancestors": [{"id": "456"}]' in called_data


def test_on_post_build_skips_when_disabled(plugin):
    plugin.enabled = False
    plugin.pages = [{"title": "Skipped", "body": "<p>Skip</p>"}]

    with mock.patch("requests.put") as mock_put, mock.patch("requests.post") as mock_post:
        plugin.on_post_build(config={}, files=[])

    mock_put.assert_not_called()
    mock_post.assert_not_called()
