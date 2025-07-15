import logging
from atlassian import Confluence

log = logging.getLogger("mk2conflu.api")

class ConfluenceAPI:
    def __init__(self, url, username, token, space):
        self.client = Confluence(
            url=url,
            username=username,
            password=token,
            cloud=True
        )
        self.space = space

    def get_page(self, title, parent_id=None):
        """Get a page by title and optional parent."""
        return self.client.get_page_by_title(space=self.space, title=title, parent_id=parent_id)

    def create_page(self, title, body, parent_id=None):
        """Create a new Confluence page under a given parent."""
        return self.client.create_page(
            space=self.space,
            title=title,
            body=body,
            parent_id=parent_id,
            type="page",
            representation="storage"
        )

    def update_page(self, page_id, title, body, version):
        """Update an existing Confluence page."""
        return self.client.update_page(
            parent_id=None,  # You typically don't change parent during update
            page_id=page_id,
            title=title,
            body=body,
            type="page",
            representation="storage",
            minor_edit=True,
            version_comment="Automated update",
            version=version
        )

    def get_page_id(self, title, parent_id=None):
        """Helper to retrieve page ID for a title under a parent."""
        page = self.get_page(title, parent_id)
        return page.get("id") if page else None

    def page_exists(self, title, parent_id=None):
        return self.get_page_id(title, parent_id) is not None

    def get_page_version(self, page_id):
        """Get the current version number of a page."""
        page = self.client.get_page_by_id(page_id, expand='version')
        return page.get("version", {}).get("number", 1)

    def attach_file(self, page_id, file_path):
        """Attach a file to a Confluence page."""
        return self.client.attach_file(file_path, page_id=page_id)

    def delete_page(self, page_id):
        """Delete a page (careful!)"""
        return self.client.remove_page(page_id)

    def find_page_by_title_anywhere(self, title):
        """Fallback: search for a page by title anywhere in the space."""
        results = self.client.cql(f'space="{self.space}" AND title="{title}"', limit=10)
        return results["results"][0] if results["results"] else None
