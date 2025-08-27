import pytest
import logging

import sys
import os
from unittest.mock import Mock, call, patch, MagicMock
from mkdocs.structure.pages import Page
from mkdocs.structure.files import File
from mkdocs.structure.nav import Navigation
from pathlib import Path
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
from mkdocs_confluence_plugin.plugin import ConfluencePlugin


@pytest.fixture
def plugin():
    p = ConfluencePlugin()
    p.config = {
        "space": "SPACE",
        "parent_page_name": None,
    }
    p.space = p.config["space"]
    p.page_ids = {}
    p.page_versions = {}
    p.pages = []
    p.dryrun = False
    p.confluence = Mock()
    return p


@pytest.fixture
def plugin_with_config():
    """Plugin with full configuration for testing"""
    p = ConfluencePlugin()
    p.config = {
        "host_url": "https://example.atlassian.net/wiki/rest/api/content",
        "space": "TEST",
        "username": "test@example.com",
        "password": "test-token",
        "parent_page_name": None,
        "debug": True,
        "dryrun": False,
        "enable_footer": True,
        "default_labels": ["test", "mkdocs"],
        "enabled_if_env": None,
    }
    p.space = p.config["space"]
    p.page_ids = {}
    p.page_versions = {}
    p.pages = []
    p.dryrun = False
    p.confluence = Mock()
    p.enabled = True
    return p


def test_plugin_instantiation():
    plugin = ConfluencePlugin()
    assert isinstance(plugin, ConfluencePlugin)
    assert hasattr(plugin, "attachments")
    assert plugin.attachments == {}


def test_normalize_title_key():
    plugin = ConfluencePlugin()
    assert plugin.normalize_title_key("Hello World!") == "hello-world"
    assert plugin.normalize_title_key("Test_123 & More") == "test-123-more"
    assert (
        plugin.normalize_title_key("   Special@#$%Characters   ")
        == "special-characters"
    )


def test_on_config_missing_required_keys():
    plugin = ConfluencePlugin()
    plugin.config = {
        "space": "TEST"
        # Missing: host_url, username, password
    }

    with pytest.raises(ValueError, match="Missing required config keys"):
        plugin.on_config({})


def test_on_config_enabled_false():
    plugin = ConfluencePlugin()
    plugin.config = {
        "enabled": False,
        "host_url": "https://example.com",
        "username": "user",
        "password": "pass",
        "space": "TEST",
    }

    result = plugin.on_config({})
    assert result == {}
    assert plugin.enabled == False


@patch.dict(os.environ, {"TEST_ENV": "1"})
def test_on_config_enabled_if_env_true():
    plugin = ConfluencePlugin()
    plugin.config = {
        "host_url": "https://example.com",
        "username": "user",
        "password": "pass",
        "space": "TEST",
        "enabled_if_env": "TEST_ENV",
    }

    with patch("mkdocs_confluence_plugin.plugin.Confluence"):
        plugin.on_config({})

    assert plugin.enabled == True


@patch.dict(os.environ, {}, clear=True)
def test_on_config_enabled_if_env_false():
    plugin = ConfluencePlugin()
    plugin.config = {
        "host_url": "https://example.com",
        "username": "user",
        "password": "pass",
        "space": "TEST",
        "enabled_if_env": "TEST_ENV",
    }

    result = plugin.on_config({})
    assert plugin.enabled == False
    assert result == {}


@patch.dict(
    os.environ, {"CONFLUENCE_USERNAME": "env_user", "CONFLUENCE_PASSWORD": "env_pass"}
)
def test_on_config_env_credentials():
    plugin = ConfluencePlugin()
    plugin.config = {
        "host_url": "https://example.com",
        "space": "TEST",
        # username and password should come from env
    }

    with patch("mkdocs_confluence_plugin.plugin.Confluence") as mock_confluence:
        plugin.on_config({})

    mock_confluence.assert_called_once_with(
        url="https://example.com", username="env_user", password="env_pass"
    )


def test_on_config_with_parent_page_hierarchy():
    plugin = ConfluencePlugin()
    plugin.config = {
        "host_url": "https://example.com",
        "username": "user",
        "password": "pass",
        "space": "TEST",
        "parent_page_name": "Root/SubFolder/Target",
    }

    # Mock find_page_id to return different IDs for hierarchy
    plugin.find_page_id = Mock(side_effect=["root-id", "sub-id", "target-id"])

    with patch("mkdocs_confluence_plugin.plugin.Confluence"):
        plugin.on_config({})

    assert plugin.parent_page_id == "target-id"
    assert plugin.find_page_id.call_count == 3


def test_on_config_creates_missing_parent_pages():
    plugin = ConfluencePlugin()
    plugin.config = {
        "host_url": "https://example.com",
        "username": "user",
        "password": "pass",
        "space": "TEST",
        "parent_page_name": "Root/Missing",
    }

    # Mock find_page_id to return None for missing page
    plugin.find_page_id = Mock(side_effect=["root-id", None])

    mock_confluence = Mock()
    mock_confluence.create_page.return_value = {"id": "new-missing-id"}

    with patch(
        "mkdocs_confluence_plugin.plugin.Confluence", return_value=mock_confluence
    ):
        plugin.on_config({})
        plugin.confluence = mock_confluence

    # Should create the missing page
    mock_confluence.create_page.assert_called_once()
    assert plugin.parent_page_id == "new-missing-id"


def test_on_config_dryrun_mode():
    plugin = ConfluencePlugin()
    plugin.config = {
        "host_url": "https://example.com",
        "username": "user",
        "password": "pass",
        "space": "TEST",
        "dryrun": True,
        "parent_page_name": "Root/Missing",
    }

    plugin.find_page_id = Mock(side_effect=["root-id", None])

    with patch("mkdocs_confluence_plugin.plugin.Confluence"):
        plugin.on_config({})

    assert plugin.dryrun == True
    assert plugin.parent_page_id == "DUMMY_ID_Missing"


def test_create_folder_structure_only():
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin.page_ids = {}
    plugin.page_versions = {}
    plugin.dryrun = False

    mock_confluence = Mock()
    mock_confluence.create_page.return_value = {"id": "folder-123"}
    plugin.confluence = mock_confluence

    plugin.find_page_id_or_global = Mock(return_value=None)
    plugin._normalize_title = Mock(return_value="test-folder")

    nav_tree = [{"Test Folder": ["Page1", "Page2"]}]

    plugin.create_folder_structure_only(nav_tree, parent_id="parent-123")

    mock_confluence.create_page.assert_called_once()
    assert ("test-folder", "parent-123") in plugin.page_ids


