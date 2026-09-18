"""FastAPI 入口：启动资源、会话、学习偏好、图片上传和流式对话。"""
import asyncio
import contextlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, Field

from kedazi.agent import StudyContext, build_agent, run_study
from kedazi.config import Settings
from kedazi.memory import SQLiteProfileStore
from kedazi.models import create_chat_model, create_embeddings
from kedazi.rag import CourseRAG
from kedazi.repository import Repository
from kedazi.uploads import MAX_BYTES, ImageStorage

settings = Settings()
logger = logging.getLogger("kedazi")
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app):
    """服务启动时执行一次，不要在每次提问时重新建立 Agent 和索引。"""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    model = create_chat_model(settings)
    rag = CourseRAG(settings, model, create_embeddings(settings))
    await rag.build_index()

    async with AsyncSqliteSaver.from_conn_string(str(settings.data_dir / "checkpoints.sqlite")) as saver:
        await saver.setup()
        repo = await Repository.open(settings.data_dir / "app.sqlite")
        try:
            store = SQLiteProfileStore(settings.data_dir / "profiles.sqlite")
            app.state.repo = repo
            app.state.store = store
            app.state.rag = rag
            app.state.agent = await build_agent(model, rag, saver, store)
            app.state.storage = ImageStorage(settings)
            app.state.busy = set()  # 避免同一讨论同时写入两轮消息。
            yield
        finally:
            await repo.db.close()


app = FastAPI(title="课搭子", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type"],
)


class ChatInput(BaseModel):
    thread_id: uuid.UUID
    message: str = Field(min_length=1, max_length=4000)
    mode: Literal["hint", "check", "explain"] = "hint"
    image_id: uuid.UUID | None = None


class Profile(BaseModel):
    style: Literal["先给提示", "多举例子", "简洁公式"] = "先给提示"
    weak_topics: list[str] = Field(default_factory=list, max_length=10)


async def check_thread(thread_id):
    if not await app.state.repo.thread_exists(str(thread_id)):
        raise HTTPException(404, "讨论不存在")


@app.get("/health")
async def health():
    return {"status": "ok", "chunks": len(app.state.rag.chunks),
            "text_model": settings.text_model, "vision_model": settings.vision_model}


@app.post("/api/threads")
async def new_thread():
    return {"id": await app.state.repo.create_thread()}


@app.get("/api/threads")
async def threads():
    return await app.state.repo.list_threads()


@app.get("/api/threads/{thread_id}")
async def history(thread_id: uuid.UUID):
    await check_thread(thread_id)
    return await app.state.repo.history(str(thread_id))


@app.get("/api/profile")
async def get_profile():
    item = await app.state.store.aget(("users", "local-student"), "profile")
    return item.value if item else Profile().model_dump()


@app.put("/api/profile")
async def save_profile(profile: Profile):
    if any(len(topic) > 60 for topic in profile.weak_topics):
        raise HTTPException(422, "每个薄弱点不超过60字")
    await app.state.store.aput(("users", "local-student"), "profile", profile.model_dump())
    return profile


@app.post("/api/uploads")
async def upload(file: UploadFile = File(...)):
    try:
        data = await file.read(MAX_BYTES + 1)
        key = await app.state.storage.upload(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.warning("上传失败：%s", type(exc).__name__)
        raise HTTPException(502, "OSS 上传失败，请检查 Bucket、地域和 AccessKey 权限") from exc
    finally:
        await file.close()
    return {"image_id": await app.state.repo.add_upload(key)}


@app.post("/api/chat")
async def chat(body: ChatInput, request: Request):
    await check_thread(body.thread_id)
    if not body.message.strip():
        raise HTTPException(422, "问题不能为空")
    object_key = None
    if body.image_id:
        object_key = await app.state.repo.upload_key(str(body.image_id))
        if not object_key:
            raise HTTPException(404, "图片不存在")

    thread_id = str(body.thread_id)
    if thread_id in app.state.busy:
        raise HTTPException(409, "这个讨论正在回答，请等待完成")
    app.state.busy.add(thread_id)
    context = StudyContext(mode=body.mode, request_id=uuid.uuid4().hex, queue=asyncio.Queue())

    async def generate():
        try:
            async with asyncio.timeout(120):
                result = await run_study(
                    app.state.agent, settings, app.state.repo, app.state.storage,
                    body, context, object_key,
                )
                context.emit("done", result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("回答失败 request_id=%s type=%s", context.request_id, type(exc).__name__)
            context.emit("error", {"message": f"本轮未完成（{type(exc).__name__}），请检查模型配置或重新提问。",
                                   "request_id": context.request_id})
        finally:
            app.state.busy.discard(thread_id)
            context.queue.put_nowait(None)

    async def events():
        task = asyncio.create_task(generate())
        try:
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(context.queue.get(), timeout=5)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            app.state.busy.discard(thread_id)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
