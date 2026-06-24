import ast
from pathlib import Path


def test_memory_hot_paths_do_not_write_to_stdout():
    repo_root = Path(__file__).resolve().parents[2]

    for relative_path in ("memory/persona.py", "memory/recent.py"):
        source = (repo_root / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative_path)
        print_calls = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ]
        assert print_calls == []
