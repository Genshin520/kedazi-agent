"""资料任务：MCP 提交 MinerU，后台轮询解析结果，再更新知识库。"""
import asyncio
import io
import logging
import sqlite3
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from kedazi.uploads import ImageStorage


class MaterialStore:
    """SQLite 同时供后端与 MCP 子进程读取；只保存任务，不保存密钥和签名链接。"""

    def __init__(self, data_dir):
        self.directory = data_dir / "knowledge"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "materials.sqlite"
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS materials (
                id TEXT PRIMARY KEY, filename TEXT, object_key TEXT,
                status TEXT, task_id TEXT, error TEXT DEFAULT '',
                chunks INTEGER DEFAULT 0, updated REAL)""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def add(self, filename, object_key):
        material_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO materials(id,filename,object_key,status,updated) VALUES(?,?,?,?,?)",
                       (material_id, filename, object_key, "uploaded", time.time()))
        return self.get(material_id)

    def get(self, material_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
        return dict(row) if row else None

    def list(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM materials ORDER BY updated DESC")]

    def update(self, material_id, **values):
        if not values.keys() <= {"status", "task_id", "error", "chunks"}:
            raise ValueError("无效任务字段")
        values["updated"] = time.time()
        with self.connect() as db:
            db.execute("UPDATE materials SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?",
                       (*values.values(), material_id))

    def claim(self, material_id):
        # 同一资料并发调用工具时，只有一次能够提交，避免重复解析计费。
        with self.connect() as db:
            return db.execute(
                "UPDATE materials SET status='submitting', error='', updated=? "
                "WHERE id=? AND status IN ('uploaded','failed')",
                (time.time(), material_id),
            ).rowcount == 1

    def markdown_path(self, material_id):
        return self.directory / (str(uuid.UUID(material_id)) + ".md")


def public_material(row):
    return {key: row[key] for key in ("id", "filename", "status", "error", "chunks")}


async def mineru_request(settings, method, path, **kwargs):
    token = settings.mineru_token.get_secret_value()
    if not token:
        raise ValueError("请在 .env 配置 MINERU_TOKEN")
    async with httpx.AsyncClient(timeout=35) as client:
        response = await client.request(
            method, "https://mineru.net/api/v4/extract/task" + path,
            headers={"Authorization": "Bearer " + token}, **kwargs,
        )
    if response.status_code in (401, 403):
        raise ValueError("MinerU Token 无效、过期或无接口权限，请检查 MINERU_TOKEN")
    if response.status_code != 200:
        raise ValueError(f"MinerU 请求失败（HTTP {response.status_code}），请稍后重试")
    payload = response.json()
    code = payload.get("code")
    if str(code) in {"A0202", "A0211"}:
        raise ValueError("MinerU Token 无效或过期，请更新 MINERU_TOKEN 并重启")
    if code != 0:
        # 不把第三方原始响应或 URL 放到日志、模型上下文中。
        raise ValueError(f"MinerU 拒绝请求（code={payload.get('code')}），请检查 Token、额度和文件限制")
    return payload["data"]


async def submit_material(settings, material_id):
    store = MaterialStore(settings.data_dir)
    row = store.get(material_id)
    if not row:
        raise ValueError("资料不存在，请先通过页面上传文件")
    if not store.claim(material_id):
        return public_material(row)
    try:
        # 上次已提交成功的任务可以继续取结果，不必重新消耗解析额度。
        if row["task_id"]:
            store.update(material_id, status="pending")
        else:
            url = await ImageStorage(settings).signed_url(row["object_key"], expires=3600)
            data = await mineru_request(settings, "POST", "", json={
                "url": url, "model_version": "vlm",
                "enable_formula": True, "enable_table": True,
            })
            store.update(material_id, status="pending", task_id=data["task_id"])
    except Exception as exc:
        error = str(exc) if isinstance(exc, ValueError) else "提交未完成，请重试；网络超时可能已在 MinerU 创建任务"
        store.update(material_id, status="failed", error=error)
        raise ValueError(error) from None
    return public_material(store.get(material_id))


async def download_markdown(url):
    """只读取压缩包中的 Markdown，不把远程压缩包直接解压到磁盘。"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("MinerU 返回的下载地址无效")
    buffer = bytearray()
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            async for part in response.aiter_bytes():
                buffer.extend(part)
                if len(buffer) > 100 * 1024 * 1024:
                    raise ValueError("解析结果超过100MB，请拆分资料")
    with zipfile.ZipFile(io.BytesIO(buffer)) as archive:
        candidates = [item for item in archive.infolist() if Path(item.filename).name == "full.md"]
        if not candidates:
            candidates = [item for item in archive.infolist() if item.filename.endswith(".md")]
        if len(candidates) != 1:
            raise ValueError("解析结果没有唯一的 Markdown 文件")
        item = candidates[0]
        if item.file_size > 10 * 1024 * 1024:
            raise ValueError("Markdown 超过10MB，请拆分资料")
        markdown = archive.read(item).decode("utf-8-sig").strip()
    if not markdown:
        raise ValueError("资料解析结果为空")
    return markdown


async def process_material(settings, store, rag, row):
    material_id = row["id"]
    path = store.markdown_path(material_id)
    if not path.exists():
        data = await mineru_request(settings, "GET", "/" + row["task_id"])
        state = data["state"]
        if state == "failed":
            store.update(material_id, status="failed", task_id=None,
                         error="MinerU 解析失败，请检查文件是否加密、损坏或超过服务页数限制，再重试")
            return
        if state != "done":
            store.update(material_id, status="parsing" if state in ("running", "converting") else "pending", error="")
            return
        markdown = await download_markdown(data["full_zip_url"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(markdown, encoding="utf-8")
        temporary.replace(path)
    store.update(material_id, status="indexing", error="")
    count = await rag.import_markdown(path, row["filename"], material_id)
    store.update(material_id, status="ready", chunks=count, error="")


async def material_worker(settings, store, rag):
    # 提交时进程退出的任务不能永久显示“提交中”。
    for row in store.list():
        if row["status"] == "submitting":
            store.update(row["id"], status="failed", error="上次提交被中断，请重试")
    while True:
        for row in store.list():
            if row["status"] == "submitting" and time.time() - row["updated"] > 90:
                store.update(row["id"], status="failed", error="提交被中断或超时，请重试")
                continue
            if row["status"] not in {"pending", "parsing", "indexing"}:
                continue
            try:
                await process_material(settings, store, rag, row)
            except (httpx.TimeoutException, httpx.NetworkError):
                store.update(row["id"], error="网络暂时不可用，后台会继续尝试")
            except Exception as exc:
                logging.warning("资料入库失败 id=%s type=%s", row["id"], type(exc).__name__)
                error = str(exc) if isinstance(exc, ValueError) else "解析或索引失败，请检查服务配置后重试"
                store.update(row["id"], status="failed", error=error)
        await asyncio.sleep(5)
