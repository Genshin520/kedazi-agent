"""按笔记的顺序实现 RAG：每一步一个函数，search() 把它们串起来。"""
import asyncio
import hashlib

import dashscope
import jieba
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from rank_bm25 import BM25Plus

from kedazi.config import ROOT
from kedazi.models import configure_reranker


# 1. 读取 Markdown，先按标题切分，再限制每块长度。
def load_and_split_documents(path, source=None, material_id="builtin"):
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "一级标题"), ("##", "二级标题"), ("###", "三级标题")]
    )
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=350, chunk_overlap=50, separators=["\n\n", "\n", "。", " ", ""]
    )
    text = path.read_text(encoding="utf-8")
    documents = text_splitter.split_documents(header_splitter.split_text(text))
    source = source or path.name
    for index, doc in enumerate(documents):
        title = " / ".join(doc.metadata.values()) or source
        doc.page_content = title + "\n" + doc.page_content
        fingerprint = f"{material_id}/{source}/{index}/{doc.page_content}"
        doc.metadata = {
            "id": "doc_" + hashlib.sha256(fingerprint.encode()).hexdigest()[:20],
            "source": source, "title": title, "material_id": material_id,
        }
    if not documents:
        raise ValueError("Markdown 中没有可用的文字")
    return documents


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
        # 模型改变时使用不同集合，避免混用不同维度或语义空间的向量。
        model_key = hashlib.sha256(
            (settings.dashscope_base_url + settings.embedding_model).encode()
        ).hexdigest()[:12]
        self.vectorstore = Chroma(
            collection_name="study_" + model_key,
            embedding_function=embeddings,
            persist_directory=str(settings.data_dir / "chroma"),
        )
        self.lock = asyncio.Lock()
        configure_reranker(settings)

    async def refresh_bm25(self):
        saved = await asyncio.to_thread(self.vectorstore.get, include=["documents", "metadatas"])
        self.chunks = [Document(page_content=text, metadata=meta)
                       for text, meta in zip(saved["documents"], saved["metadatas"])]
        self.corpus = [document_to_dict(doc) for doc in self.chunks]
        self.corpus_tokens = [list(jieba.cut(doc["content"])) for doc in self.corpus]
        self.bm25 = BM25Plus(self.corpus_tokens) if self.corpus_tokens else None

    async def import_markdown(self, path, source=None, material_id="builtin"):
        documents = await asyncio.to_thread(load_and_split_documents, path, source, material_id)
        # 添加稳定ID，重复导入和重启不重复调用向量模型。
        async with self.lock:
            saved = await asyncio.to_thread(self.vectorstore.get, where={"material_id": material_id})
            existing = set(saved["ids"])
            desired = {doc.metadata["id"] for doc in documents}
            additions = [doc for doc in documents if doc.metadata["id"] not in existing]
            try:
                for offset in range(0, len(additions), 20):
                    batch = additions[offset:offset + 20]
                    await self.vectorstore.aadd_documents(batch, ids=[doc.metadata["id"] for doc in batch])
            except Exception:
                # 本轮失败时清理已写入的新片段；旧版本仍然可用。
                added_ids = [doc.metadata["id"] for doc in additions]
                if added_ids:
                    await self.vectorstore.adelete(added_ids)
                raise
            obsolete = list(existing - desired)
            if obsolete:
                await self.vectorstore.adelete(obsolete)
            await self.refresh_bm25()
        return len(documents)

    # 3. 启动时同步内置文档，并从 Chroma 恢复已入库的资料。
    async def build_index(self):
        from kedazi.materials import MaterialStore
        builtins = list(sorted((ROOT / "knowledge").glob("*.md")))
        active = {"builtin:" + path.name for path in builtins}
        for path in builtins:
            await self.import_markdown(path, material_id="builtin:" + path.name)
        for row in MaterialStore(self.settings.data_dir).list():
            if row["status"] == "ready":
                active.add(row["id"])
                path = MaterialStore(self.settings.data_dir).markdown_path(row["id"])
                if path.exists():
                    await self.import_markdown(path, row["filename"], row["id"])
        saved = await asyncio.to_thread(self.vectorstore.get, include=["metadatas"])
        stale = [key for key, meta in zip(saved["ids"], saved["metadatas"])
                 if meta["material_id"] not in active]
        if stale:
            await self.vectorstore.adelete(stale)
        await self.refresh_bm25()

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
        if self.bm25 is None:
            return []
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
        async with self.lock:
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
