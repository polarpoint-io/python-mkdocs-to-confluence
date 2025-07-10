import time
import os
import hashlib
import sys
import re
import requests
import mimetypes
import mistune
import contextlib
import logging
from urllib.parse import quote
from pathlib import Path

import mkdocs
from mkdocs.config import config_options
from mkdocs.plugins import BasePlugin
from mkdocs.structure.nav import Navigation
from mkdocs.structure.pages import Page
from md2cf.confluence_renderer import ConfluenceRenderer
from atlassian import Confluence

TEMPLATE_BODY = "<p> TEMPLATE </p>"
MKDOCS_FOOTER = "This page is auto-generated and will be overwritten at the next run."

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
formatter = logging.Formatter("mk2conflu [%(levelname)8s] : %(message)s")
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
log.addHandler(stream_handler)


@contextlib.contextmanager
def nostdout():
    save_stdout = sys.stdout
    sys.stdout = DummyFile()
    yield
    sys.stdout = save_stdout


class DummyFile:
    def write(self, x):
        pass


class ConfluencePlugin(BasePlugin):
    config_scheme = (
        ("host_url", config_options.Type(str, default=None)),
        ("github_base_url", config_options.Type(str, default=None)),
        ("space", config_options.Type(str, default=None)),
        ("parent_page_name", config_options.Type(str, default=None)),
        (
            "username",
            config_options.Type(str, default=os.environ.get("CONFLUENCE_USERNAME")),
        ),
        (
            "password",
            config_options.Type(str, default=os.environ.get("CONFLUENCE_PASSWORD")),
        ),
        ("enabled_if_env", config_options.Type(str, default=None)),
        ("verbose", config_options.Type(bool, default=False)),
        ("debug", config_options.Type(bool, default=False)),
        ("dryrun", config_options.Type(bool, default=False)),
        ("enable_footer", config_options.Type(bool, default=False)),
        ("default_labels", config_options.Type(list, default=["cpe", "mkdocs"])),
    )

    def __init__(self):
        self.enabled = True
        self.confluence_renderer = ConfluenceRenderer(use_xhtml=True)
        self.confluence_mistune = mistune.Markdown(renderer=self.confluence_renderer)
        self.session = requests.Session()
        self.pages = []
        self.page_ids = {}
        self.page_versions = {}
        self.dryrun = False
        self.tab_nav = []

    def on_config(self, config):
        plugin_cfg = self.config

        if not plugin_cfg.get("enabled", True):
            self.enabled = False
            return config

        if not plugin_cfg.get("username"):
            plugin_cfg["username"] = os.environ.get("CONFLUENCE_USERNAME")
        if not plugin_cfg.get("password"):
            plugin_cfg["password"] = os.environ.get("CONFLUENCE_PASSWORD")

        required_keys = ["host_url", "username", "password", "space"]
        missing_keys = [k for k in required_keys if not plugin_cfg.get(k)]
        if missing_keys:
            raise ValueError(f"Missing required config keys: {', '.join(missing_keys)}")

        self.confluence = Confluence(
            url=plugin_cfg["host_url"].replace("/rest/api/content", ""),
            username=plugin_cfg["username"],
            password=plugin_cfg["password"],
        )

        self.default_labels = plugin_cfg.get("default_labels", ["cpe", "mkdocs"])
        self.dryrun = plugin_cfg.get("dryrun", False)

        if plugin_cfg.get("debug", False):
            log.setLevel(logging.DEBUG)

        enabled_if_env = plugin_cfg.get("enabled_if_env")
        if enabled_if_env:
            self.enabled = os.environ.get(enabled_if_env) == "1"
            if not self.enabled:
                log.warning(f"Exporting MKDOCS pages to Confluence turned OFF: set env var {enabled_if_env}=1 to enable.")
                return config
            else:
                log.info(f"Exporting MKDOCS pages to Confluence turned ON (env var {enabled_if_env}=1).")
        else:
            log.info("Exporting MKDOCS pages to Confluence turned ON by default!")

        if self.dryrun:
            log.warning("DRYRUN MODE ENABLED: No changes will be made to Confluence.")

        if plugin_cfg.get("parent_page_name"):
            parent_parts = plugin_cfg["parent_page_name"].split("/")
            current_parent_id = None

            for part in parent_parts:
                page_id = self.find_page_id(part, parent_id=current_parent_id)
                if not page_id:
                    if self.dryrun:
                        log.warning(f"DRYRUN: Would create missing intermediate page: {part}")
                        page_id = f"DUMMY_ID_{part}"
                    else:
                        log.warning(f"Intermediate parent page '{part}' not found. Creating it...")
                        result = self.confluence.create_page(
                            space=plugin_cfg["space"],
                            title=part,
                            body=TEMPLATE_BODY,
                            parent_id=current_parent_id,
                            representation="storage"
                        )
                        if result and "id" in result:
                            page_id = result["id"]
                            self.page_ids[(part, current_parent_id)] = page_id
                            self.page_versions[(part, current_parent_id)] = 1
                            log.info(f"Created intermediate parent page '{part}' with ID {page_id}")
                        else:
                            raise ValueError(f"Failed to create intermediate parent page: {part}")

                current_parent_id = page_id

            self.parent_page_id = current_parent_id
            log.info(f"Using final root parent page ID {self.parent_page_id} for path '{plugin_cfg['parent_page_name']}'")

        return config


    def on_nav(self, nav: Navigation, config, files):
        def add_to_tree(tree, parts):
            part = parts[0].replace("_", " ").title()
            if len(parts) == 1:
                tree.setdefault(part, None)
            else:
                subtree = tree.setdefault(part, {})
                add_to_tree(subtree, parts[1:])

        tree = {}
        for file in files.documentation_pages():
            parts = file.src_path.split(os.sep)
            if parts[-1].endswith(".md"):
                parts[-1] = parts[-1][:-3]
            add_to_tree(tree, parts)

        def flatten_tree(t):
            result = []
            for key, value in sorted(t.items()):
                if value is None:
                    result.append(key)
                else:
                    result.append({key: flatten_tree(value)})
            return result

        nav_structure = flatten_tree(tree)
        self.tab_nav = self._collect_all_page_names(nav_structure)
        log.info(f"Auto-generated nested nav: {nav_structure}")

    def _collect_all_page_names(self, nav_list):
        result = []
        for item in nav_list:
            if isinstance(item, dict):
                for key, value in item.items():
                    result.append(key)
                    result.extend(self._collect_all_page_names(value))
            else:
                result.append(item)
        return result

    def on_page_markdown(self, markdown, page: Page, config, files):
        if not hasattr(page, "file") or not page.file.src_path:
            return markdown

        relative_path = page.file.src_path
        github_url = f"{self.config['github_base_url']}/{quote(relative_path)}"
        header = f"[Update markdown]({github_url})\n\n"
        return header + markdown


    def on_page_content(self, html, page: Page, config, files):
        if not self.enabled:
            return html

        # Build the page path and convert to Confluence-style titles
        page_path = page.file.src_path.replace("\\", "/").split("/")
        if page_path[-1].endswith(".md"):
            page_path[-1] = page_path[-1][:-3]
        page_titles = [part.replace("_", " ").title() for part in page_path]

        # Traverse to find the parent ID based on the folder hierarchy
        parent_id = self.parent_page_id
        for part in page_titles[:-1]:
            parent_id = self.page_ids.get((part, parent_id), parent_id)

        # Append the page with parent_id
        self.pages.append({
            "title": page_titles[-1],
            "body": html,
            "parent_id": parent_id
        })

        log.info(f"📄 Queued page for publish: {' / '.join(page_titles)}")

        # Optional footer
        if self.config.get("enable_footer", False):
            relative_path = page.file.src_path
            github_url = f"{self.config['github_base_url']}/{quote(relative_path)}"
            footer_macro = f"""
            <ac:structured-macro ac:name="info">
                <ac:rich-text-body>
                    <p style="font-size:small;">{MKDOCS_FOOTER}</p>
                    <p style="font-size:small;">✏️ <a href="{github_url}">Edit this page on GitHub</a></p>
                </ac:rich-text-body>
            </ac:structured-macro>
            """
            html += footer_macro

        return html



    def on_post_build(self, config, **kwargs):
        if not self.enabled:
            log.info("Confluence plugin disabled; skipping post-build.")
            return

        log.info(f"🔁 Nav structure for folder pages creation:\n{self.tab_nav}")

        # Recursively create folders and pages, publishing each respecting hierarchy
        self.build_and_publish_tree(self.tab_nav, parent_id=self.parent_page_id)

        log.info(f"📄 Total pages defined in MkDocs: {len(self.pages)}")



    def _normalize_title(self, title: str) -> str:
        return title.strip().lower().replace(" ", "")

    def ensure_folder_pages_exist(self, nav_tree, parent_id=None):
        for node in nav_tree:
            if isinstance(node, dict):
                for folder_title, children in node.items():
                    log.debug(
                        f"Checking folder page '{folder_title}' under parent ID {parent_id}"
                    )
                    folder_id = self.find_page_id(folder_title, parent_id=parent_id)
                    if not folder_id:
                        log.info(
                            f"Folder page '{folder_title}' not found, creating placeholder"
                        )
                        if self.dryrun:
                            log.info(
                                f"DRYRUN: Would create folder page '{folder_title}'"
                            )
                            folder_id = None
                        else:
                            result = self.confluence.create_page(
                                space=self.config["space"],
                                title=folder_title,
                                body=TEMPLATE_BODY,
                                parent_id=parent_id,
                                representation="storage",
                            )
                            if result and "id" in result:
                                folder_id = result["id"]
                                self.page_ids[(folder_title, parent_id)] = folder_id
                                self.page_versions[(folder_title, parent_id)] = 1
                                log.info(
                                    f"Created folder page '{folder_title}' with ID {folder_id}"
                                )
                            else:
                                log.error(
                                    f"Failed to create folder page '{folder_title}'"
                                )
                                folder_id = None
                    else:
                        log.debug(
                            f"Folder page '{folder_title}' already exists with ID {folder_id}"
                        )

                    if folder_id and not any(
                        p["title"] == folder_title and p.get("parent_id") == parent_id
                        for p in self.pages
                    ):
                        self.pages.append(
                            {
                                "title": folder_title,
                                "body": TEMPLATE_BODY,
                                "parent_id": parent_id,
                                "is_folder": True,
                            }
                        )

                    self.ensure_folder_pages_exist(children, parent_id=folder_id)

    def publish_nav_structure(self, nav_tree, parent_id=None):
        for node in nav_tree:
            if isinstance(node, dict):
                for title, children in node.items():
                    page_id = self.find_page_id(title, parent_id=parent_id)
                    if not page_id:
                        page_id = self.find_or_create_page(title, parent_id=parent_id)
                        log.info(
                            f"Folder page '{title}' created or found with ID {page_id}"
                        )

                    self.page_ids[(title, parent_id)] = page_id

                    if not any(
                        p["title"] == title and p.get("parent_id") == parent_id
                        for p in self.pages
                    ):
                        self.pages.append(
                            {
                                "title": title,
                                "body": TEMPLATE_BODY,
                                "parent_id": parent_id,
                                "is_folder": True,
                            }
                        )

                    self.publish_nav_structure(children, parent_id=page_id)
            else:
                normalized_node = self._normalize_title(node)
                page = next(
                                (p for p in self.pages if self._normalize_title(p["title"]) == normalized_node),
                                None
                            )
                if page:
                    log.debug(
                        f"Publishing page '{page['title']}' under parent ID {parent_id}"
                    )
                    self.publish_page(page["title"], page["body"], parent_id)
                    self.sync_page_attachments(page["title"], parent_id)
                else:
                    log.warning(
                        f"❌ Page titled '{node}' not found in self.pages under parent ID {parent_id}"
                    )

    def publish_page(self, title, body, parent_id):
        page_id = self.find_page_id(title, parent_id=parent_id)

        if page_id:
            log.info(f"Updating Confluence page '{title}' (ID: {page_id})")
            if self.dryrun:
                log.info(f"DRYRUN: Would update page '{title}'")
                return

            response = self.confluence.update_page(page_id, title, body)
            if response:
                log.info(f"Successfully updated page '{title}'")
                self.page_versions[(title, parent_id)] = (
                    self.page_versions.get((title, parent_id), 1) + 1
                )
            else:
                log.error(f"Failed to update page '{title}'")
        else:
            log.info(f"Creating new Confluence page '{title}'")
            if self.dryrun:
                log.info(f"DRYRUN: Would create page '{title}'")
                return

            try:
                response = self.confluence.create_page(
                    space=self.config["space"],
                    title=title,
                    body=body,
                    parent_id=parent_id,
                    representation="storage",
                )
            except requests.exceptions.HTTPError as e:
                if "already exists" in str(e):
                    log.warning(f"⚠️ Page '{title}' already exists — trying to update instead")
                    # Retry by finding it again
                    page_id = self.find_page_id(title, parent_id=parent_id)
                    if page_id:
                        response = self.confluence.update_page(page_id, title, body)
                        if response:
                            log.info(f"Successfully updated page '{title}' after creation conflict")
                            self.page_versions[(title, parent_id)] = (
                                self.page_versions.get((title, parent_id), 1) + 1
                            )
                            return
                        else:
                            log.error(f"Failed to update page '{title}' after conflict")
                            return
                raise  # re-raise any other HTTPError

            if response:
                log.info(f"Successfully created page '{title}'")
                page_id = response.get("id")
                if page_id:
                    self.page_ids[(title, parent_id)] = page_id
                    self.page_versions[(title, parent_id)] = 1
            else:
                log.error(f"Failed to create page '{title}'")


    def find_or_create_page(self, title, parent_id=None):
        # Try to find page ID with parent context
        page_id = self.find_page_id(title, parent_id=parent_id)
        if page_id:
            return page_id

        log.info(f"Creating Confluence page '{title}' under parent ID {parent_id}")
        if self.dryrun:
            log.info(f"DRYRUN: Would create page '{title}'")
            # Optionally, return a dummy ID or None
            return None

        result = self.confluence.create_page(
            space=self.config["space"],
            title=title,
            body=TEMPLATE_BODY,
            parent_id=parent_id,
            representation="storage",
        )
        if result and "id" in result:
            page_id = result["id"]
            # Use tuple key for consistency
            self.page_ids[(title, parent_id)] = page_id
            self.page_versions[(title, parent_id)] = 1
            return page_id

        log.error(f"Failed to create or find page '{title}'")
        return None

    def create_or_update_page(self, title, body, parent_id):
        log.debug(
            f"Attempting to find page ID for title='{title}', parent_id='{parent_id}'"
        )
        page_id = self.find_page_id(title, parent_id)
        if not page_id:
            log.info(f"Creating new Confluence page '{title}'")
            if self.dryrun:
                log.info(f"DRYRUN: Would create page '{title}'")
                return

            try:
                response = self.confluence.create_page(
                    space=self.config["space"],
                    title=title,
                    body=body,
                    parent_id=parent_id,
                    representation="storage",
                )
            except Exception as e:
                log.error(
                    f"Exception occurred while creating page '{title}': {e}",
                    exc_info=True,
                )
                return

            if response:
                page_id = response.get("id")
                if page_id:
                    log.info(f"Successfully created page '{title}' with ID {page_id}")
                    self.page_ids[(title, parent_id)] = page_id
                    self.page_versions[(title, parent_id)] = 1
                else:
                    log.error(f"Page creation response missing 'id' for page '{title}'")
            else:
                log.error(f"Failed to create page '{title}': No response received")
        else:
            version = self.page_versions.get((title, parent_id), 1) + 1
            log.info(f"Updating Confluence page '{title}' (new version {version})")
            if self.dryrun:
                log.info(f"DRYRUN: Would update page '{title}' to version {version}")
                return

            data = {
                "version": {"number": version},
                "title": title,
                "type": "page",
                "body": {"storage": {"value": body, "representation": "storage"}},
            }
            url = f"{self.config['host_url']}/rest/api/content/{page_id}"
            try:
                response = self.session.put(
                    url,
                    json=data,
                    auth=(self.config["username"], self.config["password"]),
                )
            except Exception as e:
                log.error(
                    f"Exception occurred while updating page '{title}': {e}",
                    exc_info=True,
                )
                return

            if response.ok:
                log.info(f"Successfully updated page '{title}' to version {version}")
                self.page_versions[(title, parent_id)] = version
            else:
                log.error(
                    f"Failed to update page '{title}': {response.status_code} {response.text}"
                )

    def find_page_id(self, title, parent_id=None):
        cql = f'title = "{title}" and space = "{self.config["space"]}"'
        results = self.confluence.cql(cql)
        if results.get("results"):
            page = None
            if parent_id:
                for r in results["results"]:
                    ancestors = (
                        r.get("ancestors")
                        or r.get("content", {}).get("ancestors")
                        or []
                    )
                    if ancestors and str(ancestors[-1].get("id")) == str(parent_id):
                        page = r
                        break
            else:
                page = results["results"][0]

            if page:
                page_id = page.get("id") or page.get("content", {}).get("id")
                version = page.get("version", {}).get("number", 1)
                log.debug(f"Found page '{title}' with ID {page_id} (version {version})")
                self.page_ids[(title, parent_id)] = page_id
                self.page_versions[(title, parent_id)] = version
                return page_id
        log.debug(
            f"Page '{title}' not found in space '{self.config['space']}' with parent ID {parent_id}"
        )
        return None

    def sync_page_attachments(self, page_title, parent_id):
        normalized_title = page_title.lower().replace(" ", "_")
        page_id = self.page_ids.get((page_title, parent_id))
        if not page_id:
            log.warning(
                f"Attachment sync skipped: Page ID for '{page_title}' with parent '{parent_id}' not found"
            )
            return
        for root, _, files in os.walk("docs"):
            for file in files:
                if file.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf")
                ):
                    filepath = Path(root) / file
                    if normalized_title in filepath.stem.lower().replace(" ", "_"):
                        self.add_or_update_attachment(page_id, page_title, filepath)

    def add_or_update_attachment(self, page_name, filepath):
        log.info(f"Handling attachment for page '{page_name}': file '{filepath.name}'")
        page_id = self.find_page_id(page_name)
        if not page_id:
            log.error(
                f"Cannot find Confluence page id for '{page_name}'. Attachment skipped."
            )
            return

        file_hash = self.get_file_sha1(filepath)
        attachment_comment = f"ConfluencePlugin [v{file_hash}]"

        existing_attachment = self.get_attachment(page_id, filepath)
        if existing_attachment:
            file_hash_regex = re.compile(r"\[v([a-f0-9]+)\]")
            current_hash_match = file_hash_regex.search(
                existing_attachment.get("metadata", "")
            )
            if current_hash_match and current_hash_match.group(1) == file_hash:
                log.info(
                    f"Attachment '{filepath.name}' is up-to-date. Skipping upload."
                )
                return
            else:
                self.delete_attachment(existing_attachment["id"])
                log.info(f"Deleted outdated attachment '{filepath.name}'.")

        self.upload_attachment(page_id, filepath, attachment_comment)

    def get_attachment(self, page_id, filepath):
        url = f"{self.config['host_url']}/rest/api/content/{page_id}/child/attachment"
        params = {"filename": filepath.name}
        response = self.session.get(url, params=params)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                return results[0]
        return None

    def upload_attachment(self, page_id, filepath, comment):
        url = f"{self.config['host_url']}/rest/api/content/{page_id}/child/attachment"
        with open(filepath, "rb") as f:
            files = {"file": (filepath.name, f, mimetypes.guess_type(filepath.name)[0])}
            data = {"comment": comment}
            response = self.session.post(url, files=files, data=data)
        if response.status_code in (200, 201):
            log.info(f"Uploaded attachment '{filepath.name}' to page ID {page_id}.")
        else:
            log.error(
                f"Failed to upload attachment '{filepath.name}' (status {response.status_code})."
            )

    def delete_attachment(self, attachment_id):
        url = f"{self.config['host_url']}/rest/api/content/{attachment_id}"
        response = self.session.delete(url)
        if response.status_code == 204:
            log.info(f"Deleted attachment ID {attachment_id}.")
        else:
            log.error(
                f"Failed to delete attachment ID {attachment_id} (status {response.status_code})."
            )

    def build_and_publish_tree(self, nav_tree, parent_id=None):
        """
        Recursively create folder pages and publish all pages respecting
        the navigation hierarchy in Confluence.
        """
        for node in nav_tree:
            if isinstance(node, dict):
                # Folder node with children
                for folder_title, children in node.items():
                    norm_title = folder_title.strip()

                    folder_page_id = self.find_page_id(norm_title, parent_id=parent_id)
                    if not folder_page_id:
                        if self.dryrun:
                            log.info(f"DRYRUN: Would create folder page '{norm_title}' under parent ID {parent_id}")
                            folder_page_id = None
                        else:
                            try:
                                log.info(f"Creating folder page '{norm_title}' under parent ID {parent_id}")
                                result = self.confluence.create_page(
                                    space=self.config["space"],
                                    title=norm_title,
                                    body=TEMPLATE_BODY,
                                    parent_id=parent_id,
                                    representation="storage",
                                )
                                if result and "id" in result:
                                    folder_page_id = result["id"]
                                    self.page_ids[(norm_title, parent_id)] = folder_page_id
                                    self.page_versions[(norm_title, parent_id)] = 1
                                    log.info(f"Created folder page '{norm_title}' with ID {folder_page_id}")
                                else:
                                    log.error(f"Failed to create folder page '{norm_title}'")
                                    folder_page_id = None
                            except requests.exceptions.HTTPError as e:
                                if "already exists" in str(e):
                                    log.warning(f"⚠️ Folder page '{norm_title}' already exists. Skipping creation.")
                                    folder_page_id = self.find_page_id(norm_title, parent_id=parent_id)
                                else:
                                    raise

                    # Only add folder to self.pages if it's not already there
                    if folder_page_id and not any(
                        p["title"] == norm_title and p.get("parent_id") == parent_id for p in self.pages
                    ):
                        self.pages.append({
                            "title": norm_title,
                            "body": TEMPLATE_BODY,
                            "parent_id": parent_id,
                            "is_folder": True,
                        })

                    # Recurse on children
                    self.build_and_publish_tree(children, parent_id=folder_page_id)

            else:
                # Leaf page
                page_title = node.strip()

                existing_page = next(
                    (p for p in self.pages if p["title"] == page_title and p.get("parent_id") == parent_id),
                    None,
                )

                if existing_page:
                    log.info(f"Publishing page '{page_title}' under parent ID {parent_id}")
                    self.publish_page(page_title, existing_page["body"], parent_id)
                    self.sync_page_attachments(page_title, parent_id)
                else:
                    log.warning(f"Page '{page_title}' not found under parent ID {parent_id}, creating placeholder")
                    if not self.dryrun:
                        created_id = self.find_or_create_page(page_title, parent_id=parent_id)
                        if created_id:
                            self.pages.append({
                                "title": page_title,
                                "body": TEMPLATE_BODY,
                                "parent_id": parent_id,
                                "is_folder": False,
                            })
                            self.publish_page(page_title, TEMPLATE_BODY, parent_id)
                            self.sync_page_attachments(page_title, parent_id)
                    else:
                        log.info(f"DRYRUN: Would create placeholder page '{page_title}' under parent ID {parent_id}")


    def get_file_sha1(self, file_path):
        hash_sha1 = hashlib.sha1()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha1.update(chunk)
        return hash_sha1.hexdigest()
