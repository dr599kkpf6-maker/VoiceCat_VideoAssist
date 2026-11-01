import sys
import threading
from pathlib import Path

import webview

import bilibili_controller
import command_parser
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

    def start_listening(self):
        with self._lock:
            if self.listener is not None and self.listener.alive:
                return {"ok": True}
            self.caption = ""
            listener = voice_rec.Listener(self._on_text)
            if not listener.start():
                return {"ok": False}
            self.listener = listener
            self.caption = ""
            startup_checks.log("控制器已启动，开始监听")
            return {"ok": True}

    def stop_listening(self):
        with self._lock:
            listener = self.listener
            self.listener = None
            self.caption = ""
        if listener is not None:
            listener.stop()
            startup_checks.log("控制器已断开，停止监听")
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
        commands = command_parser.parse(heard)
        bilibili_controller.execute(heard, commands)


class Api:
    def __init__(self):
        self.session = Session()

    def check_item(self, item_id):
        return startup_checks.check_item(str(item_id))

    def repair_item(self, item_id):
        return startup_checks.repair_item(str(item_id))

    def client_status(self):
        return startup_checks.client_status()

    def start_listening(self):
        return self.session.start_listening()

    def stop_listening(self):
        return self.session.stop_listening()

    def sound_level(self):
        return self.session.sound_level()


def main() -> None:
    page = (app_dir() / "ui" / "index.html").resolve()
    webview.create_window(
        "视频助手猫师傅",
        page.as_uri(),
        width=816,
        height=492,
        min_size=(816, 492),
        background_color="#F7F3F8",
        js_api=Api(),
    )
    webview.start()


if __name__ == "__main__":
    main()
