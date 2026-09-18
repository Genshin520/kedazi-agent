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

### build_index()

```python
self.chunks = load_and_split_documents()
await self.vectorstore.aadd_documents(self.chunks)
self.corpus = [document_to_dict(doc) for doc in self.chunks]
self.corpus_tokens = [list(jieba.cut(doc["content"])) for doc in self.corpus]
self.bm25 = BM25Plus(self.corpus_tokens)
```

self.vectorstore 在初始化时已经设置为 InMemoryVectorStore(embedding=embeddings)。

aadd_documents 内部会调用 Embedding，把每段文字变成向量，再存进内存。BM25则保存分词后的词频统计。它们处理同一份 chunks，返回时通过 id 对齐。

self 只是表示“这个 CourseRAG 对象保存的变量”。用一个类是为了让索引在服务启动时建一次，后续问题重复使用，而不是每次提问都重新建库。

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
- 每轮先用 tool_choice 指定 search_course；尝试检索后切回 auto，让模型自主选择后续工具。这对应你笔记里的“动态控制工具调用”。
- SummarizationMiddleware：消息达到24条时做摘要，并保留最近8条消息。

可以先只看 build_agent，再看 middleware，最后看 app.py 的HTTP和流式传输。不用一次把所有文件都读完。
