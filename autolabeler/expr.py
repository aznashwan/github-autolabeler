# Copyright 2023 Cloudbase Solutions Srl
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

""" Module for parsing and executing simple Python code statements. """


import ast
import itertools
import logging


LOG = logging.getLogger(__name__)

DEFAULT_ALLOWED_IMPORTS = list(itertools.chain(*[
    [
        # Stdlib utility modules we'd always like to offer in full:
        "abc",
        "arrays",
        "base64",
        "bisect",
        "binascii",
        "cmath",
        "collections",
        "colorsys",
        "contextlib",
        "copy",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "itertools",
        "json",
        "math",
        "mimetypes",
        "numbers",
        "operator",
        "pprint",
        "re",
        "random",
        "shlex",
        "statistics",
        "string",
        "unicodedata",
        "zoneinfo",
    ],
    # Some utilities from os.path:
    [f"os.path.{f}" for f in ["isabs", "join", "sep"]],
]))


def _get_first_format_span(string: str) -> dict:
    start = string.find("{")
    if start < 0:
        return {}

    opened_inner_braces = 0
    for i, c in enumerate(string[start+1:]):
        if opened_inner_braces < 0:
            break

        if c == "{":
            opened_inner_braces += 1
            continue
        if c == "}":
            if opened_inner_braces == 0:
                return {
                    "start": start,
                    "end": start + i + 1}
            opened_inner_braces -= 1

    raise SyntaxError(
        f"Provided format string has inbalanced braces: {string}")


def format_string_with_expressions(string: str, variables: dict) -> str:
    """ Runs all expressions in formatted strings, calls str() on their
    results, and formats them back into the original string.

    E.g.: "this is a { var.field } format".format({"var": {"field": "example"}})
    """
    result = ""
    search_pos = 0
    while True:
        span = _get_first_format_span(string[search_pos:])
        if not span:
            result = f"{result}{string[search_pos:]}"
            break
        span_start = span["start"]
        span_end = span["end"]
        result = f"{result}{string[search_pos:search_pos+span_start]}"

        statement = string[search_pos+span_start+1: search_pos+span_end].strip()  # strip braces
        check_string_expression(statement, variables)
        try:
            expr_res = evaluate_string_expression(statement, variables)
        except (NameError, SyntaxError) as ex:
            raise ex.__class__(
                f"Failed to run statement '{statement}' from format '{string}': "
                f"{ex}") from ex

        result = f"{result}{expr_res}"
        search_pos = search_pos + span_end + 1

    LOG.debug(
        f"Successfully processed statement '{string}' into '{result}' "
        f"with variables: {variables}")
    return result


def evaluate_string_expression(
        statement: str, variables: dict) -> object:
    """ Evaluates the given string expression and returns the resulting object. """
    if not isinstance(statement, str):
        raise TypeError(f"Expected string statement, got: {statement}")

    builtins = _get_safe_builtins()

    globs = {k: v for k, v in variables.items()}
    globs["__builtins__"] = builtins
    return eval(statement, globs, {})


def check_string_expression(statement: str, variables: dict):
    """ Checks whether a string expression is safe to run. """
    banned_strings = ["__loader__"]
    present = [s for s in banned_strings if s in statement]
    if present:
        raise NameError(
            f"Cannot use any of the following banned string in statement "
            f"{statement}: {present}")

    expr = ast.parse(statement, mode='eval', filename=__name__)
    return _check_expression_safety(
        expr.body, variables, builtins=_get_safe_builtins())


def check_imports_for_definitions(
        definitions: str, variables: dict, allowed_import_names=None) -> list[str]:
    """ Parses the given defnitions string ensuring that any imports are allowed.

    Returns the list of names imported by the definitions, including nested
    imports using 'from mod import item' imports as 'mod.item'.
    """
    _ = variables

    if not allowed_import_names:
        allowed_import_names = []
    modules = []
    for node in ast.walk(ast.parse(definitions)):
        if isinstance(node, ast.Import):
            modules.extend([n.name for n in node.names])
        elif isinstance(node, ast.ImportFrom):
            modname = node.module
            # NOTE(aznashwan): preventing requiring modname.
            # modules.append(modname)
            for name in node.names:
                modules.append(f"{modname}.{name.name}")

    forbidden = [m for m in modules if m not in allowed_import_names]
    if forbidden:
        raise ImportError(
            f"Cannot import items {forbidden} in definition string: {definitions}\n"
            f"Only allowed imports are {allowed_import_names}")

    return modules


