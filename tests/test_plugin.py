import pytest
from plugin import ConfluenceUploaderPlugin
from mkdocs.config.defaults import get_schema, load_config
from mkdocs.structure.pages import Page
from mkdocs.structure.files import Files

def test_plugin_instantiation():
    plugin = ConfluenceUploaderPlugin()
    assert isinstance(plugin, ConfluenceUploaderPlugin)

def test_on_post_build_runs(monkeypatch):
    plugin = ConfluenceUploaderPlugin()

    class DummyConfig:
        site_dir = "site"
        config_file_path = "mkdocs.yml"

    try:
        plugin.on_post_build(DummyConfig())
    except Exception as e:
        pytest.fail(f"Plugin failed on post_build: {e}")
