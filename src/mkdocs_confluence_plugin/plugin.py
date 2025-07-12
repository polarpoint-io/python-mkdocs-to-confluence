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
import string
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
                log.warning(
                    f"Exporting MKDOCS pages to Confluence turned OFF: set env var {enabled_if_env}=1 to enable."
                )
                return config
            else:
                log.info(
                    f"Exporting MKDOCS pages to Confluence turned ON (env var {enabled_if_env}=1)."
                )
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
                        log.warning(
                            f"DRYRUN: Would create missing intermediate page: {part}"
                        )
                        page_id = f"DUMMY_ID_{part}"
                    else:
                        log.warning(
                            f"Intermediate parent page '{part}' not found. Creating it..."
                        )
                        result = self.confluence.create_page(
                            space=plugin_cfg["space"],
                            title=part,
                            body=TEMPLATE_BODY,
                            parent_id=current_parent_id,
                            representation="storage",
                        )
                        if result and "id" in result:
                            page_id = result["id"]
                            self.page_ids[(part, current_parent_id)] = page_id
                            self.page_versions[(part, current_parent_id)] = 1
                            log.info(
                                f"Created intermediate parent page '{part}' with ID {page_id}"
                            )
                        else:
                            raise ValueError(
                                f"Failed to create intermediate parent page: {part}"
                            )

                current_parent_id = page_id

            self.parent_page_id = current_parent_id
            log.info(
                f"Using final root parent page ID {self.parent_page_id} for path '{plugin_cfg['parent_page_name']}'"
            )

        return config

    def on_pre_build(self, config, **kwargs):
        if not self.enabled:
            return
        log.info("🛠️ Pre-building Confluence folder hierarchy before content processing")
        self.create_folder_structure_only(self.tab_nav, parent_id=self.parent_page_id)

    def _normalize_parent_id(self, parent_id):
        return str(parent_id) if parent_id else None

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

    def create_folder_structure_only(self, nav_tree, parent_id=None):
        for node in nav_tree:
            if isinstance(node, str):
                # Leaf node, nothing to do here
                continue

            if isinstance(node, dict):
                for folder_title, children in node.items():
                    norm_title = folder_title.strip()
                    norm_key = (
                        self._normalize_title(norm_title),
                        str(parent_id) if parent_id else None,
                    )

                    # Skip if already created
                    if norm_key in self.page_ids:
                        folder_page_id = self.page_ids[norm_key]
                        log.debug(
                            f"Folder page '{norm_title}' already cached with ID {folder_page_id}"
                        )
                    else:
                        folder_page_id = self.find_page_id_or_global(
                            norm_title, parent_id=parent_id
                        )

                        if not folder_page_id:
                            if self.dryrun:
                                log.info(
                                    f"DRYRUN: Would create folder page '{norm_title}' under parent ID {parent_id}"
                                )
                            else:
                                log.info(
                                    f"Creating folder page '{norm_title}' under parent ID {parent_id}"
                                )
                                result = self.confluence.create_page(
                                    space=self.config["space"],
                                    title=norm_title,
                                    body="",  # No body for folder
                                    parent_id=parent_id,
                                    representation="storage",
                                )
                                if result and "id" in result:
                                    folder_page_id = result["id"]
                                    self.page_ids[norm_key] = folder_page_id
                                    self.page_versions[norm_key] = 1
                                    log.info(
                                        f"✅ Created folder page '{norm_title}' with ID {folder_page_id}"
                                    )
                                else:
                                    log.error(
                                        f"❌ Failed to create folder page '{norm_title}'"
                                    )
                                    continue
                        else:
                            self.page_ids[norm_key] = folder_page_id
                            self.page_versions[norm_key] = 1
                            log.debug(
                                f"Found existing folder page '{norm_title}' with ID {folder_page_id}"
                            )

                    # ✅ Recurse into children
                    self.create_folder_structure_only(
                        children, parent_id=folder_page_id
                    )

    def clear_cached_page_info(self):
        self.page_ids.clear()
        self.page_versions.clear()

    def dryrun_log(self, action: str, title: str, parent_id=None):
        parent_info = f" under parent ID {parent_id}" if parent_id else ""
        log.info(f"DRYRUN: Would {action} page '{title}'{parent_info}")

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
        self.tab_nav = nav_structure  # 🛠️ Use nested structure directly
        log.info(f"Auto-generated nested nav: {nav_structure}")

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
            lookup_key = (part, parent_id)
            if lookup_key not in self.page_ids:
                parent_id = self.find_page_id_or_global(part, parent_id)
            else:
                parent_id = self.page_ids[lookup_key]
        log.info(f"✅ Appending page '{page_titles[-1]}' with parent ID {parent_id}")

        log.debug(
            f"on_page_content: Adding '{page_titles[-1]}' with body length {len(html)}"
        )

        if html.strip() == TEMPLATE_BODY:
            log.warning(
                f"⚠️ HTML content for '{page_titles[-1]}' is TEMPLATE_BODY – check markdown rendering"
            )

        # Append the page with parent_id and real html body (do not replace with TEMPLATE_BODY)
        self.pages.append(
            {"title": page_titles[-1], "body": html, "parent_id": parent_id}
        )
        log.info(
            f"✅ Appending content page: {page_titles[-1]} under parent ID: {parent_id} with body length: {len(html)}"
        )
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

        self.debug_dump_pages()

        log.info(f"📄 Total pages defined in MkDocs: {len(self.pages)}")
        published_titles = [
            self._normalize_title(p["title"]) for p in self.pages if p.get("body")
        ]
        all_nav_titles = [
            self._normalize_title(n) for n in self._collect_all_page_names(self.tab_nav)
        ]

        missing = set(published_titles) - set(all_nav_titles)
        if missing:
            log.warning(
                f"🚨 These pages have content but were not matched in nav: {missing}"
            )

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
                                body="",  # Changed from TEMPLATE_BODY to empty string
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
                                "body": "",  # Changed from TEMPLATE_BODY to empty string
                                "parent_id": parent_id,
                                "is_folder": True,
                            }
                        )

                    self.ensure_folder_pages_exist(children, parent_id=folder_id)

    def get_page_url(self, title, parent_id=None):
        page_id = self.page_ids.get((title, parent_id))
        if not page_id:
            page_id = self.find_page_id(title, parent_id)
        if page_id:
            return f"{self.config['host_url'].rstrip('/')}/pages/viewpage.action?pageId={page_id}"
        return None

    def page_exists(self, title, parent_id=None):
        return self.find_page_id(title, parent_id) is not None

    def _normalize_title(self, title: str) -> str:
        table = str.maketrans("", "", string.punctuation)
        return title.strip().lower().translate(table).replace(" ", "")

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

                    self.page_ids[
                        (
                            self._normalize_title(title),
                            str(parent_id) if parent_id else None,
                        )
                    ] = page_id

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
                    (
                        p
                        for p in self.pages
                        if self._normalize_title(p["title"]) == normalized_node
                    ),
                    None,
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

    def publish_page(self, title, body, parent_id=None):
        if body is None or not body.strip() or body.strip() == TEMPLATE_BODY:
            log.warning(
                f"⚠️ Skipping publish of '{title}' due to empty or placeholder body"
            )
            return

        norm_title = self._normalize_title(title)
        norm_parent_id = str(parent_id) if parent_id is not None else None
        cache_key = self._cache_key(title, norm_parent_id)

        if self.dryrun:
            self.dryrun_log("publish", title, parent_id)
            return

        # Attempt to create the page
        try:
            log.info(
                f"📄 Attempting to create page '{title}' under parent ID {norm_parent_id}"
            )
            response = self.confluence.create_page(
                space=self.config["space"],
                title=title,
                body=body,
                parent_id=norm_parent_id,
                representation="storage",
            )
            if response and "id" in response:
                page_id = response["id"]
                self.page_ids[cache_key] = page_id
                self.page_versions[cache_key] = 1
                log.info(f"✅ Created page '{title}' with ID {page_id}")
                return
        except Exception as e:
            if "already exists with the same TITLE" in str(e):
                log.warning(f"⚠️ Page '{title}' already exists — attempting to update")
            else:
                log.error(f"❌ Failed to create page '{title}': {e}", exc_info=True)
                return

        # If creation failed, attempt to update
        page_id = self.find_page_id(title, parent_id=norm_parent_id)
        if not page_id:
            log.error(
                f"❌ Cannot update '{title}': page ID not found after creation failure"
            )
            return

        previous_version = self.page_versions.get(cache_key, 1)
        new_version = previous_version + 1

        try:
            self.confluence.update_page(
                page_id=page_id,
                title=title,
                body=body,
                parent_id=norm_parent_id,
                type="page",
                representation="storage",
                minor_edit=False,
            )
            self.page_ids[cache_key] = page_id
            self.page_versions[cache_key] = new_version
            log.info(f"🔁 Updated page '{title}' to version {new_version}")
        except Exception as e:
            log.error(
                f"❌ Failed to update page '{title}' (ID {page_id}): {e}", exc_info=True
            )

    def find_or_create_page(self, title, parent_id=None):
        norm_title = self._normalize_title(title)
        norm_parent_id = str(parent_id) if parent_id is not None else None
        cache_key = (norm_title, norm_parent_id)

        page_id = self.find_page_id(title, parent_id=parent_id)
        if page_id:
            return page_id

        log.info(f"Creating Confluence page '{title}' under parent ID {parent_id}")
        if self.dryrun:
            self.dryrun_log("create", title, parent_id)
            return None

        result = self.confluence.create_page(
            space=self.config["space"],
            title=title,
            body="",  # Changed from TEMPLATE_BODY to empty string
            parent_id=parent_id,
            representation="storage",
        )
        if result and "id" in result:
            page_id = result["id"]
            self.page_ids[cache_key] = page_id
            self.page_versions[cache_key] = 1
            return page_id

        log.error(f"Failed to create or find page '{title}'")
        return None

    def find_page_id(self, title, parent_id=None):
        norm_title = self._normalize_title(title)
        norm_parent_id = str(parent_id) if parent_id is not None else None
        cache_key = (norm_title, norm_parent_id)

        if cache_key in self.page_ids:
            log.debug(
                f"Cache hit for page '{title}' with parent ID {parent_id}: {self.page_ids[cache_key]}"
            )
            return self.page_ids[cache_key]

        # Add type="page" to filter only pages
        query = f'title="{title}" AND space="{self.config["space"]}" AND type="page"'
        log.debug(
            f"Running CQL query for page '{title}' in space '{self.config['space']}': {query}"
        )
        response = self.confluence.cql(query)
        log.debug(f"CQL response for '{title}': {response}")
        results = response.get("results", [])

        if not results:
            log.debug(
                f"No pages found for title '{title}' in space '{self.config['space']}'"
            )
            return None

        for result in results:
            # Try to get page ID, either directly or nested under 'content'
            page_id = result.get("id") or result.get("content", {}).get("id")
            if not page_id:
                log.warning(
                    f"Skipping result with no page ID for title '{title}': {result}"
                )
                continue

            # Fetch full page info to check parent (ancestors)
            try:
                page_info = self.confluence.get_page_by_id(page_id, expand="ancestors")
            except Exception as e:
                log.warning(
                    f"Failed to fetch full page info for page ID {page_id}: {e}"
                )
                continue

            ancestors = page_info.get("ancestors", [])
            if ancestors:
                immediate_parent_id = str(ancestors[-1]["id"])
            else:
                immediate_parent_id = None

            # Match on parent ID if provided
            if norm_parent_id is None or norm_parent_id == immediate_parent_id:
                log.debug(
                    f"Found matching page '{title}' with ID {page_id} and parent ID {immediate_parent_id}"
                )
                self.page_ids[cache_key] = str(page_id)
                return str(page_id)

        log.debug(
            f"No matching page '{title}' found with parent ID {parent_id} in space '{self.config['space']}'"
        )
        return None

    def find_page_id_global(self, title):
        cql = f'title = "{title}" and space = "{self.config["space"]}"'
        results = self.confluence.cql(cql)
        if results.get("results"):
            page = results["results"][0]
            page_id = page.get("id") or page.get("content", {}).get("id")
            version = page.get("version", {}).get("number", 1)
            log.debug(
                f"Found global page '{title}' with ID {page_id} (version {version})"
            )
            return page_id
        return None

    def find_page_id_or_global(self, title, parent_id=None):
        norm_parent_id = self._normalize_parent_id(parent_id)
        norm_title = self._normalize_title(title)
        key = (norm_title, norm_parent_id)

        if key in self.page_ids:
            return self.page_ids[key]

        page_id = self.find_page_id(title, parent_id)
        if page_id:
            self.page_ids[key] = page_id
            return page_id

        log.debug(
            f"Page '{title}' not found with parent ID {parent_id}, trying global lookup"
        )
        page_id = self.find_page_id_global(title)
        if page_id:
            self.page_ids[(norm_title, None)] = page_id
        return page_id

    def sync_page_attachments(self, page_title, parent_id):
        normalized_title = page_title.lower().replace(" ", "_")
        cache_key = self._cache_key(page_title, parent_id)
        page_id = self.page_ids.get(cache_key) or self.find_page_id(
            page_title, parent_id
        )
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
                        self.add_or_update_attachment(page_title, filepath)

    def add_or_update_attachment(self, page_title, filepath):
        log.info(f"Handling attachment for page '{page_title}': file '{filepath.name}'")
        cache_key = self._cache_key(page_title, self.parent_page_id)
        page_id = self.page_ids.get(cache_key) or self.find_page_id(
            page_title, self.parent_page_id
        )
        if not page_id:
            log.error(
                f"Cannot find Confluence page id for '{page_title}'. Attachment skipped."
            )
            return

        file_hash = self.get_file_sha1(filepath)
        attachment_comment = f"ConfluencePlugin [v{file_hash}]"

        existing_attachment = self.get_attachment(page_id, filepath)
        if existing_attachment:
            file_hash_regex = re.compile(r"\[v([a-f0-9]+)\]")
            current_hash_match = file_hash_regex.search(
                existing_attachment.get("metadata", {}).get("comment", "")
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

    def debug_dump_pages(self):
        if not self.pages:
            log.warning("⚠️ debug_dump_pages: self.pages is empty.")
            return

        log.info(f"📄 Debug dump of self.pages ({len(self.pages)} entries):")
        for idx, page in enumerate(self.pages, 1):
            title = page.get("title", "<no title>")
            parent_id = (
                str(page.get("parent_id"))
                if page.get("parent_id") is not None
                else "None"
            )
            body = page.get("body", "")
            is_folder = page.get("is_folder", False)
            body_preview = body[:60].replace("\n", " ") + (
                "..." if len(body) > 60 else ""
            )
            log.info(
                f"  {idx:3}: Title='{title}', ParentID='{parent_id}' ({type(parent_id).__name__}), "
                f"IsFolder={is_folder}, BodyLen={len(body)}, BodyPreview='{body_preview}'"
            )

        log.info("✅ End of debug dump.")

    def build_and_publish_tree(self, nav_tree, parent_id=None):
        norm_parent_id = str(parent_id) if parent_id is not None else None
        log.debug(f"build_and_publish_tree called with parent_id={norm_parent_id}")

        # First pass: create all folder pages
        for node in nav_tree:
            if isinstance(node, dict):
                for folder_title, children in node.items():
                    norm_title = folder_title.strip()
                    norm_key = (self._normalize_title(norm_title), norm_parent_id)

                    folder_page_id = self.find_page_id(
                        norm_title, parent_id=norm_parent_id
                    )
                    if folder_page_id:
                        log.info(
                            f"Found existing folder page '{norm_title}' with ID {folder_page_id}"
                        )
                        self.page_ids[norm_key] = folder_page_id
                    else:
                        log.info(
                            f"Folder page '{norm_title}' not found, creating under parent ID {norm_parent_id}"
                        )
                        if self.dryrun:
                            log.info(
                                f"DRYRUN: Would create folder page '{norm_title}' under parent ID {norm_parent_id}"
                            )
                            folder_page_id = None
                        else:
                            result = self.confluence.create_page(
                                space=self.config["space"],
                                title=norm_title,
                                body="",  # empty body for folders
                                parent_id=norm_parent_id,
                                representation="storage",
                            )
                            folder_page_id = result.get("id")
                            if folder_page_id:
                                log.info(
                                    f"Created folder page '{norm_title}' with ID {folder_page_id}"
                                )
                                self.page_ids[norm_key] = folder_page_id
                                self.page_versions[norm_key] = 1
                            else:
                                log.error(
                                    f"Failed to create folder page '{norm_title}'"
                                )
                                folder_page_id = None

                    # Append folder to self.pages to track it
                    if folder_page_id and not any(
                        self._normalize_title(p["title"])
                        == self._normalize_title(norm_title)
                        and (str(p.get("parent_id")) if p.get("parent_id") else None)
                        == norm_parent_id
                        for p in self.pages
                    ):
                        self.pages.append(
                            {
                                "title": norm_title,
                                "body": "",
                                "parent_id": folder_page_id,
                                "is_folder": True,
                            }
                        )

                    # Recursively create child folders and pages
                    if children:
                        if folder_page_id:
                            log.debug(
                                f"Recursing into folder '{norm_title}' with parent ID {folder_page_id}"
                            )
                            self.build_and_publish_tree(
                                children, parent_id=folder_page_id
                            )
                        else:
                            log.warning(
                                f"Cannot recurse into '{norm_title}' because folder_page_id is None"
                            )
            else:
                # This node is a content page (leaf)
                pass  # Will be handled in the second pass

        # Second pass: create and publish content pages under the current parent
        for node in nav_tree:
            if not isinstance(node, dict):
                page_title = node.strip()
                norm_title = self._normalize_title(page_title)
                content_page = next(
                    (
                        p
                        for p in self.pages
                        if self._normalize_title(p["title"]) == norm_title
                        and not p.get("is_folder")
                        and (str(p.get("parent_id")) if p.get("parent_id") else None)
                        == norm_parent_id
                    ),
                    None,
                )

                if content_page:
                    body = content_page.get("body", "")
                    if body in ("", "TEMPLATE", TEMPLATE_BODY):
                        log.warning(
                            f"Skipping page '{page_title}' due to empty or placeholder body"
                        )
                        continue
                    log.info(
                        f"Publishing content page '{page_title}' under parent ID {norm_parent_id}"
                    )
                    self.publish_page(page_title, body, norm_parent_id)
                    self.sync_page_attachments(page_title, norm_parent_id)
                else:
                    log.warning(
                        f"Content page '{page_title}' not found under parent {norm_parent_id}, creating placeholder"
                    )
                    if not self.dryrun:
                        created_id = self.find_or_create_page(
                            page_title, parent_id=norm_parent_id
                        )
                        if created_id:
                            self.pages.append(
                                {
                                    "title": page_title,
                                    "body": "",
                                    "parent_id": norm_parent_id,
                                    "is_folder": False,
                                }
                            )
                            self.publish_page(page_title, "", norm_parent_id)
                    else:
                        log.info(
                            f"DRYRUN: Would create placeholder page '{page_title}' under parent {norm_parent_id}"
                        )

    def _cache_key(self, title: str, parent_id) -> tuple:
        return (self._normalize_title(title), parent_id)

    def get_file_sha1(self, file_path):
        hash_sha1 = hashlib.sha1()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha1.update(chunk)
        return hash_sha1.hexdigest()
