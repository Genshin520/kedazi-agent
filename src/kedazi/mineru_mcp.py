"""MinerU MCP 服务：工具只提交任务，耗时解析由后端继续跟进。"""
from mcp.server.fastmcp import FastMCP

from kedazi.config import Settings
from kedazi.materials import submit_material

mcp = FastMCP("kedazi-mineru")


@mcp.tool()
async def import_material(material_id: str) -> dict:
    """把本轮已上传的资料加入知识库。传入系统给出的资料ID，不能传URL。
    返回 pending 表示已提交后台任务，只有 ready 才表示可检索；不要宣称提交后立即入库完成。
    """
    return await submit_material(Settings(), material_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
