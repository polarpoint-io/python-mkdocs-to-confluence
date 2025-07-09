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


def test_publish_nav_structure_creates_pages_and_syncs_attachments(plugin):
    plugin.page_ids = {}
    plugin.page_versions = {}

    nav_tree = [{"Parent Page": [{"Child Page": ["Leaf Page"]}]}]

    plugin.find_page_id = lambda title, parent_id=None: plugin.page_ids.get((title, parent_id))

    def mock_find_or_create_page(title, parent_id=None):
        page_id = f"id_{title.replace(' ', '_')}"
        plugin.page_ids[(title, parent_id)] = page_id
        return page_id

    plugin.find_or_create_page = mock_find_or_create_page

    # Use dynamic parent IDs that reflect the structure
    parent_id = mock_find_or_create_page("Parent Page", None)
    child_id = mock_find_or_create_page("Child Page", parent_id)

    plugin.pages = [
        {"title": "Parent Page", "body": "<p>parent body</p>", "parent_id": None},
        {"title": "Child Page", "body": "<p>child body</p>", "parent_id": parent_id},
        {"title": "Leaf Page", "body": "<p>leaf body</p>", "parent_id": child_id},
    ]

    plugin.publish_page = Mock()
    plugin.sync_page_attachments = Mock()

    plugin.publish_nav_structure(nav_tree)

    expected_call = call("Leaf Page", "<p>leaf body</p>", child_id)
    assert expected_call in plugin.publish_page.call_args_list


def test_sync_page_attachments_calls_add_or_update_attachment(
    monkeypatch, tmp_path, plugin):
    subdir = tmp_path / "images"
    subdir.mkdir()
    img_file = subdir / "parent_page_image.png"
    img_file.write_bytes(b"dummy image data")

    monkeypatch.setattr(
        "os.walk",
        lambda root: [
            (str(tmp_path / "images"), [], ["parent_page_image.png"]),
            (str(tmp_path), ["images"], ["other.txt"]),
        ],
    )

    plugin.add_or_update_attachment = Mock()

    # ✅ Ensure page ID is available so sync proceeds
    plugin.page_ids[("Parent Page", None)] = "mock-page-id"

    plugin.sync_page_attachments("Parent Page", parent_id=None)

    plugin.add_or_update_attachment.assert_called_once()




def test_find_or_create_page_creates_new_page(plugin):
    plugin.pages = [{"title": "New Page", "body": "<p>body</p>"}]
    plugin.page_ids = {}
    plugin.page_versions = {}
    plugin.dryrun = False
    plugin.config = {"space": "SPACE"}

    plugin.tab_nav = ["New Page"]

    plugin.confluence.cql = Mock(return_value={"results": []})

    created_pages = []

    def mock_create_page(space, title, body, parent_id=None, representation=None):
        created_pages.append(title)
        return {"id": "new_id"}

    plugin.confluence.create_page = mock_create_page

    plugin.find_page_id = lambda title, parent_id=None: plugin.page_ids.get((title, parent_id))

    page_id = plugin.find_or_create_page("New Page", parent_id=None)

    if page_id:
        plugin.page_ids[("New Page", None)] = page_id

    assert page_id == "new_id"
    assert "New Page" in created_pages
    assert plugin.page_ids[("New Page", None)] == "new_id"

    plugin.publish_page = Mock()
    plugin.publish_nav_structure(plugin.tab_nav)
    assert "New Page" in created_pages


def test_find_or_create_page_returns_existing(plugin):
    plugin.page_ids = {("Existing Page", None): "existing_id"}
    plugin.confluence.create_page = Mock()
    plugin.confluence.cql = Mock(return_value={
        "results": [
            {
                "id": "existing_id",
                "version": {"number": 1},
                "ancestors": [],
            }
        ]
    })

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
    plugin.only_in_nav = False

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

    plugin.parent_page_id = None  # Add this line

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

        def cql(self, query):
            return {}

    plugin.confluence = DummyConfluence()

    plugin.page_ids = {}
    plugin.page_versions = {}
    plugin.pages = [{"title": "New Page", "body": "<p>body</p>"}]

    plugin.tab_nav = ["New Page"]
    plugin.publish_page = Mock()
    plugin.sync_page_attachments = Mock()

    plugin.on_post_build(config={}, files=[])



def test_find_page_id_with_and_without_parent_id(plugin):
    plugin.config = {"space": "TEST"}
    mock_result = {
        "results": [
            {"id": "123", "title": "Page A", "version": {"number": 3}, "ancestors": [{"id": "456"}]}
        ]
    }
    plugin.confluence.cql = Mock(return_value=mock_result)

    page_id = plugin.find_page_id("Page A", parent_id="456")
    assert page_id == "123"
    assert plugin.page_ids[("Page A", "456")] == "123"
    assert plugin.page_versions[("Page A", "456")] == 3

    page_id = plugin.find_page_id("Page A", parent_id="999")
    assert page_id is None


TEMPLATE_BODY = "<p> TEMPLATE </p>"

def test_ensure_folder_pages_exist_creates_structure(plugin):
    plugin.config = {"space": "TEST"}
    plugin.dryrun = False
    plugin.page_ids = {}
    plugin.page_versions = {}

    plugin.confluence.create_page = Mock(return_value={"id": "123"})
    plugin.find_page_id = Mock(return_value=None)

    nav_tree = [{"Folder A": [{"Subfolder A1": []}]}]

    plugin.ensure_folder_pages_exist(nav_tree)

    plugin.confluence.create_page.assert_any_call(
        space="TEST",
        title="Folder A",
        body=TEMPLATE_BODY,
        parent_id=None,
        representation="storage"
    )
    plugin.confluence.create_page.assert_any_call(
        space="TEST",
        title="Subfolder A1",
        body=TEMPLATE_BODY,
        parent_id="123",
        representation="storage"
    )


def test_publish_nav_structure_creates_hierarchy(plugin):
    plugin.pages = [
        {"title": "Final Page", "body": "<p>Body</p>", "parent_id": "Middle-Top-None"}
    ]
    plugin.page_ids = {}
    plugin.page_versions = {}

    plugin.find_page_id = Mock(return_value=None)
    plugin.find_or_create_page = Mock(side_effect=lambda title, parent_id=None: f"{title}-{parent_id}")
    plugin.publish_page = Mock()
    plugin.sync_page_attachments = Mock()

    nav_tree = [{"Top": [{"Middle": ["Final Page"]}]}]

    plugin.publish_nav_structure(nav_tree)

    plugin.find_or_create_page.assert_any_call("Top", parent_id=None)
    plugin.find_or_create_page.assert_any_call("Middle", parent_id="Top-None")
    plugin.publish_page.assert_called_once_with("Final Page", "<p>Body</p>", "Middle-Top-None")



def test_get_file_sha1(tmp_path, plugin):
    file = tmp_path / "hash.txt"
    content = "Hello, world!"
    file.write_text(content)
    expected_hash = "943a702d06f34599aee1f8da8ef9f7296031d699"

    actual_hash = plugin.get_file_sha1(file)
    assert actual_hash == expected_hash