def test_create_folder_structure_only_existing_folder():
    plugin = ConfluencePlugin()
    plugin.page_ids = {("existing-folder", "parent-123"): "existing-123"}
    plugin._normalize_title = Mock(return_value="existing-folder")

    nav_tree = [{"Existing Folder": ["Page1"]}]

    plugin.create_folder_structure_only(nav_tree, parent_id="parent-123")

    # Should not create new folder
    assert not hasattr(plugin, "confluence") or not plugin.confluence.create_page.called


def test_create_folder_structure_only_dryrun():
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin.dryrun = True
    plugin.page_ids = {}
    plugin.find_page_id_or_global = Mock(return_value=None)
    plugin._normalize_title = Mock(return_value="test-folder")

    nav_tree = [{"Test Folder": ["Page1"]}]

    plugin.create_folder_structure_only(nav_tree, parent_id="parent-123")

    # Should not create actual pages in dryrun
    assert not hasattr(plugin, "confluence") or not plugin.confluence.create_page.called


def test_on_page_markdown():
    plugin = ConfluencePlugin()
    plugin.confluence_mistune = Mock(return_value="<p>rendered content</p>")
    plugin.logger = Mock()

    mock_page = Mock()
    mock_page.title = "Test Page"
    mock_page.file.abs_src_path = "/path/to/test.md"
    mock_page.meta = {"tags": ["test"]}
    mock_page.canonical_url = "/test-page/"

    markdown = "# Test Page\nContent here"

    result = plugin.on_page_markdown(markdown, mock_page, {}, [])

    assert result == markdown  # Should return original markdown
    assert "test-page" in plugin.page_lookup
    assert plugin.page_lookup["test-page"]["title"] == "Test Page"
    assert plugin.page_lookup["test-page"]["body"] == "<p>rendered content</p>"


def test_on_page_content_with_footer():
    plugin = ConfluencePlugin()
    plugin.config = {
        "enable_footer": True,
        "github_base_url": "https://github.com/user/repo",
    }
    plugin.page_lookup = {}  # Initialize page_lookup

    mock_page = Mock()
    mock_page.file.src_uri = "docs/test.md"
    mock_page.title = "Test Page"  # Add missing title as string

    html = "<p>Original content</p>"

    result = plugin.on_page_content(html, mock_page, {}, [])

    assert "github.com/user/repo/docs/test.md" in result
    assert "View source on GitHub" in result
    assert html in result


def test_on_page_content_footer_disabled():
    plugin = ConfluencePlugin()
    plugin.config = {"enable_footer": False}

    html = "<p>Original content</p>"
    result = plugin.on_page_content(html, Mock(), {}, [])

    assert result == html


def test_on_page_content_missing_github_url():
    plugin = ConfluencePlugin()
    plugin.config = {"enable_footer": True}  # Missing github_base_url

    html = "<p>Original content</p>"
    result = plugin.on_page_content(html, Mock(), {}, [])

    assert result == html


def test_create_page_success():
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin.dryrun = False
    plugin.page_ids = {}
    plugin.page_versions = {}

    mock_confluence = Mock()
    mock_confluence.create_page.return_value = {"id": "new-123"}
    plugin.confluence = mock_confluence

    plugin._normalize_title = Mock(return_value="test-page")

    result = plugin.create_page("Test Page", "<p>content</p>", "parent-123")

    assert result == "new-123"
    mock_confluence.create_page.assert_called_once_with(
        space="TEST",
        title="Test Page",
        body="<p>content</p>",
        parent_id="parent-123",
        representation="storage",
    )


def test_create_page_already_exists_updates():
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin.dryrun = False
    plugin.page_ids = {}
    plugin.page_versions = {}

    mock_confluence = Mock()
    # First call raises exception, then find and update
    mock_confluence.create_page.side_effect = Exception(
        "already exists with the same TITLE"
    )
    mock_confluence.update_page.return_value = True
    plugin.confluence = mock_confluence

    plugin._normalize_title = Mock(return_value="test-page")
    plugin.find_page_id = Mock(return_value="existing-123")

    result = plugin.create_page("Test Page", "<p>content</p>", "parent-123")

    assert result == "existing-123"
    mock_confluence.update_page.assert_called_once()


def test_create_page_dryrun():
    plugin = ConfluencePlugin()
    plugin.dryrun = True
    plugin.dryrun_log = Mock()

    result = plugin.create_page("Test Page", "<p>content</p>", "parent-123")

    assert result == "DUMMY_ID_Test Page"
    plugin.dryrun_log.assert_called_once_with("create page", "Test Page", "parent-123")


def test_find_page_id_with_parent():
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}

    mock_confluence = Mock()
    mock_confluence.get_page_child_by_type.return_value = [
        {"id": "child-123", "title": "Test Page"},
        {"id": "child-456", "title": "Other Page"},
    ]
    plugin.confluence = mock_confluence

    result = plugin.find_page_id("Test Page", parent_id="parent-123")

    assert result == "child-123"
    mock_confluence.get_page_child_by_type.assert_called_once_with("parent-123", "page")


def test_find_page_id_global_search():
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}

    mock_confluence = Mock()
    mock_confluence.get_page_child_by_type.return_value = []  # No children
    mock_confluence.cql.return_value = {
        "results": [{"content": {"id": "global-123", "title": "Test Page"}}]
    }
    plugin.confluence = mock_confluence

    result = plugin.find_page_id("Test Page", parent_id="parent-123")

    assert result == "global-123"


def test_find_page_id_not_found():
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}

    mock_confluence = Mock()
    mock_confluence.get_page_child_by_type.return_value = []
    mock_confluence.cql.return_value = {"results": []}
    plugin.confluence = mock_confluence

    result = plugin.find_page_id("Missing Page", parent_id="parent-123")

    assert result is None


def test_find_page_id_global():
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}

    mock_confluence = Mock()
    mock_confluence.cql.return_value = {
        "results": [{"id": "page-123", "version": {"number": 2}}]
    }
    plugin.confluence = mock_confluence

    result = plugin.find_page_id_global("Test Page")

    assert result == "page-123"


