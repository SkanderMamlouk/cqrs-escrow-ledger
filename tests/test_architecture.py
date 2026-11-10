"""The business core (banking/) must stay pure Python: no Django import allowed."""

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "banking"


def test_domain_layer_does_not_depend_on_django():
    offenders = []
    for file in CORE.glob("*.py"):
        tree = ast.parse(file.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{file.name}: {n}" for n in names if n.split(".")[0] in ("django", "readmodel", "web")]
    assert offenders == []
