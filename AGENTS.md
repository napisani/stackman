# Stackman Agent Guidelines

Stackman is a Python 3.12 CLI for managing stacked Git branches.

## Commands

- Install/sync dependencies: `make sync`
- Format and apply safe lint fixes: `make format`
- Check formatting and lint: `make lint`
- Type-check production code: `make typecheck`
- Run tests: `make test`
- Run the complete project gate: `make check`
- Build Python distributions: `make build`
- Build the Nix package: `make nix-build`
- Enter the pinned development shell: `nix develop`
- Run all flake checks: `nix flake check`

## Conventions

- Use Python 3.12 and the existing `src/` package layout.
- Manage dependencies with uv and commit `uv.lock` whenever project metadata changes.
- Use Ruff for formatting, import sorting, and linting; do not add Black, isort, or Flake8 configuration.
- Use ty for production-code type checking; prefer precise types and narrow fixes over broad ignores.
- Use pytest and the real-Git fixtures for Git workflow behavior.
- Preserve the CLI entry point, SQLite data location, output streams, and Git safety guarantees.
- Run the smallest focused test while iterating, then `make check` before completion.
