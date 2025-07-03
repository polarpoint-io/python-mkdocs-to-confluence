import pytest
import sys
import os
from unittest import mock
from unittest.mock import Mock
from mkdocs.structure.pages import Page
from mkdocs.structure.files import File
from mkdocs.structure.nav import Navigation

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from mkdocs_confluence_plugin.plugin import ConfluencePlugin


@pytest.fixture
def plugin():
    return ConfluencePlugin()


def test_plugin_instantiation():
    plugin = ConfluencePlugin()
    assert isinstance(plugin, ConfluencePlugin)


def test_on_config_sets_confluence(monkeypatch, plugin):
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
    plugin.on_config(config)
    assert plugin.enabled is True
    assert plugin.confluence.url == "https://example.atlassian.net/wiki"
    assert plugin.confluence.username == "testuser"
    assert plugin.confluence.password == "secrettoken"
    assert plugin.default_labels == ["cpe", "mkdocs"]
    assert plugin.dryrun is True


def test_on_config_missing_keys_raises(plugin):
    config = {"confluence": {}}
    with pytest.raises(ValueError):
        plugin.on_config(config)



def test_on_nav_builds_tab_nav(plugin):
    class DummyFile:
        def __init__(self, src_path):
            self.src_path = src_path

    class DummyFiles:
        def documentation_pages(self):
            return [
                DummyFile("dir1/page1.md"),
                DummyFile("dir1/subdir/page2.md"),
                DummyFile("readme.md")
            ]

    dummy_files = DummyFiles()
    nav = Navigation(items=[], pages=[])
    plugin.on_nav(nav, config=None, files=dummy_files)

    # Tab nav should contain all titles, title-cased and with .md removed
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
    assert result.startswith("[Update markdown](https://github.com/repo/docs/readme.md)")


def test_on_page_content_footer(plugin):
    plugin.config = {
        "github_base_url": "https://github.com/repo",
        "enable_footer": True,
        "username": "user",
        "password": "pass"
    }
    plugin.enabled = True
    plugin.only_in_nav = False  # <-- bypass nav check

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

    dummy_confluence = DummyConfluence()
    plugin.confluence = dummy_confluence

    # Test creating a new page
    plugin.page_ids = {}
    plugin.page_versions = {}
    plugin.pages = [{"title": "New Page", "body": "<p>body</p>"}]
    plugin.on_post_build(config={}, files=[])
    assert "New Page" in dummy_confluence.created_pages

    # Test updating an existing page
    plugin.page_ids = {"Existing": "321"}
    plugin.page_versions = {"Existing": 1}
    plugin.pages = [{"title": "Existing", "body": "<p>updated body</p>"}]
    plugin.on_post_build(config={}, files=[])
    assert any(p[0] == "Existing" for p in dummy_confluence.updated_pages)


def test_add_or_update_attachment(monkeypatch, tmp_path, plugin):
    plugin.config = {
        "host_url": "https://example.atlassian.net/wiki",
        "username": "user",
        "password": "pass",
    }

    class DummyConfluence:
        def __init__(self):
            self.uploaded = False
            self.deleted = False

        def cql(self, query):
            return {}

    dummy_confluence = DummyConfluence()
    plugin.confluence = dummy_confluence

    # Create dummy file
    dummy_file = tmp_path / "file.txt"
    dummy_file.write_text("content")

    # Patch requests.Session methods
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

    # Should upload attachment as none exists
    plugin.add_or_update_attachment("Page Name", dummy_file)


def test_get_file_sha1(tmp_path, plugin):
    file = tmp_path / "hash.txt"
    content = "Hello, world!"
    file.write_text(content)
    expected_hash = "943a702d06f34599aee1f8da8ef9f7296031d699"  # precomputed SHA1 for that content

    actual_hash = plugin.get_file_sha1(file)
    assert actual_hash == expected_hash
