"""受限 AST 数值解释器。这里没有 eval、exec、属性访问或任意函数调用。"""

import ast
import math
import operator

OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}


def calculate(expression: str) -> dict:
    if not 1 <= len(expression) <= 200:
        raise ValueError("表达式长度须为 1–200")
    root = ast.parse(expression, mode="eval")
    if len(list(ast.walk(root))) > 64:
        raise ValueError("表达式过于复杂")

    def visit(node, depth=0):
        if depth > 16:
            raise ValueError("嵌套过深")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = float(node.value)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand, depth + 1) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in OPS:
            left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
            if isinstance(node.op, ast.Pow) and abs(right) > 12:
                raise ValueError("指数绝对值不可超过 12")
            value = OPS[type(node.op)](left, right)
        else:
            raise ValueError("仅支持数字、括号、+ - * / **")
        if isinstance(value, complex) or not math.isfinite(value) or abs(value) > 1e12:
            raise ValueError("结果超出有限实数范围")
        return value

    try:
        return {"expression": expression, "value": visit(root.body), "kind": "numeric_check"}
    except (ZeroDivisionError, OverflowError) as exc:
        raise ValueError("除零或数值溢出") from exc
