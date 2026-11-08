"""把转写出来的一句话拆成指令，并按模糊匹配挑选视频标题。"""

from __future__ import annotations

import re

from pypinyin import Style, lazy_pinyin
from rapidfuzz import fuzz

WAKE = "猫师傅"
WAKE_ALIASES = ("猫师傅", "高师傅", "毛师傅", "喵师傅", "猫师父", "猫师付", "老师傅", "小猫", "猫猫")
TABS = ("直播", "推荐", "热门", "追番", "影视")

_SPACE = re.compile(r"\s+")
_TAG = re.compile(r"<\|.*?\|>")
_EDGE = re.compile(r"^[，,。！？、!?.；;：:\s]+|[，,。！？、!?.；;：:\s]+$")
_COMPACT = re.compile(r"[\s，。！？、,.!?:：;；\"'“”‘’（）()【】\[\]《》<>\-—_]+")
_TAB_TAIL = "(?:栏目|南目|南幕|蓝目|兰目|男目|拦目|栏木|南木|栏幕|频道|页面|页|区|栏)?"
_BREATH = re.compile(r"[嗯啊呃额哦噢喔哈呵哼嘿呀呐唔吖诶唉嗨]+")
_LOOSE_WAKE = re.compile(r"(?<![猫高毛喵老小])[A-Za-z哦喔欧奥噢啊额厄]?师傅")
_MISHEARD_FU = re.compile(r"(?<=[猫高毛喵老小])师普")
_TAB = re.compile(
    rf"^(?:请|帮我|给我|我要|我想)?(?:切换|切+|换到|换|打开|看一下|看|现在)"
    rf"(?:.(?=到|刀|倒|道))?(?:到|刀|倒|道)?({'|'.join(TABS)}){_TAB_TAIL}(?:一下|吧)?$"
)
_NEXT = re.compile(r"(下一个|下一条|下一集|我要看下一个|看下一个)")
_PREV = re.compile(r"(上一个|上一条|上一集|我要看上一个|看上一个)")
_HOME = re.compile(
    r"^(?:请|帮我|给我)?(?:现在|在)?(?:返回|回到|回)+(?:一下|到|去)?(?:主界面|主页面|主页|首页面|首页)(?:一下|吧|去|了)?$"
)
_REFRESH = re.compile(
    r"^(?:请|帮我|给我)?(?:刷新)+(?:一下)?(?:当前页面|这个页面|当前页|页面)?(?:一下|吧)?$"
)
_SCROLL = re.compile(
    r"^(?:请|帮我|给我)?(?:往上|向上|上|往下|向下|下)(?:滑|滚动|滚)(?:一下|页面|列表|吧)?$"
)
_BACK = re.compile(r"^(?:请)?(?:返回上一页|回到上一页|返回上一个页面|返回|回去)$")
_CLOSE = re.compile(
    r"^(?:请|帮我|给我)?(?:把)?(?:现在|在)?(?:关闭|关掉|关上|关了)+(?:一下|掉|了|吧)?(?:当前|这个|正在看的|正在播放的)?视频(?:窗口)?(?:一下|吧)?$"
    r"|^(?:请|帮我|给我)?(?:把)?(?:当前|这个|正在看的|正在播放的)视频(?:窗口)?(?:关闭|关掉|关上|关了)(?:一下|吧|了)?$"
)
_PLAY = re.compile(r"^(?:请|帮我|给我)?(?:把视频)?(?:现在|在)?(?:暂停|播放|继续)+(?:一下|吧|视频|了|啊)?$")
_EXIT_FULL = re.compile(
    r"^(?:请|帮我|给我)?(?:现在|在)?(?:退出|取消|关闭|关掉)全屏(?:幕)?(?:观看|播放|模式)?(?:一下|吧)?$"
    r"|^(?:请|帮我|给我)?窗口观看(?:一下|吧)?$"
)
_LIVE_OPEN = re.compile(
    r"^(?:请|帮我|给我)?(?:我要|我想)?(?:看|打开|播放)(?:一下)?(.+?)(?:这场|这个|的)?直播(?:间)?(?:一下|吧)?$"
)
_SEARCH = re.compile(
    r"^(?:请|帮我|给我)?(?:我要|我想)?(?:搜索|搜一下|搜)(?:一下)?(.+?)(?:这个|的)?(?:视频)?(?:一下|吧)?$"
)
_SEARCH_LEAD = re.compile(r"^(?:搜索|搜一下|搜)")
_QUALITY = re.compile(
    r"^(?:请|帮我|给我)?(?:把)?(?:视频)?(?:切换|切到|换成|换到|调到|调成|设置)?(?:清晰度|画质|分辨率)?(?:到|为|成)?"
    r"(1080\s*[Pp]|1080|720\s*[Pp]|720|一零八零|一千零八十|七二零|七百二十)"
    r"(?:清晰度|画质)?(?:一下|吧)?$",
    re.IGNORECASE,
)
_TITLE_PREFIX = re.compile(r"^(?:我要看|我想看|想看|给我放|给我看|请播放|请打开|播放|打开|看)")
_TITLE_SUFFIX = re.compile(r"(?:这个视频|那条视频|视频)$")