def evaluate_string_definitions(
        definitions: str, variables: dict,
        scrub_imports: bool=False,
        allowed_import_names: list[str]|None=None) -> dict:
    """ `exec()`s the provided Python definitions string and returns
    a dict with all new definitions within it.

    param scrub_imports: dictates whether any imported items inlcuded
    in the definition should be removed from resulting namespace or not.
    """
    imported_names = check_imports_for_definitions(
        definitions, variables, allowed_import_names=allowed_import_names)

    # NOTE: we execute with all items as globals so locals will
    # contain all the new definitions.
    # safe_builtins = _get_safe_builtins()
    safe_builtins = {}
    safe_builtins.update(variables)
    locals = {}
    exec(definitions, safe_builtins, locals)

    if scrub_imports:
        locals = {k: locals[k] for k in locals if k not in imported_names}

    return locals


def _get_safe_builtins() -> dict:
    forbidden = [
        "__import__", "__loader__", "breakpoint", "compile",
        "eval", "exec", "exit", "open", "input", "copyright",
        "memoryview", "print", "quit"]

    builtins_copy = {k: v for k, v in __builtins__.items()}
    for item in forbidden:
        _ = builtins_copy.pop(item, None)

    return builtins_copy


def _validate_function_call(call: ast.Call, values: dict, builtins=None):
    """ Ensures function is one of the safe allowed calls.

    This includes builtins such as len()/bool()/etc, as well as any method
    on base types such as int, float, str, list etc...
    """
    if builtins is None:
        builtins = {}
    if isinstance(call.func, ast.Name):
        if call.func.id not in builtins and call.func.id not in values:
            raise NameError(
                f"Cannot use function '{call.func.id}'. "
                f"Available functions are: "
                f"{set(builtins.keys()).union(values.keys())}")
        return

    # Method call:
    if isinstance(call.func, ast.Attribute):
        recv = call.func.value
        attr = call.func.attr
        # if not isinstance(recv, ast.Name):
        #     raise NameError(
        #         f"Cannot call method {recv}.{attr}(). "
        #         f"{recv} ({type(recv)}) must be an ast.Name.")

        if isinstance(recv, ast.Name):
            receiver_name = recv.id
            if receiver_name not in values:
                raise NameError(
                    f"Cannot call method {attr}() on {receiver_name}. "
                    f"Can only call methods on: {values}")

    for arg in call.args:
        _check_expression_safety(arg, values)  # pyright: ignore


def _check_expression_safety(
        expr: ast.expr, variables: dict, builtins=None):
    if builtins is None:
        builtins = {}

    supported_exprs = (
            ast.Expression, ast.Name, ast.BinOp, ast.Compare,
            ast.Call, ast.Constant, ast.Attribute, ast.BoolOp,
            ast.DictComp, ast.ListComp, ast.SetComp, ast.GeneratorExp,
            ast.Subscript)

    match type(expr):
        case ast.Call:
            _validate_function_call(
                expr, variables, builtins=builtins)  # pyright: ignore
        case ast.Name:
            name = expr.id  # pyright: ignore
            if name not in variables and name not in builtins:
                raise NameError(
                    f"Undefined variable name: '{name}'. "
                    f"Current variable namespace is: {variables}")
        case ast.BinOp:
            _check_expression_safety(expr.left, variables)  # pyright: ignore
            _check_expression_safety(expr.right, variables)  # pyright: ignore
        case ast.Compare:
            _check_expression_safety(expr.left, variables)  # pyright: ignore
            for cmp in expr.comparators:  # pyright: ignore
                _check_expression_safety(cmp, variables)
        case some if some in supported_exprs:
            pass
        case other:
            raise SyntaxError(
                f"Expression {expr} must be one of {supported_exprs}. "
                f"Actual type: {other}")
