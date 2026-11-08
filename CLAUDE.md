# Repository guidance

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the current file map,
calculation conventions, data flow, and task-to-file reference.
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) covers TWS and Docker setup.

- Python 3.12; launch with `python main.py --demo` or `python main.py`.
- Backend code must not import Dash or `ui`; the architecture test enforces it.
- Put financial calculations in `backend/portfolio`, provider calls in
  `backend/providers`, storage in `backend/storage`, and rendering in `ui`.
- Reuse shared exposure and daily-P&L rules for UI, Coach, and PDF.
- Missing inputs produce unavailable results; zero is not missing.
- Keep `main.py`'s event-loop initialization before imports that load ib_async.
- Preserve branding, existing runtime data, and the one-process deployment.
- Before committing, run `ruff check .`, `python smoke_test.py`, and
  `python -m pytest -q`. Do not commit a failing snapshot.
- Generated `graphify-out/`, environments, caches, and private data stay local.

The original findings and their implementation status are in
[REPO_AUDIT.md](REPO_AUDIT.md). Old line references there describe the historical
checkout, not the current directory structure.