def _mend_wake(text: str) -> str:
    return _MISHEARD_FU.sub("师傅", text)


def has_wake(text: str) -> bool:
    cleaned = _mend_wake(_SPACE.sub("", _TAG.sub("", text or "")))
    if any(alias in cleaned for alias in WAKE_ALIASES) or "师傅" in cleaned:
        return True
    return _phonetic_wake_span(cleaned) is not None


def _normalize_wake(text: str) -> str:
    aliases = sorted(WAKE_ALIASES, key=len, reverse=True)
    pieces = []
    index = 0
    while index < len(text):
        alias = next((item for item in aliases if text.startswith(item, index)), "")
        if alias:
            pieces.append(WAKE)
            index += len(alias)
        else:
            pieces.append(text[index])
            index += 1
    normalized = _LOOSE_WAKE.sub(WAKE, "".join(pieces))
    if WAKE in normalized:
        return normalized
    span = _phonetic_wake_span(normalized)
    if span is None:
        return normalized
    start, end = span
    return normalized[:start] + WAKE + normalized[end + 1:]


def parse(text: str, platform: str = "bilibili") -> list[dict]:
    cleaned = _mend_wake(_SPACE.sub("", _TAG.sub("", text or "")))
    if not has_wake(cleaned):
        return []
    reader = parse_mango_one if platform == "mango" else parse_one
    commands = []
    for part in _normalize_wake(cleaned).split(WAKE)[1:]:
        segment = _EDGE.sub("", part)
        commands.append(reader(segment))
    return commands


_PINYIN_INITIALS = (
    "zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l",
    "g", "k", "h", "j", "q", "x", "r", "z", "c", "s", "y", "w",
)
_SIBILANT = set("j q x zh ch sh z c s".split())
_UNASPIRATED = {"b": "b", "p": "b", "d": "d", "t": "d", "g": "g", "k": "g"}
_NASAL_FINAL = {
    "ang": "an", "an": "an", "eng": "en", "en": "en", "ing": "in", "in": "in",
    "iang": "ian", "ian": "ian", "uang": "uan", "uan": "uan",
}
_SOUND_ACCEPT = 88.0
_SOUND_MARGIN = 8.0
_WAKE_ACCEPT = 87.0
_CLIP = re.compile(r"^(?:请|帮我|给我|现在)+|(?:一下|吧|了|啊|呀|哦|噢)+$")
_CATALOG: dict[str, list[tuple[dict, str, str]]] = {}


def _soften(segment: str) -> str:
    text = _BREATH.sub("", segment)
    return re.sub(r"(.)\1+", r"\1", text)


def _syllables(text: str, dedupe: bool) -> list[str]:
    sounds = []
    for sound in lazy_pinyin(text or "", style=Style.NORMAL, errors="ignore"):
        if not sound.isalpha():
            continue
        if dedupe and sounds and sounds[-1] == sound:
            continue
        sounds.append(sound)
    return sounds


def _skeleton(syllable: str) -> str:
    initial = next((item for item in _PINYIN_INITIALS if syllable.startswith(item) and len(syllable) > len(item)), "")
    final = syllable[len(initial):]
    if initial in _SIBILANT:
        initial = "z"
    else:
        initial = _UNASPIRATED.get(initial, initial)
    return initial + _NASAL_FINAL.get(final, final)


