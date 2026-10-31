# -*- coding: utf-8 -*-
"""
云水间 · 听音  ——  桌面客户端入口

行为：
  1. 若本地服务未启动，则在本进程内拉起（后台线程）
  2. 用 Edge 的「应用窗口」模式打开播放器：独立窗口、无地址栏 / 标签栏，任务栏独立图标
  3. 窗口关闭 → 服务随之退出
  4. 若已有窗口在运行，则把它提到前台，不再新开

之所以用应用窗口而不是 pywebview：本机 WebView2 宿主控件加载失败（窗口空白），
Edge 的应用窗口同样是独立窗口，但渲染链路最稳。
"""
import os
import sys
import time
import ctypes
import subprocess
import threading
from ctypes import wintypes

if getattr(sys, "frozen", False):
    HERE = os.path.dirname(sys.executable)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import app as backend  # noqa: E402

PORT = 8791  # 仅作兜底参考，实际端口由 ensure_server() 动态决定
PROFILE = os.path.join(HERE, ".window-profile")
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
APP_TITLE_HINT = "云水间"


# ---------------------------------------------------------------- 窗口查找
def _enum_windows(keyword):
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if keyword not in title:
            return True
        # 排除浏览器主窗口（标题形如 "xxx 和另外 2 个页面 - Microsoft Edge"）
        if "Microsoft Edge" in title or "和另外" in title:
            return True
        cls = ctypes.create_unicode_buffer(128)
        u.GetClassNameW(hwnd, cls, 128)
        if "Chrome_WidgetWin" in cls.value or "WebView" in cls.value:
            found.append(hwnd)
        return True

    u.EnumWindows(cb, 0)
    return found


def focus_existing_window():
    """已有窗口则激活并返回 True"""
    for hwnd in _enum_windows(APP_TITLE_HINT):
        u = ctypes.windll.user32
        u.ShowWindow(hwnd, 9)          # SW_RESTORE
        u.SetForegroundWindow(hwnd)
        return True
    return False


# ---------------------------------------------------------------- 服务
def _kill_zombies():
    """清理占着候选端口、却不响应 /api/ping 的僵死服务。

    只对 python / pythonw / 本程序进程下手，避免误杀别的软件。
    """
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                             capture_output=True, timeout=8)
        text = out.stdout.decode("gbk", "replace")
    except Exception:
        return

    pids = set()
    for line in text.splitlines():
        if "LISTENING" not in line.upper():
            continue
        for p in backend.PORT_CANDIDATES:
            if ":%d " % p in line:
                pids.add(line.split()[-1])
                break
    if not pids:
        return

    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                             capture_output=True, timeout=8)
        rows = out.stdout.decode("gbk", "replace").splitlines()
    except Exception:
        return

    safe = {"python.exe", "pythonw.exe", "云水间听音.exe"}
    for row in rows:
        cols = [c.strip().strip('"') for c in row.split('","')]
        if len(cols) < 2 or cols[1] not in pids:
            continue
        if cols[0].lower() in {n.lower() for n in safe}:
            subprocess.run(["taskkill", "/F", "/PID", cols[1]], capture_output=True)
            print(f"[清理] 已结束僵死服务 PID {cols[1]} ({cols[0]})")


def ensure_server():
    """返回 (端口, 是否本进程启动)。

    端口为 None 表示彻底起不来。
    """
    # 1) 已有健康服务 → 直接复用
    for p in backend.PORT_CANDIDATES:
        if backend.already_running(p):
            return p, False

    # 2) 端口被占但不应答 = 僵尸，先清掉
    _kill_zombies()

    # 3) 逐个候选端口尝试监听
    backend.refresh_library(verbose=True)
    backend.patch_meta_bg()
    for p in backend.PORT_CANDIDATES:
        try:
            httpd = backend.ThreadingHTTPServer(("127.0.0.1", p), backend.Handler)
        except OSError as e:
            print(f"[窗口] 端口 {p} 不可用：{e}")
            continue
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True, name="httpd").start()
        for _ in range(50):
            if backend.already_running(p):
                print(f"[窗口] 服务就绪 http://127.0.0.1:{p}/")
                return p, True
            time.sleep(0.1)
        return p, True
    return None, False


def find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def spawn_window(url):
    edge = find_edge()
    if not edge:
        import webbrowser
        webbrowser.open(url)
        return False
    args = [
        edge,
        f"--app={url}",
        "--window-size=1240,820",
        f"--user-data-dir={PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
        "--disable-extensions",
    ]
    try:
        subprocess.Popen(args, close_fds=True)
        return True
    except Exception as e:
        print(f"[窗口] 启动失败：{e}")
        import webbrowser
        webbrowser.open(url)
        return False


def wait_window_appear(timeout=18):
    for _ in range(int(timeout / 0.3)):
        time.sleep(0.3)
        if _enum_windows(APP_TITLE_HINT):
            return True
    return False


def wait_window_gone(port):
    """窗口连续 3 次检测不到才认为关闭（避免标题瞬时变化误判）"""
    miss = 0
    while True:
        time.sleep(2)
        if _enum_windows(APP_TITLE_HINT):
            miss = 0
            continue
        miss += 1
        if miss >= 3:
            return
        if not backend.already_running(port):
            return


def main():
    port, own = ensure_server()
    if port is None:
        print("[错误] 所有候选端口都不可用")
        return

    url = f"http://127.0.0.1:{port}/"

    # 服务在别处跑着：只管把窗口叫出来，本进程可以安全退出
    if not own:
        if not focus_existing_window():
            spawn_window(url)
        return

    # 本进程负责起服务 —— 那就必须由本进程保活到窗口关闭。
    # 绝不能在这里 return，否则服务线程随进程一起消失，
    # 窗口还开着但所有请求都没响应（表现为搜索失灵、收藏不刷新）。
    if not focus_existing_window():
        spawn_window(url)

    if not wait_window_appear():
        print("[窗口] 未检测到窗口，保持服务运行")

    wait_window_gone(port)
    print("[窗口] 已关闭")
    os._exit(0)


if __name__ == "__main__":
    main()
