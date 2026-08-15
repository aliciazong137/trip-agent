#!/usr/bin/env python3
"""验证 PersistentMCPTool 连接复用：3 次真实 text_search，对比短连接耗时"""
import subprocess
import sys
import time
sys.path.insert(0, ".")

from app.tools.persistent_mcp import PersistentMCPTool
from app.config import settings


def count_amap_processes():
    r = subprocess.run(["pgrep", "-f", "amap-mcp-server"], capture_output=True, text=True)
    return len([l for l in r.stdout.strip().splitlines() if l.strip()])


def make_tool():
    tool = PersistentMCPTool(
        name="amap",
        description="高德地图服务",
        server_command=["uvx", "amap-mcp-server"],
        env={"AMAP_MAPS_API_KEY": settings.amap_api_key},
        auto_expand=True,
    )
    tool.expandable = True
    return tool


def main():
    tool = make_tool()
    print(f"[init done] amap processes: {count_amap_processes()}")

    # 1. 短连接模式（父类 run，每次新建进程）
    print("\n=== 短连接模式（3 次调用）===")
    t0 = time.time()
    for i, kw in enumerate(["中山陵", "夫子庙", "南京博物院"]):
        t = time.time()
        tool.run({"tool_name": "maps_text_search", "arguments": {"keywords": kw, "city": "南京"}})
        print(f"  call {i+1} ({kw}): {time.time()-t:.1f}s")
    short_total = time.time() - t0
    print(f"短连接总计: {short_total:.1f}s")

    # 2. 长连接模式（open_shared 一次 + 3 次复用）
    print("\n=== 长连接模式（open 一次 + 3 次复用）===")
    t0 = time.time()
    tool.open_shared()
    print(f"  open_shared: {time.time()-t0:.1f}s  processes: {count_amap_processes()}")
    t1 = time.time()
    for i, kw in enumerate(["中山陵", "夫子庙", "南京博物院"]):
        t = time.time()
        tool.run({"tool_name": "maps_text_search", "arguments": {"keywords": kw, "city": "南京"}})
        print(f"  call {i+1} ({kw}): {time.time()-t:.1f}s")
    shared_calls = time.time() - t1
    tool.close_shared()
    long_total = time.time() - t0
    print(f"长连接总计: {long_total:.1f}s (open + 3 calls={shared_calls:.1f}s + close)")

    print(f"\n=== 对比 ===")
    print(f"短连接: {short_total:.1f}s | 长连接: {long_total:.1f}s | 节省: {short_total-long_total:.1f}s")


main()
