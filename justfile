# Local dashboard of running Claude Code sessions (http://localhost:8447)
dev:
    uv run claude-monitor

# Same as dev; matches `just dash` in work/devenv
dash: dev

test *args:
    uv run pytest {{args}}

lint:
    uv run ruff check .
    uv run ruff format --check .

fmt:
    uv run ruff check --fix .
    uv run ruff format .

deps-update:
    uv lock --upgrade
    uv sync
