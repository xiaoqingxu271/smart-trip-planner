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

    # ---------- 价格源（批次 C，可选） ----------
    # StayAPI 为第三方聚合（携程酒店广告最低价），仅作「起价参考」；
    # 留空则酒店不显示真实起价、降级为「起价未知」。失败/超时不影响主流程。
    stayapi_key: str = ""
    stayapi_base_url: str = "https://api.stayapi.com"

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
    # user（默认）：开放注册 + 登录后使用，行程按用户隔离（示例/无主行程全员只读），此时忽略 APP_PASSWORD；
    # none：无鉴权，行程共享池，仅适合本机演示——公网部署切勿关闭
    auth_mode: str = "user"
    rate_limit_auth: str = "10/60"  # 注册/登录：10 次 / 分钟 / IP（抑制暴力破解）

    # ---------- 限流（每 IP 固定窗口计数，Redis 不可用时放行；格式 "次数/窗口秒"） ----------
    rate_limit_enabled: bool = True
    rate_limit_heavy: str = "5/300"     # plan/replan 共用：5 次 / 5 分钟
    rate_limit_geo: str = "30/60"       # 地理编码：30 次 / 分钟
    rate_limit_img: str = "120/60"      # 图片代理：120 次 / 分钟
    rate_limit_default: str = "120/60"  # 其余 /api/* 兜底

    # ---------- 用户级规划配额（成本护栏，批次 A2） ----------
    # 每个登录用户每天可触发的规划次数（含 re-plan），超限 429；
    # 未登录走 IP 限流兜底；0 表示关闭。多用户模式下的成本上限 = 注册用户数 × 此值。
    quota_plan_daily: int = 3

    # ---------- 高德调用节流（批次 B） ----------
    # 认证等级对应的搜索/LBS QPS：个人认证 3、企业认证 30、技术服务许可 100。
    # pacer 按 1/qps 作为最小调用间隔；image/route 等所有高德调用统一走此处。
    amap_qps: float = 3.0

    # ---------- 运维指标端点（批次 E） ----------
    # /api/metrics 的 Prometheus 采集令牌；留空则关闭该端点（返回 404），仅在需要被
    # Prometheus 等采集器抓取时配置，避免把 LLM 用量/规划失败率暴露给普通用户。
    metrics_token: str = ""

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
