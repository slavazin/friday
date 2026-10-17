"""The telemetry catalogue (docs/telemetry.md) read as data: every row name it lists, with its section and kind.

Parses the catalogue's tables only. A row named with a `<phase>` placeholder expands to the phases its
attributes list before the first `;`; a metric row whose attributes begin "also" adds each dotted name they
list as a row of the same kind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_SECTION = re.compile(r"^## (.+?)\s*$")
_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([a-z]+)[^|]*\|\s*(.*?)\s*\|\s*$")
_TICKED = re.compile(r"`([^`]+)`")


@dataclass(frozen=True)
class CatalogueRow:
    name: str
    kind: str          # span | metric | event
    section: str       # the catalogue heading it sits under: Run, Round, Pass, Eval, Judge, Model call, ...


def parse_catalogue(text: str) -> dict[str, CatalogueRow]:
    """Row name -> its catalogue entry, for every table row in `text`. Raises ValueError on a repeated name."""
    rows: dict[str, CatalogueRow] = {}
    section = ""

    def add(name: str, kind: str) -> None:
        if name in rows:
            raise ValueError(f"telemetry catalogue lists {name!r} twice")
        rows[name] = CatalogueRow(name, kind, section)

    for line in text.splitlines():
        head = _SECTION.match(line)
        if head:
            section = head.group(1).split(" (")[0]
            continue
        m = _ROW.match(line)
        if m is None or m.group(1) == "Row":
            continue
        name, kind, attrs = m.groups()
        if "<phase>" in name:
            phases = _TICKED.findall(attrs.split(";")[0])
            if not phases:
                raise ValueError(f"{name}: no phases listed before the first ';'")
            for phase in phases:
                add(name.replace("<phase>", phase), kind)
            continue
        add(name, kind)
        if attrs.startswith("also "):
            for extra in _TICKED.findall(attrs):
                if "." in extra:
                    add(extra, kind)
    return rows


def load_catalogue(path: str | Path) -> dict[str, CatalogueRow]:
    """`parse_catalogue` over the file at `path` (docs/telemetry.md)."""
    return parse_catalogue(Path(path).read_text(encoding="utf-8"))
