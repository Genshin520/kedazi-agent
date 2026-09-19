# 像读笔记一样读 rag.py

先看最下面的 CourseRAG.search()。它只是顺序调用几个函数，不包含隐藏的检索分支。

## 启动时：建立知识库

### load_and_split_documents()

1. read_text 读取 knowledge/*.md。
2. MarkdownHeaderTextSplitter 按标题划分章节。
3. RecursiveCharacterTextSplitter 将较长章节切为350字左右，重叠50字。
4. 给每个 Document 保存 id、source、title。
5. 标题加到正文前面，帮助检索。

对应你笔记里的：加载文档 → 按标题切分 → 递归切分 → 加 metadata。

### build_index() 与 import_markdown()

原来的内存向量库替换为 Chroma，仍然使用 aadd_documents 和 asimilarity_search。

1. load_and_split_documents(path) 读取并切分一份 Markdown。
2. 根据资料ID、文件名、片段位置和文本生成稳定的片段ID。
3. import_markdown 查询 Chroma 已有ID，只对新增内容调用 Embedding。
4. 同步删除该资料已经失效的旧片段，避免修改文档后检索旧内容。
5. refresh_bm25 从持久化文本恢复关键词索引。

Chroma 文件在 data/chroma/，重启不需要重复计算未变化的向量。BM25 对象仍在内存中，但它的原始文本从 Chroma 恢复，不会丢失。

这一部分比原来的纯内存版本多了“比较新旧片段”的步骤，是为了支持上传和持久化；检索算法没有变。

## 提问时：检索资料

### rewrite_query()

把口语问题改写为适合检索的完整问题，保留原来的数字和条件。用的是同一个文本模型，不需要额外注册模型服务。

主 Agent 会先根据历史补全指代，例如把“它的分母呢”补成“贝叶斯公式中的分母如何计算”，再交给检索工具。rewrite_query 负责进一步压缩表达。

### dense_search()

```python
documents = await self.vectorstore.asimilarity_search(query, k=5)
```

与笔记中的 similarity_search 一样，前面多一个 a 表示异步。内部先把 query 向量化，再查语义接近的片段。

### bm25_search()

query 用 jieba 分词，再用 BM25 评分，取最高的5个片段。对公式、章节名称等关键词匹配，它能补充向量检索。

代码使用 BM25Plus，属于 BM25 的变体。因为它对所有文档有基础分，额外用一行关键词交集检查排除完全没匹配到词的片段。

### reciprocal_rank_fusion()

输入：
```text
向量结果：[doc_3, doc_1, doc_5]
BM25结果：[doc_1, doc_2, doc_3]
```

每出现一次，就加 1/(60+排名)。doc_1 两路都出现，分数就是 1/62 + 1/61。同一个 id 合并为一个文档，按累计分数排序。

不要直接把 BM25 分数与余弦相似度相加，因为两种分数的尺度不一样。

### rerank()

RRF只是融合已有名次；rerank 会让专门模型重新看“问题与每个片段”，给它们打相关性分数。

```python
response = dashscope.TextReRank.call(
    model=settings.rerank_model,
    api_key=settings.dashscope_api_key.get_secret_value(),
    query=query,
    documents=[doc["content"] for doc in documents],
    top_n=3,
)
```

返回的 index 表示对应哪个输入片段；relevance_score 是相关性分数。按响应给出的顺序取出原文，就得到了最终资料。

SDK这个调用是同步的，所以 search() 用：
```python
final_docs = await asyncio.to_thread(self.rerank, query, fused_docs[:6])
```

可以先把它理解成：“让这个同步函数在工作线程执行，避免它卡住 FastAPI 的其他请求”。没有增加新的检索算法。

## 最后：交给 Agent

search_course 是一个普通的 @tool。它调用 rag.search()，把资料原文与编号返回给 Agent，同时把相同资料发给前端展示。

Agent 按 hint/check/explain 模式回答，并用 [课程:doc_3] 这样的编号引用资料。代码只检查编号是否真实返回过，不把这个检查当成答案正确性证明。

## 从 RAG 接着读 Agent

- create_agent：把文本模型、本地工具、MCP工具、记忆和中间件组合起来。
- Context：本轮教学模式、固定本机用户标识、请求编号。
- State：当前会话消息和调用计数，通过 SQLite Checkpointer 保存。
- Store：跨会话的学习偏好，用户在前端主动保存。
- Middleware：模型调用前加入偏好和模式，调用后计数，工具调用时显示进度。
- 附有资料ID时先用 tool_choice 指定 import_material，提交后切回 auto；普通学习问题先 search_course。用一个简单的开头匹配识别“我要上传”等请求，在没有文件时提示选择附件，避免无关检索。
- SummarizationMiddleware：消息达到24条时做摘要，并保留最近8条消息。

可以先只看 build_agent，再看 middleware，最后看 app.py 的HTTP和流式传输。不用一次把所有文件都读完。


## MinerU 入库的三个部分

- uploads.py：接收真实文件并上传 OSS，返回服务端生成的资料ID。
- mineru_mcp.py：向 Agent 暴露 import_material 工具。MCP 子进程读取 .env 和同一份任务数据库，用短时 OSS 链接调用 MinerU；密钥和下载链接不传给模型。
- materials.py：后台每5秒查看任务，解析完成后读取ZIP内的Markdown，再调用 rag.import_markdown。

后台协程由 FastAPI lifespan 启动，服务关闭时取消；任务记录保存在 SQLite，重启后能继续轮询。当前不引入 Redis 或额外任务队列。MCP 工具只等“提交成功”，避免PDF解析期间长时间占用Agent调用。

失败任务可从页面附上原资料ID，再由 Agent 调用同一个工具。已存在远程任务ID时继续该任务，解析明确失败时才允许重新提交。只有 ready 状态才能宣称已入库。

短期记忆仍是 SQLite Checkpointer，长期偏好仍是 Store，本次没有改变其接口。
