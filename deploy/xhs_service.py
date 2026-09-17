"""仅供 trip-agent Docker 内网调用的小红书搜索服务。"""
import asyncio
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

SPIDER_DIR = Path("/app/Spider_XHS")
COOKIE_FILE = SPIDER_DIR / "cookie.txt"
sys.path.insert(0, str(SPIDER_DIR))

app = FastAPI(title="Trip Agent XHS Search", docs_url=None, redoc_url=None)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=120)
    limit: int = Field(default=5, ge=1, le=10)


def search_notes(query: str, limit: int) -> dict[str, Any]:
    from apis.xhs_pc_apis import XHS_Apis
    from xhs_utils.xhs_pc import XHSPcAuth

    cookie = COOKIE_FILE.read_text().strip() if COOKIE_FILE.exists() else ""
    if not cookie:
        return {"success": False, "msg": "cookie 为空，需重新扫码登录"}
    try:
        api = XHS_Apis(XHSPcAuth.from_cookie(cookie)).bootstrap()
        success, message, result = api.search_note(
            query, note_type=2, sort_type_choice=2, note_time=3
        )
        if not success or not result:
            return {"success": False, "msg": f"search failed: {message}"}
        items = result.get("data", {}).get("items", [])[:limit]
        notes = []
        for item in items:
            card = item.get("note_card", {})
            note_id = item.get("id") or card.get("note_id")
            token = item.get("xsec_token", "")
            title = card.get("display_title", "")
            images = card.get("image_list") or []
            cover_url = ""
            if images and images[0].get("info_list"):
                cover_url = images[0]["info_list"][-1].get("url", "")
            note_url = ""
            description = ""
            if note_id and token:
                note_url = (
                    f"https://www.xiaohongshu.com/explore/{note_id}"
                    f"?xsec_token={token}&xsec_source=pc_search"
                )
                detail_ok, _, detail = api.get_note_info(note_url)
                if detail_ok and detail:
                    detail_card = detail.get("data", {}).get("items", [{}])[0].get("note_card", {})
                    description = detail_card.get("desc", "")
                    title = detail_card.get("title", "") or title
                    detail_images = detail_card.get("image_list") or images
                    if detail_images and detail_images[0].get("info_list"):
                        cover_url = detail_images[0]["info_list"][-1].get("url", "") or cover_url
            notes.append({
                "title": title,
                "desc": description,
                "note_id": note_id,
                "cover_url": cover_url,
                "note_url": note_url,
            })
        return {"success": True, "notes": notes}
    except Exception as exc:
        return {"success": False, "msg": f"exception: {exc}"}


@app.get("/health")
def health() -> dict[str, bool]:
    return {"status": "ok", "has_cookie": COOKIE_FILE.exists() and bool(COOKIE_FILE.read_text().strip())}


@app.post("/search")
async def search(request: SearchRequest) -> dict[str, Any]:
    result = await asyncio.to_thread(search_notes, request.query, request.limit)
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("msg", "search failed"))
    return result
