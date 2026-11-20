# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# Sphinx configuration for the OdyCAT documentation site.
# Build: make docs  (pip install -r docs/requirements.txt first)
import os
import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
REPO = DOCS.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(DOCS / "_ext"))

project = "OdyCAT"
author = "Javier Martínez García"
copyright = "2026, Ocean Octane S.L.U. Developed in Spain, EU 🇪🇺"
release = re.search(r'^__version__ = "([^"]+)"', (REPO / "src/odycat/_version.py").read_text(),
                    re.M).group(1)
version = release            # EPUB and the Read the Docs version selector need it

extensions = ["myst_parser", "sphinx.ext.autodoc", "sphinx.ext.napoleon", "odycat_docs"]
source_suffix = {".md": "markdown"}
root_doc = "index"

# Translations: docs/<language>/<page>.md is the translation of docs/<page>.md
# (Spanish: docs/es/). Built with language=<language> (Read the Docs gives a
# translation project's language in READTHEDOCS_LANGUAGE; locally,
# `make docs-es`), the site takes each page from docs/<language>/ when it is
# there, and in English otherwise. Headings keep their English anchors with
# {#anchor} above them where other pages link to them. `make docs-es-status`
# lists the pages whose English text changed since they were translated.
language = os.environ.get("READTHEDOCS_LANGUAGE", "en")
TRANSLATIONS = ["es"]
# README.md is the folder's page in the repository browser; the site includes it from index.md.
exclude_patterns = ["_build", "README.md"] + TRANSLATIONS

myst_heading_anchors = 3
# Examples with "..." in JSON blocks fall back to plain highlighting.
suppress_warnings = ["misc.highlighting_failure"]
myst_enable_extensions = ["colon_fence", "attrs_block"]
autodoc_member_order = "bysource"
autodoc_typehints = "description"

html_theme = "furo"
html_title = f"OdyCAT {release}"
html_static_path = ["img"]
html_theme_options = {"light_logo": "odycat_letters_bright.svg",
                      "dark_logo": "odycat_letters_dark.svg",
                      "sidebar_hide_name": True}
html_favicon = "img/odycat_logo_only_bright.svg"

# Links from the docs to files elsewhere in the repository (../LICENSING.md,
# ../examples/...) point at the public repository.
odycat_repo_url = "https://github.com/odycat/odycat/blob/main/"


def _translated_page(app, docname, source):
    """With a language other than English, a page's text comes from its
    translation in docs/<language>/ when there is one."""
    page = DOCS / app.config.language / f"{docname}.md"
    if app.config.language != "en" and page.is_file():
        source[0] = page.read_text(encoding="utf-8")
        app.env.note_dependency(str(page))


def setup(app):
    app.connect("source-read", _translated_page)
