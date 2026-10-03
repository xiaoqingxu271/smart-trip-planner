"""MCP 工具白名单：展开过滤 + 拼写告警 + 父入口拦截。"""
import pytest
from app.agents.mcp_tool import MCPError, MCPTool
from hello_agents.tools.response import ToolStatus

METAS = [
    {"name": "maps_text_search", "inputSchema": {"properties": {"keywords": {"type": "string"}}, "required": ["keywords"]}},
    {"name": "maps_weather", "inputSchema": {}},
    {"name": "maps_geo", "inputSchema": {}},
]


class FakeMCPClient:
    def __init__(self, metas):
        self._metas = metas
        self._initialized = False
        self.calls = []

    def start(self):
        pass

    def _alive(self):
        return True

    def initialize(self):
        self._initialized = True

    def list_tools(self):
        return self._metas

    def call_tool(self, name, arguments=None):
        self.calls.append(name)
        return "ok"


def test_allowlist_filters_expansion_and_blocks_parent_entry():
    fake = FakeMCPClient(METAS)
    tool = MCPTool(name="t", description="d", client=fake,
                   allowlist=["maps_text_search", "maps_weather", "maps_typo"])

    expanded = tool.get_expanded_tools()
    assert {t.name for t in expanded} == {"maps_text_search", "maps_weather"}

    blocked = tool.run({"tool": "maps_geo", "arguments": {}})
    assert blocked.status == ToolStatus.ERROR
    assert blocked.error_info["code"] == "TOOL_NOT_ALLOWED"
    assert fake.calls == [], "被拦截的工具不应触达子进程"

    ok = tool.run({"tool": "maps_text_search", "arguments": {"keywords": "故宫"}})
    assert ok.status == ToolStatus.SUCCESS
    assert fake.calls == ["maps_text_search"]


def test_without_allowlist_behaves_as_before():
    fake = FakeMCPClient(METAS)
    tool = MCPTool(name="t2", description="d", client=fake)
    assert {t.name for t in tool.get_expanded_tools()} == {m["name"] for m in METAS}
    assert tool.run({"tool": "maps_geo", "arguments": {}}).status == ToolStatus.SUCCESS
    assert fake.calls == ["maps_geo"]


def test_expansion_failure_returns_none():
    class DeadClient(FakeMCPClient):
        def start(self):
            raise MCPError("cannot start")

    tool = MCPTool(name="t3", description="d", client=DeadClient(METAS))
    assert tool.get_expanded_tools() is None
