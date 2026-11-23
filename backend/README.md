# qorvexa (backend)

Python package for the QORVEXA Core backend: data pipeline, models, evidence/RCA,
uncertainty, simulation and the FastAPI service.

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy
uv run uvicorn qorvexa.api.app:app --reload
```

Large data, model caches and experiment runs live outside the repository; see
`QORVEXA_DATA_DIR` in `src/qorvexa/config.py`.
