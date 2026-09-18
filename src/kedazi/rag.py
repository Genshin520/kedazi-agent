"""按笔记的顺序实现 RAG：每一步一个函数，search() 把它们串起来。"""
import asyncio

import dashscope
import jieba
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from rank_bm25 import BM25Plus

from kedazi.config import ROOT
from kedazi.models import configure_reranker


# 1. 读取 Markdown，先按标题切分，再限制每块长度。
def load_and_split_documents():
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "一级标题"), ("##", "二级标题"), ("###", "三级标题")]
    )
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=350, chunk_overlap=50, separators=["\n\n", "\n", "。", " ", ""]
    )
    chunks = []
    for path in sorted((ROOT / "knowledge").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        header_docs = header_splitter.split_text(text)
        documents = text_splitter.split_documents(header_docs)
        for doc in documents:
            title = " / ".join(doc.metadata.values())
            doc.page_content = title + "\n" + doc.page_content
            doc.metadata = {
                "id": f"doc_{len(chunks) + 1}",
                "source": path.name,
                "title": title,
            }
            chunks.append(doc)
    if not chunks:
        raise ValueError("knowledge 文件夹里没有可用的 Markdown 内容")
    return chunks


def document_to_dict(doc):
    """把 LangChain Document 变成方便前端展示的字典。"""
    return {**doc.metadata, "content": doc.page_content}


# 2. RRF 融合：只看每一路的名次，不直接相加不同体系的分数。
def reciprocal_rank_fusion(ranked_lists, k=60):
    scores = {}
    documents = {}
    for ranked_list in ranked_lists:
        for rank, doc in enumerate(ranked_list, start=1):
            doc_id = doc["id"]
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)
            documents[doc_id] = doc
    sorted_ids = sorted(scores, key=scores.get, reverse=True)
    return [{**documents[doc_id], "rrf_score": scores[doc_id]} for doc_id in sorted_ids]


class CourseRAG:
    """只把模型和索引放在同一个对象里，便于多个请求复用。"""

    def __init__(self, settings, model, embeddings):
        self.settings = settings
        self.model = model
        self.vectorstore = InMemoryVectorStore(embedding=embeddings)
        configure_reranker(settings)

    # 3. 服务启动时建立两份索引，之后每次提问直接检索。
    async def build_index(self):
        self.chunks = load_and_split_documents()
        await self.vectorstore.aadd_documents(self.chunks)
        self.corpus = [document_to_dict(doc) for doc in self.chunks]
        self.corpus_tokens = [list(jieba.cut(doc["content"])) for doc in self.corpus]
        self.bm25 = BM25Plus(self.corpus_tokens)

    # 4. 查询改写：与笔记里的 rewrite_query 相同，只改成异步调用。
    async def rewrite_query(self, query):
        response = await self.model.ainvoke([
            SystemMessage(content="把问题改写为适合课程检索的简短查询。保留事件、公式、数字及条件。只输出查询，不解题。"),
            HumanMessage(content=query),
        ])
        return response.text.strip()[:600] or query

    # 5. 向量检索：寻找语义相近的知识片段。
    async def dense_search(self, query, k=5):
        documents = await self.vectorstore.asimilarity_search(query, k=k)
        return [document_to_dict(doc) for doc in documents]

    # 6. BM25 检索：使用中文分词进行关键词匹配。
    def bm25_search(self, query, k=5):
        query_tokens = list(jieba.cut(query))
        scores = self.bm25.get_scores(query_tokens)
        indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        # BM25Plus 有基础分；没有关键词交集的片段不参与召回。
        matched = [i for i in indices if set(query_tokens) & set(self.corpus_tokens[i])]
        return [self.corpus[i].copy() for i in matched[:k]]

    # 7. 模型精排：将“问题 + 候选片段”一起交给重排模型。
    def rerank(self, query, documents, top_k=3):
        if not documents:
            return []
        response = dashscope.TextReRank.call(
            api_key=self.settings.dashscope_api_key.get_secret_value(),
            model=self.settings.rerank_model,
            query=query,
            documents=[doc["content"] for doc in documents],
            top_n=min(top_k, len(documents)),
            return_documents=False,
        )
        if response.status_code != 200:
            raise RuntimeError(f"重排调用失败：HTTP {response.status_code} / {response.code}")
        results = []
        for item in response.output.results:
            doc = documents[item.index].copy()
            doc["rerank_score"] = float(item.relevance_score)
            results.append(doc)
        return results

    # 8. 完整流程：重点先看这十几行，再回头看上面每个函数。
    async def search(self, query):
        rewritten_query = await self.rewrite_query(query)
        dense_docs = await self.dense_search(rewritten_query)
        bm25_docs = self.bm25_search(rewritten_query)
        fused_docs = reciprocal_rank_fusion([dense_docs, bm25_docs])

        # 同步 SDK 放到工作线程里，不阻塞 FastAPI 的事件循环。
        final_docs = await asyncio.to_thread(self.rerank, query, fused_docs[:6])

        return {
            "query": query,
            "rewritten_query": rewritten_query,
            "documents": final_docs,
            "dense_ids": [doc["id"] for doc in dense_docs],
            "bm25_ids": [doc["id"] for doc in bm25_docs],
            "rerank_model": self.settings.rerank_model,
        }
