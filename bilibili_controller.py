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
  const tidy = (raw) => String(raw || "").replace(/\s+/g, " ").trim();
  const stripTail = (text) => text.replace(/(?:\s+\d+(?:\.\d+)?[万亿]?){1,}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$/g, "").trim();
  const usable = (raw) => {
    const text = stripTail(tidy(raw));
    if (text.length < 2 || text.length > 80) return "";
    const left = text.replace(/[0-9.\s万亿播放弹幕时长点赞收藏投币分享:：]+/g, "");
    return left.length < 2 ? "" : text;
  };
  const tightRoot = (link) => {
    let node = link;
    while (node.parentElement && node.parentElement !== document.body) {
      const parent = node.parentElement;
      const ids = new Set([...parent.querySelectorAll("a")].map((item) => idOf(item.href)).filter(Boolean));
      if (ids.size > 1) break;
      node = parent;
    }
    return node;
  };
  const titleOf = (link) => {
    const root = tightRoot(link);
    const nodes = [...root.querySelectorAll("h3, h2, [class*='tit']")];
    if (!nodes.includes(link)) nodes.unshift(link);
    let best = "";
    for (const node of nodes) {
      const attr = usable(node.getAttribute && node.getAttribute("title"));
      const text = usable(node.innerText);
      const titled = /tit/i.test(String(node.className || "")) || node.tagName === "H3" || node.tagName === "H2";
      const choice = titled && attr ? attr : (attr && text.includes("·") ? attr : (text || attr));
      if (!choice || choice.includes("·")) continue;
      if (!best || (titled && choice.length >= best.length) || choice.length > best.length) best = choice;
    }
    return best || usable(link.getAttribute("title")) || usable((link.innerText || "").split("·")[0]);
  };
  const byId = new Map();
  for (const link of document.querySelectorAll("a")) {
    const id = idOf(link.href);
    if (!id) continue;
    const box = boxOf(link);
    if (!box) continue;
    const title = titleOf(link);
    if (!title) continue;
    const prev = byId.get(id);
    const prefer = !prev
      || (title.includes("·") === prev.title.includes("·") ? title.length > prev.title.length : !title.includes("·"));
    if (prefer) {
      byId.set(id, {title, id, x: box.x, y: box.y, right: box.x >= innerWidth * 0.62});
    }
  }
  const cards = [...byId.values()];
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
    if intent == "exit_fullscreen":
        _exit_fullscreen(heard)
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
    if intent == "open_live":
        _open_live(heard, str(command.get("query") or ""))
        return
    if intent == "search":
        _search(heard, str(command.get("query") or ""))
        return
    if intent == "quality":
        _set_quality(heard, str(command.get("level") or ""))
        return
    if intent == "back":
        _go_back(heard)
        return
    if intent == "home":
        _go_home(heard)
        return
    if intent == "refresh":
        _refresh(heard)
        return
    if intent == "scroll":
        _scroll(heard, str(command.get("direction") or ""))
        return
    if intent == "close_video":
        _close_video(heard)
        return
    startup_checks.log(f"听到「{heard}」，没有对上指令")


_FULLSCREEN_STATE_JS = r"""
(() => {
  if (document.fullscreenElement) return "on";
  const blob = [
    document.documentElement.className,
    document.body.className,
    ...[...document.querySelectorAll(".bpx-player-container, [class*='player']")].slice(0, 6).map((el) => el.className),
  ].join(" ");
  if (/bpx-state-fullscreen|bpx-state-web-fullscreen|player-fullscreen|mode-fullscreen/.test(blob)) return "on";
  for (const el of document.querySelectorAll("[aria-label*='全屏'],[title*='全屏']")) {
    const text = String(el.getAttribute("aria-label") || el.getAttribute("title") || "").replace(/\s+/g, "");
    if (text === "退出全屏" || text === "取消全屏") return "on";
    if (text === "全屏" || text === "进入全屏" || text === "网页全屏") return "off";
  }
  return "unknown";
})()
"""


