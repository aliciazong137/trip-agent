"""H1 验证：HelloAgents + GLM-5.2 + 工具调用跑通"""
from dotenv import load_dotenv
from hello_agents import HelloAgentsLLM, ToolRegistry, ReActAgent
from hello_agents.tools.builtin import TodoWriteTool

load_dotenv()


def main():
    llm = HelloAgentsLLM()
    print(f"LLM model: {llm.model}")
    print(f"LLM base_url: {llm.base_url}")

    # 验证简单对话
    print("\n--- 简单对话 ---")
    resp = llm.invoke([{"role": "user", "content": "回复一个字：好"}])
    print(f"GLM 响应: {resp.content}")

    # 验证 ReActAgent + 工具
    print("\n--- ReActAgent + TodoWrite 工具（max_steps=15）---")
    registry = ToolRegistry()
    registry.register_tool(TodoWriteTool())

    # 默认 max_steps=5 偏小，旅行规划类任务需要更多步
    agent = ReActAgent(
        "验证助手",
        llm,
        tool_registry=registry,
        max_steps=15,
    )
    result = agent.run("用 TodoWrite 创建两个任务：1. 验证环境 2. 验证工具，然后报告完成")
    print(f"\nAgent 结果: {result[:500] if isinstance(result, str) else str(result)[:500]}")


if __name__ == "__main__":
    main()
