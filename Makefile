# Shortcuts for the usual steps. "make help" lists them.
PY ?= python3

help:            ## list the targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

install:         ## Python packages for building and testing, at the tested versions
	$(PY) -m pip install -r requirements-dev.txt -c constraints.txt
	$(PY) -m playwright install chromium

data:            ## download all series (needs FRED_API_KEY in .env) and build docs/data.json
	$(PY) pipeline.py --db macro.db --start 2000-01 --strict
	$(PY) build_data.py --db macro.db --out docs/data.json
	$(PY) validate_data.py docs/data.json

page:            ## build docs/index.html and the pages around it
	$(PY) build_page.py
	$(PY) site/build_site.py

lint:            ## static checks of the Python code
	ruff check macro_config.py pipeline.py build_data.py check_alerts.py validate_data.py tests/unit

test: lint       ## everything CI runs: unit tests, server tests, browser checks
	$(PY) -m pytest
	cd worker && node --test
	$(PY) build_page.py && $(PY) tests/test_page.py
	$(PY) site/build_site.py && $(PY) tests/test_site.py

serve:           ## the built site on http://localhost:8000
	cd docs && $(PY) -m http.server 8000

.PHONY: help install data page lint test serve