def _sound_forms(text: str, dedupe: bool) -> tuple[str, str]:
    sounds = _syllables(text, dedupe)
    return "".join(sounds), "".join(_skeleton(item) for item in sounds)


def _sound_score(heard_raw: str, heard_skel: str, wanted_raw: str, wanted_skel: str) -> float:
    if not heard_raw or not wanted_raw:
        return 0.0
    raw = fuzz.ratio(heard_raw, wanted_raw)
    if raw < 70:
        return raw
    return max(raw, fuzz.ratio(heard_skel, wanted_skel))


def _clip_phrase(text: str) -> str:
    clipped = (text or "").replace("一下", "")
    while True:
        nxt = _CLIP.sub("", clipped)
        if nxt == clipped:
            return nxt
        clipped = nxt


def _phonetic_wake_span(text: str) -> tuple[int, int] | None:
    han = [(index, char) for index, char in enumerate(text) if "\u4e00" <= char <= "\u9fff"]
    best: tuple[float, int, int] | None = None
    for alias in WAKE_ALIASES:
        size = len(alias)
        if len(han) < size:
            continue
        window = han[:size]
        heard, _ = _sound_forms("".join(char for _, char in window), False)
        score = fuzz.ratio(heard, _sound_forms(alias, False)[0])
        if score >= _WAKE_ACCEPT and (best is None or score > best[0]):
            best = (score, window[0][0], window[-1][0])
    if best is None:
        return None
    return best[1], best[2]


def _remember(rows: list, payload: dict, phrases: tuple[str, ...]) -> None:
    for phrase in phrases:
        raw, skel = _sound_forms(phrase, True)
        if len(_syllables(phrase, True)) < 2:
            continue
        rows.append((payload, raw, skel))


def _build_catalog(platform: str) -> list[tuple[dict, str, str]]:
    rows: list[tuple[dict, str, str]] = []
    _remember(rows, {"intent": "exit_fullscreen"}, ("关闭全屏", "关掉全屏", "关上全屏", "退出全屏", "取消全屏", "窗口观看"))
    _remember(rows, {"intent": "fullscreen"}, ("全屏", "打开全屏", "进入全屏", "切到全屏", "全屏观看", "全屏播放"))
    _remember(rows, {"intent": "toggle_play"}, ("暂停", "播放", "继续", "暂停播放", "继续播放"))
    _remember(rows, {"intent": "volume", "direction": "mute"}, ("静音", "关闭声音", "关掉声音"))
    _remember(rows, {"intent": "volume", "direction": "up"}, ("大声", "音量加", "声音大", "音量调高", "调大"))
    _remember(rows, {"intent": "volume", "direction": "down"}, ("小声", "音量减", "声音小", "音量调低", "调小"))
    _remember(rows, {"intent": "back"}, ("返回", "回去", "返回上一页", "回到上一页"))
    _remember(rows, {"intent": "home"}, ("返回首页", "回到首页", "返回主页", "回到主页", "回首页", "回主页", "返回主界面"))
    _remember(rows, {"intent": "refresh"}, ("刷新", "刷新页面", "刷新当前页面"))
    _remember(rows, {"intent": "scroll", "direction": "up"}, ("上滑", "往上滑", "向上滑", "上滚", "往上滚"))
    _remember(rows, {"intent": "scroll", "direction": "down"}, ("下滑", "往下滑", "向下滑", "下滚", "往下滚"))
    if platform != "mango":
        _remember(rows, {"intent": "close_video"}, ("关闭当前视频", "关掉当前视频", "关闭视频", "关掉视频", "关闭当前窗口"))
    tabs = _MANGO_TABS if platform == "mango" else TABS
    for tab in tabs:
        _remember(
            rows,
            {"intent": "switch_tab", "tab": tab},
            (f"切到{tab}", f"切换到{tab}", f"换到{tab}", f"打开{tab}", f"切{tab}", f"看{tab}"),
        )
    if platform == "mango":
        _remember(rows, {"intent": "danmaku", "state": "on"}, ("开弹幕", "打开弹幕", "开启弹幕"))
        _remember(rows, {"intent": "danmaku", "state": "off"}, ("关弹幕", "关闭弹幕", "关掉弹幕"))
        _remember(rows, {"intent": "pip", "state": "on"}, ("小窗", "小窗播放", "打开小窗"))
        _remember(rows, {"intent": "pip", "state": "off"}, ("关闭小窗", "退出小窗", "关掉小窗"))
        _remember(rows, {"intent": "speed", "value": "1.5"}, ("一点五倍", "一点五倍速"))
        _remember(rows, {"intent": "speed", "value": "2"}, ("两倍", "二倍", "两倍速", "二倍速"))
        _remember(rows, {"intent": "speed", "value": "1"}, ("正常速度", "原速", "一倍速"))
        _remember(rows, {"intent": "episode", "direction": "next"}, ("下一集",))
        _remember(rows, {"intent": "episode", "direction": "previous"}, ("上一集",))
    else:
        _remember(rows, {"intent": "adjacent", "direction": "next"}, ("下一个", "下一条", "下一集"))
        _remember(rows, {"intent": "adjacent", "direction": "previous"}, ("上一个", "上一条", "上一集"))
    return rows


