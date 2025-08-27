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
from urllib.parse import quote_plus
from typing import Optional
from difflib import get_close_matches

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
        self.page_lookup = {}
        self.enabled = True
        self.logger = log
        self.confluence_renderer = ConfluenceRenderer(use_xhtml=True)
        self.confluence_mistune = mistune.Markdown(renderer=self.confluence_renderer)
        self.session = requests.Session()
        self.pages = []
        self.page_ids = {}
        self.page_versions = {}
        self.dryrun = False
        self.tab_nav = []
        self.attachments = {}
        self.auth_configured = False
        # Store attachments for deferred processing after all plugins have run
        self.deferred_attachments = []

    def normalize_title_key(self, title: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")

    def extract_meaningful_words(self, text: str) -> set:
        """Extract meaningful words from text, filtering out common prefixes and numbers."""
        # Remove common prefixes and patterns, but be careful not to damage abbreviations
        text = re.sub(
            r"^(kb|rb)-", "", text.lower()
        )  # Only remove kb- and rb- prefixes
        text = re.sub(r"^docs?-", "", text)  # Remove docs- prefix
        text = re.sub(r"^\d{4}-?", "", text)  # Remove leading numbers like "0001-"

        # Handle common abbreviations and expand them
        abbreviations = {
            "adrs": [
                "architecture",
                "design",
                "records",
                "decision",
            ],  # Include both design and decision
            "adr": ["architecture", "design", "record", "decision"],
            "arch": ["architecture"],
            "sso": ["single", "sign", "on"],
            "auth": ["authentication", "authorization", "auth"],
            "kb": ["knowledge", "base"],
            "rb": ["runbook"],
            "ci": ["continuous", "integration"],
            "cd": ["continuous", "delivery"],
            "cicd": ["continuous", "integration", "deployment", "delivery"],
            "ci/cd": ["continuous", "integration", "deployment", "delivery"],
            "aws": ["amazon", "web", "services"],
            "api": ["application", "programming", "interface"],
            "apis": ["application", "programming", "interface", "endpoints"],
            "rest": ["representational", "state", "transfer"],
            "ui": ["user", "interface"],
            "db": ["database"],
            "config": ["configuration"],
            "admin": ["administration", "administrator"],
            "mgmt": ["management"],
            "ops": ["operations"],
            "dev": ["development"],
            "prod": ["production"],
            "env": ["environment"],
            "tech": ["technology"],
            "deploy": ["deployment"],
            "troubleshoot": ["troubleshooting"],
            "setup": ["setup", "configuration"],
            "guide": ["guide", "guidelines"],
        }

        # Split on various separators and filter out short/meaningless words
        words = re.split(r"[-_\s\./]+", text)
        meaningful_words = set()

        # First, check if the whole text (lowercased) is an abbreviation
        text_lower = text.lower()
        if text_lower in abbreviations:
            meaningful_words.update(abbreviations[text_lower])

        for word in words:
            word = word.strip().lower()
            if len(word) > 2 and not word.isdigit():
                # Check if word is an abbreviation
                if word in abbreviations:
                    meaningful_words.update(abbreviations[word])
                elif word not in {
                    "the",
                    "and",
                    "for",
                    "with",
                    "are",
                    "not",
                    "how",
                    "can",
                    "you",
                    "but",
                    "was",
                }:
                    meaningful_words.add(word)
            elif len(word) == 2 and word in abbreviations:
                # Handle 2-letter abbreviations
                meaningful_words.update(abbreviations[word])
            elif len(word) >= 1 and word in abbreviations:
                # Handle any length abbreviations
                meaningful_words.update(abbreviations[word])

        return meaningful_words

    def calculate_word_similarity(self, text1: str, text2: str) -> float:
        """Calculate similarity between two texts based on shared meaningful words."""
        words1 = self.extract_meaningful_words(text1)
        words2 = self.extract_meaningful_words(text2)

        if not words1 or not words2:
            return 0.0

        intersection = words1.intersection(words2)
        union = words1.union(words2)

        return len(intersection) / len(union) if union else 0.0

    def on_config(self, config):
        plugin_cfg = self.config
        self.space = self.config.get("space")
        self.enabled = plugin_cfg.get("enabled", True)
        self.only_in_nav = plugin_cfg.get("only_in_nav", False)

        if not self.enabled:
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

        # Configure session for attachment uploads
        self.session.auth = (plugin_cfg["username"], plugin_cfg["password"])
        self.auth_configured = True

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
        # Handle the case where nav_list is a dict (for recursive calls)
        if isinstance(nav_list, dict):
            nav_list = [nav_list]

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
                                try:
                                    result = self.confluence.create_page(
                                        space=self.config["space"],
                                        title=norm_title,
                                        body="",  # No body for folder pages
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
                                        log.warning(
                                            f"Failed to create folder page '{norm_title}': No ID returned"
                                        )
                                except Exception as e:
                                    log.error(
                                        f"❌ Failed to create folder page '{norm_title}': {e}"
                                    )
                                    folder_page_id = None
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
        self.tab_nav = nav_structure  # Nested nav structure

        # Build parent-child mapping from nav
        self.page_parents = self._flatten_nav_with_parents(self.tab_nav)

        log.info(f"Auto-generated nested nav: {nav_structure}")

    def _flatten_nav_with_parents(self, nav, parent=None):
        result = {}
        # Handle the case where nav is a dict (for recursive calls)
        if isinstance(nav, dict):
            nav = [nav]

        for item in nav:
            if isinstance(item, str):
                result[item] = parent
            elif isinstance(item, dict):
                for k, v in item.items():
                    result[k] = parent
                    result.update(self._flatten_nav_with_parents(v, parent=k))
        return result

    def _build_page_path(self, title):
        path = [title]
        parent = self.page_parents.get(title)
        while parent:
            path.insert(0, parent)
            parent = self.page_parents.get(parent)
        return " / ".join(path)

    def on_page_markdown(self, markdown, page, config, files):
        """Capture page content before it's rendered and store by normalized title."""
        abs_src_path = page.file.abs_src_path
        title_key = self.normalize_title_key(page.title)
        rendered = self.confluence_mistune(markdown)

        page_info = {
            "title": page.title,
            "body": rendered,
            "abs_src_path": abs_src_path,
            "meta": page.meta,
            "url": page.canonical_url,
        }

        # Store under page title key
        self.page_lookup[title_key] = page_info

        # Create a reverse lookup from normalized title to page info for fuzzy matching
        if not hasattr(self, "title_to_page"):
            self.title_to_page = {}
        self.title_to_page[title_key] = page_info

        # Also store under file path key for navigation matching
        if abs_src_path:
            rel_path = os.path.relpath(abs_src_path, "docs").replace("\\", "/")
            # Remove .md extension for the path
            if rel_path.endswith(".md"):
                rel_path = rel_path[:-3]
            path_key = self.normalize_title_key(rel_path)
            self.page_lookup[path_key] = page_info

            # Also store under just the filename (without directory)
            filename = os.path.basename(rel_path)
            filename_key = self.normalize_title_key(filename)
            self.page_lookup[filename_key] = page_info

        self.logger.debug(
            f"📥 Cached page content under key '{title_key}' from '{abs_src_path}'"
        )
        if abs_src_path:
            self.logger.debug(
                f"📥 Also cached under path key '{path_key}' and filename key '{filename_key}'"
            )
        return markdown  # Let MkDocs proceed as usual

    def on_page_content(self, html, page, config, files):
        """Process page content and add footer if enabled."""
        log.debug("🧪 on_page_content called")

        if not self.config.get("enable_footer"):
            log.debug("🚫 Footer disabled")
            return html

        github_base_url = self.config.get("github_base_url")
        if not github_base_url:
            log.warning("⚠️ Missing github_base_url - footer cannot be generated")
            return html

        if not hasattr(page.file, "src_uri"):
            log.warning("❌ No src_uri on page.file - footer cannot be generated")
            return html

        footer = f'<p><em><a href="{github_base_url}/{page.file.src_uri}">View source on GitHub</a></em></p>'
        log.debug(f"✅ Adding footer: {footer}")

        # Store the footer in page_lookup for later use in Confluence
        title_key = self.normalize_title_key(page.title)
        if title_key in self.page_lookup:
            self.page_lookup[title_key]["footer"] = footer

        return html + footer

    def debug_dump_page_parents(self):
        print("🔍 Page parent mapping:")
        for child, parent in self.page_parents.items():
            print(f"  {child} ← {parent}")

    def on_post_build(self, config, **kwargs):
        if not self.enabled:
            log.info("Confluence plugin disabled; skipping post-build.")
            return

        log.info(f"🔁 Nav structure for folder pages creation:\n{self.tab_nav}")
        self.debug_dump_pages()

        # 💡 Optional: Dump the page_lookup keys for debugging
        log.debug(f"📄 Keys in page_lookup: {list(self.page_lookup.keys())}")

        # 🧩 Populate self.pages based on page_lookup
        self.pages = list(self.page_lookup.values())

        log.info(f"📄 Total pages defined in MkDocs: {len(self.pages)}")

        published_titles = [
            self._normalize_title(p["title"]) for p in self.pages if p.get("content")
        ]
        all_nav_titles = [
            self._normalize_title(n) for n in self._collect_all_page_names(self.tab_nav)
        ]

        missing = set(published_titles) - set(all_nav_titles)
        if missing:
            log.warning(
                f"🚨 These pages have content but were not matched in nav: {missing}"
            )

        # ✅ Publish content pages via structured tree
        self.build_and_publish_tree(self.tab_nav, self.parent_page_id)

        # 🔗 Process all deferred attachments after all pages are created
        if self.deferred_attachments:
            log.info(
                f"🔗 Processing {len(self.deferred_attachments)} deferred attachment collections after all plugins have finished"
            )

            for i, attachment_info in enumerate(self.deferred_attachments, 1):
                page_id = attachment_info["page_id"]
                page_title = attachment_info["page_title"]
                src_path = attachment_info["src_path"]
                original_content = attachment_info["original_content"]

                log.debug(
                    f"Processing deferred attachments {i}/{len(self.deferred_attachments)} for page '{page_title}' (ID: {page_id})"
                )

                # Try to collect attachments from original content first (before PlantUML processing)
                attachments = []
                if original_content:
                    log.debug(
                        f"Attempting to collect attachments from original content"
                    )
                    attachments = self.collect_page_attachments(
                        src_path, original_content
                    )

                # If no attachments found in original content, check if files exist anyway
                # (PlantUML might have generated them and we can detect them by file existence)
                if not attachments:
                    log.debug(
                        f"No attachments found in original content, checking for generated files..."
                    )
                    # Re-read the current file content to see what PlantUML might have generated
                    if src_path and Path(src_path).exists():
                        current_content = Path(src_path).read_text()
                        attachments = self.collect_page_attachments(
                            src_path, current_content
                        )

                if attachments:
                    log.debug(
                        f"Found {len(attachments)} attachments for page '{page_title}'"
                    )
                    for j, attachment in enumerate(attachments, 1):
                        try:
                            file_size = attachment.stat().st_size
                            log.debug(
                                f"  Attachment {j}: {attachment.name} ({file_size} bytes) - {attachment}"
                            )
                        except Exception as e:
                            log.debug(
                                f"  Attachment {j}: {attachment.name} - Could not get file size: {e}"
                            )

                    if not self.dryrun:
                        self.sync_page_attachments(page_id, attachments)
                    else:
                        log.info(
                            f"DRYRUN: Would sync {len(attachments)} attachments for page '{page_title}'"
                        )
                else:
                    log.debug(f"No attachments found for page '{page_title}'")

            log.info(f"✅ Completed processing all deferred attachments")
        else:
            log.debug("No deferred attachments to process")

    def get_page_url(self, title, parent_id=None):
        cache_key = self._cache_key(title, parent_id)
        page_id = self.page_ids.get(cache_key)
        if not page_id:
            page_id = self.find_page_id(title, parent_id)
        if page_id:
            return f"{self.config['host_url'].rstrip('/')}/pages/viewpage.action?pageId={page_id}"
        return None

    def page_exists(self, title, parent_id=None):
        page_id = self.find_page_id(title, parent_id)
        return (page_id is not None, page_id)

    def _normalize_title(self, title: str) -> str:
        """
        Normalize title by lowercasing, removing punctuation, and stripping whitespace.
        Preserves letters and digits, removes spaces and all punctuation characters.
        """
        title = title.strip().lower()
        return title.translate(str.maketrans("", "", string.punctuation)).replace(
            " ", ""
        )

    def apply_labels_to_page(self, page_id, labels=None, page_meta=None):
        """Apply labels to a Confluence page."""
        all_labels = []

        # Add default labels
        default_labels = getattr(self, "default_labels", [])
        if default_labels:
            all_labels.extend(default_labels)

        # Add labels from page metadata
        if page_meta:
            page_labels = page_meta.get("labels", []) or page_meta.get("tags", [])
            if page_labels:
                # Ensure labels are strings and clean them
                clean_page_labels = [
                    str(label).strip() for label in page_labels if label
                ]
                all_labels.extend(clean_page_labels)

        # Add any explicitly passed labels
        if labels:
            all_labels.extend(labels)

        # Remove duplicates while preserving order
        unique_labels = []
        seen = set()
        for label in all_labels:
            if label not in seen:
                unique_labels.append(label)
                seen.add(label)

        if not unique_labels:
            log.debug(f"📝 No labels to apply to page ID {page_id}")
            return

        if self.dryrun:
            log.info(f"DRYRUN: Would apply labels {unique_labels} to page ID {page_id}")
            return

        try:
            # Get current labels to avoid duplicates
            current_labels = self.confluence.get_page_labels(page_id)
            current_label_names = [
                label["name"] for label in current_labels.get("results", [])
            ]

            # Only add labels that don't already exist
            new_labels = [
                label for label in unique_labels if label not in current_label_names
            ]

            if new_labels:
                for label in new_labels:
                    self.confluence.set_page_label(page_id, label)
                log.debug(f"✅ Applied labels {new_labels} to page ID {page_id}")
            else:
                log.debug(
                    f"📝 All labels {unique_labels} already exist on page ID {page_id}"
                )

        except Exception as e:
            log.error(
                f"❌ Failed to apply labels {unique_labels} to page ID {page_id}: {e}"
            )

    def create_or_update_page(
        self,
        title,
        body="",
        parent_id=None,
        is_folder=False,
        attachments=None,
        abs_src_path=None,
    ):
        """Create or update a Confluence page. Handles folders, dry run, and logging."""
        if not title:
            log.warning("⚠️ create_or_update_page: Missing title. Skipping.")
            return None

        key = self.normalize_title_key(title)
        page_exists, existing_id = self.page_exists(title, parent_id)

        # Get page info to check for footer and metadata
        page_info = None
        title_key = self.normalize_title_key(title)
        page_info = self.page_lookup.get(title_key)

        if not page_info:
            # Try to find by title match
            for lookup_key, info in self.page_lookup.items():
                if info.get("title") == title:
                    page_info = info
                    break

        # Add footer to body if it exists
        final_body = body
        if page_info and page_info.get("footer") and not is_folder:
            final_body = body + page_info["footer"]

        # Extract metadata for labels
        page_meta = page_info.get("meta", {}) if page_info else {}

        if page_exists:
            page_id = existing_id
            log.info(f"📝 Page exists: '{title}' (ID={page_id}) — updating.")
            if not self.dryrun:
                self.confluence.update_page(page_id, title, final_body)
                # Apply labels to updated page (including page metadata labels)
                if not is_folder:
                    self.apply_labels_to_page(page_id, page_meta=page_meta)
            else:
                self.dryrun_log("update", title, parent_id)
        else:
            log.info(f"🆕 Page does not exist: '{title}' — creating.")
            if not self.dryrun:
                created = self.confluence.create_page(
                    self.space, title, final_body, parent_id
                )
                page_id = created.get("id")
                # Apply labels to newly created page (including page metadata labels)
                if page_id and not is_folder:
                    self.apply_labels_to_page(page_id, page_meta=page_meta)
            else:
                page_id = f"DRYRUN-{title}"
                self.dryrun_log("create", title, parent_id)

        # Attachments handling - defer processing until after all plugins have run
        if abs_src_path:
            # Store the original markdown content before any plugins modify it
            original_content = None
            if abs_src_path and Path(abs_src_path).exists():
                original_content = Path(abs_src_path).read_text()

            # Store attachment info for deferred processing
            attachment_info = {
                "page_id": page_id,
                "page_title": title,
                "src_path": abs_src_path,
                "original_content": original_content,
                "processed_content": body,
            }
            self.deferred_attachments.append(attachment_info)
            log.debug(
                f"Deferred attachment processing for page '{title}' (ID: {page_id})"
            )

        self.page_ids[key] = page_id
        return page_id

    def create_page(self, title, body, parent_id, is_folder=False):
        norm_title = self._normalize_title(title)
        norm_parent_id = str(parent_id) if parent_id else None
        cache_key = (norm_title, norm_parent_id)

        if self.dryrun:
            self.dryrun_log("create page", title, parent_id)
            return f"DUMMY_ID_{title}"

        # Get page metadata for labels
        title_key = self.normalize_title_key(title)
        page_info = self.page_lookup.get(title_key, {})
        page_meta = page_info.get("meta", {})

        try:
            log.info(
                f"📄 Attempting to create page '{title}' under parent ID {parent_id}"
            )
            # Use empty string for folder body, avoid TEMPLATE_BODY for child/content pages
            body_to_use = "" if is_folder else (body or "")
            result = self.confluence.create_page(
                space=self.config["space"],
                title=title,
                body=body_to_use,
                parent_id=parent_id,
                representation="storage",
            )
            if result and "id" in result:
                page_id = result["id"]
                self.page_ids[cache_key] = page_id
                self.page_versions[cache_key] = 1

                # Apply labels to newly created page (including page metadata labels)
                if not is_folder:
                    self.apply_labels_to_page(page_id, page_meta=page_meta)

                log.info(
                    f"✅ Created {'folder' if is_folder else 'content'} page '{title}' with ID {page_id}"
                )
                return page_id
        except Exception as e:
            if "already exists with the same TITLE" in str(e):
                log.warning(
                    f"⚠️ Page '{title}' already exists — attempting update instead"
                )
            else:
                log.error(f"❌ Failed to create page '{title}': {e}", exc_info=True)
                return None

        # Fallback: update existing page if creation fails
        page_id = self.find_page_id(title, parent_id)
        if not page_id:
            log.error(
                f"❌ Cannot update '{title}': page ID not found after creation failure"
            )
            return None

        prev_version = self.page_versions.get(cache_key, 1)
        new_version = prev_version + 1

        try:
            log.info(
                f"🔁 Updating page '{title}' (ID {page_id}) to version {new_version}"
            )
            self.confluence.update_page(
                page_id=page_id,
                title=title,
                body="" if is_folder else (body or ""),  # Folder pages get empty body
                parent_id=parent_id,
                type="page",
                representation="storage",
                minor_edit=False,
            )
            self.page_ids[cache_key] = page_id
            self.page_versions[cache_key] = new_version

            # Apply labels to updated page (including page metadata labels)
            if not is_folder:
                self.apply_labels_to_page(page_id, page_meta=page_meta)

            log.info(f"✅ Updated page '{title}' (version {new_version})")
            return page_id
        except Exception as e:
            log.error(
                f"❌ Failed to update page '{title}' (ID {page_id}): {e}", exc_info=True
            )
            return None

    def publish_page(self, page_title, body, parent_id, source_path=None, dryrun=False):
        norm_title = self._normalize_title(page_title)
        norm_parent_id = str(parent_id) if parent_id else None
        cache_key = (norm_title, norm_parent_id)

        if dryrun:
            self.dryrun_log("publish page", page_title, parent_id)
            return f"DUMMY_ID_{page_title}"

        # Get page metadata for labels
        title_key = self.normalize_title_key(page_title)
        page_info = self.page_lookup.get(title_key, {})
        page_meta = page_info.get("meta", {})

        # Try to create page first
        try:
            log.info(f"📄 Creating page '{page_title}' under parent ID {parent_id}")
            result = self.confluence.create_page(
                space=self.config["space"],
                title=page_title,
                body=body or "",
                parent_id=parent_id,
                representation="storage",
            )
            if result and "id" in result:
                page_id = result["id"]
                self.page_ids[cache_key] = page_id
                self.page_versions[cache_key] = 1

                # Apply labels to newly created page
                self.apply_labels_to_page(page_id, page_meta=page_meta)

                log.info(f"✅ Created page '{page_title}' with ID {page_id}")
                return page_id
        except Exception as e:
            if "already exists with the same TITLE" in str(e):
                log.warning(f"⚠️ Page '{page_title}' already exists — attempting update")
            else:
                log.error(
                    f"❌ Failed to create page '{page_title}': {e}", exc_info=True
                )
                return None

        # Fallback: Update existing page
        page_id = self.find_page_id(page_title, parent_id)
        if not page_id:
            log.error(f"❌ Cannot update '{page_title}': page ID not found")
            return None

        prev_version = self.page_versions.get(cache_key, 1)
        new_version = prev_version + 1
        try:
            log.info(
                f"🔁 Updating page '{page_title}' (ID {page_id}) to version {new_version}"
            )
            self.confluence.update_page(
                page_id=page_id,
                title=page_title,
                body=body or "",
                parent_id=parent_id,
                type="page",
                representation="storage",
                minor_edit=False,
            )
            self.page_ids[cache_key] = page_id
            self.page_versions[cache_key] = new_version

            # Apply labels to updated page
            self.apply_labels_to_page(page_id, page_meta=page_meta)

            log.info(f"✅ Updated page '{page_title}' (version {new_version})")
            return page_id
        except Exception as e:
            log.error(
                f"❌ Failed to update page '{page_title}' (ID {page_id}): {e}",
                exc_info=True,
            )
            return None

    def find_or_create_page(self, title, parent_id=None, is_folder=False):
        norm_title = self._normalize_title(title)
        norm_parent_id = str(parent_id) if parent_id is not None else None
        cache_key = self._cache_key(title, norm_parent_id)

        page_id = self.find_page_id(title, parent_id=parent_id)
        if page_id:
            return page_id

        log.info(f"Creating Confluence page '{title}' under parent ID {parent_id}")
        if self.dryrun:
            self.dryrun_log("create", title, parent_id)
            return f"DUMMY_ID_{title}"

        result = self.confluence.create_page(
            space=self.config["space"],
            title=title,
            body="" if is_folder else TEMPLATE_BODY,
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

    def find_page_id(self, title: str, parent_id: str | None = None) -> str | None:
        """
        Find a Confluence page ID by its title and parent page ID.
        If parent_id is None, search top-level pages in the space.

        Returns page ID if found, else None.
        """
        # Normalize title for consistent lookup if needed (depends on your implementation)
        normalized_title = title.strip().lower()

        # 1) Search children of parent page if parent_id provided
        if parent_id:
            children = self.confluence.get_page_child_by_type(parent_id, "page")
            for child in children:
                if child["title"].strip().lower() == normalized_title:
                    return child["id"]

        # 2) If no parent or not found above, search globally in space by title
        # Use Confluence CQL (Confluence Query Language) to search pages by title in the space
        cql = f'title="{title}" and space="{self.config["space"]}" and type="page"'
        search_result = self.confluence.cql(cql, limit=10)
        for result in search_result.get("results", []):
            page = result.get("content")
            if page and page.get("title", "").strip().lower() == normalized_title:
                return page.get("id")

        # Not found
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

    def collect_page_attachments(self, src_path, content):
        """Collect attachment files referenced in the markdown content."""
        import re
        from pathlib import Path

        attachments = []
        if not src_path:
            log.debug("collect_page_attachments: No source path provided")
            return attachments

        src_dir = Path(src_path).parent
        log.debug(f"Collecting attachments from {src_path} (source dir: {src_dir})")

        # Find markdown image references: ![alt](path) and ![alt](path "title")
        img_pattern = r"!\[([^\]]*)\]\(([^)]+)\)"
        matches = re.findall(img_pattern, content)
        log.debug(
            f"Found {len(matches)} image references in markdown: {[match[1] for match in matches]}"
        )

        for alt_text, img_path in matches:
            # Remove any quotes and title text
            img_path = img_path.split('"')[0].strip()

            # Skip external URLs
            if img_path.startswith(("http://", "https://", "//")):
                continue

            img_file = None

            # Handle relative paths - try multiple resolution strategies
            if img_path.startswith("./"):
                # Remove ./ prefix
                img_path = img_path[2:]
                img_file = src_dir / img_path
            elif img_path.startswith("../"):
                # Handle parent directory references - try multiple strategies

                # Strategy 1: Resolve relative to source file
                img_file = (src_dir / img_path).resolve()

                # Strategy 2: If not found, try relative to docs root
                if not img_file.exists():
                    # If the path goes up to project root, try prefixing with docs/
                    if img_path.startswith("../../../"):
                        # This likely goes to project root, so try docs/ prefix
                        alt_path = img_path[9:]  # Remove ../../../
                        img_file = Path("docs") / alt_path

                # Strategy 3: Try relative to project root
                if not img_file.exists() and img_path.startswith("../"):
                    # Resolve from source directory and see if it makes sense
                    try:
                        project_relative = (src_dir / img_path).resolve()
                        if project_relative.exists():
                            img_file = project_relative
                    except:
                        pass

            else:
                # Non-relative paths: try both relative to source file and relative to docs root
                img_file = src_dir / img_path
                if not img_file.exists():
                    img_file = Path("docs") / img_path

            # Check if file exists and is an image
            if (
                img_file
                and img_file.exists()
                and img_file.suffix.lower()
                in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".webp")
            ):
                resolved_path = img_file.resolve()
                file_size = resolved_path.stat().st_size
                attachments.append(resolved_path)
                log.debug(
                    f"✓ Found attachment: {img_file} ({file_size} bytes) from markdown reference: {img_path}"
                )
            else:
                log.warning(
                    f"✗ Referenced image not found: {img_path} (resolved to {img_file})"
                )

        return attachments

    def sync_page_attachments(self, page_id, attachments):
        """Sync attachments for a page."""
        if not self.auth_configured:
            log.warning("Authentication not configured for attachment uploads")
            return

        if not attachments:
            log.debug(f"No attachments to sync for page ID {page_id}")
            return

        log.info(f"Syncing {len(attachments)} attachments for page ID {page_id}")
        for i, attachment_path in enumerate(attachments, 1):
            try:
                log.debug(
                    f"Processing attachment {i}/{len(attachments)}: {attachment_path.name}"
                )
                self.add_or_update_attachment(page_id, attachment_path)
            except Exception as e:
                log.error(f"Failed to sync attachment {attachment_path}: {e}")

    def add_or_update_attachment(self, page_id, filepath):
        """Add or update an attachment for a page."""
        if not self.auth_configured:
            log.warning("Authentication not configured for attachment uploads")
            return

        try:
            file_size = filepath.stat().st_size
            log.info(
                f"Handling attachment: file '{filepath.name}' ({file_size} bytes) for page ID {page_id}"
            )
        except Exception as e:
            log.info(
                f"Handling attachment: file '{filepath.name}' (size unknown: {e}) for page ID {page_id}"
            )

        if not page_id:
            log.error("Cannot upload attachment: Page ID is missing")
            return

        try:
            file_hash = self.get_file_sha1(filepath)
            attachment_comment = f"ConfluencePlugin [v{file_hash}]"
            log.debug(f"Attachment '{filepath.name}' hash: {file_hash}")

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
                    log.debug(
                        f"Attachment '{filepath.name}' has changed (old hash: {current_hash_match.group(1) if current_hash_match else 'unknown'}, new hash: {file_hash})"
                    )
                    self.delete_attachment(existing_attachment["id"])
                    log.info(f"Deleted outdated attachment '{filepath.name}'.")
            else:
                log.debug(
                    f"No existing attachment found for '{filepath.name}', will upload new one"
                )

            self.upload_attachment(page_id, filepath, attachment_comment)
        except Exception as e:
            log.error(f"Error handling attachment {filepath}: {e}")

    def get_attachment(self, page_id, filepath):
        """Get existing attachment by page ID and filename."""
        try:
            # Use base URL without /rest/api/content since we add it below
            base_url = self.config["host_url"].replace("/rest/api/content", "")
            url = f"{base_url}/rest/api/content/{page_id}/child/attachment"
            params = {"filename": filepath.name}
            response = self.session.get(url, params=params)
            if response.status_code == 200:
                results = response.json().get("results", [])
                if results:
                    return results[0]
            elif response.status_code != 404:
                log.warning(
                    f"Failed to check existing attachment (status {response.status_code}): {response.text}"
                )
        except Exception as e:
            log.error(f"Error checking existing attachment: {e}")
        return None

    def upload_attachment(self, page_id, filepath, comment):
        """Upload an attachment to a page."""
        try:
            file_size = filepath.stat().st_size
            log.debug(
                f"Starting upload of '{filepath.name}' ({file_size} bytes) to page ID {page_id}"
            )

            # Use base URL without /rest/api/content since we add it below
            base_url = self.config["host_url"].replace("/rest/api/content", "")
            url = f"{base_url}/rest/api/content/{page_id}/child/attachment"
            log.debug(f"Upload URL: {url}")

            # Set headers for Confluence Cloud API
            headers = {
                "X-Atlassian-Token": "no-check",  # Disable XSRF check
            }

            with open(filepath, "rb") as f:
                files = {
                    "file": (filepath.name, f, mimetypes.guess_type(filepath.name)[0])
                }
                data = {"comment": comment}
                log.debug(f"Uploading file with comment: {comment}")
                response = self.session.post(
                    url, files=files, data=data, headers=headers
                )

            if response.status_code in (200, 201):
                log.info(
                    f"✓ Successfully uploaded attachment '{filepath.name}' ({file_size} bytes) to page ID {page_id}"
                )
                log.debug(f"Upload response status: {response.status_code}")
            else:
                log.error(
                    f"✗ Failed to upload attachment '{filepath.name}' (status {response.status_code}): {response.text}"
                )
        except Exception as e:
            log.error(f"✗ Error uploading attachment {filepath}: {e}")

    def delete_attachment(self, attachment_id):
        """Delete an attachment by ID."""
        try:
            # Use base URL without /rest/api/content since we add it below
            base_url = self.config["host_url"].replace("/rest/api/content", "")
            url = f"{base_url}/rest/api/content/{attachment_id}"
            response = self.session.delete(url)
            if response.status_code == 204:
                log.info(f"Deleted attachment ID {attachment_id}.")
            else:
                log.error(
                    f"Failed to delete attachment ID {attachment_id} (status {response.status_code}): {response.text}"
                )
        except Exception as e:
            log.error(f"Error deleting attachment {attachment_id}: {e}")

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

    def build_and_publish_tree(
        self,
        nav_tree: list,
        parent_id: Optional[str] = None,
        path_stack: list = None,
        processed_pages: set = None,
    ):
        if path_stack is None:
            path_stack = []

        # Initialize processed_pages set at the top level
        if processed_pages is None:
            processed_pages = set()

        for node in nav_tree:
            if isinstance(node, str):
                path_stack_full = path_stack + [node]
                lookup_key = self.normalize_title_key("/".join(path_stack_full))

                page_info = self.page_lookup.get(lookup_key)

                # If not found, try fallback strategies
                if not page_info:
                    # Strategy 1: Try just the node name
                    fallback_key = self.normalize_title_key(node)
                    page_info = self.page_lookup.get(fallback_key)
                    if page_info:
                        log.debug(
                            f"✅ Found page using fallback key '{fallback_key}' for '{node}'"
                        )

                    # Strategy 2: Try removing .md extension if present
                    if not page_info and node.endswith(".md"):
                        node_without_ext = node[:-3]  # Remove .md
                        ext_fallback_key = self.normalize_title_key(node_without_ext)
                        page_info = self.page_lookup.get(ext_fallback_key)
                        if page_info:
                            log.debug(
                                f"✅ Found page using extension-stripped key '{ext_fallback_key}' for '{node}'"
                            )

                    # Strategy 3: Try title-based fuzzy matching first, then fallback to key matching
                    if not page_info:
                        # Convert navigation entry to clean format for comparison
                        node_clean = (
                            node.replace(".md", "").replace("-", " ").replace("_", " ")
                        )

                        # Strategy 3a: Priority title matching - direct comparison with page titles
                        best_match = None
                        best_similarity = 0.0

                        # First pass: Look for title matches with high priority
                        for key, page_data in self.page_lookup.items():
                            page_title = page_data.get("title", "")
                            if not page_title:
                                continue

                            # Calculate similarity between navigation entry and page title
                            title_similarity = self.calculate_word_similarity(
                                node_clean, page_title
                            )

                            # Bonus for context matching - check if the page path contains folder context
                            context_bonus = 0.0
                            if len(path_stack) > 0:
                                # Check if any words from the path stack appear in the page key or title
                                path_context = (
                                    " ".join(path_stack)
                                    .lower()
                                    .replace("-", " ")
                                    .replace("_", " ")
                                )
                                path_words = set(path_context.split())

                                # Check page key for context words
                                key_words = set(
                                    key.lower()
                                    .replace("-", " ")
                                    .replace("_", " ")
                                    .split()
                                )
                                title_words = set(
                                    page_title.lower()
                                    .replace("-", " ")
                                    .replace("_", " ")
                                    .split()
                                )

                                key_context_overlap = len(
                                    path_words.intersection(key_words)
                                )
                                title_context_overlap = len(
                                    path_words.intersection(title_words)
                                )

                                if key_context_overlap > 0 or title_context_overlap > 0:
                                    context_bonus = min(
                                        0.2,
                                        (key_context_overlap + title_context_overlap)
                                        * 0.05,
                                    )

                            # Apply context bonus to title similarity
                            adjusted_similarity = title_similarity + context_bonus

                            # Higher priority for title matches
                            if (
                                adjusted_similarity > best_similarity
                                and adjusted_similarity >= 0.25
                            ):
                                best_similarity = adjusted_similarity
                                best_match = (
                                    key,
                                    page_data,
                                    "title",
                                    title_similarity,
                                    context_bonus,
                                )

                        # Second pass: Only if no good title match, try key matching
                        if (
                            best_similarity < 0.4
                        ):  # Only fallback to key matching if title match is poor
                            for key, page_data in self.page_lookup.items():
                                # Calculate similarity between navigation entry and lookup key
                                key_similarity = self.calculate_word_similarity(
                                    node_clean, key.replace("-", " ")
                                )

                                # Apply same context bonus logic for key matching
                                context_bonus = 0.0
                                if len(path_stack) > 0:
                                    path_context = (
                                        " ".join(path_stack)
                                        .lower()
                                        .replace("-", " ")
                                        .replace("_", " ")
                                    )
                                    path_words = set(path_context.split())
                                    key_words = set(
                                        key.lower()
                                        .replace("-", " ")
                                        .replace("_", " ")
                                        .split()
                                    )
                                    key_context_overlap = len(
                                        path_words.intersection(key_words)
                                    )
                                    if key_context_overlap > 0:
                                        context_bonus = min(
                                            0.2, key_context_overlap * 0.05
                                        )

                                adjusted_similarity = key_similarity + context_bonus

                                if (
                                    adjusted_similarity > best_similarity
                                    and adjusted_similarity >= 0.25
                                ):
                                    best_similarity = adjusted_similarity
                                    best_match = (
                                        key,
                                        page_data,
                                        "key",
                                        key_similarity,
                                        context_bonus,
                                    )

                        if best_match:
                            page_info = best_match[1]
                            match_type = best_match[2]
                            base_similarity = best_match[3]
                            context_bonus = best_match[4]
                            log.debug(
                                f"✅ Found page using {match_type} matching '{best_match[0]}' (similarity: {base_similarity:.3f} + context: {context_bonus:.3f} = {best_similarity:.3f}) for '{node}' in path {path_stack}"
                            )

                        # Strategy 3b: Enhanced fuzzy matching as final fallback
                        if not page_info:
                            possible_keys = list(self.page_lookup.keys())
                            matches = get_close_matches(
                                lookup_key, possible_keys, n=10, cutoff=0.6
                            )

                            # If node has .md extension, also try fuzzy matching without it
                            if node.endswith(".md"):
                                node_without_ext = node[:-3]
                                ext_stripped_key = self.normalize_title_key(
                                    node_without_ext
                                )
                                ext_matches = get_close_matches(
                                    ext_stripped_key, possible_keys, n=10, cutoff=0.6
                                )
                                matches.extend(ext_matches)
                            # Strategy 3c: Try traditional fuzzy matching on the results
                            if not page_info:
                                for match in matches:
                                    page_title = self.page_lookup[match].get(
                                        "title", ""
                                    )
                                    # More flexible title matching
                                    normalized_page_title = (
                                        page_title.lower()
                                        .replace(" ", "-")
                                        .replace("_", "-")
                                    )
                                    normalized_node = (
                                        node.lower()
                                        .replace(" ", "-")
                                        .replace("_", "-")
                                        .replace(".md", "")
                                    )

                                    if (
                                        normalized_page_title == normalized_node
                                        or match
                                        == self.normalize_title_key(
                                            node_without_ext
                                            if node.endswith(".md")
                                            else node
                                        )
                                    ):
                                        page_info = self.page_lookup[match]
                                        log.debug(
                                            f"✅ Found page using fuzzy match '{match}' for '{node}'"
                                        )
                                        break

                        if not page_info:
                            log.warning(
                                f"⚠️ No page data found for '{node}' → tried key '{lookup_key}' and fallback '{fallback_key}'"
                            )
                            log.debug(
                                f"🔍 Best similarity was: {best_similarity:.3f} (threshold: 0.25)"
                            )
                            if (
                                len(self.page_lookup) <= 20
                            ):  # Only show all keys if there aren't too many
                                log.debug(
                                    f"🔍 Available page_lookup keys: {list(self.page_lookup.keys())}"
                                )
                            else:
                                log.debug(
                                    f"🔍 {len(self.page_lookup)} page_lookup keys available"
                                )
                            continue

                # Mark this page as processed using the key that actually worked
                if page_info:
                    # Figure out which key was actually used
                    if lookup_key in self.page_lookup:
                        processed_pages.add(lookup_key)
                    else:
                        fallback_key = self.normalize_title_key(node)
                        if fallback_key in self.page_lookup:
                            processed_pages.add(fallback_key)
                        else:
                            # Must have been found via fuzzy matching, find the actual key
                            for key, info in self.page_lookup.items():
                                if info == page_info:
                                    processed_pages.add(key)
                                    break

                body = page_info.get("body", "")
                abs_src_path = page_info.get("abs_src_path")
                attachments = (
                    self.attachments.get(abs_src_path, []) if abs_src_path else []
                )

                page_id = self.create_or_update_page(
                    title=page_info.get("title", node),
                    body=body,
                    parent_id=parent_id,
                    attachments=attachments,
                    abs_src_path=abs_src_path,
                )
                self.sync_page_attachments(page_id, attachments)

            elif isinstance(node, dict):
                for folder, children in node.items():
                    folder_title = folder
                    path_stack_full = path_stack + [folder_title]
                    folder_lookup_key = self.normalize_title_key(
                        "/".join(path_stack_full)
                    )

                    folder_page_info = self.page_lookup.get(
                        folder_lookup_key,
                        {
                            "title": folder_title,
                            "body": "",
                            "is_folder": True,
                        },
                    )

                    # Mark folder as processed if it exists in page_lookup
                    if folder_lookup_key in self.page_lookup:
                        processed_pages.add(folder_lookup_key)
                    else:
                        # Try fallback for folders too
                        fallback_folder_key = self.normalize_title_key(folder_title)
                        if fallback_folder_key in self.page_lookup:
                            folder_page_info = self.page_lookup[fallback_folder_key]
                            processed_pages.add(fallback_folder_key)
                            log.debug(
                                f"✅ Found folder using fallback key '{fallback_folder_key}' for '{folder_title}'"
                            )

                    folder_id = self.create_or_update_page(
                        title=folder_page_info.get("title", folder_title),
                        body=folder_page_info.get("body", ""),
                        parent_id=parent_id,
                        is_folder=folder_page_info.get("is_folder", True),
                    )
                    self.build_and_publish_tree(
                        children,
                        parent_id=folder_id,
                        path_stack=path_stack_full,
                        processed_pages=processed_pages,
                    )

        # Report orphan pages (only at the top level to avoid duplicate reporting)
        if not path_stack:  # Only report orphans at the root level
            orphan_pages = set(self.page_lookup.keys()) - processed_pages
            for orphan_key in orphan_pages:
                orphan_info = self.page_lookup[orphan_key]
                orphan_title = orphan_info.get("title", orphan_key)
                log.info(
                    f"📄 Orphan page found: '{orphan_title}' (not referenced in navigation)"
                )

    def build_page_lookup(self):
        self.page_lookup = {}
        for page in self.pages:
            abs_path = page.get("abs_src_path")
            if not abs_path:
                continue
            rel_path = os.path.relpath(abs_path, "docs").replace("\\", "/")
            path_parts = rel_path.replace(".md", "").split("/")
            normalized_key = self.normalize_title_key("/".join(path_parts))
            self.page_lookup[normalized_key] = page

    def debug_dump_page_parents(self):
        print("🔍 Page parent mapping:")
        for child, parent in self.page_parents.items():
            print(f"  {child} ← {parent}")

    def dryrun_log(self, action: str, title: str, parent_id=None):
        """Log dry run actions with consistent formatting."""
        parent_info = f" under parent ID {parent_id}" if parent_id else ""
        # Ensure "page" is included in the action for test compatibility
        if (
            action.lower() in ["create", "update", "publish"]
            and "page" not in action.lower()
        ):
            action = f"{action} page"
        log.info(f"DRYRUN: Would {action} '{title}'{parent_info}")

    def _cache_key(self, title: str, parent_id) -> tuple:
        return (self._normalize_title(title), str(parent_id) if parent_id else None)

    def get_file_sha1(self, file_path):
        hash_sha1 = hashlib.sha1()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha1.update(chunk)
        return hash_sha1.hexdigest()
