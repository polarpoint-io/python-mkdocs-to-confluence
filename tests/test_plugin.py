import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from mkdocs_confluence_plugin.plugin import MkdocsToConfluence
from mkdocs.config import load_config
from mkdocs.structure.pages import Page
from mkdocs.structure.files import Files



def test_plugin_instantiation():
    plugin = MkdocsToConfluence()
    assert isinstance(plugin, MkdocsToConfluence)
