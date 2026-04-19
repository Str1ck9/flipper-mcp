.PHONY: help install dev test lint format clean smoke

help:
	@echo "flipper-mcp — common dev tasks"
	@echo ""
	@echo "  make install      create .venv and install package + dev deps"
	@echo "  make test         run pytest"
	@echo "  make lint         ruff check (no fixes)"
	@echo "  make format       ruff format + fix"
	@echo "  make smoke        connect to Flipper and run device_info"
	@echo "  make validate     validate every bundled protocol JSON"
	@echo "  make clean        remove build artifacts and .venv"

install:
	uv venv
	uv pip install -e ".[dev]"

test:
	.venv/bin/pytest -v

lint:
	.venv/bin/ruff check flipper_mcp tests

format:
	.venv/bin/ruff format flipper_mcp tests
	.venv/bin/ruff check --fix flipper_mcp tests

smoke:
	.venv/bin/flipper-smoke

validate:
	@for f in flipper_mcp/protocols/*.json; do \
		.venv/bin/flipper-registry validate "$$f" || exit 1; \
	done

clean:
	rm -rf .venv dist build *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
