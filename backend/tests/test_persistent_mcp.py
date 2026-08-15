"""
第二阶段：PersistentMCPTool 连接复用测试

覆盖：
  - open_shared 启动一次进程，多次 run 复用同一连接
  - call_tool 调用次数 = 业务调用次数（不新建进程）
  - close_shared 关闭进程 + loop
  - 无共享连接时 run 回落父类短连接（行为不变）
  - open_shared 幂等（重复调不重复启动）

全程 mock MCPClient，不启动真实 uvx 进程。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hello_agents.tools import MCPTool
from app.tools.persistent_mcp import PersistentMCPTool


def _make_tool(mock_client_cls):
    """构造一个 PersistentMCPTool，__init__ 的 _discover_tools 也走 mock

    MCPTool.run / _discover_tools / open_shared 都是函数内局部 import：
        from hello_agents.protocols.mcp.client import MCPClient
    所以 patch 源模块属性即可影响所有局部 import。
    """
    with patch("hello_agents.protocols.mcp.client.MCPClient", mock_client_cls):
        tool = PersistentMCPTool(
            name="amap",
            description="test",
            server_command=["uvx", "amap-mcp-server"],
            env={"AMAP_MAPS_API_KEY": "test_key"},
            auto_expand=True,
        )
    return tool


@pytest.fixture
def mock_mcp_client():
    """mock MCPClient 类：实例的 aenter/aexit/call_tool 都是 AsyncMock"""
    instance = MagicMock()
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=None)
    instance.call_tool = AsyncMock(return_value='{"pois": []}')
    instance.list_tools = AsyncMock(return_value=[])
    cls = MagicMock(return_value=instance)
    cls.instance = instance
    return cls


class TestSharedConnectionLifecycle:
    """共享连接生命周期

    注意：__init__ 的 _discover_tools 会经 `async with MCPClient(...)` 调一次 aenter/aexit，
    所以断言用「open/run 操作的增量」而非绝对次数。
    """

    def test_open_starts_process_once(self, mock_mcp_client):
        tool = _make_tool(mock_mcp_client)
        before = mock_mcp_client.instance.__aenter__.call_count
        with patch("hello_agents.protocols.mcp.client.MCPClient", mock_mcp_client):
            tool.open_shared()
        assert tool.has_shared_connection
        # open_shared 恰好新增 1 次进程启动
        assert mock_mcp_client.instance.__aenter__.call_count == before + 1

    def test_multiple_runs_reuse_one_connection(self, mock_mcp_client):
        """3 次 run 复用同一 client：期间不新增进程启动，call_tool 3 次"""
        tool = _make_tool(mock_mcp_client)
        with patch("hello_agents.protocols.mcp.client.MCPClient", mock_mcp_client):
            tool.open_shared()
            aenter_before_runs = mock_mcp_client.instance.__aenter__.call_count
            for i in range(3):
                result = tool.run({"tool_name": "maps_text_search", "arguments": {"keywords": f"k{i}"}})
                assert "maps_text_search" in result
        # run 期间没有新建进程
        assert mock_mcp_client.instance.__aenter__.call_count == aenter_before_runs
        # 业务调用 3 次
        assert mock_mcp_client.instance.call_tool.call_count == 3

    def test_close_shuts_down_process(self, mock_mcp_client):
        tool = _make_tool(mock_mcp_client)
        with patch("hello_agents.protocols.mcp.client.MCPClient", mock_mcp_client):
            tool.open_shared()
            aexit_before = mock_mcp_client.instance.__aexit__.call_count
            tool.close_shared()
        assert not tool.has_shared_connection
        assert mock_mcp_client.instance.__aexit__.call_count == aexit_before + 1

    def test_open_is_idempotent(self, mock_mcp_client):
        tool = _make_tool(mock_mcp_client)
        with patch("hello_agents.protocols.mcp.client.MCPClient", mock_mcp_client):
            tool.open_shared()
            after_first = mock_mcp_client.instance.__aenter__.call_count
            tool.open_shared()  # 第二次应直接返回
        assert mock_mcp_client.instance.__aenter__.call_count == after_first


class TestFallbackToShortConnection:
    """无共享连接时回落父类短连接"""

    def test_run_without_shared_falls_back_to_super(self, mock_mcp_client):
        tool = _make_tool(mock_mcp_client)
        assert not tool.has_shared_connection
        with patch.object(MCPTool, "run", return_value="short_conn_result") as mock_super:
            result = tool.run({"tool_name": "maps_text_search", "arguments": {}})
        assert result == "short_conn_result"
        mock_super.assert_called_once()

    def test_run_after_close_falls_back_to_super(self, mock_mcp_client):
        """close_shared 后 run 回落短连接"""
        tool = _make_tool(mock_mcp_client)
        with patch("hello_agents.protocols.mcp.client.MCPClient", mock_mcp_client):
            tool.open_shared()
            tool.close_shared()
        with patch.object(MCPTool, "run", return_value="short_conn_result") as mock_super:
            result = tool.run({"tool_name": "maps_text_search", "arguments": {}})
        assert result == "short_conn_result"
        mock_super.assert_called_once()

    def test_missing_tool_name_returns_error(self, mock_mcp_client):
        """共享连接模式下缺 tool_name 返回错误"""
        tool = _make_tool(mock_mcp_client)
        with patch("hello_agents.protocols.mcp.client.MCPClient", mock_mcp_client):
            tool.open_shared()
            result = tool.run({"arguments": {}})
        assert "错误" in result
