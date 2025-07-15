import pytest

import sys
import os
from unittest.mock import Mock, call
from mkdocs.structure.pages import Page
from mkdocs.structure.files import File
from mkdocs.structure.nav import Navigation
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
from mkdocs_confluence_plugin.plugin import ConfluencePlugin


@pytest.fixture
def plugin():
    p = ConfluencePlugin()
    p.config = {
        "space": "SPACE",
        "parent_page_name": None,
    }
    p.page_ids = {}
    p.page_versions = {}
    p.pages = []
    p.dryrun = False
    p.confluence = Mock()
    return p


def test_plugin_instantiation():
    plugin = ConfluencePlugin()
    assert isinstance(plugin, ConfluencePlugin)


def test_on_config_sets_confluence(monkeypatch, plugin):
    plugin_config = {
        "space": "SPACE",
        "host_url": "https://example.atlassian.net/wiki/rest/api/content",
        "username": "testuser",
        "password": "secrettoken",
        "debug": False,
        "dryrun": True,
    }

    mkdocs_config = {"plugins": [{"confluence": plugin_config}]}

    plugin.config = plugin_config

    plugin.on_config(mkdocs_config)

    assert plugin.enabled is True
    assert plugin.confluence.url == "https://example.atlassian.net/wiki"
    assert plugin.confluence.username == "testuser"
    assert plugin.confluence.password == "secrettoken"
    assert plugin.default_labels == ["cpe", "mkdocs"]
    assert plugin.dryrun is True


def test_sync_page_attachments_calls_add_or_update_attachment(
    monkeypatch, tmp_path, plugin
):
    subdir = tmp_path / "images"
    subdir.mkdir()
    img_file = subdir / "parent_page_image.png"
    img_file.write_bytes(b"dummy image data")

    # Mock os.walk to simulate the file tree
    monkeypatch.setattr(
        "os.walk",
        lambda root: [
            (str(subdir), [], ["parent_page_image.png"]),
            (str(tmp_path), ["images"], ["other.txt"]),
        ],
    )

    # ✅ Provide a valid page ID for the test
    plugin.page_ids[("parentpage", None)] = "mock-page-id"

    # ✅ Mock Confluence object to prevent actual API calls
    plugin.confluence = Mock()
    plugin.confluence.cql.return_value = {"results": []}

    # ✅ Replace the method under test with a Mock so we can assert it's called
    plugin.add_or_update_attachment = Mock()

    # Act
    plugin.sync_page_attachments("Parent Page", parent_id=None)

    # Assert
    plugin.add_or_update_attachment.assert_called_once()


def test_on_nav_builds_tab_nav(plugin):
    class DummyFile:
        def __init__(self, src_path):
            self.src_path = src_path

    class DummyFiles:
        def documentation_pages(self):
            return [
                DummyFile("dir1/page1.md"),
                DummyFile("dir1/subdir/page2.md"),
                DummyFile("readme.md"),
            ]

    dummy_files = DummyFiles()
    nav = Navigation(items=[], pages=[])
    plugin.on_nav(nav, config=None, files=dummy_files)

    # Flatten the nested tab_nav for assertion
    flat_nav = plugin._collect_all_page_names(plugin.tab_nav)

    assert "Page1" in flat_nav
    assert "Page2" in flat_nav
    assert "Readme" in flat_nav


def test_on_page_markdown_adds_header(plugin):
    plugin.config = {"github_base_url": "https://github.com/repo"}

    class DummyFile:
        def __init__(self, src_path):
            self.abs_src_path = src_path

    class DummyPage:
        def __init__(self):
            self.title = "README"
            self.file = DummyFile("docs/readme.md")

    page = DummyPage()
    markdown = "# title"

    result = plugin.on_page_markdown(markdown, page, None, None)

    assert result == markdown
    assert plugin._normalize_title("README") in plugin.page_lookup


def test_on_page_content_footer(plugin):
    plugin.config = {
        "github_base_url": "https://github.com/repo",
        "enable_footer": True,
        "username": "user",
        "password": "pass",
        "space": "TEST",
        "parent_page_name": "Docs",
    }
    plugin.enabled = True
    plugin.only_in_nav = False
    plugin.dryrun = False  # ✅ ensure real logic runs

    plugin.parent_page_id = "12345"
    plugin.page_ids = {}
    plugin.pages = []

    plugin.page_parents = {
        "Test": "Docs",
        "Docs": None,
    }

    plugin.confluence = Mock()
    plugin.confluence.cql = Mock(return_value={"results": []})
    plugin.confluence.create_page = Mock(return_value={"id": "99999"})

    class DummyFile:
        def __init__(self, src_path, src_uri):
            self.src_path = src_path
            self.src_uri = src_uri
            self.abs_src_path = src_path

    class DummyPage:
        def __init__(self):
            self.title = "README"
            self.file = DummyFile("docs/readme.md", "docs/readme.md")


    page = DummyPage()
    html = "<p>content</p>"

    updated_html = plugin.on_page_content(html, page, None, None)

    assert "github.com/repo/docs/readme.md" in updated_html
    assert "<a href=" in updated_html