def _catalog(platform: str) -> list[tuple[dict, str, str]]:
    found = _CATALOG.get(platform)
    if found is None:
        found = _build_catalog(platform)
        _CATALOG[platform] = found
    return found


def _rescue(text: str, platform: str) -> dict | None:
    clipped = _clip_phrase(text)
    if re.search(r"\d", clipped):
        return None
    heard = _syllables(clipped, True)
    if len(heard) < 2 or len(heard) > 8:
        return None
    heard_raw, heard_skel = _sound_forms(clipped, True)
    best: dict[tuple, tuple[float, dict]] = {}
    for payload, raw, skel in _catalog(platform):
        score = _sound_score(heard_raw, heard_skel, raw, skel)
        key = tuple(sorted(payload.items()))
        current = best.get(key)
        if current is None or score > current[0]:
            best[key] = (score, payload)
    ranked = sorted(best.values(), key=lambda item: item[0], reverse=True)
    if not ranked or ranked[0][0] < _SOUND_ACCEPT:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < _SOUND_MARGIN:
        return None
    return dict(ranked[0][1])


def parse_one(segment: str) -> dict:
    segment = _COMPACT.sub("", _EDGE.sub("", segment or ""))
    if not segment:
        return {"intent": "unknown", "text": ""}
    softened = _soften(segment)
    if _HOME.fullmatch(softened):
        return {"intent": "home", "text": segment}
    if _REFRESH.fullmatch(softened):
        return {"intent": "refresh", "text": segment}
    scroll = _SCROLL.fullmatch(softened)
    if scroll:
        direction = "up" if "上" in softened else "down"
        return {"intent": "scroll", "direction": direction, "text": segment}
    if _CLOSE.fullmatch(softened):
        return {"intent": "close_video", "text": segment}
    if _BACK.fullmatch(softened):
        return {"intent": "back", "text": segment}
    tab = _TAB.fullmatch(softened)
    if tab and "右边" not in segment:
        return {"intent": "switch_tab", "tab": tab.group(1), "text": segment}
    if _NEXT.search(segment) and "右边" not in segment:
        return {"intent": "adjacent", "direction": "next", "text": segment}
    if _PREV.search(segment) and "右边" not in segment:
        return {"intent": "adjacent", "direction": "previous", "text": segment}
    if "我要看" not in segment and "右边" not in segment:
        rescued = _rescue(softened, "bilibili")
        if rescued:
            rescued["text"] = segment
            return rescued
        if _EXIT_FULL.fullmatch(softened):
            return {"intent": "exit_fullscreen", "text": segment}
        if "全屏" in segment:
            return {"intent": "fullscreen", "text": segment}
        if re.search(r"静音", segment):
            return {"intent": "volume", "direction": "mute", "text": segment}
        if re.search(r"大声|音量加|声音大|音量调高|调大", segment):
            return {"intent": "volume", "direction": "up", "text": segment}
        if re.search(r"小声|音量减|声音小|音量调低|调小", segment):
            return {"intent": "volume", "direction": "down", "text": segment}
        if _PLAY.fullmatch(softened):
            return {"intent": "toggle_play", "text": segment}
    quality = _QUALITY.fullmatch(softened)
    if quality and "右边" not in segment and "我要看" not in segment:
        return {"intent": "quality", "level": _quality_level(quality.group(1)), "text": segment}
    found = _SEARCH.fullmatch(softened)
    if found and "右边" not in segment:
        name = _clean_search(found.group(1))
        if len(_COMPACT.sub("", name)) >= 2 and name != "视频":
            return {"intent": "search", "query": name, "text": segment}
    live = _LIVE_OPEN.fullmatch(softened)
    if live and "右边" not in segment:
        name = _EDGE.sub("", live.group(1))
        if len(_COMPACT.sub("", name)) >= 2:
            return {"intent": "open_live", "query": name, "text": segment}
    query = _title_query(segment)
    if not query:
        return {"intent": "unknown", "text": segment}
    return {
        "intent": "open_title",
        "scope": "right" if "右边" in segment else "cards",
        "query": query,
        "text": segment,
    }


