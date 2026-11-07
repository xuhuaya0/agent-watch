r"""
tray.py — Windows 系统托盘 / macOS 顶部菜单栏驻留程序
================================================================================
功能：
  1. 后台启动 Agent Watch 服务（本机采集 + 本地网页 + 多 IP 远端同步）
  2. 根据当前任务状态动态切换托盘图标颜色：
       - 橙红：有任务需要介入（等待批准 / 等待回复 / 出错 / 疑似卡住）
       - 蓝色：有任务正在运行
       - 绿色：有任务刚完成待确认
       - 灰白：全部空闲
  3. 右键菜单支持：打开仪表盘、查看实时计数、立即扫描、一键勾选「开机自动启动」、退出
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from aw import autostart  # noqa: E402
import hub  # noqa: E402


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _make_icon_image(color: str):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # outer dark ring + colored status core
    d.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(17, 24, 39, 255))
    palette = {
        "warn": (245, 158, 11, 255),   # amber/orange: needs attention
        "run": (59, 130, 246, 255),    # blue: running
        "done": (34, 197, 94, 255),    # green: done
        "idle": (156, 163, 175, 255),  # gray: idle
    }
    fill = palette.get(color, palette["idle"])
    d.ellipse([16, 16, 48, 48], fill=fill)
    return img


def _current_badge() -> tuple[str, str]:
    tasks = hub.STORE.view(include_idle=True)
    c = hub.STORE.counts(tasks)
    title = f"Agent Watch — 介入:{c['attention']} 完成:{c['done']} 进行:{c['running']}"
    if c["attention"] > 0:
        return "warn", title
    if c["running"] > 0:
        return "run", title
    if c["done"] > 0:
        return "done", title
    return "idle", title


def open_dashboard(_icon=None, _item=None):
    port = int(hub.CFG.get("port", 8090))
    webbrowser.open(f"http://127.0.0.1:{port}/")


def main():
    if sys.platform == 'darwin':
        from aw.mac_instance import acquire
        instance_lock = acquire(hub.DATA_DIR)
        if instance_lock is None:
            return
    port = int(hub.CFG.get("port", 8090))
    hub.start_background_scanner()
    if not _port_in_use(port):
        threading.Thread(
            target=lambda: hub.app.run(host=hub.CFG["host"], port=port, threaded=True, use_reloader=False),
            daemon=True,
        ).start()

    try:
        import pystray
    except ImportError:
        # Fallback if pystray / Pillow is not installed: run headless server and open browser
        open_dashboard()
        while True:
            time.sleep(3600)

    if sys.platform == "darwin":
        # A menu-bar utility should not create a separate Python Dock item.
        # Apply this to the Python process itself, including LaunchAgent starts.
        import AppKit
        import Foundation
        Foundation.NSProcessInfo.processInfo().setProcessName_("Agent Watch")
        mac_app = AppKit.NSApplication.sharedApplication()
        if not mac_app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory):
            hub.log.warning("Could not activate macOS menu-bar-only mode")

    icons = {k: _make_icon_image(k) for k in ("warn", "run", "done", "idle")}

    def status_text(_item):
        _, t = _current_badge()
        return t.replace("Agent Watch — ", "")

    def on_scan_now(_icon, _item):
        hub.trigger_scan()

    def on_toggle_autostart(_icon, _item):
        autostart.set_enabled(not autostart.is_enabled())

    def on_quit(icon, _item):
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("打开监控面板", open_dashboard, default=True),
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.MenuItem("立即扫描并同步", on_scan_now),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("开机自动启动", on_toggle_autostart, checked=lambda _item: autostart.is_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出 Agent Watch", on_quit),
    )

    icon = pystray.Icon("AgentWatch", icons["idle"], "Agent Watch", menu)

    def updater():
        last_color = "idle"
        last_title = ""
        while icon.visible or True:
            time.sleep(3)
            try:
                color, title = _current_badge()
                if color != last_color:
                    icon.icon = icons[color]
                    last_color = color
                if title != last_title:
                    icon.title = title
                    last_title = title
            except Exception:
                pass

    threading.Thread(target=updater, daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
