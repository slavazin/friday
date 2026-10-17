import os
from pathlib import Path

import pytest

from friday.config import load_spec

KEY_ENV = load_spec(Path(__file__).resolve().parents[1] / "run.yml").models.endpoint.api_key_env


def pytest_collection_modifyitems(config, items):
    """Live tests need the Crusoe key; without it they skip, whatever -m expression selected them."""
    if os.environ.get(KEY_ENV):
        return
    skip = pytest.mark.skip(reason=f"{KEY_ENV} not set; run with: uv run --env-file .env pytest -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