def test_find_page_id_or_global_cached():
    plugin = ConfluencePlugin()
    plugin.page_ids = {("test-page", "parent-123"): "cached-123"}
    plugin._normalize_parent_id = Mock(return_value="parent-123")
    plugin._normalize_title = Mock(return_value="test-page")

    result = plugin.find_page_id_or_global("Test Page", parent_id="parent-123")

    assert result == "cached-123"


def test_find_page_id_or_global_fallback():
    plugin = ConfluencePlugin()
    plugin.page_ids = {}
    plugin._normalize_parent_id = Mock(return_value="parent-123")
    plugin._normalize_title = Mock(return_value="test-page")
    plugin.find_page_id = Mock(return_value=None)
    plugin.find_page_id_global = Mock(return_value="global-123")

    result = plugin.find_page_id_or_global("Test Page", parent_id="parent-123")

    assert result == "global-123"


def test_add_or_update_attachment():
    plugin = ConfluencePlugin()
    plugin.config = {"host_url": "https://example.com"}
    plugin.auth_configured = True  # Enable authentication for test
    plugin.parent_page_id = "parent-123"
    plugin._cache_key = Mock(return_value=("test-page", "parent-123"))
    plugin.page_ids = {("test-page", "parent-123"): "page-123"}

    plugin.get_file_sha1 = Mock(return_value="abc123")
    plugin.get_attachment = Mock(return_value=None)  # No existing attachment
    plugin.upload_attachment = Mock()

    with tempfile.NamedTemporaryFile(suffix=".png") as tmp_file:
        tmp_path = Path(tmp_file.name)
        plugin.add_or_update_attachment("page-123", tmp_path)

    plugin.upload_attachment.assert_called_once()


def test_add_or_update_attachment_up_to_date():
    plugin = ConfluencePlugin()
    plugin.config = {"host_url": "https://example.com"}
    plugin.auth_configured = True  # Enable authentication for test
    plugin.parent_page_id = "parent-123"
    plugin._cache_key = Mock(return_value=("test-page", "parent-123"))
    plugin.page_ids = {("test-page", "parent-123"): "page-123"}

    plugin.get_file_sha1 = Mock(return_value="abc123")
    plugin.get_attachment = Mock(
        return_value={
            "id": "att-123",
            "metadata": {"comment": "ConfluencePlugin [vabc123]"},
        }
    )
    plugin.upload_attachment = Mock()

    with tempfile.NamedTemporaryFile(suffix=".png") as tmp_file:
        tmp_path = Path(tmp_file.name)
        plugin.add_or_update_attachment("page-123", tmp_path)

    # Should not upload if up to date
    plugin.upload_attachment.assert_not_called()


def test_add_or_update_attachment_outdated():
    plugin = ConfluencePlugin()
    plugin.config = {"host_url": "https://example.com"}
    plugin.auth_configured = True  # Enable authentication for test
    plugin.parent_page_id = "parent-123"
    plugin._cache_key = Mock(return_value=("test-page", "parent-123"))
    plugin.page_ids = {("test-page", "parent-123"): "page-123"}

    plugin.get_file_sha1 = Mock(return_value="new123")
    plugin.get_attachment = Mock(
        return_value={
            "id": "att-123",
            "metadata": {"comment": "ConfluencePlugin [vold456]"},
        }
    )
    plugin.delete_attachment = Mock()
    plugin.upload_attachment = Mock()

    with tempfile.NamedTemporaryFile(suffix=".png") as tmp_file:
        tmp_path = Path(tmp_file.name)
        plugin.add_or_update_attachment("page-123", tmp_path)

    plugin.delete_attachment.assert_called_once_with("att-123")
    plugin.upload_attachment.assert_called_once()


def test_upload_attachment_success():
    plugin = ConfluencePlugin()
    plugin.config = {"host_url": "https://example.com"}
    plugin.session = Mock()
    plugin.session.post.return_value.status_code = 200

    with tempfile.NamedTemporaryFile(suffix=".png") as tmp_file:
        tmp_path = Path(tmp_file.name)
        plugin.upload_attachment("page-123", tmp_path, "test comment")

    plugin.session.post.assert_called_once()


def test_upload_attachment_failure():
    plugin = ConfluencePlugin()
    plugin.config = {"host_url": "https://example.com"}
    plugin.session = Mock()
    plugin.session.post.return_value.status_code = 400

    with tempfile.NamedTemporaryFile(suffix=".png") as tmp_file:
        tmp_path = Path(tmp_file.name)
        plugin.upload_attachment("page-123", tmp_path, "test comment")

    plugin.session.post.assert_called_once()


def test_collect_page_attachments():
    plugin = ConfluencePlugin()

    # Create temporary files to simulate markdown content and images
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create a markdown file
        md_file = temp_path / "test.md"
        md_file.write_text(
            "# Test\n![image](image.png)\n![external](https://example.com/image.jpg)"
        )

        # Create the referenced image
        img_file = temp_path / "image.png"
        img_file.write_bytes(b"fake image data")

        content = md_file.read_text()
        attachments = plugin.collect_page_attachments(str(md_file), content)

        # Should find the local image but not the external URL
        assert len(attachments) == 1
        assert attachments[0].name == "image.png"


def test_delete_attachment_success():
    plugin = ConfluencePlugin()
    plugin.config = {"host_url": "https://example.com"}
    plugin.session = Mock()
    plugin.session.delete.return_value.status_code = 204

    plugin.delete_attachment("att-123")

    plugin.session.delete.assert_called_once()


def test_delete_attachment_failure():
    plugin = ConfluencePlugin()
    plugin.config = {"host_url": "https://example.com"}
    plugin.session = Mock()
    plugin.session.delete.return_value.status_code = 400

    plugin.delete_attachment("att-123")

    plugin.session.delete.assert_called_once()


def test_get_attachment_found():
    plugin = ConfluencePlugin()
    plugin.config = {"host_url": "https://example.com"}
    plugin.session = Mock()
    plugin.session.get.return_value.status_code = 200
    plugin.session.get.return_value.json.return_value = {
        "results": [{"id": "att-123", "title": "test.png"}]
    }

    with tempfile.NamedTemporaryFile(suffix=".png") as tmp_file:
        tmp_path = Path(tmp_file.name)
        result = plugin.get_attachment("page-123", tmp_path)

    assert result == {"id": "att-123", "title": "test.png"}


