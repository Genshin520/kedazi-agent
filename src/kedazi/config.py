"""读取 .env。密钥、模型名称和 OSS 信息都集中在这里。"""
from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    dashscope_api_key: SecretStr = SecretStr("")
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    text_model: str = "qwen-plus"
    vision_model: str = "qwen3.5-plus"
    embedding_model: str = "qwen3.7-text-embedding-flash"
    rerank_model: str = "gte-rerank-v2"

    oss_endpoint: str = "https://oss-cn-shenzhen.aliyuncs.com"
    oss_bucket: str = ""
    oss_access_key_id: SecretStr = SecretStr("")
    oss_access_key_secret: SecretStr = SecretStr("")

    data_dir: Path = ROOT / "data"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @model_validator(mode="after")
    def check_settings(self):
        if not self.dashscope_api_key.get_secret_value():
            raise ValueError("请在 .env 填写 DASHSCOPE_API_KEY")
        if not self.data_dir.is_absolute():
            self.data_dir = ROOT / self.data_dir
        return self
