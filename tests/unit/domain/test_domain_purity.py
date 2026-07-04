"""Architectural guard: the domain core must import no framework or vendor code.

This encodes the dependency rule from CLAUDE.md §4/§7 as an executable test —
``domain/`` may depend on Pydantic (its validation library) and the standard
library only. A regression here means a seam was crossed.
"""

from __future__ import annotations

import ast
from pathlib import Path

DOMAIN_DIR = Path(__file__).resolve().parents[3] / "src" / "conduit" / "domain"

FORBIDDEN = {
    "fastapi",
    "starlette",
    "sqlalchemy",
    "asyncpg",
    "alembic",
    "httpx",
    "redis",
    "uvicorn",
    "gunicorn",
    "openai",
    "opentelemetry",
    "prometheus_client",
    "arq",
}


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_domain_imports_no_framework_or_vendor() -> None:
    offenders: dict[str, set[str]] = {}
    for py_file in DOMAIN_DIR.rglob("*.py"):
        crossed = _top_level_imports(py_file) & FORBIDDEN
        if crossed:
            offenders[py_file.name] = crossed
    assert not offenders, f"domain/ crossed a seam: {offenders}"