_MANGO_TABS = ("本地播放", "纪录片", "电视剧", "综艺", "电影", "动漫", "少儿", "直播", "首页")
_MANGO_TAB = re.compile(
    rf"^(?:请|帮我|给我|我要|我想)?(?:切换|切+|换到|换|打开|看一下|看|现在)"
    rf"(?:.(?=到|刀|倒|道))?(?:到|刀|倒|道)?({'|'.join(_MANGO_TABS)}){_TAB_TAIL}(?:一下|吧)?$"
)
_EPISODE = re.compile(
    r"^(?:请|帮我|给我)?(?:我要看|我想看|播放|打开|看)?第([0-9]{1,3}|[零一二三四五六七八九十两]+)集(?:一下|吧)?$"
    r"|^(?:请|帮我|给我)?(?:播放)?(?:下一集|上一集)(?:一下|吧)?$"
)
_DANMAKU = re.compile(r"^(?:请|帮我|给我)?(?:把)?(?:弹幕)?(?:打开|开启|关掉|关闭|开|关)(?:一下|掉)?弹幕(?:一下|吧)?$")
_PIP = re.compile(r"^(?:请|帮我|给我)?(?:现在|在)?(?:退出|关闭|关掉|取消)?小窗(?:播放|模式)?(?:一下|吧)?$")
_SPEED = re.compile(
    r"^(?:请|帮我|给我)?(?:把)?(?:播放)?(?:速度|倍速)?(?:切换|切到|换成|调到|调成|设为|设置为)?"
    r"(15倍速|15倍|1\.5倍速|1\.5倍|一点五倍速|一点五倍|二倍速|两倍速|2倍速|2倍|两倍|二倍|正常速度|原速|一倍速|1倍速|1倍)"
    r"(?:播放)?(?:一下|吧)?$"
)
_MANGO_QUALITY = re.compile(
    r"^(?:请|帮我|给我)?(?:把)?(?:视频)?(?:切换|切到|换成|换到|调到|调成|设置)?(?:清晰度|画质|分辨率)?(?:到|为|成)?"
    r"(1080\s*[Pp]|1080|720\s*[Pp]|720|576\s*[Pp]|576|480\s*[Pp]|480|"
    r"一零八零|一千零八十|七二零|七百二十|五七六|五百七十六|四八零|四百八十|蓝光|超清|高清|标清)"
    r"(?:清晰度|画质)?(?:一下|吧)?$",
    re.IGNORECASE,
)