def _exit_fullscreen(heard: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，退出全屏，调试端口不可连接")
        return
    player = _player_page(pages)
    if player is None:
        startup_checks.log(f"听到「{heard}」，退出全屏，没有正在播放的视频")
        return
    state = _eval(player["ws"], _FULLSCREEN_STATE_JS)
    if state == "off":
        startup_checks.log(f"听到「{heard}」，退出全屏，已经是窗口观看")
        return
    if state != "on":
        startup_checks.log(f"听到「{heard}」，退出全屏，看不出是不是全屏，没有退出")
        return
    keys = startup_checks.load_config().get("keys") or {}
    names = keys.get("fullscreen") if isinstance(keys, dict) else None
    if not isinstance(names, list) or not names or any(str(name).lower() not in VK for name in names):
        startup_checks.log(f"听到「{heard}」，退出全屏，快捷键无效")
        return
    if not _focus_client():
        startup_checks.log(f"听到「{heard}」，退出全屏，窗口没能置前")
        return
    for name in names:
        virtual_key = VK[str(name).lower()]
        _tap(virtual_key)
        time.sleep(0.05)
    startup_checks.log(f"听到「{heard}」，退出全屏，已改为窗口观看")


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


def _window_title(hwnd) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _client_hwnd(prefer_home: bool = False):
    pids = set(startup_checks._pids_named(startup_checks._process_name()))
    if not pids:
        return None
    chosen = {"hwnd": None, "area": -1, "iconic": None, "home": False}

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
        home_title = "哔哩哔哩" in _window_title(hwnd)
        better = chosen["hwnd"] is None
        if prefer_home and home_title != chosen["home"]:
            better = home_title
        elif iconic and chosen["iconic"] is False:
            better = False
        elif (not iconic) and chosen["iconic"] is True:
            better = True
        elif area > chosen["area"]:
            better = True
        if better:
            chosen["hwnd"] = hwnd
            chosen["area"] = area
            chosen["iconic"] = iconic
            chosen["home"] = home_title
        return True

    _user32.EnumWindows(visit, 0)
    return chosen["hwnd"]


def _focus_client(prefer_home: bool = False) -> bool:
    hwnd = _client_hwnd(prefer_home)
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
    player = _player_page(pages)
    if player is not None:
        _eval(player["ws"], _CLICK_PLAYER_HOME)
        time.sleep(0.45)
        pages = _pages() or pages
    home = _home_page(pages) or _first_bilibili(pages)
    if home is None:
        startup_checks.log(f"听到「{heard}」，切到{tab}，回不到主页面")
        return
    deadline = time.time() + 3
    while time.time() < deadline:
        if _eval(home["ws"], _CLICK_TAB % json.dumps(tab)):
            _focus_client(prefer_home=True)
            startup_checks.log(f"听到「{heard}」，切到{tab}，已打开")
            return
        time.sleep(0.35)
        pages = _pages() or []
        home = _home_page(pages) or home
    startup_checks.log(f"听到「{heard}」，切到{tab}，主页面标签栏上没有这个标签")


COLLECT_LIVE_JS = r"""
(() => {
  const tidy = (raw) => String(raw || "").replace(/\s+/g, "").trim();
  const show = (raw) => String(raw || "").replace(/\s+/g, " ").trim();
  const onLive = location.hash.indexOf("/page/home/live") !== -1;
  const follows = [];
  const seenNames = new Set();
  for (const item of document.querySelectorAll(".home_live--users .users-item")) {
    const label = item.querySelector(".users-name-wrap");
    const name = tidy(label && label.innerText);
    if (name.length < 2 || seenNames.has(name)) continue;
    seenNames.add(name);
    follows.push({name});
  }
  const rooms = [];
  const seenRooms = new Set();
  for (const link of document.querySelectorAll(".home_live--rooms a[href*='live.bilibili.com']")) {
    const href = String(link.getAttribute("href") || "");
    const found = href.match(/live\.bilibili\.com\/(\d+)/);
    if (!found || seenRooms.has(found[1])) continue;
    const box = link.getBoundingClientRect();
    if (box.width < 16 || box.height < 10) continue;
    if (box.bottom < 0 || box.right < 0 || box.top > innerHeight || box.left > innerWidth) continue;
    const style = getComputedStyle(link);
    if (style.visibility === "hidden" || style.display === "none" || Number(style.opacity) === 0) continue;
    const card = link.closest(".bili-live-card") || link;
    const title = show(card.querySelector(".bili-live-card__info--tit") && card.querySelector(".bili-live-card__info--tit").innerText);
    const uname = tidy(card.querySelector(".bili-live-card__info--uname") && card.querySelector(".bili-live-card__info--uname").innerText);
    if (title.length < 2 && uname.length < 2) continue;
    seenRooms.add(found[1]);
    rooms.push({id: found[1], title, uname});
  }
  return {onLive, follows, rooms};
})()
"""

_CLICK_LIVE_USER = r"""
(() => {
  const name = %s;
  const item = [...document.querySelectorAll(".home_live--users .users-item")].find((el) => {
    const label = el.querySelector(".users-name-wrap");
    return String((label && label.innerText) || "").replace(/\s+/g, "") === name;
  });
  if (!item) return false;
  item.click();
  return true;
})()
"""

_CLICK_LIVE_ROOM = r"""
(() => {
  const id = %s;
  const pattern = new RegExp("live\\.bilibili\\.com/" + id + "(?:$|[/?#])");
  const links = [...document.querySelectorAll("a")].filter((link) => pattern.test(String(link.href || "")));
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
    if scope == "cards" and _open_search_work(heard, query, pages):
        return
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


def _live_groups(follows: list, rooms: list) -> tuple[list[dict], list[dict]]:
    names = []
    seen = set()
    for item in follows:
        name = str((item or {}).get("name") or "").strip()
        if len(name) < 2 or name in seen:
            continue
        seen.add(name)
        names.append(name)
    followed = set(names)
    primary = [{"text": name, "kind": "follow", "where": "follow", "name": name, "title": name} for name in names]
    secondary = []
    for room in rooms:
        room_id = str((room or {}).get("id") or "")
        title = str((room or {}).get("title") or "").strip()
        uname = str((room or {}).get("uname") or "").strip()
        if not room_id.isdigit() or (len(title) < 2 and len(uname) < 2):
            continue
        where = "follow" if uname in followed else "page"
        bucket = primary if where == "follow" else secondary
        shown = title or uname
        if len(title) >= 2:
            bucket.append({"text": title, "kind": "room", "where": where, "id": room_id, "name": uname, "title": shown})
        if len(uname) >= 2 and uname != title:
            bucket.append({"text": uname, "kind": "room", "where": where, "id": room_id, "name": uname, "title": shown})
    return primary, secondary


def _match_live(query: str, entries: list[dict], threshold: float, margin: float):
    matched = command_parser.match_title(query, [item["text"] for item in entries], threshold, margin)
    if not matched["ok"]:
        return None, matched
    same = [item for item in entries if item["text"] == matched["title"]]
    chosen = next((item for item in same if item["kind"] == "follow"), same[0])
    return chosen, matched


def _open_live(heard: str, query: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，打开直播「{query}」，调试端口不可连接")
        return
    home = _home_page(pages)
    if home is None:
        startup_checks.log(f"听到「{heard}」，打开直播「{query}」，不在直播页面")
        return
    data = _eval(home["ws"], COLLECT_LIVE_JS)
    if not isinstance(data, dict):
        return
    if not data.get("onLive"):
        startup_checks.log(f"听到「{heard}」，打开直播「{query}」，不在直播页面")
        return
    primary, secondary = _live_groups(data.get("follows") or [], data.get("rooms") or [])
    if not primary and not secondary:
        startup_checks.log(f"听到「{heard}」，打开直播「{query}」，直播页面没有已显示的直播")
        return
    config = startup_checks.load_config()
    threshold = float(config.get("match_threshold") or 0.72)
    margin = float(config.get("match_margin") or 0.05)
    chosen, matched = _match_live(query, primary, threshold, margin)
    if chosen is None:
        page_choice, page_matched = _match_live(query, secondary, threshold, margin)
        if page_choice is not None or page_matched["score"] > matched["score"]:
            chosen, matched = page_choice, page_matched
    if chosen is None:
        startup_checks.log(
            f"听到「{heard}」，打开直播「{query}」，未打开，最接近「{matched['title']}」"
            f"（{matched['score']}），次接近「{matched['second']}」（{matched['second_score']}）"
        )
        return
    if chosen["kind"] == "follow":
        opened = _eval(home["ws"], _CLICK_LIVE_USER % json.dumps(chosen["name"]))
        place = f"关注的主播「{chosen['name']}」"
    else:
        opened = _eval(home["ws"], _CLICK_LIVE_ROOM % json.dumps(chosen["id"]))
        place = f"关注主播的直播「{chosen['title']}」" if chosen["where"] == "follow" else f"页面中的「{chosen['title']}」"
    if opened:
        _focus_client(prefer_home=True)
        startup_checks.log(f"听到「{heard}」，打开直播「{query}」，已打开{place}，分数 {matched['score']}")
    else:
        startup_checks.log(f"听到「{heard}」，打开直播「{query}」，页面上没有点到这条直播")


COLLECT_MEDIA_JS = r"""
(() => {
  const frame = document.querySelector("#appSearchFrame");
  const doc = frame && frame.contentDocument;
  if (!doc) return { onSearch: false, works: [] };
  const tidy = (raw) => String(raw || "").replace(/\s+/g, " ").trim();
  const compact = (raw) => String(raw || "").replace(/\s+/g, "");
  const list = [...doc.querySelectorAll(".search-all-list")].find((node) => {
    const style = getComputedStyle(node);
    const box = node.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) !== 0 && box.width > 40 && box.height > 40;
  });
  if (!list) return { onSearch: false, works: [] };
  const works = [];
  const seen = new Set();
  for (const card of list.querySelectorAll(".media-card")) {
    const link = card.querySelector("a.media-card-image") || card.querySelector("a[href]");
    const href = link ? String(link.getAttribute("href") || link.href || "") : "";
    const titleNode = card.querySelector(".media-card-content-head-title");
    const tagNode = card.querySelector(".media-card-content-head-title-tag");
    let title = tidy(titleNode && titleNode.innerText);
    const tag = compact(tagNode && tagNode.innerText);
    if (tag && compact(title).startsWith(tag)) title = tidy(title.replace(tagNode.innerText, ""));
    if (title.length < 2 || !href || seen.has(href)) continue;
    seen.add(href);
    works.push({ title, href });
  }
  return { onSearch: works.length > 0, works };
})()
"""

_CLICK_MEDIA = r"""
(() => {
  const href = %s;
  const frame = document.querySelector("#appSearchFrame");
  const doc = frame && frame.contentDocument;
  if (!doc) return false;
  const links = [...doc.querySelectorAll(".search-all-list .media-card a.media-card-image, .search-all-list .media-card a[href]")];
  const target = links.find((link) => link.getAttribute("href") === href || link.href === href);
  if (!target) return false;
  target.click();
  return true;
})()
"""


def _open_search_work(heard: str, query: str, pages: list[dict]) -> bool:
    home = _home_page(pages)
    if home is None:
        return False
    data = _eval(home["ws"], COLLECT_MEDIA_JS)
    if not isinstance(data, dict) or not data.get("onSearch"):
        return False
    works = [item for item in (data.get("works") or []) if str(item.get("title") or "").strip() and item.get("href")]
    if not works:
        return False
    config = startup_checks.load_config()
    threshold = float(config.get("match_threshold") or 0.72)
    matched = command_parser.match_title(
        query,
        [str(item["title"]) for item in works],
        threshold,
        float(config.get("match_margin") or 0.05),
    )
    if not matched["ok"]:
        if matched["score"] < threshold:
            return False
        startup_checks.log(
            f"听到「{heard}」，打开「{query}」，未打开影视作品，最接近「{matched['title']}」"
            f"（{matched['score']}），次接近「{matched['second']}」（{matched['second_score']}）"
        )
        return True
    chosen = next(item for item in works if item["title"] == matched["title"])
    opened = _eval(home["ws"], _CLICK_MEDIA % json.dumps(chosen["href"]))
    if opened:
        _focus_client(prefer_home=True)
        startup_checks.log(
            f"听到「{heard}」，打开「{query}」，已打开影视作品「{chosen['title']}」，分数 {matched['score']}"
        )
    else:
        startup_checks.log(f"听到「{heard}」，打开「{query}」，页面上没有点到这部影视作品")
    return True


_SEARCH_JS = r"""
(() => {
  const raw = %s;
  const query = String(raw || "").replace(/^(?:搜索|搜一下|搜)+/, "").replace(/^[\s，,。]+|[\s，,。]+$/g, "");
  const input = document.querySelector(".header_search input[placeholder*='搜索']");
  const icon = document.querySelector(".header_search .search-icon");
  const frame = document.querySelector("#appSearchFrame");
  const encoded = encodeURIComponent(query);
  const keywordOf = (href) => {
    const found = String(href || "").match(/[?&#]keyword=([^&#]*)/);
    if (!found) return "";
    try { return decodeURIComponent(found[1]); } catch (error) { return found[1]; }
  };
  const openSearch = () => {
    const root = document.querySelector("#app");
    const app = root && root.__vue_app__;
    const provides = app && app._context && app._context.provides;
    if (!provides) return false;
    for (const key of Object.getOwnPropertySymbols(provides)) {
      const value = provides[key];
      if (value && typeof value.push === "function" && value.currentRoute) {
        if (value.currentRoute.value && value.currentRoute.value.name !== "SearchPage") {
          value.push({ name: "SearchPage" });
        }
        return true;
      }
    }
    return false;
  };
  const readHref = () => {
    try { return frame && frame.contentWindow ? frame.contentWindow.location.href : ""; }
    catch (error) { return ""; }
  };
  const write = (text) => {
    if (!input) return;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    if (input.value !== text) setter.call(input, text);
  };
  if (query && keywordOf(readHref()) === query) {
    if (input && input.value !== query) write(query);
    return !input || input.value === query ? "ok" : "pending";
  }
  if (input && query) {
    input.focus();
    write("");
    write(query);
    const key = { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true };
    input.dispatchEvent(new KeyboardEvent("keydown", key));
    input.dispatchEvent(new KeyboardEvent("keypress", key));
    input.dispatchEvent(new KeyboardEvent("keyup", key));
    if (icon) icon.click();
    if (input.value !== query) write(query);
  }
  if (query && keywordOf(readHref()) === query && (!input || input.value === query)) return "ok";
  if (frame && frame.contentWindow && query) {
    frame.contentWindow.location.href = "https://bilipc.bilibili.com/search.html#/search/all?keyword=" + encoded;
    if (input && input.value !== query) write(query);
    return "pending";
  }
  openSearch();
  return input ? "pending" : "no-input";
})()
"""


def _search(heard: str, query: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，搜索「{query}」，调试端口不可连接")
        return
    home = _home_page(pages)
    if home is None:
        startup_checks.log(f"听到「{heard}」，搜索「{query}」，找不到主页面")
        return
    query = command_parser._clean_search(query)
    if len(query) < 2:
        startup_checks.log(f"听到「{heard}」，搜索，没有找到要搜索的名称")
        return
    script = _SEARCH_JS % json.dumps(query, ensure_ascii=False)
    result = None
    deadline = time.time() + 3.2
    while time.time() < deadline:
        result = _eval(home["ws"], script)
        if result == "ok":
            break
        time.sleep(0.35)
    if result == "ok":
        _focus_client(prefer_home=True)
        startup_checks.log(f"听到「{heard}」，搜索「{query}」，已打开搜索页")
        return
    if result == "no-input":
        startup_checks.log(f"听到「{heard}」，搜索「{query}」，没有找到搜索框")
        return
    startup_checks.log(f"听到「{heard}」，搜索「{query}」，没有打开搜索页")


_QUALITY_LABEL = {"1080": "1080P 高清", "720": "720P 准高清"}

_QUALITY_READ_JS = r"""
(() => {
  if (!document.querySelector(".bpx-player-ctrl-quality")) return "";
  const node = document.querySelector(".bpx-player-ctrl-quality-result-name");
  return String((node && node.innerText) || "").replace(/\s+/g, " ").trim();
})()
"""

_QUALITY_JS = r"""
(() => {
  const label = %s;
  const textOf = (node) => String((node && node.innerText) || "").replace(/\s+/g, " ").trim();
  const items = [...document.querySelectorAll(".bpx-player-ctrl-quality-menu-item")];
  const target = items.find((item) => textOf(item.querySelector(".bpx-player-ctrl-quality-text")) === label);
  if (!target) return "missing";
  const opener = document.querySelector(".bpx-player-ctrl-quality");
  if (opener) opener.click();
  target.click();
  return "clicked";
})()
"""


def _set_quality(heard: str, level: str) -> None:
    label = _QUALITY_LABEL.get(level)
    if not label:
        startup_checks.log(f"听到「{heard}」，切换清晰度，没有这个清晰度")
        return
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，切换{label}，调试端口不可连接")
        return
    player = _player_page(pages)
    if player is None:
        startup_checks.log(f"听到「{heard}」，切换{label}，没有正在播放的视频")
        return
    current = _eval(player["ws"], _QUALITY_READ_JS)
    if current == "":
        startup_checks.log(f"听到「{heard}」，切换{label}，没有正在播放的视频")
        return
    if current == label:
        startup_checks.log(f"听到「{heard}」，已经是清晰度「{label}」")
        return
    if _eval(player["ws"], _QUALITY_JS % json.dumps(label)) == "missing":
        startup_checks.log(f"听到「{heard}」，切换{label}，没有这个清晰度")
        return
    deadline = time.time() + 2.2
    while time.time() < deadline:
        if _eval(player["ws"], _QUALITY_READ_JS) == label:
            _focus_client()
            startup_checks.log(f"听到「{heard}」，已切换清晰度「{label}」")
            return
        time.sleep(0.25)
    startup_checks.log(f"听到「{heard}」，切换{label}，没有这个清晰度")


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


def _close_video(heard: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，关闭当前视频，调试端口不可连接")
        return
    player = _player_page(pages)
    if player is None:
        startup_checks.log(f"听到「{heard}」，关闭当前视频，没有正在播放的视频")
        return
    if _eval(player["ws"], _CLICK_PLAYER_CLOSE):
        _focus_client(prefer_home=True)
        startup_checks.log(f"听到「{heard}」，关闭当前视频，已关闭")
        return
    startup_checks.log(f"听到「{heard}」，关闭当前视频，没有点到关闭按钮")


def _on_home_feed(url: str) -> bool:
    return "/page/home/recommends" in str(url or "")


def _foreground_page(pages: list[dict]) -> dict | None:
    hwnd = _client_hwnd()
    title = _window_title(hwnd) if hwnd else ""
    home = _home_page(pages)
    player = _player_page(pages)
    if home is not None and player is not None:
        return home if "哔哩哔哩" in title else player
    return player or home or _first_bilibili(pages)


def _go_home(heard: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，返回主界面，调试端口不可连接")
        return
    player = _player_page(pages)
    if player is not None:
        _eval(player["ws"], _CLICK_PLAYER_HOME)
        time.sleep(0.45)
        pages = _pages() or pages
    home = _home_page(pages) or _first_bilibili(pages)
    if home is None:
        startup_checks.log(f"听到「{heard}」，返回主界面，回不到主页面")
        return
    if _on_home_feed(str(home.get("href") or "")):
        _focus_client(prefer_home=True)
        startup_checks.log(f"听到「{heard}」，返回主界面，已在主页面")
        return
    if _eval(home["ws"], _GO_HOME_JS):
        _focus_client(prefer_home=True)
        startup_checks.log(f"听到「{heard}」，返回主界面，已返回主页面")
        return
    startup_checks.log(f"听到「{heard}」，返回主界面，回不到主页面")


def _refresh(heard: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，刷新，调试端口不可连接")
        return
    page = _foreground_page(pages)
    if page is None:
        startup_checks.log(f"听到「{heard}」，刷新，没有可刷新的页面")
        return
    _eval(page["ws"], "location.reload()")
    _focus_client(prefer_home=_is_home(str(page.get("href") or "")))
    startup_checks.log(f"听到「{heard}」，刷新，已刷新当前页面")


def _scroll(heard: str, direction: str) -> None:
    pages = _pages()
    label = "上滑" if direction == "up" else "下滑"
    if pages is None:
        startup_checks.log(f"听到「{heard}」，{label}，调试端口不可连接")
        return
    page = _foreground_page(pages)
    if page is None:
        startup_checks.log(f"听到「{heard}」，{label}，没有可滚动的列表")
        return
    step = -1 if direction == "up" else 1
    result = _eval(page["ws"], _SCROLL_JS % step)
    if result == "ok":
        _focus_client(prefer_home=_is_home(str(page.get("href") or "")))
        startup_checks.log(f"听到「{heard}」，{label}，已滚动")
        return
    if result == "top":
        startup_checks.log(f"听到「{heard}」，{label}，已经到顶")
        return
    if result == "bottom":
        startup_checks.log(f"听到「{heard}」，{label}，已经到底")
        return
    startup_checks.log(f"听到「{heard}」，{label}，没有可滚动的列表")


def _is_player(url: str) -> bool:
    text = str(url or "")
    return "player.html" in text or "/video/" in text


def _is_home(url: str) -> bool:
    if _is_player(url):
        return False
    parsed = urllib.parse.urlparse(url)
    if "bilibili" not in parsed.netloc:
        return False
    path = parsed.path.rstrip("/")
    return path in ("", "/index", "/index.html")


def _player_page(pages: list[dict]) -> dict | None:
    return next((page for page in pages if _is_player(page.get("href") or "")), None)


def _home_page(pages: list[dict]) -> dict | None:
    return next((page for page in pages if _is_home(page.get("href") or "")), None)


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

_CLICK_TAB = r"""
(() => {
  const name = %s;
  const nodes = [...document.querySelectorAll(".vui_tabs--nav-item, .vui_tabs--nav-text")];
  const target = nodes.find((node) => (node.innerText || "").replace(/\s+/g, "").trim() === name);
  if (!target) return false;
  target.click();
  return true;
})()
"""

_CLICK_PLAYER_CLOSE = r"""
(() => {
  const buttons = [...document.querySelectorAll(".right-window-btns > .app_player--header-icon")];
  const close = buttons.find((node) => {
    const path = node.querySelector("path");
    return path && String(path.getAttribute("d") || "").includes("M9.22");
  });
  if (!close) return false;
  close.click();
  return true;
})()
"""

_CLICK_PLAYER_HOME = r"""
(() => {
  const marked = document.querySelector(".app_player--header-home");
  if (marked) {
    marked.click();
    return true;
  }
  const node = [...document.querySelectorAll("div,span,button,a")].find((el) => {
    const text = (el.innerText || "").replace(/\s+/g, "").trim();
    const box = el.getBoundingClientRect();
    return text === "回到主界面" && box.height > 8 && box.height < 80 && box.width < 260;
  });
  if (!node) return false;
  node.click();
  return true;
})()
"""

_GO_HOME_JS = r"""
(() => {
  if (location.hash.indexOf("/page/home/recommends") !== -1) return "already";
  const root = document.querySelector("#app");
  const app = root && root.__vue_app__;
  const provides = app && app._context && app._context.provides;
  if (provides) {
    for (const key of Object.getOwnPropertySymbols(provides)) {
      const value = provides[key];
      if (value && typeof value.push === "function" && value.currentRoute) {
        value.push("/page/home/recommends");
        return "ok";
      }
    }
  }
  location.hash = "#/page/home/recommends";
  return "ok";
})()
"""

_SCROLL_JS = r"""
(() => {
  const direction = %s;
  const pick = (doc) => {
    const nodes = [doc.scrollingElement, ...doc.querySelectorAll("div,main,section,ul")];
    let best = null;
    let score = 0;
    for (const el of nodes) {
      if (!el || el.scrollHeight < el.clientHeight + 80) continue;
      const style = getComputedStyle(el);
      const scrollable = style.overflowY === "auto" || style.overflowY === "scroll" || style.overflowY === "overlay" || el === doc.scrollingElement;
      if (!scrollable) continue;
      const box = el.getBoundingClientRect();
      if (box.width < 160 || box.height < 120) continue;
      const area = box.width * box.height;
      if (area > score) {
        best = el;
        score = area;
      }
    }
    return best || doc.scrollingElement || doc.documentElement;
  };
  let doc = document;
  if (location.hash.indexOf("search-page") !== -1) {
    const frame = document.querySelector("#appSearchFrame");
    if (frame && frame.contentDocument && frame.getBoundingClientRect().height > 80) doc = frame.contentDocument;
  }
  const scroller = pick(doc);
  if (!scroller) return "none";
  const amount = Math.max(120, Math.round((scroller.clientHeight || window.innerHeight) * 2 / 3));
  const before = scroller.scrollTop || 0;
  const max = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
  if (direction > 0 && before >= max - 4) return "bottom";
  if (direction < 0 && before <= 4) return "top";
  scroller.scrollBy({ top: direction * amount, behavior: "smooth" });
  return "ok";
})()
"""
