"""对应笔记里的“初始化模型”：文本模型、图片模型和向量模型。"""
import dashscope
from langchain_openai import ChatOpenAI, OpenAIEmbeddings


def create_chat_model(settings, vision=False):
    return ChatOpenAI(
        model=settings.vision_model if vision else settings.text_model,
        api_key=settings.dashscope_api_key.get_secret_value(),
        base_url=settings.dashscope_base_url,
        temperature=0,
        max_tokens=2200,
        timeout=60,
        max_retries=1,
        # qwen3.5-plus 默认开启思考。先关闭，让图片转录和工具调用保持简单。
        extra_body={"enable_thinking": False},
    )


def create_embeddings(settings):
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.dashscope_api_key.get_secret_value(),
        base_url=settings.dashscope_base_url,
        # 直接发送原文；不要按 OpenAI 的 tokenizer 把中文转成 token ID。
        check_embedding_ctx_length=False,
        chunk_size=10,
        request_timeout=45,
        max_retries=1,
    )


def configure_reranker(settings):
    # SDK 会拼出重排接口路径。它与聊天接口共用同一个阿里云域名。
    # 例如：https://dashscope.aliyuncs.com/compatible-mode/v1
    # 转成：https://dashscope.aliyuncs.com/api/v1
    host = settings.dashscope_base_url.split("/compatible-mode")[0].rstrip("/")
    dashscope.base_http_api_url = host + "/api/v1"
