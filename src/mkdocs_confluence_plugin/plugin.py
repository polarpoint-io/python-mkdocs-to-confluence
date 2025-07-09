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
import mkdocs
from mkdocs.config import config_options
from mkdocs.plugins import BasePlugin
from md2cf.confluence_renderer import ConfluenceRenderer
from os import environ
from pathlib import Path
from mkdocs.structure.nav import Navigation, Section
from mkdocs.structure.pages import Page
from atlassian import Confluence
from urllib.parse import quote

TEMPLATE_BODY = "<p> TEMPLATE </p>"
MKDOCS_FOOTER = "This page is auto-generated and will be overwritten at the next run. "

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
formatter = logging.Formatter("mk2conflu [%(levelname)8s] : %(message)s")
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
log.addHandler(stream_handler)


def wait_until_result(func, interval=10, timeout=10):
    start = time.time()

    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        while result is None and time.time() - start < timeout:
            result = func(*args, **kwargs)
            log.debug("Sleeping...")
            time.sleep(interval)
            log.debug("Returned value is None. Retrying...")
        return result

    return wrapper


@contextlib.contextmanager
def nostdout():
    save_stdout = sys.stdout
    sys.stdout = DummyFile()
    yield
    sys.stdout = save_stdout


class DummyFile(object):
    def write(self, x):
        pass


