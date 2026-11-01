"""把转写出来的一句话拆成指令，并按模糊匹配挑选视频标题。"""

from __future__ import annotations

import re

from pypinyin import Style, lazy_pinyin
from rapidfuzz import fuzz

WAKE = "猫师傅"
TABS = ("直播", "推荐", "热门", "追番", "影视")

_SPACE = re.compile(r"\s+")
_TAG = re.compile(r"<\|.*?\|>")
_EDGE = re.compile(r"^[，,。！？、!?.；;：:\s]+|[，,。！？、!?.；;：:\s]+$")
_COMPACT = re.compile(r"[\s，。！？、,.!?:：;；\"'“”‘’（）()【】\[\]《》<>\-—_]+")
_TAB = re.compile(rf"(?:切到|切换到|换到|打开|看)({'|'.join(TABS)})$")
_NEXT = re.compile(r"(下一个|下一条|下一集|我要看下一个|看下一个)")
_PREV = re.compile(r"(上一个|上一条|上一集|我要看上一个|看上一个)")
_HOME = re.compile(r"(返回主界面|回到主界面|回主界面|返回主页|回到主页|回主页|返回首页|回到首页|回首页)")
_BACK = re.compile(r"^(?:请)?(?:返回上一页|回到上一页|返回上一个页面|返回|回去)$")
_PLAY = re.compile(r"^(?:请|帮我)?(?:把视频)?(?:暂停|播放|继续)(?:一下|吧|视频)?$")
_TITLE_PREFIX = re.compile(r"^(?:我要看|我想看|想看|给我放|给我看|请播放|请打开|播放|打开|看)")
_TITLE_SUFFIX = re.compile(r"(?:这个视频|那条视频|视频)$")


def parse(text: str) -> list[dict]:
    cleaned = _SPACE.sub("", _TAG.sub("", text or ""))
    if WAKE not in cleaned:
        return []
    commands = []
    for part in cleaned.split(WAKE)[1:]:
        segment = _EDGE.sub("", part)
        commands.append(parse_one(segment))
    return commands


def parse_one(segment: str) -> dict:
    segment = _EDGE.sub("", segment or "")
    if not segment:
        return {"intent": "unknown", "text": ""}
    if _HOME.search(segment):
        return {"intent": "home", "text": segment}
    if _BACK.fullmatch(segment):
        return {"intent": "back", "text": segment}
    tab = _TAB.search(segment)
    if tab and "右边" not in segment:
        return {"intent": "switch_tab", "tab": tab.group(1), "text": segment}
    if _NEXT.search(segment) and "右边" not in segment:
        return {"intent": "adjacent", "direction": "next", "text": segment}
    if _PREV.search(segment) and "右边" not in segment:
        return {"intent": "adjacent", "direction": "previous", "text": segment}
    if "我要看" not in segment and "右边" not in segment:
        if "全屏" in segment:
            return {"intent": "fullscreen", "text": segment}
        if re.search(r"静音", segment):
            return {"intent": "volume", "direction": "mute", "text": segment}
        if re.search(r"大声|音量加|声音大|音量调高|调大", segment):
            return {"intent": "volume", "direction": "up", "text": segment}
        if re.search(r"小声|音量减|声音小|音量调低|调小", segment):
            return {"intent": "volume", "direction": "down", "text": segment}
        if _PLAY.fullmatch(segment):
            return {"intent": "toggle_play", "text": segment}
    query = _title_query(segment)
    if not query:
        return {"intent": "unknown", "text": segment}
    return {
        "intent": "open_title",
        "scope": "right" if "右边" in segment else "cards",
        "query": query,
        "text": segment,
    }


def _title_query(segment: str) -> str:
    text = segment
    if "右边" in text:
        text = re.sub(r"^.*右边的", "", text)
    text = _TITLE_PREFIX.sub("", text)
    text = _TITLE_SUFFIX.sub("", text)
    return _EDGE.sub("", text)


def match_title(query: str, titles: list[str], threshold: float, margin: float) -> dict:
    wanted = _COMPACT.sub("", query or "").lower()
    empty = {"ok": False, "title": "", "score": 0.0, "second": "", "second_score": 0.0}
    if len(wanted) < 2:
        return empty
    best_by_title: dict[str, float] = {}
    for title in titles:
        name = str(title or "").strip()
        compact = _COMPACT.sub("", name).lower()
        if len(compact) < 2:
            continue
        score = _pair_score(wanted, compact)
        current = best_by_title.get(name)
        if current is None or score > current:
            best_by_title[name] = score
    ranked = sorted(best_by_title.items(), key=lambda item: item[1], reverse=True)
    if not ranked:
        return empty
    title, score = ranked[0]
    second, second_score = ranked[1] if len(ranked) > 1 else ("", 0.0)
    ok = score >= threshold and (not second or score - second_score >= margin)
    return {
        "ok": ok,
        "title": title,
        "score": round(score, 3),
        "second": second,
        "second_score": round(second_score, 3),
    }


def _pair_score(query: str, title: str) -> float:
    characters = max(fuzz.ratio(query, title), fuzz.partial_ratio(query, title)) / 100
    query_pinyin = "".join(lazy_pinyin(query, style=Style.NORMAL, errors="ignore"))
    title_pinyin = "".join(lazy_pinyin(title, style=Style.NORMAL, errors="ignore"))
    phonetic = 0.0
    if query_pinyin and title_pinyin:
        phonetic = max(fuzz.ratio(query_pinyin, title_pinyin), fuzz.partial_ratio(query_pinyin, title_pinyin)) / 100
    return max(characters, phonetic)
