.PHONY: install test lint clean help

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

clean: ## Remove build artifacts and venv
	rm -rf $(VENV) build/ dist/ *.egg-info .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

agents: install ## List available agents
	$(VENV)/bin/python -m agent_baton.cli.main agents

validate: install ## Validate agent definitions
	$(VENV)/bin/python -m agent_baton.cli.main validate agents/
