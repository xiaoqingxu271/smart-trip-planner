"""全局配置：从 backend/.env 读取密钥与开关。"""
from functools import lru_cache
import json

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- LLM ----------
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model_id: str = "deepseek-chat"
    llm_temperature: float = 0.3

    # ---------- 高德 ----------
    amap_api_key: str = ""      # Web 服务 Key（MCP 服务器使用）
    amap_js_key: str = ""       # Web 端 JS API Key（前端地图使用）
    amap_js_secret: str = ""    # JS API 安全密钥

    # ---------- 高德 MCP 服务器 ----------
    mcp_server_command: str = "npx"
    mcp_server_args: str = '["-y", "@amap/amap-maps-mcp-server"]'

    # ---------- Unsplash ----------
    unsplash_access_key: str = ""

    # ---------- MySQL（行程持久化，唯一事实来源） ----------
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "trip_planner"

    # ---------- Redis（缓存/排行/锁，可降级） ----------
    redis_url: str = "redis://localhost:6379/0"

    # ---------- 应用 ----------
    demo_mode: bool = False

    # ---------- 访问码鉴权（单管理员密码模式） ----------
    # 留空 = 关闭鉴权（本地演示）；配置后所有 /api/* 需携带访问码（X-Access-Code 头或 ?code= 参数）
    app_password: str = ""

    # ---------- 多用户体系（AUTH_MODE=user 时启用注册/登录与按用户隔离） ----------
    # none（默认）：无鉴权，行程共享池，行为与从前一致；
    # user：开放注册 + 登录后使用，行程按用户隔离（示例/无主行程全员只读），此时忽略 APP_PASSWORD
    auth_mode: str = "none"
    rate_limit_auth: str = "10/60"  # 注册/登录：10 次 / 分钟 / IP（抑制暴力破解）

    # ---------- 限流（每 IP 固定窗口计数，Redis 不可用时放行；格式 "次数/窗口秒"） ----------
    rate_limit_enabled: bool = True
    rate_limit_heavy: str = "5/300"     # plan/replan 共用：5 次 / 5 分钟
    rate_limit_geo: str = "30/60"       # 地理编码：30 次 / 分钟
    rate_limit_img: str = "120/60"      # 图片代理：120 次 / 分钟
    rate_limit_default: str = "120/60"  # 其余 /api/* 兜底

    @property
    def mcp_args_list(self) -> list[str]:
        try:
            args = json.loads(self.mcp_server_args)
            return args if isinstance(args, list) else []
        except json.JSONDecodeError:
            return ["-y", "@amap/amap-maps-mcp-server"]

    @property
    def ready_for_agents(self) -> bool:
        """真实模式运行所需的最低配置。"""
        return bool(self.llm_api_key and self.amap_api_key)

    @property
    def user_auth_enabled(self) -> bool:
        return self.auth_mode == "user"


@lru_cache
def get_settings() -> Settings:
    return Settings()
