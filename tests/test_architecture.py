"""Module boundaries that keep everything except the SimConnect bridge runnable on macOS/Linux."""

import ast
import importlib
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "localtc"


def imports_of(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def modules(*parts: str) -> list[Path]:
    return sorted(SRC.joinpath(*parts).rglob("*.py"))


def test_only_app_imports_sim_bridge():
    offenders = []
    for path in modules():
        rel = path.relative_to(SRC)
        if rel.parts[0] == "sim_bridge" or rel == Path("app.py"):
            continue
        if any(name == "localtc.sim_bridge" or name.startswith("localtc.sim_bridge.") for name in imports_of(path)):
            offenders.append(str(rel))
    assert offenders == []


def test_sim_api_depends_on_nothing_else_in_localtc():
    offenders = []
    for path in modules("sim_api"):
        for name in imports_of(path):
            if name.startswith("localtc.") and not name.startswith("localtc.sim_api"):
                offenders.append(f"{path.relative_to(SRC)}: {name}")
    assert offenders == []


def test_every_module_imports_on_any_os():
    for path in modules():
        rel = path.relative_to(SRC.parent).with_suffix("")
        parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
        importlib.import_module(".".join(parts))
