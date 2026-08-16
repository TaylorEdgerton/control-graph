SYSTEM_PYTHON ?= python3
VENV ?= .venv
PYTHON ?= $(VENV)/bin/python
PORT ?= 8765

.PHONY: install build run dev test check clean

install:
	$(SYSTEM_PYTHON) -m venv $(VENV)
	$(PYTHON) -m pip install -e .
	npm install

build:
	npm run build

run: build
	$(PYTHON) -m controlgraph --port $(PORT)

dev:
	@$(PYTHON) -m controlgraph --api-only --port $(PORT) & api_pid=$$!; \
	trap 'kill $$api_pid 2>/dev/null || true' INT TERM EXIT; \
	npm run dev

test:
	$(PYTHON) -m unittest discover -s tests -v

check:
	$(PYTHON) -m compileall -q controlgraph tests
	npm run check
	$(PYTHON) -m unittest discover -s tests -v

clean:
	rm -rf frontend/dist controlgraph.egg-info
