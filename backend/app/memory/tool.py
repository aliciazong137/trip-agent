"""
MemoryTool 复刻（对齐 HelloAgents MemoryTool action 接口）

支持 action:
  add/search/summary/stats/update/remove/forget/consolidate/clear_all
"""
from typing import Any, Dict, Optional

from app.memory.manager import MemoryManager


class MemoryTool:
    def __init__(self, user_id: str = "default_user", manager: Optional[MemoryManager] = None):
        self.user_id = user_id
        self.manager = manager or MemoryManager()

    def run(self, parameters: Dict[str, Any]) -> str:
        action = parameters.get("action")
        if action == "add":
            mid = self.manager.add_memory(
                content=parameters.get("content", ""),
                user_id=parameters.get("user_id", self.user_id),
                memory_type=parameters.get("memory_type", "working"),
                importance=float(parameters.get("importance", 0.5)),
                metadata=parameters.get("metadata") or {},
                modality=parameters.get("modality", "text"),
                file_path=parameters.get("file_path"),
            )
            return f"✅ 已添加记忆: {mid}"
        if action == "search":
            results = self.manager.search(
                user_id=parameters.get("user_id", self.user_id),
                query=parameters.get("query", ""),
                limit=int(parameters.get("limit", 5)),
                memory_types=[parameters["memory_type"]] if parameters.get("memory_type") else None,
                min_importance=float(parameters.get("min_importance", 0.0)),
            )
            if not results:
                return "未找到相关记忆"
            return "\n".join(f"[{i+1}] ({r.item.memory_type}, score={r.score:.2f}) {r.item.content}" for i, r in enumerate(results))
        if action == "summary":
            return self.manager.summary(parameters.get("user_id", self.user_id), limit=int(parameters.get("limit", 10)))
        if action == "stats":
            return str(self.manager.stats(parameters.get("user_id", self.user_id)))
        if action == "update":
            ok = self.manager.update_memory(parameters.get("memory_id"), parameters.get("content"), parameters.get("importance"))
            return "✅ 更新成功" if ok else "❌ 未找到记忆"
        if action == "remove":
            ok = self.manager.remove_memory(parameters.get("memory_id"))
            return "✅ 删除成功" if ok else "❌ 未找到记忆"
        if action == "forget":
            n = self.manager.forget(
                user_id=parameters.get("user_id", self.user_id),
                strategy=parameters.get("strategy", "importance_based"),
                threshold=float(parameters.get("threshold", 0.1)),
                max_age_days=int(parameters.get("max_age_days", 30)),
            )
            return f"✅ 已遗忘 {n} 条记忆"
        if action == "consolidate":
            n = self.manager.consolidate(
                user_id=parameters.get("user_id", self.user_id),
                from_type=parameters.get("from_type", "episodic"),
                to_type=parameters.get("to_type", "semantic"),
                importance_threshold=float(parameters.get("importance_threshold", 0.7)),
            )
            return f"✅ 已整合 {n} 条记忆"
        if action == "clear_all":
            n = self.manager.clear_all(parameters.get("user_id", self.user_id))
            return f"✅ 已清空 {n} 条记忆"
        return f"❌ 不支持的操作: {action}"

    def add_knowledge(self, content: str, importance: float = 0.9) -> str:
        return self.run({"action": "add", "content": content, "importance": importance, "memory_type": "semantic"})

    def get_context_for_query(self, query: str, limit: int = 3) -> str:
        return self.manager.get_context_for_query(self.user_id, query, limit)

    def auto_record_conversation(self, user_input: str, agent_response: str) -> str:
        content = f"用户输入：{user_input}\nAgent 回复：{agent_response}"
        return self.run({"action": "add", "content": content, "importance": 0.6, "memory_type": "episodic"})