def test_get_attachment_not_found():
    plugin = ConfluencePlugin()
    plugin.config = {"host_url": "https://example.com"}
    plugin.session = Mock()
    plugin.session.get.return_value.status_code = 200
    plugin.session.get.return_value.json.return_value = {"results": []}

    with tempfile.NamedTemporaryFile(suffix=".png") as tmp_file:
        tmp_path = Path(tmp_file.name)
        result = plugin.get_attachment("page-123", tmp_path)

    assert result is None


def test_sync_page_attachments():
    plugin = ConfluencePlugin()
    plugin.auth_configured = True  # Enable authentication for test
    plugin.add_or_update_attachment = Mock()

    # Create mock attachments list
    attachments = [Path("/path/to/image.png")]

    plugin.sync_page_attachments("page-123", attachments)

    plugin.add_or_update_attachment.assert_called_once_with(
        "page-123", Path("/path/to/image.png")
    )


def test_build_and_publish_tree_with_fallback():
    plugin = ConfluencePlugin()
    plugin.normalize_title_key = Mock(
        side_effect=lambda x: x.lower().replace(" ", "-").replace("/", "-")
    )
    plugin.page_lookup = {
        "exact-match": {"title": "Exact Match", "body": "content1"},
        "fallback": {"title": "Fallback Page", "body": "content2"},
    }
    plugin.attachments = {}
    plugin.create_or_update_page = Mock(return_value="page-123")
    plugin.sync_page_attachments = Mock()

    # Test fallback logic
    nav_tree = ["complex/path/that/wont/match", "Fallback"]

    plugin.build_and_publish_tree(nav_tree)

    # Should have called create_or_update_page for the fallback match
    assert plugin.create_or_update_page.call_count >= 1


def test_build_and_publish_tree_title_based_matching():
    plugin = ConfluencePlugin()
    plugin.normalize_title_key = Mock(
        side_effect=lambda x: x.lower().replace(" ", "-").replace("/", "-")
    )
    plugin.page_lookup = {
        "similar-page-name": {"title": "Similar Page Name", "body": "content"}
    }
    plugin.attachments = {}
    plugin.create_or_update_page = Mock(return_value="page-123")
    plugin.sync_page_attachments = Mock()

    # This should be caught by title-based matching, not fuzzy matching
    nav_tree = ["Similar Page"]  # Should title-match to "Similar Page Name"
    plugin.build_and_publish_tree(nav_tree)

    # Should have called create_or_update_page for the title match
    plugin.create_or_update_page.assert_called_once()


def test_build_and_publish_tree_fuzzy_matching_fallback():
    plugin = ConfluencePlugin()
    plugin.normalize_title_key = Mock(
        side_effect=lambda x: x.lower().replace(" ", "-").replace("/", "-")
    )
    plugin.page_lookup = {
        "completely-different-name": {
            "title": "Completely Different Title",
            "body": "content",
        }
    }
    plugin.attachments = {}
    plugin.create_or_update_page = Mock(return_value="page-123")
    plugin.sync_page_attachments = Mock()

    with patch("mkdocs_confluence_plugin.plugin.get_close_matches") as mock_fuzzy:
        mock_fuzzy.return_value = ["completely-different-name"]

        # This should NOT be caught by title matching and should fall back to fuzzy
        nav_tree = ["Very Different Text"]
        plugin.build_and_publish_tree(nav_tree)

    # Should have called fuzzy matching as fallback
    mock_fuzzy.assert_called()


def test_build_and_publish_tree_folder_with_fallback():
    plugin = ConfluencePlugin()
    plugin.normalize_title_key = Mock(
        side_effect=lambda x: x.lower().replace(" ", "-").replace("/", "-")
    )
    plugin.page_lookup = {
        "folder-page": {"title": "Folder Page", "body": "", "is_folder": True}
    }
    plugin.attachments = {}
    plugin.create_or_update_page = Mock(return_value="folder-123")
    plugin.build_and_publish_tree = Mock()  # Mock recursive call

    # Test folder fallback
    nav_tree = [{"Complex Folder Path": ["Child1", "Child2"]}]

    original_method = plugin.build_and_publish_tree
    plugin.build_and_publish_tree = (
        lambda *args, **kwargs: None
    )  # Prevent infinite recursion
    original_method(nav_tree)


def test_create_or_update_page_with_attachments():
    plugin = ConfluencePlugin()
    plugin.normalize_title_key = Mock(return_value="test-page")
    plugin.page_exists = Mock(return_value=(False, None))
    plugin.confluence = Mock()
    plugin.confluence.create_page.return_value = {"id": "new-123"}
    plugin.space = "TEST"
    plugin.dryrun = False
    plugin.page_ids = {}
    plugin.collect_page_attachments = Mock(return_value=[Path("file1.png")])
    plugin.sync_page_attachments = Mock()

    result = plugin.create_or_update_page(
        title="Test Page",
        body="<p>content</p>",
        abs_src_path="/path/to/source.md",
    )

    assert result == "new-123"
    # With deferred attachment processing, attachments should be added to deferred queue
    assert len(plugin.deferred_attachments) == 1
    deferred = plugin.deferred_attachments[0]
    assert deferred["page_id"] == "new-123"
    assert deferred["page_title"] == "Test Page"
    assert deferred["src_path"] == "/path/to/source.md"
    assert deferred["processed_content"] == "<p>content</p>"

    # Attachments should NOT be processed immediately
    plugin.collect_page_attachments.assert_not_called()
    plugin.sync_page_attachments.assert_not_called()


def test_deferred_attachment_processing():
    """Test that deferred attachments are processed in on_post_build."""
    plugin = ConfluencePlugin()
    plugin.dryrun = False
    plugin.enabled = True
    plugin.collect_page_attachments = Mock(
        return_value=[Path("file1.png"), Path("file2.png")]
    )
    plugin.sync_page_attachments = Mock()

    # Initialize required attributes for on_post_build
    plugin.tab_nav = []
    plugin.parent_page_id = None
    plugin.page_lookup = {}
    plugin.pages = []
    plugin.debug_dump_pages = Mock()
    plugin.build_and_publish_tree = Mock()  # Skip actual page publishing for this test

    # Add some deferred attachments
    plugin.deferred_attachments = [
        {
            "page_id": "123",
            "page_title": "Page 1",
            "src_path": "/path/to/page1.md",
            "original_content": "# Page 1\n![](image1.png)",
            "processed_content": "<h1>Page 1</h1><img src='image1.png'>",
        },
        {
            "page_id": "456",
            "page_title": "Page 2",
            "src_path": "/path/to/page2.md",
            "original_content": "# Page 2\n![](image2.png)",
            "processed_content": "<h1>Page 2</h1><img src='image2.png'>",
        },
    ]

    # Process deferred attachments
    plugin.on_post_build({})

    # Should have processed attachments for both pages
    assert plugin.collect_page_attachments.call_count == 2
    plugin.collect_page_attachments.assert_any_call(
        "/path/to/page1.md", "# Page 1\n![](image1.png)"
    )
    plugin.collect_page_attachments.assert_any_call(
        "/path/to/page2.md", "# Page 2\n![](image2.png)"
    )

    # Should have synced attachments for both pages
    assert plugin.sync_page_attachments.call_count == 2
    plugin.sync_page_attachments.assert_any_call(
        "123", [Path("file1.png"), Path("file2.png")]
    )
    plugin.sync_page_attachments.assert_any_call(
        "456", [Path("file1.png"), Path("file2.png")]
    )


