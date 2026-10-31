"""A small, safe arithmetic evaluator for 'formula' analyses.

Only numbers, input names, arithmetic/comparison operators, conditional
expressions and a whitelist of math functions are allowed - no attribute
access, subscripts, calls to anything else, or comprehensions.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_}
_CMP = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}
FUNCS = {
    name: getattr(math, name)
    for name in (
        "sqrt", "exp", "log", "log10", "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
        "sinh", "cosh", "tanh", "floor", "ceil", "hypot", "radians", "degrees",
    )
}
FUNCS.update({"abs": abs, "min": min, "max": max, "round": round})
CONSTS = {"pi": math.pi, "e": math.e, "g0": 9.80665}


class FormulaError(ValueError):
    pass


def validate(expr: str, names: set[str]) -> None:
    tree = _parse(expr)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in names and node.id not in CONSTS and node.id not in FUNCS:
            raise FormulaError(f"unknown name '{node.id}' in formula '{expr}'")


def _parse(expr: str) -> ast.Expression:
    if len(expr) > 2000:
        raise FormulaError("formula too long")
    try:
        return ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"invalid formula '{expr}': {exc.msg}") from exc


def evaluate(expr: str, env: dict[str, Any]) -> Any:
    return _eval(_parse(expr).body, env)


def _eval(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, bool)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in CONSTS:
            return CONSTS[node.id]
        raise FormulaError(f"unknown name '{node.id}'")
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left, env), _eval(node.right, env)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise FormulaError("exponent too large")
        return _BIN[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand, env))
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, env) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, env)
            if type(op) not in _CMP or not _CMP[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body, env) if _eval(node.test, env) else _eval(node.orelse, env)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCS and not node.keywords:
        return FUNCS[node.func.id](*[_eval(a, env) for a in node.args])
    raise FormulaError(f"unsupported expression: {ast.dump(node)[:80]}")
