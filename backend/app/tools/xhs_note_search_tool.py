"""
小红书笔记搜索工具（基于 Spider_XHS）

用途：规划阶段搜小红书热度 top 5 图文笔记的正文，给总设计师 LLM 总结避坑/看点。
设计：
  - 通过 subprocess 调 Spider_XHS/venv 的 python（独立 venv，不污染 backend）
  - 只取图文笔记（note_type=2），按点赞排序（sort_type_choice=2）
  - 失败静默返回空字符串（规划不被小红书不可用阻断）
  - Cookie 过期/签名失败/网络异常 → 返回空，记日志
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import List, Dict, Any

from hello_agents.tools import Tool, ToolParameter

logger = logging.getLogger(__name__)

# Spider_XHS 项目路径（在 backend 同级目录）
SPIDER_XHS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "Spider_XHS"
SPIDER_PYTHON = SPIDER_XHS_DIR / "venv" / "bin" / "python"
COOKIE_FILE = SPIDER_XHS_DIR / "cookie.txt"

# 抓取脚本（在 Spider_XHS 环境里跑，import 它的库）
_FETCH_SCRIPT = '''
import sys, json
sys.path.insert(0, ".")
from pathlib import Path
from xhs_utils.xhs_pc import XHSPcAuth
from apis.xhs_pc_apis import XHS_Apis

def main():
    query = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    cookie = Path("cookie.txt").read_text().strip()
    if not cookie:
        print(json.dumps({"success": False, "msg": "cookie 为空，需扫码登录"}))
        return
    try:
        auth = XHSPcAuth.from_cookie(cookie)
        api = XHS_Apis(auth).bootstrap()
        # note_type=2 图文；sort_type_choice=2 最多点赞；note_time=3 半年内
        s, m, res = api.search_note(query, note_type=2, sort_type_choice=2, note_time=3)
        if not s or not res:
            print(json.dumps({"success": False, "msg": f"search failed: {m}"}))
            return
        items = res.get("data", {}).get("items", [])[:limit]
        notes = []
        for it in items:
            nc = it.get("note_card", {})
            nid = it.get("id") or nc.get("note_id")
            tok = it.get("xsec_token", "")
            title = nc.get("display_title", "")
            image_list = nc.get("image_list") or []
            cover_url = ""
            if image_list:
                info_list = image_list[0].get("info_list") or []
                if info_list:
                    cover_url = info_list[-1].get("url") or ""
            # 先用 search 带的标题，再补正文
            desc = ""
            if nid and tok:
                url = f"https://www.xiaohongshu.com/explore/{nid}?xsec_token={tok}&xsec_source=pc_search"
                s2, m2, res2 = api.get_note_info(url)
                if s2 and res2:
                    try:
                        nc2 = res2["data"]["items"][0]["note_card"]
                        desc = nc2.get("desc", "")
                        title = nc2.get("title", "") or title
                        image_list = nc2.get("image_list") or image_list
                        if image_list:
                            info_list = image_list[0].get("info_list") or []
                            cover_url = (info_list[-1].get("url") if info_list else "") or cover_url
                    except Exception:
                        pass
            notes.append({"title": title, "desc": desc, "note_id": nid,
                          "cover_url": cover_url, "note_url": url if nid and tok else ""})
        print(json.dumps({"success": True, "notes": notes}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"success": False, "msg": f"exception: {e}"}))

main()
'''


class XhsNoteSearchTool(Tool):
    """小红书图文笔记搜索工具（热度 top N 正文，给 LLM 总结）"""

    def __init__(self):
        super().__init__(
            name="xhs_note_search",
            description="小红书图文笔记搜索（Spider_XHS）。按热度取 top N 图文笔记正文，用于攻略避坑/看点。失败返回空。",
        )

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="search_query",
                type="string",
                description="搜索关键词，如 '北京一日游 攻略'",
                required=True,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="返回笔记数量，默认 5",
                required=False,
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> str:
        query = parameters.get("search_query", "")
        limit = int(parameters.get("limit", 5) or 5)
        if not query:
            return "错误：search_query 不能为空"
        if not COOKIE_FILE.exists():
            logger.warning("XHS cookie 不存在（%s），跳过小红书搜索", COOKIE_FILE)
            return ""
        try:
            result = asyncio.get_event_loop().run_until_complete(
                self._fetch(query, limit)
            )
        except RuntimeError:
            result = asyncio.run(self._fetch(query, limit))
        if not result.get("success"):
            logger.warning("XHS 搜索失败: %s", result.get("msg", ""))
            return ""
        notes = result.get("notes", [])
        if not notes:
            return ""
        # 拼成 markdown 给 LLM：完整正文交给 LLM 总结，不在工具层提前截断
        lines = []
        for i, n in enumerate(notes[:limit], 1):
            title = n.get("title", "")
            desc = n.get("desc", "")
            if not (title or desc):
                continue
            lines.append(f"【笔记{i}】{title}\n{desc}")
        return "\n\n".join(lines) if lines else ""

    async def _fetch(self, query: str, limit: int) -> Dict[str, Any]:
        """subprocess 调 Spider_XHS venv 跑抓取脚本"""
        try:
            proc = await asyncio.create_subprocess_exec(
                str(SPIDER_PYTHON),
                "-c",
                _FETCH_SCRIPT,
                query,
                str(limit),
                cwd=str(SPIDER_XHS_DIR),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            out = stdout.decode("utf-8", errors="replace").strip()
            # 找最后一行 JSON
            for line in reversed(out.splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    return json.loads(line)
            return {"success": False, "msg": f"no json in output: {out[-200:]}"}
        except asyncio.TimeoutError:
            return {"success": False, "msg": "subprocess timeout"}
        except Exception as e:
            return {"success": False, "msg": f"subprocess error: {e}"}
