.PHONY: install test lint typecheck doctor ci-local clean help

PYTHON ?= python3
VENV := .venv

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

install: $(VENV)/bin/activate ## Install in editable mode with dev deps

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -e ".[dev]"
	@touch $@

test: install ## Run tests
	$(VENV)/bin/python -m pytest tests/ -q

test-verbose: install ## Run tests with verbose output
	$(VENV)/bin/python -m pytest tests/ -v --tb=short

lint: install ## Run lint if ruff is installed
	@if $(VENV)/bin/python -c "import ruff" >/dev/null 2>&1; then \
		$(VENV)/bin/python -m ruff check .; \
	else \
		echo "ruff not installed; skipping lint"; \
	fi

typecheck: install ## Run type checks if mypy is installed
	@if $(VENV)/bin/python -c "import mypy" >/dev/null 2>&1; then \
		$(VENV)/bin/python -m mypy agent_baton; \
	else \
		echo "mypy not installed; skipping typecheck"; \
	fi

doctor: install ## Run Baton doctor
	$(VENV)/bin/python -m agent_baton.cli.main doctor

ci-local: lint typecheck test doctor ## Run local checks available in this repo

clean: ## Remove build artifacts and venv
	rm -rf $(VENV) build/ dist/ *.egg-info .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

agents: install ## List available agents
	$(VENV)/bin/python -m agent_baton.cli.main agents

validate: install ## Validate agent definitions
	$(VENV)/bin/python -m agent_baton.cli.main validate agents/
