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
import logging
import re


LOG = logging.getLogger(__name__)

FORMAT_GROUP_REGEX = re.compile(r"\{([^\}]+)\}")


def format_string_with_expressions(string: str, variables: dict) -> str:
    """ Runs all expressions in formatted strings, calls str() on their
    results, and formats them back into the original string.

    E.g.: "this is a { var.field } format".format({"var": {"field": "example"}})
    """
    result = ""
    search_pos = 0
    while True:
        match = FORMAT_GROUP_REGEX.search(string, pos=search_pos)
        if not match:
            result = f"{result}{string[search_pos:]}"
            break
        result = f"{result}{string[search_pos:match.start()]}"

        statement = match.group(0)[1:-1].strip()  # strip braces
        check_string_expression(statement, variables)
        try:
            expr_res = evaluate_string_expression(statement, variables)
        except (NameError, SyntaxError) as ex:
            raise ex.__class__(
                f"Failed to run statement '{statement}' from format '{string}': "
                f"{ex}") from ex

        result = f"{result}{expr_res}"
        search_pos = match.end()

    return result


def evaluate_string_expression(
        statement: str, variables: dict) -> object:
    builtins = _get_safe_builtins()
    return eval(statement, {"__builtins__": builtins}, variables)


def check_string_expression(statement: str, variables: dict):
    expr = ast.parse(statement, mode='eval', filename=__name__)
    return _check_expression_safety(
        expr.body, variables, builtins=_get_safe_builtins())


def _get_safe_builtins() -> dict:
    forbidden = [
        "__import__", "breakpoint", "compile",
        "eval", "exec", "exit", "open", "input",
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
        if call.func.id not in builtins:
            raise NameError(
                f"Cannot use function {call.func.id}. "
                f"Available functions are: {builtins}")
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
        case some if some in (
                ast.Attribute, ast.Constant, ast.Subscript, ast.BoolOp):
            pass
        case other:
            supported_exprs = (
                    ast.Expression, ast.Name, ast.BinOp, ast.Compare,
                    ast.Call, ast.Constant, ast.Attribute, ast.BoolOp)
            raise SyntaxError(
                f"Expression {expr} must be one of {supported_exprs}. "
                f"Actual type: {other}")
