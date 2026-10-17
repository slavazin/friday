"""Content-addressed file cache for model replies (implements `friday.ports.models.ResponseCache`).

A key is the sha256 hex digest of the canonical JSON request body (`request_key`). Each value lives in its
own file `<root>/<key[:2]>/<key>`, written to a temporary file in the same directory and moved into place
with `os.replace`, so a reader never sees a partial value and two writers of one key leave one whole value.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

_KEY = re.compile(r"[0-9a-f]{64}")


def canonical_json(value: Any) -> str:
    """`value` as JSON with sorted keys and no insignificant whitespace: equal bodies give equal text."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def request_key(body: dict[str, Any]) -> str:
    """The cache key of one request body: sha256 of its canonical JSON.

    The body is hashed whole, as sent: model, messages (text and base64 images), temperature, seed,
    response_format and max_tokens all move the key.
    """
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


class FileResponseCache:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        if not _KEY.fullmatch(key):
            raise ValueError(f"cache key must be a sha256 hex digest, got {key!r}")
        return self._root / key[:2] / key

    def get(self, key: str) -> str | None:
        """The cached value for `key`, or None when the key has never been written."""
        path = self._path(key)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def put(self, key: str, value: str) -> None:
        """Writes `value` under `key` atomically (temporary file, then rename)."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{key[:8]}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(value)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
