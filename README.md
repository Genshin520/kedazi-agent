# 课搭子

面向大学生的课程辅导助手。支持上传题目图片、查询课程资料、分步辅导和数值核验。

代码只调用真实模型。使用 Python + uv 管理后端，独立 HTML/JavaScript 前端通过 HTTP 和流式 SSE 与后端通信。

## 启动

在项目根目录运行：

```powershell
uv sync --frozen
uv run --frozen uvicorn kedazi.app:app --host 127.0.0.1 --port 8000
```

再开一个终端，同样进入项目根目录：

```powershell
uv run --frozen python -m http.server 5173 --bind 127.0.0.1 --directory frontend
```

打开 [课搭子](http://127.0.0.1:5173)。接口文档在 [FastAPI Docs](http://127.0.0.1:8000/docs)。

已有 .env 就直接使用，不要用 .env.example 覆盖你填好的内容。第一次拿到压缩包时才复制 .env.example 为 .env 并填写自己的凭据。压缩包不含密钥。

当前不需要登录或项目访问令牌。保持监听127.0.0.1，在本机使用即可。

## .env 只需要这些

| 配置 | 作用 |
|---|---|
| DASHSCOPE_API_KEY | 阿里云百炼 API Key |
| DASHSCOPE_BASE_URL | 百炼聊天与向量接口的基础地址 |
| TEXT_MODEL=qwen-plus | 主 Agent 及检索词改写使用的文本模型 |
| VISION_MODEL=qwen3.5-plus | 读取题目图片的多模态模型 |
| EMBEDDING_MODEL=qwen3.7-text-embedding-flash | 把问题和知识片段变成向量 |
| RERANK_MODEL=gte-rerank-v2 | 对召回的知识片段再次排序 |
| OSS_ENDPOINT | Bucket 对应地域的 Endpoint |
| OSS_BUCKET | Bucket 名称 |
| OSS_ACCESS_KEY_ID | OSS AccessKey ID |
| OSS_ACCESS_KEY_SECRET | OSS AccessKey Secret |

未配置 OSS 时仍可使用文本对话，上传时会提示补齐配置。你目前填写的模型名、深圳 OSS 地址和凭据已保留。

## 先读这几个文件

1. [models.py](src/kedazi/models.py)：定义文本、图片、Embedding 模型。
2. [rag.py](src/kedazi/rag.py)：按你笔记的步骤写的 RAG。先看最下面的 search()，再看每个步骤。
3. [agent.py](src/kedazi/agent.py)：工具、MCP、Context、State、Middleware、记忆与 create_agent。
4. [app.py](src/kedazi/app.py)：把 Agent 接到前后端接口。
5. [RAG 逐步说明](docs/LEARNING.md)：与代码一一对应的中文解释。

## RAG 主流程

```python
rewritten_query = await self.rewrite_query(query)
dense_docs = await self.dense_search(rewritten_query)
bm25_docs = self.bm25_search(rewritten_query)
fused_docs = reciprocal_rank_fusion([dense_docs, bm25_docs])
final_docs = await asyncio.to_thread(self.rerank, query, fused_docs[:6])
```

向量库直接使用笔记里的 InMemoryVectorStore。每次启动读取 knowledge/*.md、切分并调用 Embedding 建库；小知识库这样最直观。修改资料后重启即可。每次启动会消耗一小次向量化调用，之后每次问题还会调用查询改写、查询向量化、重排和 Agent。

BM25 用 jieba 做中文分词；两路各召回最多5条，RRF融合后选最多6条送重排，最后返回最多3条。RRF与模型精排是两个步骤。重排分数用于相对排序，不是答案正确率。

## 为什么“重排模型”之前还要配 URL

远程模型调用始终有三个要素：地址、模型名、密钥。之前直接发送 HTTP 请求，因此把重排 URL 单独放在 .env 中，增加了你需要理解的细节。

现在改为：

```python
dashscope.TextReRank.call(
    api_key=...,
    model=settings.rerank_model,
    query=query,
    documents=[...],
    top_n=3,
)
```

你只需指定一个重排模型名。SDK负责接口路径，models.py 根据 DASHSCOPE_BASE_URL 推导同地域的SDK基础地址。不是同时调用两个重排模型。

当前 gte-rerank-v2 使用这个 SDK 调用方式。任意不同供应商的模型不能只改名字就通用；先用当前已经连通的模型即可。

## 保留的功能

- hint / check / explain 三种辅导模式。
- 视觉模型先识别题目；文本 Agent 再结合课程资料辅导。
- 图片经过校验、重新编码后上传私有 OSS，调用视觉模型时生成短时下载链接。
- 一个本地课程检索工具，一个自建 MCP 数值计算工具；每轮先查课程，再由模型选择后续工具。
- SQLite Checkpointer 持久化对话；Store 保存主动填写的学习偏好。
- Middleware 动态加入教学模式和偏好，记录工具状态，限制调用次数，压缩长对话。
- 流式回答、课程依据展示、同一讨论互斥、异常提示和停止按钮。

目录中的 memory.py 是长期 Store 适配，repository.py 是会话与图片记录，uploads.py 处理 OSS。可以先跳过这些，先看懂 rag.py 与 agent.py。

## 资料与使用范围

knowledge/probability.md 是自编的小型概率论资料，可自行增加课程 Markdown。图片模糊、课程资料未覆盖或公式推导不确定时，仍需要人工核对。

会话、图片记录和偏好保存在 data/，重启不会丢失；向量库在内存中，重启会重建。仅支持单进程运行，不要增加 --workers。中断后可能保留部分执行状态，遇到恢复异常可以新建讨论。

更多配置说明见 [SETUP.md](docs/SETUP.md)。
