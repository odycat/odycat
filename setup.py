# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
import os
import re

from setuptools import Extension, setup, find_packages

# The wheel is the Python package, plus odycat._atomic: the 64-bit release
# store that publishes outputs to the engine (needed on ARM; optional on
# x86-64, where a plain store does it). On the CPython stable ABI, so one
# wheel per architecture serves Python 3.11 and later. If it cannot be
# compiled (no compiler), the package installs without it: on x86-64 it
# works the same, elsewhere writing outputs is refused with a clear message.
#
# The C engine is built and installed separately, root-owned, so that only
# root can replace the program `sudo` runs:
#     make -C engine && sudo make -C engine install
# (docs/getting-started.md).
#
# ODYCAT_PURE=1 builds the pure-Python wheel (py3-none-any) without it: the
# fallback for x86-64 machines without a wheel of their own.
PURE = os.environ.get("ODYCAT_PURE") == "1"
# The release wheels (cibuildwheel sets CIBUILDWHEEL=1) and CI
# (ODYCAT_REQUIRE_EXT=1) must carry the extension: there a failed compile
# fails the build instead of shipping a wheel that refuses to write outputs
# on ARM.
REQUIRE_EXT = "1" in (os.environ.get("CIBUILDWHEEL"), os.environ.get("ODYCAT_REQUIRE_EXT"))

# The version is written once, in src/odycat/_version.py.
_HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_HERE, "src", "odycat", "_version.py"), encoding="utf-8") as f:
    VERSION = re.search(r'^__version__ = "([^"]+)"', f.read(), re.M).group(1)

REPO = "https://github.com/odycat/odycat"
DOCS = "https://docs.odycat.org/en/stable"


def _long_description() -> str:
    """The README for the PyPI page: without the logo block (PyPI drops
    <picture>), and with its relative links made absolute: pages of docs/ to
    the documentation site, other files to the repository."""
    with open(os.path.join(_HERE, "README.md"), encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r'\A<p align="center">\s*<picture>.*?</picture>\s*</p>\s*', "# OdyCAT\n\n",
                  text, flags=re.S)

    def absolute(m: re.Match) -> str:
        target, anchor = m.group(1), m.group(2) or ""
        if target == "docs/README.md":
            url = DOCS + "/"
        elif target.startswith("docs/") and target.endswith(".md"):
            url = f"{DOCS}/{target[5:-3]}.html"
        else:
            url = f"{REPO}/blob/main/{target}"
        return f"]({url}{anchor})"
    return re.sub(r"\]\((?![a-z]+:)([^)#\s]+)(#[^)\s]*)?\)", absolute, text)


setup(
    name="odycat",
    version=VERSION,
    description="Open machine and factory automation with Python: EtherCAT on Linux",
    long_description=_long_description(),
    long_description_content_type="text/markdown",
    author="Javier Martínez García",
    maintainer="Yoctovision S.L.U.",
    url="https://odycat.org",
    project_urls={
        "Documentation": "https://docs.odycat.org",
        "Source": REPO,
        "Issues": REPO + "/issues",
        "Changelog": REPO + "/blob/main/CHANGELOG.md",
    },
    license="GPL-3.0-only OR LicenseRef-OdyCAT-Commercial",
    license_files=["LICENSE", "LICENSING.md", "LICENSES/GPL-3.0-only.txt",
                   "LICENSES/LicenseRef-OdyCAT-Commercial.txt"],   # not MIT.txt: examples/ is not packaged
    keywords=["ethercat", "igh", "fieldbus", "industrial automation", "real-time",
              "cia402", "servo", "plc"],
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Intended Audience :: Manufacturing",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: C",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Scientific/Engineering",
        "Topic :: System :: Hardware :: Hardware Drivers",
    ],
    python_requires=">=3.11",
    package_dir={"": "src"},
    packages=find_packages("src"),
    include_package_data=False,
    install_requires=[],
    entry_points={
        "console_scripts": [
            "odycat-config = odycat._scan:main",
        ],
    },
    **({} if PURE else {
        "ext_modules": [Extension("odycat._atomic", ["src/odycat/_atomic.c"],
                                  py_limited_api=True, optional=not REQUIRE_EXT)],
        "options": {"bdist_wheel": {"py_limited_api": "cp311"}},
    }),
    zip_safe=False,
)
