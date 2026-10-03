"""MCPTool：把 MCP 服务器接入 HelloAgents 框架。

- MCPStdioClient：最小化 MCP stdio 客户端，以子进程方式启动 MCP 服务器，
  通过 stdin/stdout 收发 JSON-RPC 2.0 消息（initialize → tools/list → tools/call）。
- MCPTool：hello_agents.tools.Tool 子类，expandable=True 时经 get_expanded_tools()
  自动展开为每个 MCP 工具一个独立 Tool（对应书中 auto_expand=True 的效果），
  供 SimpleAgent.add_tool(auto_expand=True) 注册后走原生 function calling。

整个进程共享一个 MCPTool 实例即可：只启动一个 MCP 服务器子进程，
所有 Agent 复用同一连接，节省资源、便于控制调用频率。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Any

from hello_agents.tools import Tool
from hello_agents.tools.base import ToolParameter
from hello_agents.tools.response import ToolResponse

from ..services.amap_pacer import pace

logger = logging.getLogger(__name__)


class MCPError(Exception):
    pass


class MCPStdioClient:
    """单进程 MCP stdio 客户端（线程安全：请求级互斥，串行收发）。

    多个 Agent 线程共享同一子进程连接时，必须把「写请求 → 收响应」整个周期
    串行化：_pending 表按请求 id 存放响应，若两个线程交错发起请求，
    先到者的响应可能被后到者的 _pending.clear() 清掉，导致其等待超时。
    因此 _request 全程持有 _io_lock；_lock 只保护 _pending/_next_id 的读写。
    """

    def __init__(self, command: str, args: list[str] | None = None, env: dict[str, str] | None = None):
        self.command = command
        self.args = args or []
        self.env = env or {}
        self._proc: subprocess.Popen | None = None
        self._pending: dict[int | str, dict] = {}
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._next_id = 0
        self._started = False

    # ---------- 进程管理 ----------

    def start(self) -> None:
        if self._started and self._proc and self._proc.poll() is None:
            return
        # Windows 下 npx 实为 npx.cmd，CreateProcess 无法直接执行，需要完整路径
        resolved = shutil.which(self.command) or self.command
        merged_env = {**os.environ, **self.env}
        try:
            self._proc = subprocess.Popen(
                [resolved, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=merged_env,
                text=True,
                encoding="utf-8",
            )
        except OSError as e:
            raise MCPError(f"无法启动 MCP 服务器（命令: {resolved}）: {e}") from e
        threading.Thread(target=self._read_loop, daemon=True).start()
        self._started = True

    def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and "id" in msg:
                with self._lock:
                    self._pending[msg["id"]] = msg

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ---------- JSON-RPC ----------

    def _request(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        if not self._alive():
            self.start()
        assert self._proc and self._proc.stdin
        # 请求级互斥：写请求与等待响应在同一个临界区内完成
        with self._io_lock:
            with self._lock:
                self._next_id += 1
                req_id = self._next_id
                self._pending.clear()
            message: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
            if params is not None:
                message["params"] = params
            try:
                self._proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
            except OSError as e:
                raise MCPError(f"向 MCP 服务器发送请求失败: {e}") from e

            deadline = time.time() + timeout
            while True:
                with self._lock:
                    response = self._pending.pop(req_id, None)
                if response is not None:
                    if "error" in response:
                        err = response["error"]
                        raise MCPError(f"MCP 服务器返回错误 [{err.get('code')}]: {err.get('message')}")
                    return response.get("result", {})
                if not self._alive():
                    raise MCPError("MCP 服务器进程已退出")
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise MCPError(f"MCP 请求超时（{timeout}s）: {method}")
                time.sleep(min(0.05, remaining))

    def _notify(self, method: str, params: dict | None = None) -> None:
        assert self._proc and self._proc.stdin
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        # 通知虽无响应，写 stdin 也需与 _request 的写互斥，避免行交错
        with self._io_lock:
            self._proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()

    # ---------- MCP 协议 ----------

    def initialize(self) -> dict:
        result = self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "helloagents-trip-planner", "version": "1.0.0"},
            },
            timeout=120.0,  # 首次 npx 需要下载包，留足时间
        )
        self._notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict]:
        result = self._request("tools/list", {})
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        # 全局限速：LLM 驱动的调用与校验器/图片服务共享同一高德 Key 的 QPS
        pace()
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}}, timeout=60.0)
        if result.get("isError"):
            texts = self._extract_texts(result)
            raise MCPError(f"MCP 工具 {name} 调用失败: {texts or '未知错误'}")
        return self._extract_texts(result)

    @staticmethod
    def _extract_texts(result: dict) -> str:
        parts = []
        for item in result.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts)


class MCPTool(Tool):
    """MCP 服务器在框架内的统一入口。

    用法（与书中一致）::

        self.mcp_tool = MCPTool(
            name="amap_mcp",
            command="npx",
            args=["-y", "@amap/amap-maps-mcp-server"],
            env={"AMAP_MAPS_API_KEY": settings.amap_api_key},
            auto_expand=True,
        )
        agent.add_tool(self.mcp_tool)   # auto_expand=True 时自动展开为独立工具

    最小权限用法：allowlist 限制该入口能触达的 MCP 工具集合；
    传入共享 client 可让多个 MCPTool 包装同一个 MCP 服务器子进程，
    各自带不同白名单（每个 Agent 一个最小权限视图），进程内仍只有一个子进程。
    白名单在两处强制：get_expanded_tools 展开时过滤 + 父工具 run 入口拦截
    （框架在展开失败时会退回注册父工具，后者可透传任意 MCP 工具名）。
    """

    def __init__(
        self,
        name: str,
        description: str,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        auto_expand: bool = True,
        allowlist: list[str] | None = None,
        client: MCPStdioClient | None = None,
    ):
        super().__init__(name=name, description=description, expandable=auto_expand)
        self.client = client or MCPStdioClient(
            command=command or "npx", args=args, env=env
        )
        self._allowlist = set(allowlist) if allowlist is not None else None
        self._expanded: list[Tool] | None = None
        self._expand_lock = threading.Lock()

    def _ensure_ready(self) -> list[dict]:
        self.client.start()
        if not self.client._alive():
            raise MCPError("MCP 服务器进程已退出")
        # initialize 一次即可；重复 initialize 无害但会多一次往返
        if not getattr(self.client, "_initialized", False):
            self.client.initialize()
            self.client._initialized = True
        return self.client.list_tools()

    # ---- Tool 接口（父工具级别的通用调用方式） ----

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="tool", type="string", description="要调用的 MCP 工具名"),
            ToolParameter(name="arguments", type="object", description="工具参数字典", required=False),
        ]

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        tool_name = parameters.get("tool", "")
        # 白名单兜底：父工具是"任意 MCP 工具"的透传入口，必须在这里也强制
        if self._allowlist is not None and tool_name not in self._allowlist:
            return ToolResponse.error(
                code="TOOL_NOT_ALLOWED",
                message=f"工具 {tool_name} 不在本工具入口的白名单内（允许：{sorted(self._allowlist)}）",
            )
        try:
            text = self.client.call_tool(tool_name, parameters.get("arguments") or {})
        except MCPError as e:
            return ToolResponse.error(code="MCP_CALL_FAILED", message=str(e))
        return ToolResponse.success(text=text)

    # ---- 展开机制：每个 MCP 工具 → 一个独立子工具 ----

    def get_expanded_tools(self) -> list[Tool] | None:
        with self._expand_lock:
            if self._expanded is not None:
                return self._expanded
            try:
                metas = self._ensure_ready()
            except MCPError as e:
                logger.warning("MCP 工具展开失败: %s", e)
                return None
            tools = [MCPSubTool(self, meta) for meta in metas]
            if self._allowlist is not None:
                known = {t.name for t in tools}
                missing = self._allowlist - known
                if missing:
                    # 防拼写错误导致白名单静默失效
                    logger.warning("白名单包含 MCP 服务器不提供的工具（检查拼写）: %s", sorted(missing))
                tools = [t for t in tools if t.name in self._allowlist]
                logger.info("MCP 服务器工具按白名单过滤：%d 个可用（%s）", len(tools), self.name)
            else:
                logger.info("MCP 服务器共提供 %d 个工具", len(tools))
            self._expanded = tools
            return self._expanded

    def stop(self) -> None:
        if self._proc_alive():
            assert self.client._proc
            self.client._proc.terminate()

    def _proc_alive(self) -> bool:
        return self.client._alive()


class MCPSubTool(Tool):
    """展开后的单个 MCP 工具，参数 schema 来自 MCP 的 inputSchema。"""

    def __init__(self, parent: MCPTool, meta: dict):
        super().__init__(
            name=meta.get("name", "unnamed_mcp_tool"),
            description=meta.get("description", ""),
            expandable=False,
        )
        self._parent = parent
        self._schema = meta.get("inputSchema", {}) or {}

    def get_parameters(self) -> list[ToolParameter]:
        params: list[ToolParameter] = []
        properties: dict = self._schema.get("properties", {})
        required: list[str] = self._schema.get("required", [])
        for prop_name, prop in properties.items():
            params.append(
                ToolParameter(
                    name=prop_name,
                    type=str(prop.get("type", "string")),
                    description=str(prop.get("description", "")),
                    required=prop_name in required,
                    default=prop.get("default"),
                )
            )
        return params

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        try:
            text = self._parent.client.call_tool(self.name, parameters)
        except MCPError as e:
            return ToolResponse.error(code="MCP_CALL_FAILED", message=str(e))
        return ToolResponse.success(text=text)
