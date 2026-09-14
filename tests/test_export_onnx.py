from __future__ import annotations

import ast
from pathlib import Path


def test_onnx_export_contract_is_text_only() -> None:
    source = Path("training/export_onnx.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    export_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "export"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "onnx"
    ]
    assert len(export_calls) == 1

    input_names = next(
        keyword.value
        for keyword in export_calls[0].keywords
        if keyword.arg == "input_names"
    )
    assert isinstance(input_names, ast.List)
    assert [item.value for item in input_names.elts] == [
        "input_ids",
        "attention_mask",
        "position_ids",
    ]

    forward_methods = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "forward"
    ]
    assert len(forward_methods) == 1
    assert [
        argument.arg
        for argument in forward_methods[0].args.args
        if argument.arg != "self"
    ] == ["input_ids", "attention_mask", "position_ids"]
