import sys
from pathlib import Path

import webview


def app_dir() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def main() -> None:
    page = (app_dir() / "ui" / "index.html").resolve()
    webview.create_window(
        "视频助手",
        page.as_uri(),
        width=816,
        height=492,
        min_size=(816, 492),
        background_color="#F7F3F8",
    )
    webview.start()


if __name__ == "__main__":
    main()
