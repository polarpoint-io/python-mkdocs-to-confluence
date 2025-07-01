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
from pathlib import Path
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
    )

    def __init__(self):
        self.enabled = True
        self.confluence_renderer = ConfluenceRenderer(use_xhtml=True)
        self.confluence_mistune = mistune.Markdown(renderer=self.confluence_renderer)
        self.simple_log = False
        self.flen = 1
        self.session = requests.Session()
        self.page_attachments = {}


    def on_pre_build(self, config):
        self.some_data = {}

    def on_pre_build(self, config):
        self.page_ids = {}
        self.page_versions = {}

    def load_pages(self):
        pages = []
        for file_path in Path(self.docs_dir).rglob("*.md"):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            rendered = self.renderer(content)
            pages.append({"title": file_path.stem, "body": rendered})
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
        conf = self.config  # plugin config populated automatically

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

        self.default_labels = ["cpe", "mkdocs"]

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

        space = getattr(self, "space", None) or config.get(
            "confluence", {}
        ).get("space")

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

        header = f"[Update markdown]({github_url})\n\n"
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
        # self.session.headers.update({"Authorization": f"Bearer {self.config['token']}"})
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

                markdown_file_url = page.file.src_uri
                confluence_warning_macro = f"""
                <ac:structured-macro ac:name="info">
                    <ac:rich-text-body>
                        <p style="font-size:small;">{MKDOCS_FOOTER}</p>
                        <p style="font-size:small;">📄 <a href='{markdown_file_url}'>Markdown</a></p>
                    </ac:rich-text-body>
                </ac:structured-macro>
                """

                # Append the warning macro
                html += confluence_warning_macro

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
                file_hash_regex = re.compile(r"\[v([a-f0-9]{40})]$")
                existing_match = file_hash_regex.search(
                    existing_attachment["version"]["message"]
                )
                if existing_match is not None and existing_match.group(1) == file_hash:
                    log.debug(
                        f"* '{page_name}' * Existing attachment skipping * {filepath}"
                    )
                else:
                    self.update_attachment(
                        page_id, filepath, existing_attachment, attachment_message
                    )
            else:
                self.create_attachment(page_id, filepath, attachment_message)
        else:
            log.debug("PAGE DOES NOT EXISTS")

    def get_attachment(self, page_id, filepath):
        name = os.path.basename(filepath)
        log.debug(f"Get Attachment: PAGE ID: {page_id}, FILE: {filepath}")

        url = self.config["host_url"] + "/" + page_id + "/child/attachment"
        headers = {"X-Atlassian-Token": "no-check"}  # no content-type here!
        log.debug(f"URL: {url}")

        r = self.session.get(
            url, headers=headers, params={"filename": name, "expand": "version"}
        )
        r.raise_for_status()
        with nostdout():
            response_json = r.json()
        if response_json["size"]:
            return response_json["results"][0]


    def update_attachment(self, page_id, filepath, existing_attachment, message):
        log.debug(f"Update Attachment: PAGE ID: {page_id}, FILE: {filepath}")

        url = f"{self.config['host_url'].rstrip('/')}/rest/api/content/{page_id}/child/attachment/{existing_attachment['id']}/data"
        headers = {"X-Atlassian-Token": "no-check"}

        log.debug(f"URL: {url}")

        filename = os.path.basename(filepath)
        content_type, _ = mimetypes.guess_type(filepath)
        if content_type is None:
            content_type = "multipart/form-data"

        files = {
            "file": (filename, open(Path(filepath), "rb"), content_type)
        }
        data = {
            "comment": message
        }

        if not self.dryrun:
            try:
                r = self.session.post(url, headers=headers, files=files, data=data)
                r.raise_for_status()
                log.debug(r.json())
                log.debug("Returned status code %d", r.status_code)
            except requests.exceptions.RequestException as e:
                log.error(f"Failed to update attachment: {e}")
                raise



    def create_attachment(self, page_id, filepath, message):
        log.debug(f"Create Attachment: PAGE ID: {page_id}, FILE: {filepath}")

        url = self.config["host_url"] + "/" + page_id + "/child/attachment"
        headers = {"X-Atlassian-Token": "no-check"}  # no content-type here!

        log.debug(f"URL: {url}")

        filename = os.path.basename(filepath)

        # determine content-type
        content_type, encoding = mimetypes.guess_type(filepath)
        if content_type is None:
            content_type = "multipart/form-data"
        files = {
            "file": (filename, open(filepath, "rb"), content_type),
            "comment": message,
        }
        if not self.dryrun:
            r = self.session.post(url, headers=headers, files=files)
            log.debug(r.json())
            r.raise_for_status()
            log.debug("Returned status code %d", r.status_code)

    @wait_until_result
    def find_page_id(self, page_name):
        return self.confluence.get_page_id(space=self.config["space"], title=page_name)

    def add_page(self, page_name, parent_page_id, body, page: Page):
        log.info(f"Adding Page: '{page_name}', parent ID: {parent_page_id}")
        url = self.config["host_url"] + "/"
        headers = {"Content-Type": "application/json"}

        body_sha1 = hashlib.sha1(body.encode("utf-8")).hexdigest()

        data = {
            "type": "page",
            "title": page_name,
            "space": {"key": self.config["space"]},
            "ancestors": [{"id": parent_page_id}],
            "body": {
                "storage": {
                    "value": body,
                    "representation": "storage",
                }
            },
        }

        if self.dryrun:
            return

        r = self.session.post(url, json=data, headers=headers)
        log.debug("Returned status code %d", r.status_code)
        if r.status_code <= 300:
            log.info(r.json())
        r.raise_for_status()

        # Propagate labels
        # Add body SHA
        self.update_page(page_name, body, page)

    def update_page(self, page_name, body, page: Page):
        log.info(f"Updating...")
        page_id = self.find_page_id(page_name)

        if not page_id:
            log.error(f"Page '{page_name}' doesn't exist yet!")

        if self.dryrun:
            return

        page_data = self.confluence.get_page_by_id(page_id, expand="version")
        old_body_sha1 = page_data.get("version").get("message")

        new_body_sha1 = hashlib.sha1(body.encode("utf-8")).hexdigest()

        log.debug(f"Old Body SHA1 '{old_body_sha1}'")
        log.debug(f"New body SHA1 '{new_body_sha1}'")

        if str(new_body_sha1) == str(old_body_sha1):
            log.info(f"Content is up to date - skipping update.")
            return

        page_version = self.find_page_version(page_name) + 1
        labels = set(self.default_labels + page.meta.get("tags", list()))
        log.info(f"Used labels: {labels}")

        data = {
            "id": page_id,
            "title": page_name,
            "type": "page",
            "space": {"key": self.config["space"]},
            "body": {
                "storage": {
                    "value": body,
                    "representation": "storage",
                }
            },
            "version": {
                "number": page_version,
                "minorEdit": True,
                "message": new_body_sha1,
            },
            "metadata": {"labels": [{"name": tag} for tag in labels]},
        }

        url = self.config["host_url"] + "/" + page_id
        r = self.session.put(
            url, json=data, headers={"Content-Type": "application/json"}
        )
        r.raise_for_status()

    def find_page_version(self, page_name):
        log.info(f"Retrieving page version.")
        name_confl = page_name.replace(" ", "+")
        url = (
            self.config["host_url"]
            + "?title="
            + name_confl
            + "&spaceKey="
            + self.config["space"]
            + "&expand=version"
        )
        r = self.session.get(url)
        r.raise_for_status()
        with nostdout():
            response_json = r.json()
        if response_json["results"] is not None:
            log.debug(f"VERSION: {response_json['results'][0]['version']['number']}")
            return response_json["results"][0]["version"]["number"]
        else:
            log.debug("PAGE DOES NOT EXISTS")
            return None

    def find_parent_name_of_page(self, name):
        log.debug(f"Looking for the Parent page.")
        idp = self.find_page_id(name)
        url = self.config["host_url"] + "/" + idp + "?expand=ancestors"

        r = self.session.get(url)
        r.raise_for_status()
        with nostdout():
            response_json = r.json()
        if response_json:
            log.debug(f"PARENT NAME: {response_json['ancestors'][-1]['title']}")
            return response_json["ancestors"][-1]["title"]
        else:
            log.debug("PAGE DOES NOT HAVE PARENT")
            return None
