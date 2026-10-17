"""Import rules between layers, read from the source with `ast` (no import side effects)."""

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "friday"
STDLIB_OK = {"__future__", "collections", "dataclasses", "enum", "typing", "math", "random", "hashlib", "re",
             "itertools", "functools", "contextlib", "json", "abc", "types", "statistics"}

# layer -> friday subpackages it may import
ALLOWED = {
    "domain": {"domain"},
    "stats": {"stats", "registry"},
    "support": {"support"},
    "algorithms": {"algorithms", "domain", "stats", "registry"},
    "ports": {"ports", "domain"},
    "eval": {"eval", "algorithms", "domain", "ports", "stats"},
    "roles": {"roles", "algorithms", "domain", "ports", "support", "stats"},
    "adapters": {"adapters", "domain", "ports", "config", "support"},
    "app": {"app", "algorithms", "domain", "ports", "roles", "eval", "stats", "config", "registry", "support"},
    "config": set(),
    "registry": set(),
    "types": {"domain"},
}
VENDOR = {"httpx": {"adapters"}, "playwright": {"adapters"}, "yaml": {"config", "adapters", "roles", "app"},
          # network, processes and databases are adapter business; the CLI may reach them only through adapters
          "urllib": {"adapters"}, "socket": {"adapters"}, "http": {"adapters"}, "subprocess": {"adapters"},
          "sqlite3": {"adapters"}, "requests": {"adapters"}}


def imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append(node.module)
            if node.module == "friday":                 # `from friday import adapters` names the subpackage
                out += [f"friday.{a.name}" for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level > 0:
            out.append("<relative>")
    return out


def layer_of(path: Path) -> str:
    rel = path.relative_to(PKG)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def test_layers_import_only_what_they_may():
    bad = []
    for path in PKG.rglob("*.py"):
        layer = layer_of(path)
        if layer not in ALLOWED:
            continue
        for mod in imports(path):
            if mod == "<relative>":
                bad.append(f"{path.relative_to(PKG)} uses a relative import")
                continue
            top, *rest = mod.split(".")
            if top == "friday" and rest and rest[0] not in ALLOWED[layer]:
                bad.append(f"{path.relative_to(PKG)} imports {mod}")
    assert bad == []


def test_vendor_libraries_stay_in_their_layers():
    bad = []
    for path in PKG.rglob("*.py"):
        layer = layer_of(path)
        for mod in imports(path):
            top = mod.split(".")[0]
            if top in VENDOR and layer not in VENDOR[top]:
                bad.append(f"{path.relative_to(PKG)} imports {mod}")
    assert bad == []


def test_pure_layers_use_only_the_standard_library():
    bad = []
    for layer in ("domain", "stats", "algorithms"):
        for path in (PKG / layer).rglob("*.py"):
            for mod in imports(path):
                top = mod.split(".")[0]
                if top not in STDLIB_OK and top != "friday":
                    bad.append(f"{path.relative_to(PKG)} imports {mod}")
    assert bad == []


def test_no_bare_or_broad_except_outside_marked_lines():
    bad = []
    for path in PKG.rglob("*.py"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            s = line.strip()
            if (s.startswith("except Exception") or s == "except:") and "noqa: BLE001" not in s:
                bad.append(f"{path.relative_to(PKG)}:{i}")
    assert bad == []