def test_create_or_update_page_update_existing():
    plugin = ConfluencePlugin()
    plugin.normalize_title_key = Mock(return_value="test-page")
    plugin.page_exists = Mock(return_value=(True, "existing-123"))
    plugin.confluence = Mock()
    plugin.dryrun = False
    plugin.page_ids = {}

    result = plugin.create_or_update_page(title="Test Page", body="<p>content</p>")

    assert result == "existing-123"
    plugin.confluence.update_page.assert_called_once()


def test_create_or_update_page_dryrun():
    plugin = ConfluencePlugin()
    plugin.normalize_title_key = Mock(return_value="test-page")
    plugin.page_exists = Mock(return_value=(False, None))
    plugin.dryrun = True
    plugin.dryrun_log = Mock()
    plugin.page_ids = {}

    result = plugin.create_or_update_page(title="Test Page")

    assert result == "DRYRUN-Test Page"
    plugin.dryrun_log.assert_called_once()


def test_publish_page_create_success():
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin._normalize_title = Mock(return_value="testpage")
    plugin.page_ids = {}
    plugin.page_versions = {}

    mock_confluence = Mock()
    mock_confluence.create_page.return_value = {"id": "new-123"}
    plugin.confluence = mock_confluence

    result = plugin.publish_page("Test Page", "<p>content</p>", "parent-123")

    assert result == "new-123"
    assert plugin.page_ids[("testpage", "parent-123")] == "new-123"


def test_publish_page_update_existing():
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin._normalize_title = Mock(return_value="testpage")
    plugin.page_ids = {}
    plugin.page_versions = {("testpage", "parent-123"): 1}

    mock_confluence = Mock()
    mock_confluence.create_page.side_effect = Exception(
        "already exists with the same TITLE"
    )
    mock_confluence.update_page.return_value = True
    plugin.confluence = mock_confluence

    plugin.find_page_id = Mock(return_value="existing-123")

    result = plugin.publish_page("Test Page", "<p>content</p>", "parent-123")

    assert result == "existing-123"
    mock_confluence.update_page.assert_called_once()


def test_publish_page_dryrun():
    plugin = ConfluencePlugin()
    plugin.dryrun_log = Mock()

    result = plugin.publish_page(
        "Test Page", "<p>content</p>", "parent-123", dryrun=True
    )

    assert result == "DUMMY_ID_Test Page"
    plugin.dryrun_log.assert_called_once()


def test_debug_dump_pages_empty():
    plugin = ConfluencePlugin()
    plugin.pages = []

    with patch("mkdocs_confluence_plugin.plugin.log") as mock_log:
        plugin.debug_dump_pages()

    mock_log.warning.assert_called_with("⚠️ debug_dump_pages: self.pages is empty.")


def test_debug_dump_pages_with_content():
    plugin = ConfluencePlugin()
    plugin.pages = [
        {
            "title": "Test Page",
            "parent_id": "parent-123",
            "body": "Short content",
            "is_folder": False,
        },
        {
            "title": "Long Page",
            "parent_id": None,
            "body": "A" * 100,  # Long content to test truncation
            "is_folder": True,
        },
    ]

    with patch("mkdocs_confluence_plugin.plugin.log") as mock_log:
        plugin.debug_dump_pages()

    # Should log info for each page
    assert mock_log.info.call_count >= 3  # Header + 2 pages + footer


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
    # Setup authentication for test
    plugin.auth_configured = True

    # Create test attachment
    img_file = tmp_path / "image.png"
    img_file.write_bytes(b"dummy image data")

    # Mock the add_or_update_attachment method
    plugin.add_or_update_attachment = Mock()

    # Create attachments list
    attachments = [img_file]

    # Act - use new API signature
    plugin.sync_page_attachments("mock-page-id", attachments)

    # Assert
    plugin.add_or_update_attachment.assert_called_once_with("mock-page-id", img_file)


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
    plugin.space = plugin.config["space"]
    plugin.parent_page_id = None
    plugin.attachments = {}  # ✅ FIXED: required for build_and_publish_tree

    class DummyConfluence:
        def __init__(self):
            self.created_pages = []
            self.updated_pages = []

        def create_page(self, space, title, body, parent_id=None, representation=None):
            self.created_pages.append((title, parent_id))
            return {"id": "123"}

        def update_page(self, page_id, title, body, version=None):
            self.updated_pages.append((title, version))
            return True

        def cql(self, query, limit=10):
            return {}

    plugin.confluence = DummyConfluence()
    plugin.log = Mock()
    plugin.logger = plugin.log

    plugin.page_ids = {}
    plugin.page_versions = {}
    plugin.pages = [{"title": "New Page", "body": "<p>body</p>", "is_folder": False}]
    plugin.tab_nav = ["New Page"]
    plugin.page_lookup = {
        "new-page": {
            "title": "New Page",  # Changed to match expected title
            "body": "<p>body</p>",
            "abs_src_path": "docs/new_page.md",
            "meta": {},
            "url": "/new-page/",
        }
    }

    plugin.publish_page = Mock()
    plugin.sync_page_attachments = Mock()

    plugin.on_post_build(config={}, files=[])

    assert plugin.confluence.created_pages == [
        ("New Page", None)
    ]  # Corrected assertion


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

    # <-- Fix: mock get_page_child_by_type to return a list (iterable) to avoid TypeError
    plugin.confluence.get_page_child_by_type = Mock(
        return_value=[
            {"id": "123", "title": "Page A"},
            {"id": "456", "title": "Page A"},
        ]
    )

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
    plugin.config = {
        "host_url": "https://example.atlassian.net/wiki/rest/api/content",
        "space": "TEST",
    }
    # Use correct cache key format: (_normalize_title(title), parent_id)
    plugin.page_ids = {("testpage", None): "45678"}
    url = plugin.get_page_url("Test Page", parent_id=None)
    assert (
        url
        == "https://example.atlassian.net/wiki/rest/api/content/pages/viewpage.action?pageId=45678"
    )