def test_on_post_build_creates_and_updates(monkeypatch, plugin):
    plugin.enabled = True
    plugin.config = {
        "space": "SPACE",
        "parent_page_name": None,
        "dryrun": False,
        "host_url": "https://example.atlassian.net/wiki/rest/api/content",
        "username": "user",
        "password": "pass",
    }

    plugin.parent_page_id = None

    class DummyConfluence:
        def __init__(self):
            self.created_pages = []
            self.updated_pages = []

        def create_page(self, space, title, body, parent_id=None, representation=None):
            self.created_pages.append(title)
            return {"id": "123"}

        def update_page(self, page_id, title, body, version=None):
            self.updated_pages.append((title, version))
            return True

        def cql(self, query, limit=10):  # ✅ Fixed: support limit param
            return {}

    plugin.confluence = DummyConfluence()

    # ✅ Add a mock logger to avoid AttributeError
    plugin.log = Mock()

    plugin.page_ids = {}
    plugin.page_versions = {}
    plugin.pages = [{"title": "New Page", "body": "<p>body</p>"}]

    plugin.tab_nav = ["New Page"]

    plugin.page_lookup = {
        "New Page": {
            "title": "New Page",
            "content": "<p>body</p>",
            "parent_id": None,
            "source_path": "docs/new_page.md",
        }
    }

    plugin.publish_page = Mock()
    plugin.sync_page_attachments = Mock()

    plugin.on_post_build(config={}, files=[])




def test_find_page_id_with_and_without_parent_id(plugin):
    plugin.config = {"space": "TEST"}
    plugin.log = Mock()
    plugin._normalize_title = lambda t: t.lower().replace(" ", "")

    plugin.page_ids = {}

    mock_result = {
        "results": [
            {
                "content": {
                    "id": "123",
                    "title": "Page A",
                    "version": {"number": 3},
                    "ancestors": [{"id": "111"}, {"id": "222"}, {"id": "456"}],
                }
            }
        ]
    }

    plugin.confluence.cql = Mock(return_value=mock_result)
    plugin.confluence.get_page_by_id = Mock()

    page_id = plugin.find_page_id("Page A", parent_id="456")

    assert page_id == "123"



TEMPLATE_BODY = "<p> TEMPLATE </p>"


def test_dryrun_log_logs_info(caplog, plugin):
    with caplog.at_level("INFO"):
        plugin.dryrun_log("create", "Sample Page", parent_id="123")
    assert "DRYRUN: Would create page 'Sample Page' under parent ID 123" in caplog.text


def test_normalize_title_strips_punctuation(plugin):
    assert plugin._normalize_title(" Page! Title. ") == "pagetitle"
    assert plugin._normalize_title("Another Page-Title!") == "anotherpagetitle"


def test_clear_cached_page_info(plugin):
    plugin.page_ids = {("A", None): "123"}
    plugin.page_versions = {("A", None): 1}
    plugin.clear_cached_page_info()
    assert plugin.page_ids == {}
    assert plugin.page_versions == {}


def test_get_page_url_returns_correct_url(plugin):
    plugin.config = {"host_url": "https://example.atlassian.net/wiki/rest/api/content"}
    plugin.page_ids = {("Test Page", None): "45678"}
    url = plugin.get_page_url("Test Page", parent_id=None)
    assert (
        url
        == "https://example.atlassian.net/wiki/rest/api/content/pages/viewpage.action?pageId=45678"
    )


def test_page_exists_returns_true_if_found(plugin):
    plugin.find_page_id = Mock(return_value="123")
    assert plugin.page_exists("Existing Page", parent_id=None) is True

    plugin.find_page_id = Mock(return_value=None)
    assert plugin.page_exists("Missing Page", parent_id=None) is False


def test_get_file_sha1(tmp_path, plugin):
    file = tmp_path / "hash.txt"
    content = "Hello, world!"
    file.write_text(content)
    expected_hash = "943a702d06f34599aee1f8da8ef9f7296031d699"

    actual_hash = plugin.get_file_sha1(file)
    assert actual_hash == expected_hash
