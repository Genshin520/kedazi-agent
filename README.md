# 课搭子 · 个人学习助手

基于 LangChain 的多模态学习助手。上传题目图片进行辅导，或把 PDF、资料图片转为 Markdown 加入个人知识库，之后结合资料回答不同课程的问题。

## 启动

保留已填写的 .env，不要用模板覆盖。首次解压时才复制 .env.example 并填入自己的配置。

在项目根目录打开两个终端：

~~~powershell
uv sync --frozen
uv run --frozen uvicorn kedazi.app:app --host 127.0.0.1 --port 8000
~~~

~~~powershell
uv run --frozen python -m http.server 5173 --bind 127.0.0.1 --directory frontend
~~~

打开 http://127.0.0.1:5173；接口文档在 http://127.0.0.1:8000/docs。无需单独启动 Chroma 或 MCP。

## 使用方法

- **上传题目**：选择 JPEG、PNG、WEBP（5MB以内），写下问题后发送。Qwen 视觉模型转录题目，主 Agent 结合资料辅导。
- **添加资料**：选择 PDF（20MB以内）或图片（5MB以内），发送“请把这份资料加入知识库”。Agent 调用 MinerU MCP，后台解析并入库。
- **我的知识库**：查看排队、解析、索引、完成或失败状态；入库后可下载 Markdown；失败任务点击“通过助手重试”，再发送消息。
- 只说“我要上传资料”但未选文件时，助手会提示添加附件。
- 入库完成后，在任意讨论中提问。当前所有资料共用一个个人知识库，没有课程隔离。
- 支持给提示、检查步骤、完整讲解三种方式，以及持久化对话和学习偏好。

## 配置

| 字段 | 用途 |
|---|---|
| DASHSCOPE_API_KEY | 百炼密钥 |
| DASHSCOPE_BASE_URL | 百炼 OpenAI 兼容地址 |
| TEXT_MODEL | 主模型，默认 qwen-plus |
| VISION_MODEL | 图片模型，默认 qwen3.5-plus |
| EMBEDDING_MODEL | 向量模型，默认 qwen3.7-text-embedding-flash |
| RERANK_MODEL | 重排模型，默认 gte-rerank-v2 |
| MINERU_TOKEN | MinerU 官方解析 API Token |
| OSS_ENDPOINT / OSS_BUCKET | 私有文件存储地址和 Bucket |
| OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET | OSS 凭据 |

MinerU 与百炼使用各自的密钥。没有配置 MinerU 时仍可使用内置知识库和题目图片；资料入库需要 MinerU 与 OSS。真实密钥只放在 .env，Git 忽略 .env 和 data/。

## 资料入库

~~~text
前端选择文件 → FastAPI 上传私有 OSS → 返回资料ID
发送消息 → Agent 调用 import_material（MCP）
→ MinerU 接收1小时有效的下载链接并创建任务
→ 后台轮询 → 下载结果ZIP并读取 Markdown
→ 按标题切分 → Qwen Embedding → Chroma → 刷新 BM25
~~~

MinerU 的提交是异步任务，提交成功不等于入库完成。任务状态保存在 SQLite，后端重启后会继续处理已提交任务；停止聊天也不会撤销已提交的入库任务。网络中断恰好发生在提交阶段时，可能需要去 MinerU 控制台核对，避免重复解析。

原 PDF 保存在 OSS，解析出的文本保存在 data/knowledge/，向量保存在 data/chroma/。没有公开 Bucket，也不使用 STS。MinerU 需要通过短时签名链接读取资料，因此资料会交给 MinerU 解析。

## RAG 流程

~~~text
问题改写 → Chroma向量检索 + BM25关键词检索
→ RRF融合 → gte-rerank-v2重排 → Agent带来源回答
~~~

每段资料使用稳定ID。未变化的内容在重启时复用已有向量，新资料只向量化新片段。BM25 从 Chroma 中的文本恢复，两个检索来源保持一致。更换向量模型或服务地址时使用新的集合并重新建立向量。

内置 knowledge/ 包含概率论、线性代数、Python、计算机网络。修改内置 Markdown 后重启即可；通过界面入库不需要重启。

## 阅读代码

| 文件 | 内容 |
|---|---|
| src/kedazi/models.py | 文本、视觉、Embedding模型 |
| src/kedazi/rag.py | 切分、Chroma持久化、混合检索、重排 |
| src/kedazi/agent.py | Agent、工具、Runtime、Middleware、记忆 |
| src/kedazi/mineru_mcp.py | MinerU MCP 工具入口 |
| src/kedazi/materials.py | 资料状态、MinerU请求、后台入库 |
| src/kedazi/uploads.py | 图片校验、OSS文件上传 |
| src/kedazi/app.py | HTTP与SSE接口、服务生命周期 |

更多解释见 docs/LEARNING.md 和 docs/SETUP.md。

当前适用于本机单用户、单进程，不要增加 --workers。Chroma 是本地持久化模式，不需要额外数据库服务。首版导入 Markdown 文本，不做 PDF 内插图检索或页码精确定位；引用保留文件名、章节和片段ID。解析出的公式、表格和图片题目仍需核对。
