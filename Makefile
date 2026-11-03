# Stratum Open: tests and the end-to-end suite.
#   make test       the unit tests (no network, no model)
#   make e2e        the end-to-end suite against the fake model and fake GitHub servers (what CI runs)
#   make e2e-live   the end-to-end suite against a real model (Ollama on localhost, or STRATUM_MODEL_ENDPOINT)
PYTHON ?= python

.PHONY: test e2e e2e-live all

test:
	$(PYTHON) -m pytest tests --ignore=tests/e2e -q

e2e:
	$(PYTHON) -m pytest tests/e2e -m "not live" -q

e2e-live:
	$(PYTHON) -m pytest tests/e2e -m live -q -rs

all: test e2e
