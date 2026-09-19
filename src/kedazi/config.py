"""读取 .env。密钥、模型名称和 OSS 信息都集中在这里。"""
from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]                  #当前文件.resolve()得到绝对路径 parent[2]找到根目录 0-kadazi 1-src 2-agent_project_kedazi


class Settings(BaseSettings):                               #将项目会反复用到的配置 集中在一个类里 这是一个Pydantic类 通过类型注解的形式表明字段
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")    #从root下的.env读取环境变量
    #类型+参数注解+默认值 如果settings = Settings()生成对象时 会去.env里找参数 如果找到了就赋值 找不到的话就是这里的默认值
    dashscope_api_key: SecretStr = SecretStr("")            #大模型api 来自于阿里百炼 这里面都是.env的内容
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"   #url
    text_model: str = "qwen-plus"                           #文本模型 qwen-plus
    vision_model: str = "qwen3.5-plus"                      #多模态模型 qwen3.5-plus
    embedding_model: str = "qwen3.7-text-embedding-flash"   #embedding模型 qwen3.7-text-embedding-flash
    rerank_model: str = "gte-rerank-v2"                     #Rerank排序模型 gte-rerank-v2
    mineru_token: SecretStr = SecretStr("")                 #minerU的api

    oss_endpoint: str = "https://oss-cn-shenzhen.aliyuncs.com"   #阿里云OSS的配置
    oss_bucket: str = ""
    oss_access_key_id: SecretStr = SecretStr("")
    oss_access_key_secret: SecretStr = SecretStr("")

    data_dir: Path = ROOT / "data"                          #默认值为ROOT / "data" 如果从.env读到的话就是直接的data
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]  #前后端运行端口

    @model_validator(mode="after")                          #Pydantic的校验器 Settings的字段都读取完后 执行函数检查
    def check_settings(self):                               #检查函数
        if not self.dashscope_api_key.get_secret_value():   #大模型api是否存在
            raise ValueError("请在 .env 填写 DASHSCOPE_API_KEY")
        if not self.data_dir.is_absolute():                 #检查data_dir为绝对路径 从.env读出的data不是绝对路径
            self.data_dir = ROOT / self.data_dir            #如果不是绝对路径 就转成绝对路径
        return self

#Settings类用于基本信息的配置 通过settings = Setting()实例化对象时 可以通过settings.text_model访问
#实例化的settings是一个单例对象 全局实例化一个用来保存配置信息