class ConfluencePlugin(BasePlugin):
    _id = 0
    config_scheme = (
        ("host_url", config_options.Type(str, default=None)),
        ("github_base_url", config_options.Type(str, default=None)),
        ("space", config_options.Type(str, default=None)),
        ("parent_page_name", config_options.Type(str, default=None)),
        (
            "username",
            config_options.Type(str, default=environ.get("CONFLUENCE_USERNAME", None)),
        ),
        (
            "password",
            config_options.Type(str, default=environ.get("CONFLUENCE_PASSWORD", None)),
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
        self.simple_log = False
        self.flen = 1
        self.session = requests.Session()
        self.page_attachments = {}
        self.page_ids = {}
        self.page_versions = {}
        self.pages = []
        self.only_in_nav = True
        self.dryrun = False

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

        if plugin_cfg.get("debug", False):
            log.setLevel(logging.DEBUG)

        enabled_if_env = plugin_cfg.get("enabled_if_env")
        if enabled_if_env:
            self.enabled = os.environ.get(enabled_if_env) == "1"
            if not self.enabled:
                log.warning(
                    f"Exporting MKDOCS pages to Confluence turned OFF: (set environment variable {enabled_if_env} to 1 to enable)"
                )
                return config
            else:
                log.info(
                    f"Exporting MKDOCS pages to Confluence turned ON by var {enabled_if_env}==1!"
                )
        else:
            log.info("Exporting MKDOCS pages to Confluence turned ON by default!")
            self.enabled = True

        self.dryrun = plugin_cfg.get("dryrun", False)
        if self.dryrun:
            log.warning("- DRYRUN MODE turned ON")

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
        ConfluencePlugin.tab_nav = self._collect_all_page_names(nav_structure)
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

    def on_page_markdown(self, markdown, page: Page, config, files):
        if not hasattr(page, "file") or not page.file.src_path:
            return markdown

        relative_path = page.file.src_path
        github_url = f"{self.config['github_base_url']}/{quote(relative_path)}"

        header = f"[Update markdown]({github_url})\n\n"
        return header + markdown

    def on_page_content(self, html, page: Page, config, files):
        ConfluencePlugin._id += 1
        self.session.auth = (self.config["username"], self.config["password"])
        if not self.enabled:
            return html

        self.pages.append({"title": page.title, "body": html})
        log.info(f"📄 Added page to publish queue: {page.title}")

        if self.config.get("enable_footer", False):
            relative_path = page.file.src_path
            github_url = f"{self.config['github_base_url']}/{quote(relative_path)}"
            confluence_info_macro = f"""
            <ac:structured-macro ac:name="info">
                <ac:rich-text-body>
                    <p style="font-size:small;">{MKDOCS_FOOTER}</p>
                    <p style="font-size:small;">✏️ <a href=\"{github_url}\">Edit this page on GitHub</a></p>
                </ac:rich-text-body>
            </ac:structured-macro>
            """
            html += confluence_info_macro

        return html

    def on_post_build(self, config, **kwargs):
        if not self.enabled:
            log.info("Confluence plugin disabled; skipping post-build.")
            return

        log.info(f"\U0001f4e4 Publishing pages to Confluence in hierarchy...")
        parent_id = None
        if self.config.get("parent_page_name"):
            parent_id = self.find_page_id(self.config["parent_page_name"])

        self.publish_nav_structure(self.tab_nav, parent_id=parent_id)

    def publish_nav_structure(self, nav_tree, parent_id=None):
        for node in nav_tree:
            if isinstance(node, dict):
                for title, children in node.items():
                    page_id = self.find_or_create_page(title, parent_id)
                    self.publish_nav_structure(children, parent_id=page_id)
            else:
                page = next((p for p in self.pages if p["title"] == node), None)
                if page:
                    self.publish_page(node, page["body"], parent_id)
                    self.sync_page_attachments(node)

    def find_or_create_page(self, title, parent_id=None):
        page_id = self.find_page_id(title)
        if page_id:
            return page_id

        log.info(f"Creating Confluence page '{title}' under parent ID {parent_id}")
        if self.dryrun:
            log.info(f"DRYRUN: Would create page '{title}'")
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
            self.page_ids[title] = page_id
            return page_id

        log.error(f"Failed to create or find page '{title}'")
        return None

    def publish_page(self, title, body, parent_id):
        page_id = self.find_page_id(title)

        if page_id:
            log.info(f"Updating Confluence page '{title}' (ID: {page_id})")
            if self.dryrun:
                log.info(f"DRYRUN: Would update page '{title}'")
                return

            response = self.confluence.update_page(page_id, title, body)
            if response:
                log.info(f"Successfully updated page '{title}'")
            else:
                log.error(f"Failed to update page '{title}'")
        else:
            log.info(f"Creating new Confluence page '{title}'")
            if self.dryrun:
                log.info(f"DRYRUN: Would create page '{title}'")
                return

            response = self.confluence.create_page(
                space=self.config["space"],
                title=title,
                body=body,
                parent_id=parent_id,
                representation="storage",
            )
            if response:
                log.info(f"Successfully created page '{title}'")
                page_id = response.get("id")
                if page_id:
                    self.page_ids[title] = page_id
            else:
                log.error(f"Failed to create page '{title}'")

    def update_page(self, title, body, page_obj):
        page_id = self.page_ids.get(title)
        if not page_id:
            log.error(f"Cannot update page '{title}': page ID not found.")
            return

        if self.dryrun:
            log.info(f"DRYRUN: Would update page '{title}'")
            return

        response = self.confluence.update_page(page_id, title, body)
        if response:
            log.info(f"Updated page '{title}'")
        else:
            log.error(f"Failed to update page '{title}'")

    def sync_page_attachments(self, page_title):
        for root, _, files in os.walk("docs"):
            for file in files:
                if file.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf")
                ):
                    filepath = Path(root) / file
                    if page_title.lower().replace(" ", "_") in filepath.stem.lower():
                        self.add_or_update_attachment(page_title, filepath)

    # def on_post_build(self, config, **kwargs):
    #     if not self.enabled:
    #         log.info("Confluence plugin disabled; skipping post-build.")
    #         return

    #     log.info(f"\U0001F4E4 Publishing pages to Confluence in hierarchy...")
    #     parent_id = None
    #     if self.config.get("parent_page_name"):
    #         parent_id = self.find_page_id(self.config["parent_page_name"])

    #     self.publish_nav_structure(self.tab_nav, parent_id=parent_id)

    #     log.info(f"🚧 Pages to publish: {len(self.pages)}")
    #     for page in self.pages:
    #         log.info(f" - {page['title']}")

    #     space = self.config.get("space")

    #     for page in getattr(self, "pages", []):
    #         title = page.get("title")
    #         body = page.get("body", "")

    #         if title in self.page_ids:
    #             page_id = self.page_ids[title]
    #             version = self.page_versions.get(title, 1)
    #             log.info(
    #                 f"Updating Confluence page '{title}' (ID: {page_id}) to version {version + 1}"
    #             )
    #             if self.dryrun:
    #                 log.info(f"DRYRUN: Would update page '{title}'")
    #                 continue
    #             response = self.confluence.update_page(
    #                 page_id, title, body, version=version + 1
    #             )
    #             if response:
    #                 log.info(f"Successfully updated page '{title}'")
    #                 self.page_versions[title] = version + 1
    #             else:
    #                 log.error(f"Failed to update page '{title}'")
    #         else:
    #             log.info(f"Creating new Confluence page '{title}'")
    #             if self.dryrun:
    #                 log.info(f"DRYRUN: Would create page '{title}'")
    #                 continue
    #             parent_id = None
    #             if self.config.get("parent_page_name"):
    #                 parent_id = self.find_page_id(self.config["parent_page_name"])
    #             response = self.confluence.create_page(
    #                 space=space,
    #                 title=title,
    #                 body=body,
    #                 parent_id=parent_id,
    #                 representation="storage",
    #             )
    #             if response:
    #                 log.info(f"Successfully created page '{title}'")
    #                 page_id = response.get("id")
    #                 if page_id:
    #                     self.page_ids[title] = page_id
    #                     self.page_versions[title] = 1
    #             else:
    #                 log.error(f"Failed to create page '{title}'")

    def update_page(self, title, body, page_obj):
        page_id = self.page_ids.get(title)
        if not page_id:
            log.error(f"Cannot update page '{title}': page ID not found.")
            return

        version = self.page_versions.get(title, 1) + 1
        if self.dryrun:
            log.info(f"DRYRUN: Would update page '{title}' to version {version}")
            return

        response = self.confluence.update_page(page_id, title, body, version=version)
        if response:
            log.info(f"Updated page '{title}' to version {version}")
            self.page_versions[title] = version
        else:
            log.error(f"Failed to update page '{title}'")

    def add_page(self, title, parent_id, body, page_obj):
        if self.dryrun:
            log.info(f"DRYRUN: Would add page '{title}' under parent ID {parent_id}")
            return

        response = self.confluence.create_page(
            space=self.config["space"],
            title=title,
            body=body,
            parent_id=parent_id,
            representation="storage",
        )
        if response:
            log.info(f"Added page '{title}' under parent ID {parent_id}")
            page_id = response.get("id")
            if page_id:
                self.page_ids[title] = page_id
                self.page_versions[title] = 1
        else:
            log.error(f"Failed to add page '{title}'")

    def find_page_id(self, title):
        if title in self.page_ids:
            return self.page_ids[title]

        cql = f'title = "{title}" and space = "{self.config["space"]}"'
        results = self.confluence.cql(cql)
        if results.get("results"):
            page = results["results"][0]
            if "id" in page:
                page_id = page["id"]
            elif "content" in page and "id" in page["content"]:
                page_id = page["content"]["id"]
            else:
                log.error(f"Cannot find 'id' in page result for title '{title}'")
                return None
            self.page_ids[title] = page_id
            self.page_versions[title] = page.get("version", {}).get("number", 1)
            return page_id
        return None

    def find_parent_name_of_page(self, title):
        # Simple stub, can be improved to actually query parents from Confluence if needed
        return self.config.get("parent_page_name")

    def add_or_update_attachment(self, page_name, filepath):
        log.info(f"Handling attachment: '{page_name}', FILE: {filepath}")
        page_id = self.find_page_id(page_name)
        if not page_id:
            log.error(
                f"Cannot find Confluence page id for '{page_name}'. Attachment skipped."
            )
            return

        file_hash = self.get_file_sha1(filepath)
        attachment_message = f"ConfluencePlugin [v{file_hash}]"
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

        self.upload_attachment(page_id, filepath, attachment_message)

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
        if response.status_code in [200, 201]:
            log.info(f"Uploaded attachment '{filepath.name}' to page ID {page_id}.")
        else:
            log.error(
                f"Failed to upload attachment '{filepath.name}' with status {response.status_code}."
            )

    def delete_attachment(self, attachment_id):
        url = f"{self.config['host_url']}/rest/api/content/{attachment_id}"
        response = self.session.delete(url)
        if response.status_code == 204:
            log.info(f"Deleted attachment ID {attachment_id}.")
        else:
            log.error(
                f"Failed to delete attachment ID {attachment_id} with status {response.status_code}."
            )

    def get_file_sha1(self, file_path):
        hash_sha1 = hashlib.sha1()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha1.update(chunk)
        return hash_sha1.hexdigest()