def parse_mango_one(segment: str) -> dict:
    segment = _COMPACT.sub("", _EDGE.sub("", segment or ""))
    if not segment:
        return {"intent": "unknown", "text": ""}
    softened = _soften(segment)
    if _HOME.fullmatch(softened):
        return {"intent": "home", "text": segment}
    if _REFRESH.fullmatch(softened):
        return {"intent": "refresh", "text": segment}
    scroll = _SCROLL.fullmatch(softened)
    if scroll:
        direction = "up" if "上" in softened else "down"
        return {"intent": "scroll", "direction": direction, "text": segment}
    if _BACK.fullmatch(softened):
        return {"intent": "back", "text": segment}
    tab = _MANGO_TAB.fullmatch(softened)
    if tab and "右边" not in segment:
        return {"intent": "switch_tab", "tab": tab.group(1), "text": segment}
    episode = _EPISODE.fullmatch(softened)
    if episode and "右边" not in segment:
        if "上一集" in softened:
            return {"intent": "episode", "direction": "previous", "text": segment}
        if "下一集" in softened:
            return {"intent": "episode", "direction": "next", "text": segment}
        return {"intent": "episode", "number": _cn_number(episode.group(1)), "text": segment}
    if "我要看" not in segment and "右边" not in segment:
        rescued = _rescue(softened, "mango")
        if rescued:
            rescued["text"] = segment
            return rescued
        if _EXIT_FULL.fullmatch(softened):
            return {"intent": "exit_fullscreen", "text": segment}
        if "全屏" in segment:
            return {"intent": "fullscreen", "text": segment}
        if re.search(r"静音", segment):
            return {"intent": "volume", "direction": "mute", "text": segment}
        if re.search(r"大声|音量加|声音大|音量调高|调大", segment):
            return {"intent": "volume", "direction": "up", "text": segment}
        if re.search(r"小声|音量减|声音小|音量调低|调小", segment):
            return {"intent": "volume", "direction": "down", "text": segment}
        if _PLAY.fullmatch(softened):
            return {"intent": "toggle_play", "text": segment}
        if _DANMAKU.fullmatch(softened):
            opened = "开" in softened and "关" not in softened
            return {"intent": "danmaku", "state": "on" if opened else "off", "text": segment}
        if _PIP.fullmatch(softened):
            opened = not re.match(r"^(?:请|帮我|给我)?(?:现在|在)?(?:退出|关闭|关掉|取消)", softened)
            return {"intent": "pip", "state": "on" if opened else "off", "text": segment}
        speed = _SPEED.fullmatch(softened)
        if speed:
            return {"intent": "speed", "value": _speed_value(speed.group(1)), "text": segment}
    quality = _MANGO_QUALITY.fullmatch(softened)
    if quality and "右边" not in segment and "我要看" not in segment:
        return {"intent": "quality", "level": _mango_quality(quality.group(1)), "text": segment}
    found = _SEARCH.fullmatch(softened)
    if found and "右边" not in segment:
        name = _clean_search(found.group(1))
        if len(_COMPACT.sub("", name)) >= 2 and name != "视频":
            return {"intent": "search", "query": name, "text": segment}
    query = _title_query(segment)
    if not query:
        return {"intent": "unknown", "text": segment}
    return {"intent": "open_title", "query": query, "text": segment}


def _cn_number(token: str) -> int:
    text = str(token or "")
    if text.isdigit():
        return int(text)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = digits.get(left, 1 if not left else 0)
        ones = digits.get(right, 0) if right else 0
        if left and left not in digits:
            return 0
        return tens * 10 + ones
    return digits.get(text, 0)


def _speed_value(token: str) -> str:
    text = token or ""
    if "1.5" in text or "一点五" in text or text.startswith("15"):
        return "1.5"
    if text.startswith(("二", "两", "2")):
        return "2"
    return "1"


def _mango_quality(token: str) -> str:
    text = re.sub(r"\s+", "", token or "")
    named = {"蓝光": "蓝光", "超清": "超清", "高清": "高清", "标清": "标清"}
    if text in named:
        return named[text]
    upper = text.upper()
    if upper.startswith("1080") or text in {"一零八零", "一千零八十"}:
        return "1080"
    if upper.startswith("720") or text in {"七二零", "七百二十"}:
        return "720"
    if upper.startswith("576") or text in {"五七六", "五百七十六"}:
        return "576"
    if upper.startswith("480") or text in {"四八零", "四百八十"}:
        return "480"
    return text


def _clean_search(name: str) -> str:
    text = _EDGE.sub("", name or "")
    while True:
        nxt = _SEARCH_LEAD.sub("", text, count=1)
        if nxt == text:
            break
        text = nxt
    return _EDGE.sub("", _TITLE_SUFFIX.sub("", text))


def _quality_level(token: str) -> str:
    text = re.sub(r"\s+", "", token or "").upper()
    if text.startswith("1080") or text in {"一零八零", "一千零八十"}:
        return "1080"
    return "720"


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