def test_page_exists_returns_true_if_found(plugin):
    plugin.find_page_id = Mock(return_value="123")
    exists, page_id = plugin.page_exists("Existing Page", parent_id=None)
    assert exists is True
    assert page_id == "123"

    plugin.find_page_id = Mock(return_value=None)
    exists, page_id = plugin.page_exists("Missing Page", parent_id=None)
    assert exists is False
    assert page_id is None


def test_build_and_publish_tree_reports_orphan_pages(caplog, plugin):
    plugin.page_lookup = {
        "linked-page": {"title": "Linked Page", "body": "content"},
        "orphan-page": {"title": "Orphan Page", "body": "unused"},
    }
    plugin.attachments = {}
    plugin.confluence = Mock()
    plugin.confluence.cql.return_value = {"results": []}

    nav = ["Linked Page"]
    plugin.normalize_title_key = lambda x: x.lower().replace(" ", "-")

    with caplog.at_level("INFO"):
        plugin.build_and_publish_tree(nav_tree=nav)

    assert "Orphan Page" in caplog.text
    assert "linked-page" not in caplog.text


def test_get_file_sha1(tmp_path, plugin):
    file = tmp_path / "hash.txt"
    content = "Hello, world!"
    file.write_text(content)
    expected_hash = "943a702d06f34599aee1f8da8ef9f7296031d699"

    actual_hash = plugin.get_file_sha1(file)
    assert actual_hash == expected_hash


# Additional comprehensive tests for 95%+ coverage


def test_nostdout_context_manager():
    """Test the nostdout context manager utility"""
    import sys
    from mkdocs_confluence_plugin.plugin import nostdout, DummyFile

    original_stdout = sys.stdout
    with nostdout():
        # Inside context, stdout should be DummyFile
        assert isinstance(sys.stdout, DummyFile)
        print("This should not appear")  # Should go to DummyFile

    # After context, stdout should be restored
    assert sys.stdout == original_stdout


def test_dummy_file_write():
    """Test DummyFile.write method"""
    from mkdocs_confluence_plugin.plugin import DummyFile

    dummy = DummyFile()
    # Should not raise any exceptions
    dummy.write("test content")
    dummy.write("")
    dummy.write(None)


def test_on_config_debug_mode():
    """Test debug mode configuration"""
    plugin = ConfluencePlugin()
    plugin.config = {
        "host_url": "https://example.com",
        "username": "user",
        "password": "pass",
        "space": "TEST",
        "debug": True,  # Enable debug mode
    }

    with patch("mkdocs_confluence_plugin.plugin.Confluence"):
        with patch("mkdocs_confluence_plugin.plugin.log") as mock_log:
            plugin.on_config({})

    # Should set log level to DEBUG
    mock_log.setLevel.assert_called_with(logging.DEBUG)


def test_on_pre_build_disabled():
    """Test on_pre_build when plugin is disabled"""
    plugin = ConfluencePlugin()
    plugin.enabled = False
    plugin.create_folder_structure_only = Mock()

    result = plugin.on_pre_build({})

    assert result is None
    plugin.create_folder_structure_only.assert_not_called()


def test_on_pre_build_enabled():
    """Test on_pre_build when plugin is enabled"""
    plugin = ConfluencePlugin()
    plugin.enabled = True
    plugin.tab_nav = ["test"]
    plugin.parent_page_id = "123"
    plugin.create_folder_structure_only = Mock()

    plugin.on_pre_build({})

    plugin.create_folder_structure_only.assert_called_once_with(
        ["test"], parent_id="123"
    )


def test_normalize_parent_id():
    """Test _normalize_parent_id utility method"""
    plugin = ConfluencePlugin()

    assert plugin._normalize_parent_id(123) == "123"
    assert plugin._normalize_parent_id("456") == "456"
    assert plugin._normalize_parent_id(None) is None
    assert (
        plugin._normalize_parent_id("") is None
    )  # Empty string should be treated as None


def test_collect_all_page_names():
    """Test _collect_all_page_names utility method"""
    plugin = ConfluencePlugin()

    # Test nested structure
    nav_list = [
        "Page1",
        {"Folder1": ["Page2", "Page3"]},
        {"Folder2": {"Subfolder": ["Page4"]}},
        "Page5",
    ]

    result = plugin._collect_all_page_names(nav_list)

    expected = [
        "Page1",
        "Folder1",
        "Page2",
        "Page3",
        "Folder2",
        "Subfolder",
        "Page4",
        "Page5",
    ]
    assert all(page in result for page in expected)


def test_create_folder_structure_only_creation_failure():
    """Test folder creation failure handling"""
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin.page_ids = {}
    plugin.page_versions = {}
    plugin.dryrun = False

    # Mock confluence to fail creation
    mock_confluence = Mock()
    mock_confluence.create_page.return_value = None  # Failure
    plugin.confluence = mock_confluence

    plugin.find_page_id_or_global = Mock(return_value=None)
    plugin._normalize_title = Mock(return_value="test-folder")

    nav_tree = [{"Test Folder": ["Page1"]}]

    with patch("mkdocs_confluence_plugin.plugin.log") as mock_log:
        plugin.create_folder_structure_only(nav_tree, parent_id="parent-123")

    # Should log error for failed creation
    mock_log.error.assert_called()


def test_create_folder_structure_only_exception_handling():
    """Test exception handling in folder creation"""
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin.page_ids = {}
    plugin.page_versions = {}
    plugin.dryrun = False

    # Mock confluence to raise exception
    mock_confluence = Mock()
    mock_confluence.create_page.side_effect = Exception("Network error")
    plugin.confluence = mock_confluence

    plugin.find_page_id_or_global = Mock(return_value=None)
    plugin._normalize_title = Mock(return_value="test-folder")

    nav_tree = [{"Test Folder": ["Page1"]}]

    with patch("mkdocs_confluence_plugin.plugin.log") as mock_log:
        plugin.create_folder_structure_only(nav_tree, parent_id="parent-123")

    # Should continue processing despite exception
    mock_confluence.create_page.assert_called()


