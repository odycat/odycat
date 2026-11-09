# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# OdyCAT development tasks. `make` lists them.
#
# The Python used for wheels, tests and installs: the active virtualenv, else
# the repository's .venv, else python3. Override with `make ... PYTHON=...`.
PYTHON ?= $(if $(VIRTUAL_ENV),$(VIRTUAL_ENV)/bin/python,$(if $(wildcard .venv/bin/python),.venv/bin/python,python3))

help:
	@echo "make engine     build engine/odycat (checks the build dependencies)"
	@echo "sudo make -C engine install   /usr/local/bin/odycat, root-owned (after make engine)"
	@echo "make wheel      dist/odycat-*.whl: the Python package (pure Python, no engine)"
	@echo "make install    build the wheel and (re)install it into $(PYTHON)"
	@echo "make test       unit tests (no hardware)"
	@echo "make lint       static checks"
	@echo "make docs       documentation site in docs/_build/html (pip install -r docs/requirements.txt)"
	@echo "make clean      remove build output"

engine:
	$(MAKE) -C engine

# The wheel's build step (setup.py) compiles the engine through engine/Makefile.
wheel:
	rm -rf build *.egg-info src/*.egg-info dist/odycat-*.whl
	$(PYTHON) -m pip wheel . --no-deps --no-cache-dir -w dist
	@ls dist/odycat-*.whl

install: wheel
	$(PYTHON) -m pip install --force-reinstall --no-deps dist/odycat-*.whl

test:
	PYTHON=$(PYTHON) bash ci/test.sh

lint:
	PYTHON=$(PYTHON) bash ci/lint.sh

docs:
	$(PYTHON) -m sphinx -W --keep-going -b html docs docs/_build/html

clean:
	$(MAKE) -C engine clean
	rm -rf build dist *.egg-info src/*.egg-info reports docs/_build docs/generated

.PHONY: help engine wheel install test lint docs clean
