UV ?= uv

.PHONY: sync format lint typecheck test check build nix-build

sync:
	$(UV) sync --locked

format:
	$(UV) run --locked ruff format src tests
	$(UV) run --locked ruff check --fix src tests

lint:
	$(UV) run --locked ruff format --check src tests
	$(UV) run --locked ruff check src tests

typecheck:
	$(UV) run --locked ty check src/stackman

test:
	$(UV) run --locked pytest

check: sync lint typecheck test

build:
	$(UV) build

nix-build:
	nix build