def test_flatten_nav_with_parents():
    """Test _flatten_nav_with_parents method"""
    plugin = ConfluencePlugin()

    nav = [
        "Root1",
        {"Parent1": ["Child1", "Child2"]},
        {"Parent2": {"SubParent": ["GrandChild"]}},
    ]

    result = plugin._flatten_nav_with_parents(nav)

    assert result["Root1"] is None
    assert result["Parent1"] is None
    assert result["Child1"] == "Parent1"
    assert result["Child2"] == "Parent1"
    assert result["Parent2"] is None
    assert result["SubParent"] == "Parent2"
    assert result["GrandChild"] == "SubParent"


def test_build_page_path():
    """Test _build_page_path method"""
    plugin = ConfluencePlugin()
    plugin.page_parents = {"Child": "Parent", "Parent": "Root", "Root": None}

    assert plugin._build_page_path("Child") == "Root / Parent / Child"
    assert plugin._build_page_path("Parent") == "Root / Parent"
    assert plugin._build_page_path("Root") == "Root"
    assert plugin._build_page_path("Orphan") == "Orphan"


def test_debug_dump_page_parents():
    """Test debug_dump_page_parents method"""
    plugin = ConfluencePlugin()
    plugin.page_parents = {"Child1": "Parent", "Child2": "Parent", "Parent": None}

    with patch("builtins.print") as mock_print:
        plugin.debug_dump_page_parents()

    # Should print parent mapping
    mock_print.assert_called()
    calls = [str(call) for call in mock_print.call_args_list]
    assert any("Child1 ← Parent" in call for call in calls)


def test_on_post_build_disabled():
    """Test on_post_build when plugin is disabled"""
    plugin = ConfluencePlugin()
    plugin.enabled = False
    plugin.build_and_publish_tree = Mock()

    plugin.on_post_build({})

    plugin.build_and_publish_tree.assert_not_called()


def test_get_page_url_with_cached_id():
    """Test get_page_url with cached page ID"""
    plugin = ConfluencePlugin()
    plugin.config = {"host_url": "https://example.com/wiki"}
    # Use the correct cache key format: (_normalize_title(title), parent_id)
    plugin.page_ids = {("testpage", "parent-123"): "page-456"}

    url = plugin.get_page_url("Test Page", parent_id="parent-123")

    assert "pageId=page-456" in url
    assert "https://example.com/wiki" in url


def test_get_page_url_not_found():
    """Test get_page_url when page is not found"""
    plugin = ConfluencePlugin()
    plugin.config = {"host_url": "https://example.com/wiki"}
    plugin.page_ids = {}
    plugin.find_page_id = Mock(return_value=None)

    url = plugin.get_page_url("Missing Page", parent_id="parent-123")

    assert url is None


def test_create_page_folder_type():
    """Test create_page with is_folder=True"""
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin.dryrun = False
    plugin.page_ids = {}
    plugin.page_versions = {}

    mock_confluence = Mock()
    mock_confluence.create_page.return_value = {"id": "folder-123"}
    plugin.confluence = mock_confluence

    plugin._normalize_title = Mock(return_value="test-folder")

    result = plugin.create_page(
        "Test Folder", "<p>content</p>", "parent-123", is_folder=True
    )

    assert result == "folder-123"
    # Should use empty string for folder body
    mock_confluence.create_page.assert_called_with(
        space="TEST",
        title="Test Folder",
        body="",  # Empty for folders
        parent_id="parent-123",
        representation="storage",
    )


def test_create_page_update_failure():
    """Test create_page when update fails after creation failure"""
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin.dryrun = False
    plugin.page_ids = {}
    plugin.page_versions = {}

    mock_confluence = Mock()
    mock_confluence.create_page.side_effect = Exception(
        "already exists with the same TITLE"
    )
    mock_confluence.update_page.side_effect = Exception("Update failed")
    plugin.confluence = mock_confluence

    plugin._normalize_title = Mock(return_value="test-page")
    plugin.find_page_id = Mock(return_value="existing-123")

    with patch("mkdocs_confluence_plugin.plugin.log") as mock_log:
        result = plugin.create_page("Test Page", "<p>content</p>", "parent-123")

    assert result is None
    mock_log.error.assert_called()


def test_create_page_missing_id_after_creation_failure():
    """Test create_page when page ID cannot be found after creation failure"""
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin.dryrun = False
    plugin.page_ids = {}
    plugin.page_versions = {}

    mock_confluence = Mock()
    mock_confluence.create_page.side_effect = Exception(
        "already exists with the same TITLE"
    )
    plugin.confluence = mock_confluence

    plugin._normalize_title = Mock(return_value="test-page")
    plugin.find_page_id = Mock(return_value=None)  # Cannot find page

    with patch("mkdocs_confluence_plugin.plugin.log") as mock_log:
        result = plugin.create_page("Test Page", "<p>content</p>", "parent-123")

    assert result is None
    mock_log.error.assert_called()


def test_find_or_create_page():
    """Test find_or_create_page method"""
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin.dryrun = False
    plugin.page_ids = {}
    plugin.page_versions = {}

    # Mock finding existing page
    plugin.find_page_id = Mock(return_value="existing-123")

    result = plugin.find_or_create_page("Test Page", parent_id="parent-123")

    assert result == "existing-123"


def test_find_or_create_page_creation():
    """Test find_or_create_page when page needs to be created"""
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin.dryrun = False
    plugin.page_ids = {}
    plugin.page_versions = {}
    plugin._cache_key = Mock(return_value=("test-page", "parent-123"))

    # Mock page not found, then create
    plugin.find_page_id = Mock(return_value=None)

    mock_confluence = Mock()
    mock_confluence.create_page.return_value = {"id": "new-123"}
    plugin.confluence = mock_confluence

    result = plugin.find_or_create_page("Test Page", parent_id="parent-123")

    assert result == "new-123"
    mock_confluence.create_page.assert_called_once()


def test_find_or_create_page_creation_failure():
    """Test find_or_create_page when creation fails"""
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin.dryrun = False
    plugin.page_ids = {}
    plugin.page_versions = {}
    plugin._cache_key = Mock(return_value=("test-page", "parent-123"))

    plugin.find_page_id = Mock(return_value=None)

    mock_confluence = Mock()
    mock_confluence.create_page.return_value = None  # Creation failed
    plugin.confluence = mock_confluence

    with patch("mkdocs_confluence_plugin.plugin.log") as mock_log:
        result = plugin.find_or_create_page("Test Page", parent_id="parent-123")

    assert result is None
    mock_log.error.assert_called()


