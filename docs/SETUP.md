# 配置说明

## 模型配置

项目使用同一份百炼 API Key：

```dotenv
DASHSCOPE_API_KEY=你的百炼密钥
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
TEXT_MODEL=qwen-plus
VISION_MODEL=qwen3.5-plus
EMBEDDING_MODEL=qwen3.7-text-embedding-flash
RERANK_MODEL=gte-rerank-v2
```

qwen3.7-text-embedding-flash 是文本向量模型，可以用于这里的课程 RAG。qwen3.5-plus 负责图片理解。代码中显式关闭思考模式，先让转录、回答和工具调用流程保持简单。

模型名表示使用哪个模型，基础地址表示请求发送到哪里。你当前的基础地址已完成文本、向量和重排调用。以后如果控制台给你不同地域/业务空间的基础地址，修改 DASHSCOPE_BASE_URL 即可；无需为当前重排调用另外填写长URL。

重排采用阿里云 dashscope SDK 的 TextReRank.call。该 SDK 负责路径与请求格式，models.py 负责把聊天基础地址转成同域名的 SDK 基础地址。

官方说明：
- [Qwen 向量模型与 OpenAI 兼容接口](https://help.aliyun.com/zh/model-studio/embedding-interfaces-compatible-with-openai)
- [重排 SDK 调用](https://help.aliyun.com/zh/model-studio/text-rerank-api)
- [Qwen 视觉推理与思考模式](https://help.aliyun.com/zh/model-studio/visual-reasoning)

## 图片上传

```dotenv
OSS_ENDPOINT=https://oss-cn-shenzhen.aliyuncs.com
OSS_BUCKET=你的bucket名称
OSS_ACCESS_KEY_ID=你的AccessKey ID
OSS_ACCESS_KEY_SECRET=对应Secret
```

Endpoint 必须对应 Bucket 所在地域，不能把杭州地址用于深圳 Bucket。普通 AccessKey 需要有该 Bucket 内对象的写入、读取以及设置私有对象 ACL 的权限。

流程很简单：
1. 前端把图片发给后端。
2. 后端用 oss2.Auth 和 Bucket 上传图片，记录 object_key。
3. 后端生成一个5分钟有效的签名下载URL。
4. 多模态模型读取这个URL并转录题目。
5. 主 Agent 接收识别出来的文字，继续查课程资料和回答。

“签名URL”只是一张图片的临时下载地址，不需要你再申请一套凭据。保留这个步骤是因为私有 Bucket 的图片不能直接公开读取。

## 运行与排错

- 启动：项目根目录执行 uv sync --frozen，然后按 README 启动前后端。
- 首次启动要调用 Embedding 建立内存向量库，等待服务显示 Application startup complete。
- 修改 .env 或知识库后，需要重启后端。
- InvalidApiKey / 401：检查百炼 Key 和基础地址是否对应。
- ModelNotFound / 403：检查模型名、地域、开通权限。
- OSS上传502：检查 Endpoint、Bucket、AccessKey 和 RAM 权限。
- 会话409：当前讨论还有回答在生成，等它结束再发送。
- 没有配置 OSS：文本提问可以运行，上传图片时才提示配置。
- 图片转录不正确：上传更清晰的图片，并在问题中补充关键符号。

没有登录界面，前端连接设置只填写后端地址。后端默认只绑定本机127.0.0.1。
