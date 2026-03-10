# Project Guidelines

## Architecture

- Treat this repo as a small Python monorepo with clear boundaries: `discord_bot.py` is the chat entry point, `database/` is the shared storage package, `services/schwab-price-sync/` ingests Schwab candles, `services/schwab-automation/` is the host-run automation worker, and `mcp-services/` contains supporting MCP servers.
- Keep shared database access in `database/src/swing_agent_database/`; do not duplicate connection or SQLAlchemy setup inside services when the shared package already owns it.
- Use `database/migrations/` as the source of truth for schema changes. Add new date-prefixed SQL migrations instead of hiding schema changes in application startup code.
- Keep the automation worker host-run. Docker Compose in `docker/docker-compose.yml` is for MCP/support services, while `schwab_automation.main` should connect to those services over `localhost`.

## Build and Test

- Use the workspace virtual environment in `.venv` and Python 3.11+.
- Install dependencies with `python -m pip install -r requirements.txt`, which also installs the editable local packages from `database/`, `services/schwab-automation/`, and `services/schwab-price-sync/`.
- For focused service installs, use editable installs rather than copying code between packages: `python -m pip install -e ./database -e ./services/schwab-automation -e ./services/schwab-price-sync`.
- Start supporting services with `docker compose -f docker/docker-compose.yml up -d --build` when work depends on MCP endpoints or scheduled sync containers.
- Run the automation worker from the host, not from Docker. The existing VS Code launch configs in `.vscode/launch.json` are the preferred local entry points for dry-run and loop workflows.
- Run tests with `python -m unittest discover -s services/schwab-automation/tests -p "test_*.py"`.

## Conventions

- Preserve the existing package layout and import style. Many modules rely on `from __future__ import annotations`, typed dataclasses, and small focused modules rather than large multi-purpose files.
- Treat configuration objects as explicit and mostly immutable. Prefer extending the existing config loaders and dataclasses over reading environment variables ad hoc throughout the codebase.
- When local runs need unreleased source packages, keep `PYTHONPATH` aligned with the launch configs: `services/schwab-automation/src;database/src`.
- Respect the repo's runtime split: price sync can run in Docker or locally, but Copilot-backed automation analysis is expected to run on the host against MCP endpoints exposed from Docker.
- Keep prompt assets and other packaged data inside their owning package directories, and update package metadata when adding new packaged files.
- When adding tests, follow the existing `unittest` style in `services/schwab-automation/tests/` rather than introducing a new test framework.