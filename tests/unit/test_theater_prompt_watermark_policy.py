"""Keep theater and workshop model data behind the shared prompt boundary."""

import ast
from pathlib import Path
import sys

import pytest


_REPO = Path(__file__).resolve().parents[2]
_DIRECTORIES = ("services/theater", "services/theater_workshop")
_FRAMES = {"prompt_data_block", "render_prompt_context"}


def _message_data(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"HumanMessage", "AIMessage"}:
                content = next((item.value for item in node.keywords if item.arg == "content"), None)
                if content is not None:
                    yield node, content
        elif isinstance(node, ast.Dict):
            fields = {key.value: value for key, value in zip(node.keys, node.values)
                      if isinstance(key, ast.Constant) and isinstance(key.value, str)}
            role = fields.get("role")
            if isinstance(role, ast.Constant) and role.value in {"user", "assistant"} and "content" in fields:
                yield node, fields["content"]


def _unframed_messages(source, path="synthetic.py"):
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def enclosing(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                yield node

    def framed(node, seen=frozenset()):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return True  # Fixed instructions contain no external data.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FRAMES:
            return (node.func.id == "render_prompt_context" or
                    bool(node.args and isinstance(node.args[0], ast.Constant)
                         and isinstance(node.args[0].value, str)))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # Existing Actor/Evaluator builders prepend this fixed instruction
            # variable. New arbitrary prefixes must prove their own framing.
            prefix = isinstance(node.left, ast.Name) and node.left.id == "human_prefix"
            return (prefix or framed(node.left, seen)) and framed(node.right, seen)
        if isinstance(node, ast.Name) and node.id not in seen:
            for scope in enclosing(node):
                assignments = [item for item in ast.walk(scope)
                               if isinstance(item, ast.Assign) and item.lineno < node.lineno
                               and any(isinstance(target, ast.Name) and target.id == node.id
                                       for target in item.targets)
                               and next(enclosing(item), None) is scope]
                if assignments:
                    latest = max(assignments, key=lambda item: item.lineno)
                    return framed(latest.value, seen | {node.id})
        return False

    failures, checked = [], 0
    for message, content in _message_data(tree):
        # This constructs a temporary message solely to count an already
        # rendered argument; it is not dispatched to a model.
        if (path == "services/theater/numeric_v2_actor.py" and
                any(getattr(scope, "name", "") == "_prompt_token_counter" for scope in enclosing(message))):
            continue
        checked += 1
        if not framed(content):
            failures.append(f"{path}:{message.lineno}: {ast.unparse(content)}")
    return failures, checked


def test_theater_and_workshop_model_data_use_framed_inputs():
    failures, checked = [], 0
    for directory in _DIRECTORIES:
        for path in sorted((_REPO / directory).rglob("*.py")):
            found, count = _unframed_messages(path.read_text(encoding="utf-8"), path.relative_to(_REPO).as_posix())
            failures.extend(found)
            checked += count
    assert checked >= 30, f"Only {checked} model message boundaries were checked"
    assert not failures, "Model data bypassed prompt_data_block/render_prompt_context:\n" + "\n".join(failures)


def test_shared_watermark_keeps_sdk_and_frozen_runtime_import_boundaries():
    helper = ast.parse((_REPO / "services/theater/prompt_data.py").read_text(encoding="utf-8"))
    for node in ast.walk(helper):
        modules = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                   else [node.module] if isinstance(node, ast.ImportFrom) else [])
        assert all(name.split(".")[0] in sys.stdlib_module_names for name in modules)
    # Desktop builds exclude the workshop SDK; runtime framing must still load.
    for path in (_REPO / "services/theater").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("services.theater_workshop"), path


@pytest.mark.parametrize("source", [
    "HumanMessage(content=json.dumps(data))",
    "{'role': 'user', 'content': json.dumps(data)}",
    "{'role': 'assistant', 'content': response}",
    "user = json.dumps(data)\nHumanMessage(content=user)",
    "user = prompt_data_block('审核数据', data)\nuser = json.dumps(data)\nHumanMessage(content=user)",
    "HumanMessage(content=player_input + render_prompt_context(data))",
    "HumanMessage(content=prompt_data_block(player_label, data))",
])
def test_model_boundary_guard_detects_unframed_data(source):
    assert _unframed_messages(source)[0]


@pytest.mark.parametrize("source", [
    "HumanMessage(content=prompt_data_block('审核数据', data))",
    "{'role': 'user', 'content': render_prompt_context(data)}",
    "{'role': 'assistant', 'content': prompt_data_block('待修复回复', {'response': response})}",
    "user = prompt_data_block('审核数据', data)\nHumanMessage(content=user)",
    "HumanMessage(content='固定说明：' + prompt_data_block('审核数据', data))",
])
def test_model_boundary_guard_accepts_framed_data(source):
    assert not _unframed_messages(source)[0]
