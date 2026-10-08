"""Restricted device scripts for the Control Center's script box.

Scripts look like Python, but they are checked before running and may only:

* use variables, numbers, strings, lists, dicts, arithmetic and comparisons;
* use ``if``, ``for`` (no ``while``), ``break``, ``continue`` and ``pass``;
* call the functions in :data:`SCRIPT_FUNCTIONS` (``print``, ``sleep``,
  ``lease``, ``mean``, ...) and a few list/dict/string methods;
* call device methods named in the generated contracts, e.g.
  ``device.set_exposure(50)`` or ``device.read_wavelength()``.

Everything else (imports, ``def``/``class``/``lambda``, ``while``, ``with``,
``try``, any other attribute, and names starting with ``_``) is rejected before
the script runs. A script then runs in its own Python process
(:mod:`labnet.control_center.script_worker`) with no credentials; each device
call is passed back to the Control Center, which performs it with the page's
lease. That process is killed on Stop or when the time limit is reached.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable


MAX_SCRIPT_CHARACTERS = 20_000

# Functions a script may call by name (implemented in script_worker).
SCRIPT_FUNCTIONS = frozenset(
    {
        "abs", "bool", "enumerate", "float", "int", "len", "lease", "list",
        "max", "mean", "min", "print", "range", "round", "sleep", "stdev",
        "str", "sum", "zip",
    }
)

# Methods on lists, dicts and strings. str.format is deliberately absent: its
# "{0.attr}" syntax would reach attributes the checker cannot see.
SAFE_METHODS = frozenset(
    {
        "append", "copy", "count", "endswith", "extend", "get", "index",
        "insert", "items", "join", "keys", "lower", "pop", "remove", "replace",
        "reverse", "sort", "split", "startswith", "strip", "upper", "values",
    }
)

_ALLOWED_NODES = (
    ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.For, ast.If,
    ast.Break, ast.Continue, ast.Pass,
    ast.Name, ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Subscript,
    ast.Slice, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Call, ast.keyword, ast.Attribute, ast.JoinedStr, ast.FormattedValue,
    ast.expr_context, ast.operator, ast.unaryop, ast.boolop, ast.cmpop,
)

_FRIENDLY_NAMES = {
    ast.Import: "import", ast.ImportFrom: "import", ast.While: "while loops",
    ast.FunctionDef: "def", ast.AsyncFunctionDef: "def", ast.ClassDef: "class",
    ast.Lambda: "lambda", ast.With: "with", ast.Try: "try", ast.Raise: "raise",
    ast.Global: "global", ast.Nonlocal: "nonlocal", ast.Delete: "del",
    ast.ListComp: "list comprehensions", ast.DictComp: "dict comprehensions",
    ast.SetComp: "set comprehensions", ast.GeneratorExp: "generator expressions",
    ast.Await: "await", ast.Yield: "yield", ast.Starred: "* unpacking",
}


class ScriptError(ValueError):
    """The script uses something the script box does not allow."""

    def __init__(self, message: str, line: int | None = None) -> None:
        self.line = line
        super().__init__(f"Line {line}: {message}" if line else message)


def contract_method_names() -> frozenset[str]:
    # Imported here so the script process, which receives its method names,
    # starts without loading every protobuf module.
    from labnet.contracts import registry

    return frozenset(spec.python_name for contract in registry.all() for spec in contract.methods)


def validate_script(code: str, method_names: Iterable[str] | None = None) -> ast.Module:
    """Parse ``code`` and reject anything outside the script language."""

    if len(code) > MAX_SCRIPT_CHARACTERS:
        raise ScriptError(f"Scripts are limited to {MAX_SCRIPT_CHARACTERS} characters.")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise ScriptError(f"Syntax error: {exc.msg}", exc.lineno) from None

    allowed_attributes = SAFE_METHODS | frozenset(
        method_names if method_names is not None else contract_method_names()
    )
    # Attributes are only allowed as the function of a call: name.method(...).
    callable_attributes = {
        id(node.func) for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    for node in ast.walk(tree):
        line = getattr(node, "lineno", None)
        if not isinstance(node, _ALLOWED_NODES):
            what = _FRIENDLY_NAMES.get(type(node), type(node).__name__)
            raise ScriptError(f"{what} is not allowed in device scripts.", line)
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise ScriptError(f"Names starting with '_' are not allowed ({node.id}).", line)
        if isinstance(node, ast.Attribute):
            if id(node) not in callable_attributes:
                raise ScriptError("Only method calls like device.read_wavelength() are allowed.", line)
            if node.attr not in allowed_attributes:
                raise ScriptError(f"'.{node.attr}()' is not a device method or allowed method.", line)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in SCRIPT_FUNCTIONS:
                raise ScriptError(f"'{node.func.id}()' is not an allowed function.", line)
        if isinstance(node, ast.Call) and not isinstance(node.func, (ast.Name, ast.Attribute)):
            raise ScriptError("Only direct calls like name(...) or device.method(...) are allowed.", line)
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.For)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not _is_plain_target(target):
                    raise ScriptError("Assign only to plain variable names.", line)
        if isinstance(node, ast.Constant) and isinstance(node.value, bytes):
            raise ScriptError("Byte strings are not allowed.", line)
    return tree


def _is_plain_target(target: ast.AST) -> bool:
    if isinstance(target, ast.Name):
        return True
    if isinstance(target, (ast.Tuple, ast.List)):
        return all(isinstance(item, ast.Name) for item in target.elts)
    return False


__all__ = [
    "MAX_SCRIPT_CHARACTERS",
    "SAFE_METHODS",
    "SCRIPT_FUNCTIONS",
    "ScriptError",
    "contract_method_names",
    "validate_script",
]
