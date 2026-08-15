"""
PersistentMCPTool - 支持共享长连接的 MCPTool 子类（第二阶段：MCP 连接复用优化）

背景：
  框架 MCPTool.run 每次调用都 `async with MCPClient(...)` 新建 uvx 子进程（stdio），
  每次约 5-8 秒固定开销。景点 Agent 一次规划约 10 次 MCP 调用，固定开销 50-80 秒。

方案：
  子类化 MCPTool（不改框架），open_shared() 启动一次进程并保持，
  run() 检测到有共享连接就复用，没有就回落父类短连接（行为完全不变）。

正确性约束：
  - open_shared / 多次 run / close_shared 必须在同一个 loop 里
  - 调用方（trip_planner._research_phase_sync）负责在 to_thread worker 线程里
    创建新 loop，串行执行 3 个研究 Agent，finally 关闭
  - 串行调用无并发，MCP stdio 单并发约束天然满足，无需锁

第十三章原版 MCPTool 无连接复用（共享的只是工具对象不是连接），本类是超越教程的优化。
"""
from typing import Any, Dict, Optional

from hello_agents.tools import MCPTool


class PersistentMCPTool(MCPTool):
    """支持共享长连接的 MCPTool 子类"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._shared_client: Optional[Any] = None   # MCPClient 实例（长连接）
        self._shared_loop: Optional[Any] = None     # client 所属的 event loop

    @property
    def has_shared_connection(self) -> bool:
        return self._shared_client is not None and self._shared_loop is not None

    def open_shared(self) -> None:
        """
        启动一次 MCP 进程并保持连接

        必须在「没有运行中的 loop」的线程里调用（如 asyncio.to_thread 的 worker）。
        调用后所有 run() 复用该连接，直到 close_shared()。
        """
        if self.has_shared_connection:
            return  # 幂等，已打开就不重复启动

        import asyncio
        from hello_agents.protocols.mcp.client import MCPClient

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client_source = self.server if self.server else self.server_command
        client = MCPClient(client_source, self.server_args, env=self.env)
        loop.run_until_complete(client.__aenter__())
        self._shared_client = client
        self._shared_loop = loop

    def close_shared(self) -> None:
        """关闭共享进程和 loop。必须在 open_shared 的同一线程/loop 环境调用"""
        client, loop = self._shared_client, self._shared_loop
        # 先清引用，保证即使关闭抛异常后续 run 也回落短连接
        self._shared_client = None
        self._shared_loop = None
        if client is None or loop is None:
            return
        try:
            loop.run_until_complete(client.__aexit__(None, None, None))
        finally:
            loop.close()

    def run(self, parameters: Dict[str, Any]) -> str:
        """
        有共享连接 → 复用；无共享连接 → 回落父类短连接（行为不变）

        SimpleAgent 循环内的 MCPWrappedTool.run 也走这里（它调 self.mcp_tool.run），
        所以景点/天气/酒店 Agent 的工具调用自动复用长连接。
        """
        if self.has_shared_connection:
            # 共享 loop 此刻空闲（串行调用），可再次 run_until_complete
            return self._shared_loop.run_until_complete(self._call_shared(parameters))
        return super().run(parameters)

    async def _call_shared(self, parameters: Dict[str, Any]) -> str:
        """在共享连接上执行 call_tool，返回格式与父类 run 一致"""
        tool_name = parameters.get("tool_name")
        arguments = parameters.get("arguments", {})
        if not tool_name:
            return "错误：必须指定 tool_name 参数"
        result = await self._shared_client.call_tool(tool_name, arguments)
        # 与父类 MCPTool.run 的 call_tool 分支输出格式保持一致
        return f"工具 '{tool_name}' 执行结果:\n{result}"
