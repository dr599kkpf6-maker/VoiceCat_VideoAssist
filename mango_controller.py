"""执行已经识别出的芒果TV指令：快捷键，或通过调试端口读页面。"""

from __future__ import annotations

import asyncio
import json
import time
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
    "escape": 0x1B,
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
_CHANNEL_KEY = {
    "home": "首页",
    "index": "首页",
    "show": "综艺",
    "variety": "综艺",
    "tv": "电视剧",
    "movie": "电影",
    "cartoon": "动漫",
    "anime": "动漫",
    "child": "少儿",
    "doc": "纪录片",
    "documentary": "纪录片",
    "live": "直播",
    "local": "本地播放",
}

COLLECT_JS = r"""
(() => {
  const tidy = (raw) => String(raw || "").replace(/\s+/g, "").trim();
  const boxOf = (el) => {
    const box = el.getBoundingClientRect();
    if (box.width < 12 || box.height < 8) return null;
    if (box.bottom < 0 || box.right < 0 || box.top > innerHeight || box.left > innerWidth) return null;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none" || Number(style.opacity) === 0) return null;
    return box;
  };
  const nav = document.querySelector("#channel-left-nav");
  const channels = [];
  if (nav) {
    for (const node of nav.querySelectorAll("a,button,li,div,span")) {
      const text = tidy(node.innerText);
      const box = boxOf(node);
      if (!box || text.length < 2 || text.length > 6 || channels.includes(text)) continue;
      channels.push(text);
    }
  }
  const keys = {home:"首页",index:"首页",show:"综艺",variety:"综艺",tv:"电视剧",movie:"电影",cartoon:"动漫",anime:"动漫",child:"少儿",doc:"纪录片",documentary:"纪录片",live:"直播",local:"本地播放"};
  let channel = "";
  if (nav) {
    const marked = tidy(nav.getAttribute("data-channel-name"));
    channel = keys[marked] || (channels.includes(marked) ? marked : "");
    if (!channel) {
      const current = [...nav.querySelectorAll("[class*='active'],[class*='current'],[aria-current='page']")]
        .map((node) => tidy(node.innerText))
        .find((text) => channels.includes(text));
      channel = current || "";
    }
  }
  const cards = [];
  const seen = new Set();
  for (const node of document.querySelectorAll("a,h2,h3,h4,[class*='title'],[class*='name']")) {
    if (nav && nav.contains(node)) continue;
    const box = boxOf(node);
    if (!box) continue;
    const text = tidy(node.getAttribute("title") || node.innerText);
    if (text.length < 2 || text.length > 40 || seen.has(text)) continue;
    if (/^(播放|暂停|全屏|清晰度|弹幕|选集|登录|搜索)$/.test(text)) continue;
    seen.add(text);
    cards.push({title: text});
  }
  const episodes = [];
  for (const node of document.querySelectorAll("[class*='episode'] a,[class*='episode'] button,[class*='episode'] li,[class*='episode-item'],[class*='series-item']")) {
    const box = boxOf(node);
    const text = tidy(node.innerText);
    if (!box || !/^\d{1,4}$/.test(text)) continue;
    const mark = String(node.className || "") + String(node.getAttribute("aria-selected") || "");
    episodes.push({label: text, current: /active|current|selected|on/i.test(mark)});
  }
  const fullscreen = document.fullscreenElement
    || /fullscreen|full-screen|is-full/i.test(String(document.body.className || "") + String(document.documentElement.className || ""))
    ? "on" : "off";
  return {
    href: location.href,
    title: document.title || "",
    history: history.length,
    channel,
    channels,
    cards: cards.slice(0, 80),
    episodes: episodes.slice(0, 200),
    fullscreen,
    player: Boolean(document.querySelector("video,[class*='player-control'],[class*='mgtv-player']")),
  };
})()
"""

_CLICK_CHANNEL = r"""
(() => {
  const name = __VALUE__;
  const root = document.querySelector("#channel-left-nav");
  if (!root) return "no-nav";
  const tidy = (raw) => String(raw || "").replace(/\s+/g, "").trim();
  const target = [...root.querySelectorAll("a,button,li,div,span")].find((node) => {
    const box = node.getBoundingClientRect();
    return tidy(node.innerText) === name && box.width > 8 && box.height > 8;
  });
  if (!target) return "missing";
  target.click();
  return "ok";
})()
"""