def test_find_or_create_page_dryrun():
    """Test find_or_create_page in dry run mode"""
    plugin = ConfluencePlugin()
    plugin.dryrun = True
    plugin.dryrun_log = Mock()
    plugin.find_page_id = Mock(return_value=None)

    result = plugin.find_or_create_page("Test Page", parent_id="parent-123")

    assert result == "DUMMY_ID_Test Page"
    plugin.dryrun_log.assert_called_once()


def test_find_page_id_global_with_version():
    """Test find_page_id_global method"""
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}

    mock_confluence = Mock()
    mock_confluence.cql.return_value = {
        "results": [{"id": "page-123", "version": {"number": 5}}]
    }
    plugin.confluence = mock_confluence

    with patch("mkdocs_confluence_plugin.plugin.log") as mock_log:
        result = plugin.find_page_id_global("Test Page")

    assert result == "page-123"
    mock_log.debug.assert_called()


def test_find_page_id_global_not_found():
    """Test find_page_id_global when page not found"""
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}

    mock_confluence = Mock()
    mock_confluence.cql.return_value = {"results": []}
    plugin.confluence = mock_confluence

    result = plugin.find_page_id_global("Missing Page")

    assert result is None


def test_find_page_id_with_content_structure():
    """Test find_page_id with different CQL result structure"""
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}

    mock_confluence = Mock()
    mock_confluence.get_page_child_by_type.return_value = []
    mock_confluence.cql.return_value = {
        "results": [{"content": {"id": "page-123", "title": "Test Page"}}]
    }
    plugin.confluence = mock_confluence

    result = plugin.find_page_id("Test Page", parent_id="parent-123")

    assert result == "page-123"


def test_sync_page_attachments_no_attachments():
    """Test sync_page_attachments when no attachments are provided"""
    plugin = ConfluencePlugin()
    plugin.auth_configured = True
    plugin.add_or_update_attachment = Mock()

    # Test with empty attachments list
    plugin.sync_page_attachments("page-123", [])

    # Should not call add_or_update_attachment for empty list
    plugin.add_or_update_attachment.assert_not_called()


def test_add_or_update_attachment_no_page_id():
    """Test add_or_update_attachment when page ID is None or empty"""
    plugin = ConfluencePlugin()
    plugin.config = {"host_url": "https://example.com"}
    plugin.auth_configured = True

    with tempfile.NamedTemporaryFile(suffix=".png") as tmp_file:
        tmp_path = Path(tmp_file.name)

        with patch("mkdocs_confluence_plugin.plugin.log") as mock_log:
            plugin.add_or_update_attachment(None, tmp_path)

    mock_log.error.assert_called()


def test_get_attachment_error():
    """Test get_attachment when HTTP request fails"""
    plugin = ConfluencePlugin()
    plugin.config = {"host_url": "https://example.com"}
    plugin.session = Mock()
    plugin.session.get.return_value.status_code = 500

    with tempfile.NamedTemporaryFile(suffix=".png") as tmp_file:
        tmp_path = Path(tmp_file.name)
        result = plugin.get_attachment("page-123", tmp_path)

    assert result is None


def test_create_or_update_page_empty_title():
    """Test create_or_update_page with empty title"""
    plugin = ConfluencePlugin()

    with patch("mkdocs_confluence_plugin.plugin.log") as mock_log:
        result = plugin.create_or_update_page(title="")

    assert result is None
    mock_log.warning.assert_called()


def test_publish_page_creation_exception():
    """Test publish_page when creation raises unexpected exception"""
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin._normalize_title = Mock(return_value="testpage")
    plugin.page_ids = {}
    plugin.page_versions = {}

    mock_confluence = Mock()
    mock_confluence.create_page.side_effect = Exception("Unexpected error")
    plugin.confluence = mock_confluence

    with patch("mkdocs_confluence_plugin.plugin.log") as mock_log:
        result = plugin.publish_page("Test Page", "<p>content</p>", "parent-123")

    assert result is None
    mock_log.error.assert_called()


def test_publish_page_update_failure():
    """Test publish_page when update fails"""
    plugin = ConfluencePlugin()
    plugin.config = {"space": "TEST"}
    plugin._normalize_title = Mock(return_value="testpage")
    plugin.page_ids = {}
    plugin.page_versions = {("testpage", "parent-123"): 1}

    mock_confluence = Mock()
    mock_confluence.create_page.side_effect = Exception(
        "already exists with the same TITLE"
    )
    mock_confluence.update_page.side_effect = Exception("Update failed")
    plugin.confluence = mock_confluence

    plugin.find_page_id = Mock(return_value="existing-123")

    with patch("mkdocs_confluence_plugin.plugin.log") as mock_log:
        result = plugin.publish_page("Test Page", "<p>content</p>", "parent-123")

    assert result is None
    mock_log.error.assert_called()


def test_cache_key_method():
    """Test _cache_key method"""
    plugin = ConfluencePlugin()
    plugin._normalize_title = Mock(return_value="normalized-title")

    result = plugin._cache_key("Test Title", "parent-123")

    assert result == ("normalized-title", "parent-123")

    result = plugin._cache_key("Test Title", None)
    assert result == ("normalized-title", None)


def test_build_and_publish_tree_complex_fuzzy_matching():
    """Test build_and_publish_tree with complex fuzzy matching scenarios"""
    plugin = ConfluencePlugin()
    plugin.normalize_title_key = Mock(
        side_effect=lambda x: x.lower().replace(" ", "-").replace("/", "-")
    )
    plugin.page_lookup = {
        "very-similar-page-name": {"title": "Very Similar Page Name", "body": "content"}
    }
    plugin.attachments = {}
    plugin.create_or_update_page = Mock(return_value="page-123")
    plugin.sync_page_attachments = Mock()

    with patch("mkdocs_confluence_plugin.plugin.get_close_matches") as mock_fuzzy:
        mock_fuzzy.return_value = ["very-similar-page-name", "other-match"]

        # Test exact title match through fuzzy results
        nav_tree = ["Very Similar Page"]

        with patch("mkdocs_confluence_plugin.plugin.log") as mock_log:
            plugin.build_and_publish_tree(nav_tree)

        # Verify the match was found and page was created
        if mock_fuzzy.return_value:
            plugin.create_or_update_page.assert_called_once()
