# -*- coding: utf-8 -*-
"""
用 Chrome DevTools Protocol 驱动无头 Edge，抓取真实前端界面截图（报告插图）。

为什么不用 --screenshot 一次成型：
    只截图会拿到"页面刚加载完"的状态，故障前后路径、收敛耗时等面板都是空的。
    本脚本让页面**自己**执行演示流程（选源/目的 -> 发送分组 -> 注入链路故障 -> 自动重发），
    因此三个面板里都是真实数据。

用法：
    python report/scripts/capture_ui_screenshot.py
输出：
    report/figures/fig8_ui_normal.png       正常状态
    report/figures/fig9_ui_reroute.png      链路 R1-R2 故障后的自动绕行（1 跳 -> 8 跳 + 收敛耗时）
    report/figures/fig10_ui_ipv6.png        IPv6 转发演示
"""

import asyncio
import base64
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIG_DIR = ROOT / "report" / "figures"
PORT = 8791
DEBUG_PORT = 9333
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> str:
    for path in BROWSERS:
        if pathlib.Path(path).exists():
            return path
    raise RuntimeError("未找到 Edge/Chrome")


def start_backend():
    sys.path.insert(0, str(ROOT / "backend"))
    os.environ.update({"RS_NUM_NODES": "50", "RS_SEED": "20240915", "RS_LOG_LEVEL": "ERROR"})
    for name in ("main", "settings"):
        sys.modules.pop(name, None)
    import uvicorn

    import main as backend_main

    server = uvicorn.Server(uvicorn.Config(backend_main.app, host="127.0.0.1", port=PORT, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(80):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2).read()
            return server, thread
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("后端启动超时")


class DevTools:
    """极简 CDP 客户端：足够完成导航、执行 JS、截图"""

    def __init__(self, websocket_url: str):
        self.websocket_url = websocket_url
        self.next_id = 0

    async def __aenter__(self):
        import websockets

        self.ws = await websockets.connect(self.websocket_url, max_size=None)
        return self

    async def __aexit__(self, *exc):
        await self.ws.close()

    async def call(self, method: str, **params):
        self.next_id += 1
        await self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params}))
        while True:
            message = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=30))
            if message.get("id") == self.next_id:
                if "error" in message:
                    raise RuntimeError(f"{method} 失败: {message['error']}")
                return message.get("result", {})

    async def evaluate(self, expression: str):
        result = await self.call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        return result.get("result", {}).get("value")

    async def wait_for(self, expression: str, timeout: float = 30.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if await self.evaluate(expression):
                return True
            await asyncio.sleep(0.3)
        raise TimeoutError(f"等待条件超时: {expression}")

    async def screenshot(self, output: pathlib.Path):
        result = await self.call("Page.captureScreenshot", format="png", captureBeyondViewport=False)
        output.write_bytes(base64.b64decode(result["data"]))
        print(f"    已保存 {output.name}")


async def drive(debug_url: str) -> None:
    async with DevTools(debug_url) as devtools:
        await devtools.call("Page.enable")
        await devtools.call("Runtime.enable")
        await devtools.call("Emulation.setDeviceMetricsOverride",
                            width=1680, height=950, deviceScaleFactor=1, mobile=False)
        await devtools.call("Page.navigate", url=f"http://127.0.0.1:{PORT}/")
        await devtools.wait_for("document.readyState === 'complete' && typeof sendCommand === 'function'")
        await devtools.wait_for("nodesDataset && nodesDataset.length === 50")
        await asyncio.sleep(3)  # 等待 vis-network 布局与首次 fit 完成
        # 视口尺寸与页面初始布局可能不一致，截图前重新自适应一次，避免节点被裁剪
        await devtools.evaluate(
            "network.fit({animation:false});"
            "network.moveTo({scale: network.getScale()*0.86});"
        )
        await asyncio.sleep(1)

        print("  1) 正常状态截图")
        await devtools.screenshot(FIG_DIR / "fig8_ui_normal.png")

        print("  2) 演示正常转发（R1 -> R2）")
        await devtools.evaluate(
            "selectedSrc='R1'; selectedDst='R2'; updateSelectionColors(); updateSelectionTip(); sendPacket();"
        )
        await asyncio.sleep(2.5)

        print("  3) 注入链路 R1-R2 故障并自动重发，抓取绕行结果")
        await devtools.evaluate(
            "beginFaultComparison('链路','R1-R2');"
            "sendCommand('inject_fault', {type:'link', source:'R1', target:'R2'});"
        )
        await asyncio.sleep(5)
        await devtools.screenshot(FIG_DIR / "fig9_ui_reroute.png")

        print("  4) IPv6 转发演示（R2 -> R41）")
        await devtools.evaluate(
            "document.getElementById('protocol-select').value='IPv6';"
            "selectedSrc='R2'; selectedDst='R41'; updateSelectionColors(); updateSelectionTip(); sendPacket();"
        )
        await asyncio.sleep(3)
        await devtools.screenshot(FIG_DIR / "fig10_ui_ipv6.png")


def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    server, thread = start_backend()
    profile = pathlib.Path(os.environ["TEMP"]) / "edge_cdp_profile"
    browser = subprocess.Popen([
        find_browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
        f"--remote-debugging-port={DEBUG_PORT}", f"--user-data-dir={profile}",
        "--window-size=1680,950", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        target = None
        for _ in range(60):
            try:
                pages = json.loads(urllib.request.urlopen(
                    f"http://127.0.0.1:{DEBUG_PORT}/json/list", timeout=2).read())
                target = next((p for p in pages if p["type"] == "page"), None)
                if target:
                    break
            except Exception:
                time.sleep(0.3)
        if not target:
            raise RuntimeError("无法连接浏览器调试端口")
        asyncio.run(drive(target["webSocketDebuggerUrl"]))
    finally:
        browser.terminate()
        server.should_exit = True
        thread.join(timeout=15)
    print(f"\n界面截图已输出到 {FIG_DIR}")


if __name__ == "__main__":
    main()
