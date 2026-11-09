# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# Sphinx configuration for the OdyCAT documentation site.
# Build: make docs  (pip install -r docs/requirements.txt first)
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
# README.md is the folder's page in the repository browser; the site includes it from index.md.
exclude_patterns = ["_build", "README.md"]

myst_heading_anchors = 3
# Examples with "..." in JSON blocks fall back to plain highlighting.
suppress_warnings = ["misc.highlighting_failure"]
myst_enable_extensions = ["colon_fence"]
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
