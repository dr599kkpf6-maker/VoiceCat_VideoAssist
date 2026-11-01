"""执行已经识别出的哔哩哔哩指令：快捷键，或通过调试端口读页面。"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
import urllib.request
from ctypes import wintypes

import ctypes
import websockets

import command_parser
import startup_checks

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
SW_RESTORE = 9
VK = {
    "space": 0x20,
    "f": 0x46,
    "up": 0x26,
    "down": 0x28,
    "m": 0x4D,
}
KEY_ACTION = {
    "toggle_play": "toggle_play",
    "fullscreen": "fullscreen",
    "up": "volume_up",
    "down": "volume_down",
    "mute": "mute",
}

COLLECT_JS = r"""
(() => {
  const idOf = (href) => {
    const found = String(href || "").match(/BV[0-9A-Za-z]+|av\d+/i);
    return found ? found[0] : "";
  };
  const boxOf = (el) => {
    const box = el.getBoundingClientRect();
    if (box.width < 16 || box.height < 10) return null;
    if (box.bottom < 0 || box.right < 0 || box.top > innerHeight || box.left > innerWidth) return null;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none" || Number(style.opacity) === 0) return null;
    return box;
  };
  const cards = [];
  const seen = new Set();
  for (const link of document.querySelectorAll("a")) {
    const id = idOf(link.href);
    if (!id) continue;
    const box = boxOf(link);
    if (!box) continue;
    const title = (link.getAttribute("title") || link.innerText || "").replace(/\s+/g, " ").trim();
    if (title.length < 2) continue;
    const key = id + "|" + title;
    if (seen.has(key)) continue;
    seen.add(key);
    cards.push({title, id, x: box.x, y: box.y, right: box.x >= innerWidth * 0.62});
  }
  const names = ["直播", "推荐", "热门", "追番", "影视"];
  const tabs = [];
  for (const node of document.querySelectorAll("a,button,div,span,li")) {
    if (!boxOf(node)) continue;
    const text = (node.innerText || "").replace(/\s+/g, " ").trim();
    if (names.includes(text)) tabs.push(text);
  }
  const current = idOf(location.href);
  return {href: location.href, history: history.length, current, cards, tabs};
})()
"""


class _KeyInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyInput)]


class _Input(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]


def execute(heard: str, commands: list[dict]) -> None:
    if not commands:
        startup_checks.log(f"听到「{heard}」，没有带猫师傅，不执行")
        return
    for command in commands:
        try:
            _execute_one(heard, command)
        except Exception as exc:
            startup_checks.log(f"听到「{heard}」，执行失败：{exc}")


def _execute_one(heard: str, command: dict) -> None:
    intent = command.get("intent")
    if intent == "unknown":
        startup_checks.log(f"听到「{heard}」，没有对上指令")
        return
    if intent in {"toggle_play", "fullscreen"}:
        _send_named(heard, intent, KEY_ACTION[intent])
        return
    if intent == "volume":
        _send_named(heard, intent, KEY_ACTION[command.get("direction") or ""])
        return
    if intent == "switch_tab":
        _switch_tab(heard, str(command.get("tab") or ""))
        return
    if intent == "adjacent":
        _adjacent(heard, str(command.get("direction") or ""))
        return
    if intent == "open_title":
        _open_title(heard, str(command.get("query") or ""), str(command.get("scope") or "cards"))
        return
    if intent == "back":
        _go_back(heard)
        return
    if intent == "home":
        _go_home(heard)
        return
    startup_checks.log(f"听到「{heard}」，没有对上指令")


def _send_named(heard: str, intent: str, action: str) -> None:
    keys = startup_checks.load_config().get("keys") or {}
    names = keys.get(action) if isinstance(keys, dict) else None
    if not isinstance(names, list) or not names:
        startup_checks.log(f"听到「{heard}」，意图 {intent}，快捷键无效")
        return
    unknown = [name for name in names if str(name).lower() not in VK]
    if unknown:
        startup_checks.log(f"听到「{heard}」，意图 {intent}，快捷键无效")
        return
    if not _focus_client():
        startup_checks.log(f"听到「{heard}」，意图 {intent}，窗口没能置前")
        return
    for name in names:
        _tap(VK[str(name).lower()])
    startup_checks.log(f"听到「{heard}」，意图 {intent}，已发送按键")


def _client_hwnd():
    pids = set(startup_checks._pids_named(startup_checks._process_name()))
    if not pids:
        return None
    chosen = {"hwnd": None, "area": -1, "iconic": None}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids or _user32.GetWindowTextLengthW(hwnd) <= 0:
            return True
        iconic = bool(_user32.IsIconic(hwnd))
        visible = bool(_user32.IsWindowVisible(hwnd))
        if not visible and not iconic:
            return True
        rect = wintypes.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        better = chosen["hwnd"] is None
        if iconic and chosen["iconic"] is False:
            better = False
        elif (not iconic) and chosen["iconic"] is True:
            better = True
        elif area > chosen["area"]:
            better = True
        if better:
            chosen["hwnd"] = hwnd
            chosen["area"] = area
            chosen["iconic"] = iconic
        return True

    _user32.EnumWindows(visit, 0)
    return chosen["hwnd"]


def _focus_client() -> bool:
    hwnd = _client_hwnd()
    if not hwnd:
        return False
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)
    foreground = _user32.GetForegroundWindow()
    current = _kernel32.GetCurrentThreadId()
    discard = wintypes.DWORD()
    foreground_thread = _user32.GetWindowThreadProcessId(foreground, ctypes.byref(discard))
    target_thread = _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(discard))
    _user32.AttachThreadInput(current, foreground_thread, True)
    _user32.AttachThreadInput(current, target_thread, True)
    _user32.ShowWindow(hwnd, SW_RESTORE)
    _user32.SetForegroundWindow(hwnd)
    _user32.BringWindowToTop(hwnd)
    _user32.AttachThreadInput(current, target_thread, False)
    _user32.AttachThreadInput(current, foreground_thread, False)
    time.sleep(0.12)
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), ctypes.byref(pid))
    return pid.value in set(startup_checks._pids_named(startup_checks._process_name()))


def _tap(virtual_key: int) -> None:
    scan = _user32.MapVirtualKeyW(virtual_key, 0)
    inputs = (_Input * 2)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].ki.wScan = scan
    inputs[0].ki.dwFlags = KEYEVENTF_SCANCODE
    inputs[1].type = INPUT_KEYBOARD
    inputs[1].ki.wScan = scan
    inputs[1].ki.dwFlags = KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP
    _user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(_Input))


def _switch_tab(heard: str, tab: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，切到{tab}，调试端口不可连接")
        return
    if not any(tab in page["tabs"] for page in pages):
        home = _first_bilibili(pages)
        if home is None or not _eval(home["ws"], _CLICK_HOME):
            startup_checks.log(f"听到「{heard}」，切到{tab}，回不到主页面")
            return
        deadline = time.time() + 3
        while time.time() < deadline:
            time.sleep(0.35)
            pages = _pages() or []
            if any(tab in page["tabs"] for page in pages):
                break
    for page in pages or []:
        if tab in page["tabs"] and _eval(page["ws"], _CLICK_TEXT % json.dumps(tab)):
            startup_checks.log(f"听到「{heard}」，切到{tab}，已打开")
            return
    startup_checks.log(f"听到「{heard}」，切到{tab}，主页面标签栏上没有这个标签")


def _open_title(heard: str, query: str, scope: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，打开「{query}」，调试端口不可连接")
        return
    video_pages = [
        page for page in pages
        if "/video/" in str(page.get("href") or "") or page.get("current")
    ]
    cards = []
    for page in (video_pages or pages) if scope == "right" else pages:
        for card in page["cards"]:
            on_rail = bool(card.get("right"))
            if scope == "right" and not on_rail:
                continue
            if scope == "cards" and video_pages and on_rail:
                continue
            cards.append({**card, "ws": page["ws"]})
    place = "右边菜单" if scope == "right" else "当前页面"
    if not cards:
        startup_checks.log(f"听到「{heard}」，打开「{query}」，{place}没有已显示的视频")
        return
    config = startup_checks.load_config()
    matched = command_parser.match_title(
        query,
        [card["title"] for card in cards],
        float(config.get("match_threshold") or 0.72),
        float(config.get("match_margin") or 0.05),
    )
    if not matched["ok"]:
        startup_checks.log(
            f"听到「{heard}」，打开「{query}」，未打开，最接近「{matched['title']}」"
            f"（{matched['score']}），次接近「{matched['second']}」（{matched['second_score']}）"
        )
        return
    chosen = next(card for card in cards if card["title"] == matched["title"])
    opened = _eval(chosen["ws"], _CLICK_ID % json.dumps(chosen["id"]))
    if opened:
        startup_checks.log(f"听到「{heard}」，打开「{matched['title']}」，分数 {matched['score']}，已打开")
    else:
        startup_checks.log(f"听到「{heard}」，打开「{matched['title']}」，页面上没有点到这条视频")


def _adjacent(heard: str, direction: str) -> None:
    pages = _pages()
    label = "下一项" if direction == "next" else "上一项"
    if pages is None:
        startup_checks.log(f"听到「{heard}」，{label}，调试端口不可连接")
        return
    current = next((page["current"] for page in pages if page.get("current")), "")
    cards = []
    for page in pages:
        for card in page["cards"]:
            cards.append({**card, "ws": page["ws"]})
    current_key = current.lower()
    grouped: dict[int, list[dict]] = {}
    for card in cards:
        grouped.setdefault(int(card["x"] // 80), []).append(card)
    lists = []
    for group in grouped.values():
        ordered = sorted(group, key=lambda item: (item["y"], item["x"]))
        if any(str(item["id"]).lower() == current_key for item in ordered):
            lists.append(ordered)
    if not lists:
        startup_checks.log(f"听到「{heard}」，{label}，当前视频不在已显示列表里")
        return
    ordered = max(lists, key=len)
    index = next(i for i, item in enumerate(ordered) if str(item["id"]).lower() == current_key)
    step = 1 if direction == "next" else -1
    target = index + step
    if target < 0 or target >= len(ordered):
        startup_checks.log(f"听到「{heard}」，{label}，没有相邻项")
        return
    chosen = ordered[target]
    if _eval(chosen["ws"], _CLICK_ID % json.dumps(chosen["id"])):
        startup_checks.log(f"听到「{heard}」，{label}，已打开「{chosen['title']}」")
    else:
        startup_checks.log(f"听到「{heard}」，{label}，页面上没有点到相邻视频")


def _go_back(heard: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，返回，调试端口不可连接")
        return
    ordered = sorted(pages, key=lambda page: page.get("history") or 0, reverse=True)
    for page in ordered:
        if (page.get("history") or 0) > 1 and _eval(page["ws"], "history.length > 1 && (history.back(), true)"):
            startup_checks.log(f"听到「{heard}」，返回，已返回上一个页面")
            return
    startup_checks.log(f"听到「{heard}」，返回，没有上一个页面")


def _go_home(heard: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，返回主界面，调试端口不可连接")
        return
    if any(_is_home(page.get("href") or "") for page in pages):
        startup_checks.log(f"听到「{heard}」，返回主界面，已在主页面")
        return
    home = _first_bilibili(pages)
    if home is not None and _eval(home["ws"], _CLICK_HOME):
        startup_checks.log(f"听到「{heard}」，返回主界面，已返回主页面")
        return
    startup_checks.log(f"听到「{heard}」，返回主界面，回不到主页面")


def _is_home(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if "bilibili" not in parsed.netloc:
        return False
    path = parsed.path.rstrip("/")
    return path in ("", "/index", "/index.html") and "/video/" not in url


def _first_bilibili(pages: list[dict]) -> dict | None:
    for page in pages:
        if "bilibili" in str(page.get("href") or ""):
            return page
    return pages[0] if pages else None


def _pages() -> list[dict] | None:
    port = startup_checks._debug_port()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    targets = [
        item for item in payload
        if item.get("type") == "page" and item.get("webSocketDebuggerUrl")
    ]
    if not targets:
        return []
    return asyncio.run(_read_targets(targets))


async def _read_targets(targets: list[dict]) -> list[dict]:
    pages = []
    for target in targets:
        try:
            value = await _evaluate(target["webSocketDebuggerUrl"], COLLECT_JS)
        except Exception:
            continue
        if not isinstance(value, dict):
            continue
        pages.append({
            "ws": target["webSocketDebuggerUrl"],
            "href": value.get("href") or "",
            "history": value.get("history") or 0,
            "current": value.get("current") or "",
            "cards": value.get("cards") or [],
            "tabs": value.get("tabs") or [],
        })
    return pages


def _eval(ws_url: str, expression: str):
    try:
        return asyncio.run(_evaluate(ws_url, expression))
    except Exception as exc:
        startup_checks.log(f"页面读取失败：{exc}")
        return None


async def _evaluate(ws_url: str, expression: str):
    async with websockets.connect(ws_url, open_timeout=2, max_size=4_000_000) as socket:
        await socket.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True},
        }))
        while True:
            message = json.loads(await asyncio.wait_for(socket.recv(), timeout=4))
            if message.get("id") != 1:
                continue
            result = message.get("result", {}).get("result", {})
            return result.get("value")


_CLICK_ID = r"""
(() => {
  const id = %s;
  const links = [...document.querySelectorAll("a")].filter((link) => String(link.href || "").toLowerCase().includes(String(id).toLowerCase()));
  const visible = links.find((link) => {
    const box = link.getBoundingClientRect();
    return box.width > 8 && box.height > 8;
  });
  const target = visible || links[0];
  if (!target) return false;
  target.click();
  return true;
})()
"""

_CLICK_TEXT = r"""
(() => {
  const name = %s;
  const target = [...document.querySelectorAll("a,button,div,span,li")].find((node) => {
    const text = (node.innerText || "").replace(/\s+/g, " ").trim();
    const box = node.getBoundingClientRect();
    return text === name && box.width > 8 && box.height > 8;
  });
  if (!target) return false;
  target.click();
  return true;
})()
"""

_CLICK_HOME = r"""
(() => {
  const home = document.querySelector('a[href="https://www.bilibili.com/"], a[href="https://www.bilibili.com"], a[href="/"]');
  if (home) {
    home.click();
    return true;
  }
  if (String(location.href || "").includes("bilibili")) {
    location.href = "https://www.bilibili.com/";
    return true;
  }
  return false;
})()
"""