_CLICK_TITLE = r"""
(() => {
  const name = __VALUE__;
  const tidy = (raw) => String(raw || "").replace(/\s+/g, "").trim();
  const nav = document.querySelector("#channel-left-nav");
  const target = [...document.querySelectorAll("a,h2,h3,h4,[class*='title'],[class*='name']")].find((node) => {
    if (nav && nav.contains(node)) return false;
    const box = node.getBoundingClientRect();
    const text = tidy(node.getAttribute("title") || node.innerText);
    return text === name && box.width > 8 && box.height > 8;
  });
  if (!target) return false;
  (target.closest("a") || target).click();
  return true;
})()
"""

_SEARCH_JS = r"""
(() => {
  const query = String(__VALUE__).replace(/^(?:搜索|搜一下|搜)+/, "");
  const input = document.querySelector("#m-topheader input, .m-search input, input[type='search'], input[placeholder*='搜']");
  if (!input || query.length < 2) return "no-input";
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  setter.call(input, query);
  input.dispatchEvent(new Event("input", {bubbles: true}));
  input.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true}));
  input.dispatchEvent(new KeyboardEvent("keyup", {key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true}));
  const icon = input.parentElement && [...input.parentElement.querySelectorAll("button,i,span,a")].find((node) => {
    const text = String(node.innerText || node.getAttribute("aria-label") || "");
    return text.includes("搜索") || /search/i.test(String(node.className || ""));
  });
  if (icon) icon.click();
  return input.value === query ? "ok" : "mismatch";
})()
"""

_EPISODE_JS = r"""
(() => {
  const request = __VALUE__;
  const tidy = (raw) => String(raw || "").replace(/\s+/g, "").trim();
  const nodes = [...document.querySelectorAll("[class*='episode'] a,[class*='episode'] button,[class*='episode'] li,[class*='episode-item'],[class*='series-item']")].filter((node) => {
    const box = node.getBoundingClientRect();
    return box.width > 8 && box.height > 8 && /^\d{1,4}$/.test(tidy(node.innerText));
  });
  if (!nodes.length) return "none";
  const labelOf = (node) => tidy(node.innerText);
  if (request.number) {
    const target = nodes.find((node) => labelOf(node) === String(request.number));
    if (!target) return "missing";
    target.click();
    return "ok";
  }
  const index = nodes.findIndex((node) => /active|current|selected|on/i.test(String(node.className || "") + String(node.getAttribute("aria-selected") || "")));
  const current = index >= 0 ? index : 0;
  const next = request.direction === "previous" ? current - 1 : current + 1;
  if (next < 0 || next >= nodes.length) return "edge";
  nodes[next].click();
  return "ok";
})()
"""

_QUALITY_JS = r"""
(() => {
  const level = String(__VALUE__);
  const tidy = (raw) => String(raw || "").replace(/\s+/g, "").trim();
  const visible = (node) => {
    const box = node.getBoundingClientRect();
    return box.width > 8 && box.height > 8;
  };
  const wants = (text) => {
    if (["蓝光", "超清", "高清", "标清"].includes(level)) return text === level || text.startsWith(level);
    return text.includes(level) && !/60帧|4K|2160/.test(text);
  };
  const items = () => [...document.querySelectorAll("li,button,a,div,span")].filter((node) => visible(node) && wants(tidy(node.innerText)) && tidy(node.innerText).length < 16);
  let found = items();
  if (!found.length) {
    const opener = [...document.querySelectorAll("button,div,span")].find((node) => {
      const text = tidy(node.innerText);
      return visible(node) && (text === "清晰度" || /1080|720|576|480|蓝光|超清|高清|标清/.test(text)) && text.length < 16;
    });
    if (opener) {
      opener.click();
      return "opened";
    }
  }
  const target = found.find((node) => tidy(node.innerText).length > 0);
  if (!target) return "missing";
  const current = tidy(target.innerText);
  if (wants(current) && /active|current|selected|on/i.test(String(target.className || ""))) return "same";
  target.click();
  return current || "ok";
})()
"""

_TOGGLE_JS = r"""
(() => {
  const request = __VALUE__;
  const tidy = (raw) => String(raw || "").replace(/\s+/g, "").trim();
  const nodes = [...document.querySelectorAll("button,[role='button'],div,span")].filter((node) => {
    const box = node.getBoundingClientRect();
    const text = tidy(node.innerText || node.getAttribute("aria-label") || node.getAttribute("title"));
    return box.width > 8 && box.height > 8 && text.includes(request.word) && text.length < 12;
  });
  if (!nodes.length) return "missing";
  const node = nodes[0];
  const blob = tidy(node.innerText) + String(node.className || "") + String(node.getAttribute("aria-pressed") || "") + String(node.getAttribute("aria-label") || "");
  const off = /off|close|closed|false|关闭|关弹幕|退出/.test(blob);
  const on = /is-on|opened|true|开启|打开/.test(blob) && !off;
  if (!on && !off) return "unknown";
  if (request.state === "on" && on) return "same";
  if (request.state === "off" && off) return "same";
  node.click();
  return "ok";
})()
"""

