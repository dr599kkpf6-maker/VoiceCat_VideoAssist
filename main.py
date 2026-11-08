import ctypes
import os
import sys
import threading
import traceback
from pathlib import Path

import webview

import bilibili_controller
import command_parser
import mango_controller
import startup_checks
import voice_rec


def app_dir() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


class Session:
    def __init__(self):
        self._lock = threading.Lock()
        self.listener = None
        self.caption = ""
        self.platform = ""

    def start_listening(self, platform="bilibili"):
        platform = platform if platform in {"bilibili", "mango"} else "bilibili"
        label = "芒果TV" if platform == "mango" else "哔哩哔哩"
        with self._lock:
            if self.listener is not None and self.listener.alive and self.platform == platform:
                return {"ok": True, "platform": platform}
            previous = self.platform if self.listener is not None else ""
            listener = self.listener
            self.listener = None
            self.caption = ""
            self.platform = platform
        if listener is not None:
            listener.stop()
            stopped = "芒果TV" if previous == "mango" else "哔哩哔哩"
            startup_checks.log(f"{stopped}控制器已关闭")
        with self._lock:
            fresh = voice_rec.Listener(self._on_text)
            if not fresh.start():
                self.platform = ""
                return {"ok": False, "platform": platform, "stopped": previous}
            self.listener = fresh
            self.caption = ""
        startup_checks.log(f"{label}控制器已启动，开始监听")
        return {"ok": True, "platform": platform, "stopped": previous}

    def stop_listening(self):
        with self._lock:
            listener = self.listener
            platform = self.platform
            self.listener = None
            self.caption = ""
            self.platform = ""
        if listener is not None:
            listener.stop()
            label = "芒果TV" if platform == "mango" else "哔哩哔哩"
            startup_checks.log(f"{label}控制器已断开，停止监听")
        return {"ok": True}

    def sound_level(self):
        listener = self.listener
        if listener is None or not listener.alive:
            return {"running": False, "level": 0, "caption": ""}
        return {"running": True, "level": round(float(listener.level), 4), "caption": self.caption}

    def _on_text(self, text: str) -> None:
        heard = (text or "").strip()
        if command_parser.has_wake(heard):
            self.caption = heard
        if self.platform == "mango":
            commands = command_parser.parse(heard, "mango")
            mango_controller.execute(heard, commands)
            return
        commands = command_parser.parse(heard)
        bilibili_controller.execute(heard, commands)


class Api:
    def __init__(self):
        self.session = Session()
        # 必须用下划线开头。页面桥接会遍历公开属性，碰到原生窗口会在界面线程上互锁，窗口就会变成“未响应”。
        self._window = None
        self._maximized = False

    def minimize_window(self):
        if self._window is not None:
            self._window.minimize()

    def toggle_window(self):
        if self._window is None:
            return
        if self._maximized:
            self._window.restore()
            self._maximized = False
        else:
            self._window.maximize()
            self._maximized = True

    def close_window(self):
        if self._window is not None:
            self._window.destroy()

    def check_item(self, item_id, platform="bilibili"):
        return startup_checks.check_item(str(item_id), str(platform or "bilibili"))

    def repair_item(self, item_id, platform="bilibili"):
        return startup_checks.repair_item(str(item_id), str(platform or "bilibili"))

    def client_status(self, platform="bilibili"):
        return startup_checks.client_status(str(platform or "bilibili"))

    def start_listening(self, platform="bilibili"):
        return self.session.start_listening(str(platform or "bilibili"))

    def stop_listening(self):
        return self.session.stop_listening()

    def sound_level(self):
        return self.session.sound_level()


_instance_mutex = None


def _claim_single_instance() -> bool:
    global _instance_mutex
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.SetLastError(0)
    _instance_mutex = kernel32.CreateMutexW(None, False, "Local\\VideoAssist.SingleInstance")
    return kernel32.GetLastError() != 183


def _focus_existing() -> None:
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, "猫师傅VoiceCat(=^･ω･^=)")
    if not hwnd:
        return
    user32.ShowWindow(hwnd, 9 if user32.IsIconic(hwnd) else 5)
    user32.SetForegroundWindow(hwnd)


def _storage_path() -> str:
    folder = Path(os.environ["APPDATA"]) / "VideoAssist" / "webview"
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder)


def main() -> None:
    if not _claim_single_instance():
        _focus_existing()
        startup_checks.log("窗口已在运行，转到前台")
        return
    page = (app_dir() / "ui" / "index.html").resolve()
    api = Api()
    api._window = webview.create_window(
        "猫师傅VoiceCat(=^･ω･^=)",
        page.as_uri(),
        width=840,
        height=552,
        min_size=(840, 552),
        background_color="#F3F0FA",
        frameless=True,
        easy_drag=False,
        js_api=api,
    )
    webview.start(storage_path=_storage_path())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        startup_checks.log("窗口启动失败：\n" + traceback.format_exc())
