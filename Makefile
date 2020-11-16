.DEFAULT_GOAL := test
define BROWSER_PYSCRIPT
import os, webbrowser, sys
try:
	from urllib import pathname2url
except:
	from urllib.request import pathname2url

webbrowser.open("file://" + pathname2url(os.path.abspath(sys.argv[1])))
endef
export BROWSER_PYSCRIPT


VENV_PATH ?= .venv
VENV_BIN=$(VENV_PATH)/bin
BROWSER := $(VENV_BIN)/python -c "$$BROWSER_PYSCRIPT"

.PHONY: clean
clean: clean-pyc clean-build clean-test clean-venv clean-poetry clean-docs clean-mypy  ## Remove all build, test, coverage and Python artifacts

.PHONY: clean-build
clean-build:  ## Remove build artifacts
	rm -fr build/
	rm -fr dist/
	rm -fr .eggs/
	find . -name '*.egg-info' -exec rm -fr {} +
	find . -name '*.egg' -exec rm -f {} +

.PHONY: clean-pyc
clean-pyc:  ## Remove Python file artifacts
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -fr {} +

.PHONY: clean-test
clean-test:  ## Remove test and coverage artifacts
	rm -fr .tox/
	rm -f .coverage
	rm -fr htmlcov/
	rm -fr .pytest_cache

.PHONY: clean-docs
clean-docs:  ## Remove docs artifacts
	rm -rf docs/build
	rm -rf docs/source/ipapp*.rst
	rm -rf docs/source/modules.rst

.PHONY: clean-mypy
clean-mypy:  ## Remove mypy cache
	rm -rf .mypy_cache

.PHONY: clean-venv
clean-venv:  ## Remove virtual environment
	-rm -rf $(VENV_PATH)

.PHONY: clean-poetry
clean-poetry:  ## Remove poetry.lock
	-rm poetry.lock

$(VENV_PATH):  ## Create a virtual environment
	virtualenv -p python3.7 $@
	$(VENV_PATH)/bin/pip install -U pip setuptools

$(VENV_PATH)/pip-status: pyproject.toml | $(VENV_PATH) ## Install (upgrade) all development requirements
	poetry install -E fastapi -E iprpc -E oracle -E postgres -E rabbitmq -E s3 -E sftp -E dbtm -E redis -E testing
	# fix CI error: Uploading artifacts to coordinator... too large archive
	find . -type d -name __pycache__ -exec rm -rf {} \+
	# keep a real file to be able to compare its mtime with mtimes of sources:
	touch $@

.PHONY: venv  # A shortcut for "$(VENV_PATH)/pip-status"
venv: $(VENV_PATH)/pip-status ## Install (upgrade) all development requirements

.PHONY: flake8
flake8: venv  ## Check style with flake8
	$(VENV_BIN)/flake8 ipapp examples tests

.PHONY: bandit
bandit: venv  ## Find common security issues in code
	$(VENV_BIN)/bandit -x ipapp/logic/db -r ipapp examples

.PHONY: mypy
mypy: venv  ## Static type check
	$(VENV_BIN)/mypy ipapp examples --ignore-missing-imports --sqlite-cache

.PHONY: safety
safety: venv  # checks your installed dependencies for known security vulnerabilities
	$(VENV_BIN)/safety check

.PHONY: black
black: venv  # checks imports order
	$(VENV_BIN)/black examples ipapp tests --check

.PHONY: lint
lint: safety bandit mypy flake8 black  ## Run flake8, bandit, mypy

.PHONY: format
format: venv  ## Autoformat code
	$(VENV_BIN)/isort ipapp examples tests
	$(VENV_BIN)/black examples ipapp tests

.PHONY: test
test: venv  ## Run tests
	$(VENV_BIN)/docker-compose -f tests/docker-compose.yml up -d
	$(VENV_BIN)/pytest -v -s tests
	$(VENV_BIN)/docker-compose -f tests/docker-compose.yml kill

.PHONY: build
build: venv  ## Run tests
	poetry build

.PHONY: docs
docs: venv clean-docs  ## Make documentation and open it in browser
	$(VENV_BIN)/sphinx-apidoc -o docs/source/ ipapp
	. $(VENV_BIN)/activate && $(MAKE) -C docs clean && $(MAKE) -C docs html
ifndef CI
	$(BROWSER) docs/build/html/index.html
endif

.PHONY: help
help:  ## Show this help message and exit
	@grep -E '^[0-9a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-23s\033[0m %s\n", $$1, $$2}'