_SPEED_JS = r"""
(() => {
  const value = String(__VALUE__);
  const tidy = (raw) => String(raw || "").replace(/\s+/g, "").trim();
  const visible = (node) => {
    const box = node.getBoundingClientRect();
    return box.width > 8 && box.height > 8;
  };
  const match = (text) => value === "1" ? /^1(\.0)?(倍|x|X)?$|^正常|^原速/.test(text) : text.includes(value);
  let found = [...document.querySelectorAll("li,button,div,span")].filter((node) => visible(node) && match(tidy(node.innerText)) && tidy(node.innerText).length < 12);
  if (!found.length) {
    const opener = [...document.querySelectorAll("button,div,span")].find((node) => visible(node) && /倍速|倍$/.test(tidy(node.innerText)));
    if (opener) {
      opener.click();
      return "opened";
    }
  }
  if (!found.length) return "missing";
  found[0].click();
  return "ok";
})()
"""

_SCROLL_JS = r"""
(() => {
  const direction = __VALUE__;
  const nodes = [...document.querySelectorAll("body *")].filter((el) => {
    const style = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    return box.width > 80 && box.height > 80 && el.scrollHeight > el.clientHeight + 80
      && /auto|scroll|overlay/.test(style.overflowY);
  });
  const scroller = nodes.sort((a, b) => b.clientHeight - a.clientHeight)[0] || document.scrollingElement;
  if (!scroller) return "none";
  const before = scroller.scrollTop;
  const distance = Math.round(scroller.clientHeight * 2 / 3) * (direction === "up" ? -1 : 1);
  scroller.scrollBy({top: distance, behavior: "smooth"});
  const after = before + distance;
  if (direction === "up" && before <= 0) return "top";
  if (direction === "down" && before + scroller.clientHeight >= scroller.scrollHeight - 4) return "bottom";
  return after === before ? "none" : "ok";
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
    if intent == "exit_fullscreen":
        _exit_fullscreen(heard)
        return
    if intent == "switch_tab":
        _switch_channel(heard, str(command.get("tab") or ""))
        return
    if intent == "open_title":
        _open_title(heard, str(command.get("query") or ""))
        return
    if intent == "search":
        _search(heard, str(command.get("query") or ""))
        return
    if intent == "episode":
        _episode(heard, command)
        return
    if intent == "quality":
        _quality(heard, str(command.get("level") or ""))
        return
    if intent == "danmaku":
        _toggle_control(heard, "弹幕", str(command.get("state") or ""), "弹幕")
        return
    if intent == "pip":
        _toggle_control(heard, "小窗", str(command.get("state") or ""), "小窗")
        return
    if intent == "speed":
        _speed(heard, str(command.get("value") or ""))
        return
    if intent == "back":
        _back(heard)
        return
    if intent == "home":
        _switch_channel(heard, "首页")
        return
    if intent == "refresh":
        _refresh(heard)
        return
    if intent == "scroll":
        _scroll(heard, str(command.get("direction") or "down"))
        return
    startup_checks.log(f"听到「{heard}」，没有对上指令")


def _named(script: str, value) -> str:
    return script.replace("__VALUE__", json.dumps(value, ensure_ascii=False))


def _exit_fullscreen(heard: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，退出全屏，调试端口不可连接")
        return
    player = _player_page(pages)
    if player is None:
        startup_checks.log(f"听到「{heard}」，退出全屏，没有正在播放的视频")
        return
    if player.get("fullscreen") != "on":
        startup_checks.log(f"听到「{heard}」，退出全屏，已经是窗口观看")
        return
    _send_named(heard, "exit_fullscreen", "exit_fullscreen", done="已改为窗口观看")


def _switch_channel(heard: str, tab: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，切到{tab}，调试端口不可连接")
        return
    page = _nav_page(pages)
    if page is None:
        startup_checks.log(f"听到「{heard}」，切到{tab}，没有找到栏目")
        return
    if page.get("channel") == tab:
        startup_checks.log(f"听到「{heard}」，切到{tab}，已经在这个栏目")
        return
    result = _eval(page["ws"], _named(_CLICK_CHANNEL, tab))
    if result == "ok":
        startup_checks.log(f"听到「{heard}」，切到{tab}，已打开")
    elif result == "no-nav":
        startup_checks.log(f"听到「{heard}」，切到{tab}，没有找到栏目")
    else:
        startup_checks.log(f"听到「{heard}」，切到{tab}，没有这个栏目")


def _open_title(heard: str, query: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，打开「{query}」，调试端口不可连接")
        return
    cards = []
    for page in pages:
        for card in page.get("cards") or []:
            cards.append({**card, "ws": page["ws"]})
    if not cards:
        startup_checks.log(f"听到「{heard}」，打开「{query}」，当前页面没有已显示的标题")
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
    opened = _eval(chosen["ws"], _named(_CLICK_TITLE, chosen["title"]))
    if opened:
        startup_checks.log(f"听到「{heard}」，打开「{matched['title']}」，分数 {matched['score']}，已打开")
    else:
        startup_checks.log(f"听到「{heard}」，打开「{matched['title']}」，页面上没有点到")


def _search(heard: str, query: str) -> None:
    query = command_parser._clean_search(query)
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，搜索「{query}」，调试端口不可连接")
        return
    page = _nav_page(pages) or (pages[0] if pages else None)
    if page is None:
        startup_checks.log(f"听到「{heard}」，搜索「{query}」，找不到主页面")
        return
    if len(query) < 2:
        startup_checks.log(f"听到「{heard}」，搜索，没有找到要搜索的名称")
        return
    result = _eval(page["ws"], _named(_SEARCH_JS, query))
    if result == "ok":
        startup_checks.log(f"听到「{heard}」，搜索「{query}」，已打开搜索页")
    elif result == "no-input":
        startup_checks.log(f"听到「{heard}」，搜索「{query}」，没有找到搜索框")
    else:
        startup_checks.log(f"听到「{heard}」，搜索「{query}」，没有打开搜索页")


def _episode(heard: str, command: dict) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，选集，调试端口不可连接")
        return
    page = next((item for item in pages if item.get("episodes")), None)
    if page is None:
        startup_checks.log(f"听到「{heard}」，选集，没有正在播放的片子")
        return
    result = _eval(page["ws"], _named(_EPISODE_JS, {
        "direction": command.get("direction") or "",
        "number": int(command.get("number") or 0),
    }))
    if result == "ok":
        startup_checks.log(f"听到「{heard}」，选集，已打开")
    elif result == "edge":
        startup_checks.log(f"听到「{heard}」，选集，没有相邻的一集")
    elif result == "missing":
        startup_checks.log(f"听到「{heard}」，选集，没有这一集")
    else:
        startup_checks.log(f"听到「{heard}」，选集，没有正在播放的片子")


def _quality(heard: str, level: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，切换{level}，调试端口不可连接")
        return
    player = _player_page(pages)
    if player is None:
        startup_checks.log(f"听到「{heard}」，切换{level}，没有正在播放的视频")
        return
    result = _eval(player["ws"], _named(_QUALITY_JS, level))
    if result == "opened":
        time.sleep(0.25)
        result = _eval(player["ws"], _named(_QUALITY_JS, level))
    if result == "missing":
        startup_checks.log(f"听到「{heard}」，切换{level}，没有这个清晰度")
    elif result == "same":
        startup_checks.log(f"听到「{heard}」，已经是清晰度「{level}」")
    elif result:
        startup_checks.log(f"听到「{heard}」，已切换清晰度「{level}」")
    else:
        startup_checks.log(f"听到「{heard}」，切换{level}，没有这个清晰度")


def _toggle_control(heard: str, word: str, state: str, label: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，{label}，调试端口不可连接")
        return
    player = _player_page(pages)
    if player is None:
        startup_checks.log(f"听到「{heard}」，{label}，没有正在播放的视频")
        return
    result = _eval(player["ws"], _named(_TOGGLE_JS, {"word": word, "state": state}))
    if result == "ok":
        startup_checks.log(f"听到「{heard}」，{label}，已切换")
    elif result == "same":
        startup_checks.log(f"听到「{heard}」，{label}，已经是这个状态")
    elif result == "unknown":
        startup_checks.log(f"听到「{heard}」，{label}，看不出开关，没有点")
    else:
        startup_checks.log(f"听到「{heard}」，{label}，没有找到按钮")


def _speed(heard: str, value: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，倍速，调试端口不可连接")
        return
    player = _player_page(pages)
    if player is None:
        startup_checks.log(f"听到「{heard}」，倍速，没有正在播放的视频")
        return
    result = _eval(player["ws"], _named(_SPEED_JS, value))
    if result == "opened":
        time.sleep(0.25)
        result = _eval(player["ws"], _named(_SPEED_JS, value))
    if result == "ok":
        startup_checks.log(f"听到「{heard}」，倍速，已改成{value}倍")
    else:
        startup_checks.log(f"听到「{heard}」，倍速，没有这一档")


def _back(heard: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，返回，调试端口不可连接")
        return
    page = _nav_page(pages) or (pages[0] if pages else None)
    if page is None or int(page.get("history") or 0) <= 1:
        startup_checks.log(f"听到「{heard}」，返回，没有上一个页面")
        return
    _eval(page["ws"], "history.back()")
    startup_checks.log(f"听到「{heard}」，返回，已返回")


def _refresh(heard: str) -> None:
    pages = _pages()
    if pages is None:
        startup_checks.log(f"听到「{heard}」，刷新，调试端口不可连接")
        return
    page = _player_page(pages) or _nav_page(pages) or (pages[0] if pages else None)
    if page is None:
        startup_checks.log(f"听到「{heard}」，刷新，没有找到页面")
        return
    _eval(page["ws"], "location.reload()")
    startup_checks.log(f"听到「{heard}」，刷新，已刷新")


def _scroll(heard: str, direction: str) -> None:
    pages = _pages()
    label = "上滑" if direction == "up" else "下滑"
    if pages is None:
        startup_checks.log(f"听到「{heard}」，{label}，调试端口不可连接")
        return
    page = _player_page(pages) or _nav_page(pages) or (pages[0] if pages else None)
    if page is None:
        startup_checks.log(f"听到「{heard}」，{label}，没有可滚动的列表")
        return
    result = _eval(page["ws"], _named(_SCROLL_JS, direction))
    if result == "ok":
        startup_checks.log(f"听到「{heard}」，{label}，已滚动")
    elif result == "top":
        startup_checks.log(f"听到「{heard}」，{label}，已经到顶")
    elif result == "bottom":
        startup_checks.log(f"听到「{heard}」，{label}，已经到底")
    else:
        startup_checks.log(f"听到「{heard}」，{label}，没有可滚动的列表")


def _send_named(heard: str, intent: str, action: str, done: str = "已发送按键") -> None:
    names = startup_checks.client_keys("mango").get(action)
    if not isinstance(names, list) or not names or any(str(name).lower() not in VK for name in names):
        startup_checks.log(f"听到「{heard}」，意图 {intent}，快捷键无效")
        return
    if not _focus_client():
        startup_checks.log(f"听到「{heard}」，意图 {intent}，窗口没能置前")
        return
    for name in names:
        _tap(VK[str(name).lower()])
        time.sleep(0.05)
    startup_checks.log(f"听到「{heard}」，意图 {intent}，{done}")


def _window_title(hwnd) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _process_name() -> str:
    return str(startup_checks._client_settings("mango").get("process") or "芒果TV.exe")


def _client_hwnd():
    pids = set(startup_checks._pids_named(_process_name()))
    if not pids:
        return None
    chosen = {"hwnd": None, "area": -1}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids or _user32.GetWindowTextLengthW(hwnd) <= 0:
            return True
        if "芒果" not in _window_title(hwnd):
            return True
        iconic = bool(_user32.IsIconic(hwnd))
        if not _user32.IsWindowVisible(hwnd) and not iconic:
            return True
        rect = wintypes.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        if chosen["hwnd"] is None or area > chosen["area"]:
            chosen["hwnd"] = hwnd
            chosen["area"] = area
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
    return pid.value in set(startup_checks._pids_named(_process_name()))


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


def _nav_page(pages: list[dict]) -> dict | None:
    return next((page for page in pages if page.get("channels")), None) or next(iter(pages), None)


def _player_page(pages: list[dict]) -> dict | None:
    return next((page for page in pages if page.get("player") or page.get("episodes") or "player" in str(page.get("href") or "")), None)


def _channel_name(raw: str) -> str:
    text = str(raw or "").strip()
    return _CHANNEL_KEY.get(text, text)


def _pages() -> list[dict] | None:
    port = startup_checks.client_port("mango")
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
            "title": value.get("title") or "",
            "history": value.get("history") or 0,
            "channel": _channel_name(value.get("channel") or ""),
            "channels": value.get("channels") or [],
            "cards": value.get("cards") or [],
            "episodes": value.get("episodes") or [],
            "fullscreen": value.get("fullscreen") or "off",
            "player": bool(value.get("player")),
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
