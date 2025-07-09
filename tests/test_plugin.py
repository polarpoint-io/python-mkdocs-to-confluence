import pytest
import sys
import os
from unittest import mock
from unittest.mock import Mock
from mkdocs.structure.pages import Page
from mkdocs.structure.files import File
from mkdocs.structure.nav import Navigation
from pathlib import Path
from plugin import ConfluencePlugin

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if src_path not in sys.path:
    sys.path.insert(0, src_path)

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

    # Simulate mkdocs.yml config with plugin entry
    mkdocs_config = {"plugins": [{"confluence": plugin_config}]}

    # Assign plugin config directly to simulate mkdocs parsing
    plugin.config = plugin_config

    plugin.on_config(mkdocs_config)

    assert plugin.enabled is True
    assert plugin.confluence.url == "https://example.atlassian.net/wiki"
    assert plugin.confluence.username == "testuser"
    assert plugin.confluence.password == "secrettoken"
    assert plugin.default_labels == ["cpe", "mkdocs"]
    assert plugin.dryrun is True


def test_publish_nav_structure_creates_pages_and_syncs_attachments(plugin):
    plugin.pages = [
        {"title": "Parent Page", "body": "<p>parent body</p>"},
        {"title": "Child Page", "body": "<p>child body</p>"},
        {"title": "Leaf Page", "body": "<p>leaf body</p>"},
    ]
    plugin.page_ids = {}
    plugin.page_versions = {}

    nav_tree = [{"Parent Page": [{"Child Page": ["Leaf Page"]}]}]

    def mock_find_page_id(title):
        return plugin.page_ids.get(title)

    plugin.find_page_id = mock_find_page_id

    def mock_find_or_create_page(title, parent_id=None):
        if title not in plugin.page_ids:
            plugin.page_ids[title] = f"id_{title.replace(' ', '_')}"
        return plugin.page_ids[title]

    plugin.find_or_create_page = mock_find_or_create_page

    plugin.publish_page = Mock()
    plugin.sync_page_attachments = Mock()

    plugin.publish_nav_structure(nav_tree)

    for title in ["Parent Page", "Child Page", "Leaf Page"]:
        assert title in plugin.page_ids

    plugin.publish_page.assert_any_call("Leaf Page", "<p>leaf body</p>", None)
    plugin.sync_page_attachments.assert_any_call("Leaf Page")


def test_sync_page_attachments_calls_add_or_update_attachment(
    monkeypatch, tmp_path, plugin
):
    subdir = tmp_path / "images"
    subdir.mkdir()
    img_file = subdir / "parent_page_image.png"
    img_file.write_bytes(b"dummy image data")

    monkeypatch.setattr(
        "os.walk",
        lambda root: [(str(tmp_path), [], ["parent_page_image.png", "other.txt"])],
    )

    plugin.add_or_update_attachment = Mock()

    plugin.sync_page_attachments("Parent Page")

    plugin.add_or_update_attachment.assert_called_once_with("Parent Page", img_file)


def test_find_or_create_page_creates_new_page(plugin):
    plugin.page_ids = {}
    plugin.dryrun = False
    plugin.config = {"space": "SPACE"}

    created = {}

    def mock_create_page(space, title, body, parent_id=None, representation=None):
        created["title"] = title
        return {"id": "new_id"}

    plugin.confluence.create_page = mock_create_page

    page_id = plugin.find_or_create_page("New Page", None)
    assert page_id == "new_id"
    assert created["title"] == "New Page"


def test_find_or_create_page_returns_existing(plugin):
    plugin.page_ids = {"Existing Page": "existing_id"}
    plugin.confluence.create_page = Mock()

    page_id = plugin.find_or_create_page("Existing Page", None)
    assert page_id == "existing_id"
    plugin.confluence.create_page.assert_not_called()


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

    assert "Page1" in plugin.tab_nav
    assert "Page2" in plugin.tab_nav
    assert "Readme" in plugin.tab_nav


def test_on_page_markdown_adds_header(plugin):
    plugin.config = {"github_base_url": "https://github.com/repo"}

    class DummyFile:
        def __init__(self, src_path):
            self.src_path = src_path

    class DummyPage:
        def __init__(self):
            self.file = DummyFile("docs/readme.md")

    page = DummyPage()

    result = plugin.on_page_markdown("# title", page, None, None)
    assert result.startswith(
        "[Update markdown](https://github.com/repo/docs/readme.md)"
    )


def test_on_page_content_footer(plugin):
    plugin.config = {
        "github_base_url": "https://github.com/repo",
        "enable_footer": True,
        "username": "user",
        "password": "pass",
    }
    plugin.enabled = True
    plugin.only_in_nav = False  # bypass tab_nav check

    class DummyFile:
        def __init__(self, src_path, src_uri):
            self.src_path = src_path
            self.src_uri = src_uri

    class DummyPage:
        def __init__(self):
            self.title = "Test Page"
            self.file = DummyFile("docs/test.md", "docs/test.md")

    page = DummyPage()
    html = "<p>content</p>"
    updated_html = plugin.on_page_content(html, page, None, None)

    assert "Edit this page on GitHub" in updated_html
    assert "This page is auto-generated" in updated_html


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

    class DummyConfluence:
        def __init__(self):
            self.created_pages = []
            self.updated_pages = []

        def create_page(self, space, title, body, parent_id=None, representation=None):
            self.created_pages.append(title)
            return {"id": "123"}

        def update_page(self, page_id, title, body, version):
            self.updated_pages.append((title, version))
            return True

        def cql(self, query):
            return {}

    plugin.confluence = DummyConfluence()

    # Create new page
    plugin.page_ids = {}
    plugin.page_versions = {}
    plugin.pages = [{"title": "New Page", "body": "<p>body</p>"}]
    plugin.on_post_build(config={}, files=[])
    assert "New Page" in plugin.confluence.created_pages

    # Update existing page
    plugin.page_ids = {"Existing": "321"}
    plugin.page_versions = {"Existing": 1}
    plugin.pages = [{"title": "Existing", "body": "<p>updated body</p>"}]
    plugin.on_post_build(config={}, files=[])
    assert ("Existing", 2) in plugin.confluence.updated_pages


def test_add_or_update_attachment(monkeypatch, tmp_path, plugin):
    plugin.config = {
        "host_url": "https://example.atlassian.net/wiki",
        "username": "user",
        "password": "pass",
        "space": "SPACE",  # <-- Fix: required for find_page_id
    }

    class DummyConfluence:
        def __init__(self):
            self.uploaded = False
            self.deleted = False

        def cql(self, query):
            return {}

    plugin.confluence = DummyConfluence()

    dummy_file = tmp_path / "file.txt"
    dummy_file.write_text("content")

    def dummy_get(url, params=None):
        class DummyResponse:
            status_code = 200

            def json(self):
                return {"results": []}

        return DummyResponse()

    def dummy_post(url, files=None, data=None):
        class DummyResponse:
            status_code = 200

        return DummyResponse()

    def dummy_delete(url):
        class DummyResponse:
            status_code = 204

        return DummyResponse()

    plugin.session.get = dummy_get
    plugin.session.post = dummy_post
    plugin.session.delete = dummy_delete

    plugin.add_or_update_attachment("Page Name", dummy_file)


def test_get_file_sha1(tmp_path, plugin):
    file = tmp_path / "hash.txt"
    content = "Hello, world!"
    file.write_text(content)
    expected_hash = "943a702d06f34599aee1f8da8ef9f7296031d699"  # known SHA1

    actual_hash = plugin.get_file_sha1(file)
    assert actual_hash == expected_hash
