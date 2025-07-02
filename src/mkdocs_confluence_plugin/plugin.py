import time
import os
import hashlib
import sys
import re
import tempfile
import shutil
import requests
import mimetypes
import mistune
import contextlib
import logging
import mkdocs
from time import sleep
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

        # Initialize page tracking structures
        self.page_ids = {}
        self.page_versions = {}
        self.pages = []

    def on_pre_build(self, config):
        self.page_ids.clear()
        self.page_versions.clear()
        self.pages.clear()

    def load_pages(self):
        pages = []
        for file_path in Path(self.docs_dir).rglob("*.md"):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            rendered = self.renderer(content)
            pages.append({"title": file_path.stem, "body": rendered})
        self.pages = pages
        return pages

    def __recursive_search(self, items, depth: int):
        spaces = depth * 4
        sections = ""
        for item in items:
            log.debug(f"Item type is {type(item)}")
            if type(item) == mkdocs.structure.nav.Section:
                sections = (
                    sections
                    + (spaces * " ")
                    + f"{item.title}\n{self.__recursive_search(item.children, depth + 1)}"
                )
            elif type(item) == mkdocs.structure.pages.Page:
                sections = sections + (spaces * " ") + f"{item.url[:-1]}" + ".md\n"
            else:
                log.error(f"Type doesn't match. Type was {type(item)}")
        return sections

    def on_nav(self, nav: Navigation, config, files):
        log.debug(f"The contents of nav itself are \n{nav}")

        log.debug(f"Length of nav is: {len(nav)}")
        log.debug(f"nav.items is {nav.items}")

        nav_items = self.__recursive_search(nav.items, 0)
        log.debug(f"Nav: {nav_items}")

        ConfluencePlugin.tab_nav = nav_items.split("\n")

        log.info(f"Identified tabs: {ConfluencePlugin.tab_nav}")

    def on_files(self, files, config):
        pages = files.documentation_pages()
        try:
            self.flen = len(pages)
            log.info(f"Number of Files in directory tree: {self.flen}")
        except:
            log.error(
                "You have no documentation pages"
                "in the directory tree, please add at least one!"
            )

    def on_post_template(self, output_content, template_name, config):
        pass

    def on_config(self, config):
        self.config = config.get("confluence", {})
        conf = self.config

        # If disabled, skip required key validation
        if not conf.get("enabled", True):
            self.enabled = False
            return

        if not conf.get("username"):
            conf["username"] = os.environ.get("CONFLUENCE_USERNAME")
        if not conf.get("password"):
            conf["password"] = os.environ.get("CONFLUENCE_PASSWORD")

        required_keys = ["host_url", "username", "password", "space"]
        missing_keys = [key for key in required_keys if not conf.get(key)]
        if missing_keys:
            raise ValueError(f"Missing required config keys: {', '.join(missing_keys)}")

        self.confluence = Confluence(
            url=conf["host_url"].replace("/rest/api/content", ""),
            username=conf["username"],
            password=conf["password"],
        )

        # Use config default labels or fallback to ["cpe", "mkdocs"]
        self.default_labels = self.config.get("default_labels", ["cpe", "mkdocs"])

        self.only_in_nav = True

        if conf.get("debug", False):
            log.setLevel(logging.DEBUG)

        if "enabled_if_env" in conf:
            env_name = conf["enabled_if_env"]
            if env_name:
                self.enabled = os.environ.get(env_name) == "1"
                if not self.enabled:
                    log.warning(
                        "Exporting MKDOCS pages to Confluence turned OFF: "
                        f"(set environment variable {env_name} to 1 to enable)"
                    )
                    return
                else:
                    log.info(
                        "Exporting MKDOCS pages to Confluence "
                        f"turned ON by var {env_name}==1!"
                    )
                    self.enabled = True
            else:
                log.warning(
                    "Exporting MKDOCS pages to Confluence turned OFF: "
                    f"(set environment variable {env_name} to 1 to enable)"
                )
                return
        else:
            log.info("Exporting MKDOCS pages to Confluence turned ON by default!")
            self.enabled = True

        if conf.get("dryrun", False):
            log.warning("- DRYRUN MODE turned ON")
            self.dryrun = True
        else:
            self.dryrun = False

    def on_post_build(self, config, **kwargs):
        files = kwargs.get("files")
        import requests
        import logging
        import json

        log = logging.getLogger("mkdocs.plugins")

        if not getattr(self, "enabled", True):
            log.info("Confluence plugin is disabled; skipping post-build step.")
            return

        space = getattr(self, "space", None) or config.get("confluence", {}).get(
            "space"
        )

        for page in getattr(self, "pages", []):
            title = page.get("title")
            body = page.get("body", "")

            if title in self.page_ids:
                # Update existing page
                page_id = self.page_ids[title]
                version = self.page_versions.get(title, 1)

                data = {
                    "version": {"number": version + 1},
                    "title": title,
                    "type": "page",
                    "body": {"storage": {"value": body, "representation": "storage"}},
                }

                url = f"{self.curl_url}{page_id}"
                log.info(
                    f"Updating Confluence page '{title}' (ID: {page_id}) to version {version + 1}"
                )

                response = requests.put(url, json=data, auth=self.auth)

                if response.status_code >= 200 and response.status_code < 300:
                    log.info(f"Successfully updated page '{title}'")
                else:
                    log.error(
                        f"Failed to update page '{title}' (status code: {response.status_code}): {response.text}"
                    )

            else:
                # Create new page
                log.info(f"Creating new Confluence page '{title}'")

                data = {
                    "type": "page",
                    "title": title,
                    "space": {"key": space},
                    "body": {"storage": {"value": body, "representation": "storage"}},
                }

                if getattr(self, "parent_id", None):
                    data["ancestors"] = [{"id": self.parent_id}]

                response = requests.post(
                    self.curl_url,
                    auth=self.auth,
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(data),
                )

                if response.status_code >= 200 and response.status_code < 300:
                    log.info(f"Successfully created page '{title}'")
                else:
                    log.error(
                        f"Failed to create page '{title}' (status code: {response.status_code}): {response.text}"
                    )

    def on_page_markdown(self, markdown, page: Page, config, files):
        if not hasattr(page, "file") or not page.file.src_path:
            return markdown

        relative_path = page.file.src_path
        github_url = f"{self.config['github_base_url']}/{quote(relative_path)}"

        header = f"[Edit source in GitHub]({github_url})\n\n"
        return header + markdown

    def on_post_page(self, output, page: Page, config):
        site_dir = config.get("site_dir")
        attachments = self.page_attachments.get(page.title, [])

        log.debug(
            f"UPLOADING ATTACHMENTS TO CONFLUENCE FOR '{page.title}': {attachments}"
        )
        for attachment in attachments:
            log.info(f"looking for {attachment} in {site_dir}")
            for p in Path(site_dir).rglob(f"*{attachment}"):
                self.add_or_update_attachment(page.title, p)
        return output

    def on_page_content(self, html, page: Page, config, files):
        ConfluencePlugin._id += 1
        self.session.auth = (self.config["username"], self.config["password"])
        if not self.enabled:
            return html

        nav = [tab.strip() for tab in ConfluencePlugin.tab_nav]

        if self.only_in_nav:
            if not (page.title in nav or page.file.src_uri in nav):
                log.debug(f"Page '{page.file.src_uri}' is not in nav! Skipping it.")
                return html

        log.info(f"@ START @ '{page.file.src_uri}'")

        try:
            log.debug(f"Get section first level parent title")
            try:
                parent = self.__get_section_title(page.ancestors[0].__repr__())
            except IndexError as e:
                log.debug(
                    f"{e}. No first parent! Assuming '{self.config['parent_page_name']}'..."
                )
                parent = self.config["parent_page_name"]

            log.debug(f"'{parent}'")

            if self.config["parent_page_name"] is not None:
                main_parent = self.config["parent_page_name"]
            else:
                main_parent = self.config["space"]

            log.debug(f"Get section second level parent title")

            try:
                second_level_parent = self.__get_section_title(
                    page.ancestors[1].__repr__()
                )
            except IndexError as e:
                log.debug(f"Assuming parent is '{main_parent}'...")
                second_level_parent = main_parent

            log.debug(
                f"PARENT0: '{parent}', PARENT1: '{second_level_parent}', MAIN PARENT: '{main_parent}'"
            )

            log.debug(
                f"Processing SPACE: '{self.config['space']}', PARENT: '{parent}', TITLE: '{page.title}'"
            )

            attachments = []

            try:
                regex = r'img .* src="(?P<src>(?:(?!https)[^"])+)"'
                for match in re.finditer(regex, html):
                    file = match.group("src")
                    log.debug(f"Found image: {file}")

                    file_name = os.path.basename(file)
                    html = re.sub(
                        rf'img .* src="{file}" />',
                        f'ac:image ac:style="max-height: 250.0px;"><ri:attachment ri:filename="{file_name}"/></ac:image>',
                        html,
                    )
                    path = os.path.normpath(os.path.join(page.file.src_path, file))
                    attachments.append(path)
            except AttributeError as e:
                log.debug(f"WARN(({e}): No images found in html. Proceed..")

            if attachments:
                log.info(f"Page has attachments: {attachments}")
                self.page_attachments[page.title] = attachments

            page_id = self.find_page_id(page.title)

            if self.config.get("enable_footer", False):
                relative_path = page.file.src_path
                github_url = f"{self.config['github_base_url']}/{quote(relative_path)}"

                confluence_info_macro = f"""
                <ac:structured-macro ac:name="info">
                    <ac:rich-text-body>
                        <p style="font-size:small;">{MKDOCS_FOOTER}</p>
                        <p style="font-size:small;">✏️ <a href="{github_url}">Edit source in GitHub</a></p>
                    </ac:rich-text-body>
                </ac:structured-macro>
                """

                # Append the info macro
                html += confluence_info_macro

                log.info(f"Added Confluence warning macro footer to '{page.title}'.")

            # Update page
            if page_id:
                log.info("Page exists...")
                parent_name = self.find_parent_name_of_page(page.title)

                if parent_name == parent:
                    log.debug("Parents match. Continue...")
                else:
                    log.error(
                        f"Parents does not match: '{parent}' =/= '{parent_name}'.Aborting..."
                    )
                    log.info("Aborting...")
                    return html

                self.update_page(page.title, html, page)
                log.info(f"Update finished.")
            else:  # Create page
                log.debug(
                    f"Trying to add page: '{page.title}', First Level Parent: '{parent}', "
                    f"Second Level Parent: '{second_level_parent}', MAIN PARENT: '{main_parent}'"
                )

                parent_id = self.find_page_id(parent)
                second_parent_id = self.find_page_id(second_level_parent)
                main_parent_id = self.find_page_id(main_parent)

                if not (parent_id or second_parent_id or main_parent_id):
                    log.error(f"Page PARENT UNKNOWN. ABORTING!")
                    return html

                if not second_parent_id:
                    log.debug(
                        f"Trying to ADD Second Level Parent '{second_level_parent}' to Main parent ('{main_parent}')"
                    )
                    body = TEMPLATE_BODY.replace("TEMPLATE", second_level_parent)
                    self.add_page(second_level_parent, main_parent_id, body, page)
                    log.info(f"'{second_level_parent}' was added.")
                    time.sleep(10)

                if not parent_id:
                    log.debug(
                        f"Trying to ADD Parent '{parent}' to Second Level Parent ({second_level_parent})"
                    )
                    body = TEMPLATE_BODY.replace("TEMPLATE", parent)
                    self.add_page(parent, second_parent_id, body, page)
                    log.info(f"'{parent}' was added.")
                    time.sleep(10)
                    parent_id = self.find_page_id(parent)

                if not parent_id:
                    log.error("Failed to create parent page!")
                    return html

                log.debug("Trying to add page itself...")
                self.add_page(page.title, parent_id, html, page)
                log.info(
                    f"Page '{page.title}' was added to parent ({parent}) ID: {parent_id}"
                )

        except IndexError as e:
            log.error(f"ERR({e}): Exception error!")
            return html

        log.info(f"@ END @ '{page.file.src_uri}'.")

        return html

    def __get_page_url(self, section):
        return re.search("url='(.*)'\\)", section).group(1)[:-1] + ".md"

    def __get_page_name(self, section):
        return os.path.basename(re.search("url='(.*)'\\)", section).group(1)[:-1])

    def __get_section_name(self, section):
        log.debug(f"SECTION name: {section}")
        return os.path.basename(re.search("url='(.*)'\\/", section).group(1)[:-1])

    def __get_section_title(self, section):
        log.debug(f"SECTION title: {section}")
        try:
            r = re.search("Section\\(title='(.*)'\\)", section)
            return r.group(1)
        except AttributeError:
            name = self.__get_section_name(section)
            log.warning(
                f"Section '{name}' doesn't exist in the mkdocs.yml nav section!"
            )
            return name

    def __get_page_title(self, section):
        try:
            r = re.search("\\s*Page\\(title='(.*)',", section)
            return r.group(1)
        except AttributeError:
            name = self.__get_page_url(section)
            log.warning(f"Page '{name}' doesn't exist in the mkdocs.yml nav section!")
            return name

    # Adapted from https://stackoverflow.com/a/3431838
    def get_file_sha1(self, file_path):
        hash_sha1 = hashlib.sha1()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha1.update(chunk)
        return hash_sha1.hexdigest()

    def add_or_update_attachment(self, page_name, filepath):
        log.info(f"Handling attachment: '{page_name}', FILE: {filepath}")
        page_id = self.find_page_id(page_name)
        if page_id:
            file_hash = self.get_file_sha1(filepath)
            attachment_message = f"ConfluencePlugin [v{file_hash}]"
            existing_attachment = self.get_attachment(page_id, filepath)
            if existing_attachment:
                file_hash_regex = re.compile(r"\[v([a-f0-9]+)\]")
                current_hash = file_hash_regex.search(existing_attachment.get("metadata", ""))
                if current_hash and current_hash.group(1) == file_hash:
                    log.info(f"Attachment '{filepath.name}' is up-to-date. Skipping upload.")
                    return
                else:
                    self.delete_attachment(existing_attachment["id"])
                    log.info(f"Deleted outdated attachment '{filepath.name}'.")
            self.upload_attachment(page_id, filepath, attachment_message)
        else:
            log.error(f"Cannot find Confluence page id for '{page_name}'. Attachment skipped.")

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
        files = {"file": (filepath.name, open(filepath, "rb"), mimetypes.guess_type(filepath.name)[0])}
        data = {"comment": comment}
        response = self.session.post(url, files=files, data=data)
        if response.status_code in [200, 201]:
            log.info(f"Uploaded attachment '{filepath.name}' to page ID {page_id}.")
        else:
            log.error(f"Failed to upload attachment '{filepath.name}' with status {response.status_code}.")

    def delete_attachment(self, attachment_id):
        url = f"{self.config['host_url']}/rest/api/content/{attachment_id}"
        response = self.session.delete(url)
        if response.status_code == 204:
            log.info(f"Deleted attachment ID {attachment_id}.")
        else:
            log.error(f"Failed to delete attachment ID {attachment_id} with status {response.status_code}.")


    def find_page_id(self, title):
        if title in self.page_ids:
            return self.page_ids[title]

        cql = f'title = "{title}" and space = "{self.config["space"]}"'
        results = self.confluence.cql(cql)
        if results.get("results"):
            page = results["results"][0]
            # Check if 'id' is at top level or inside 'content'
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
        # This is a stub for retrieving parent name - implement as needed
        return self.config.get("parent_page_name")

    def update_page(self, title, body, page_obj):
        page_id = self.page_ids.get(title)
        if not page_id:
            log.error(f"Cannot update page '{title}' because page ID not found.")
            return

        version = self.page_versions.get(title, 1) + 1
        data = {
            "version": {"number": version},
            "title": title,
            "type": "page",
            "body": {"storage": {"value": body, "representation": "storage"}},
        }

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
        data = {
            "type": "page",
            "title": title,
            "space": {"key": self.config["space"]},
            "ancestors": [{"id": parent_id}],
            "body": {"storage": {"value": body, "representation": "storage"}},
        }

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

    def renderer(self, text):
        return self.confluence_mistune(text)

