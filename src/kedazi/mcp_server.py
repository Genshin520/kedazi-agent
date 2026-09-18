"""单独进程，通过真实 MCP stdio 协议提供一个工具。stdout 只供协议使用。"""

from mcp.server.fastmcp import FastMCP

from kedazi.calculator import calculate

mcp = FastMCP("kedazi-calculator")


@mcp.tool()
def calculate_expression(expression: str) -> dict:
    """核验纯数值表达式。仅支持数字、括号、加减乘除和有限次幂；不能证明解题逻辑正确。"""
    try:
        return calculate(expression)
    except (ValueError, SyntaxError) as exc:
        return {"error": str(exc), "kind": "invalid_expression"}


if __name__ == "__main__":
    mcp.run(transport="stdio")
