"""启动前四项检查，以及各自的修复动作。"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tarfile
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

import ctypes
import sounddevice as sd

APP_DIR = Path(os.environ["APPDATA"]) / "VideoAssist"
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "assist.log"
MODEL_DIR = APP_DIR / "models"

DEFAULT_CONFIG = {
    "wake_word": "猫师傅",
    "endpoint_silence_ms": 700,
    "max_utterance_ms": 8000,
    "match_threshold": 0.72,
    "match_margin": 0.05,
    "debug_port": 9222,
    "tabs": ["直播", "推荐", "热门", "追番", "影视"],
    "process": "哔哩哔哩.exe",
    "client_path": "",
    "keys": {
        "toggle_play": ["space"],
        "fullscreen": ["f"],
        "volume_up": ["up"],
        "volume_down": ["down"],
        "mute": ["m"],
    },
    "mango": {
        "debug_port": 9333,
        "process": "芒果TV.exe",
        "client_path": "",
        "keys": {
            "toggle_play": ["space"],
            "fullscreen": ["f"],
            "exit_fullscreen": ["escape"],
            "volume_up": ["up"],
            "volume_down": ["down"],
            "mute": ["m"],
        },
    },
}

KWS_DIR = "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
SENSE_DIR = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
KWS_FILES = {
    "tokens.txt": 500,
    "encoder-epoch-12-avg-2-chunk-16-left-64.onnx": 1_000_000,
    "decoder-epoch-12-avg-2-chunk-16-left-64.onnx": 100_000,
    "joiner-epoch-12-avg-2-chunk-16-left-64.onnx": 50_000,
}
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _logger() -> logging.Logger:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("videoassist")
    if not logger.handlers:
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def log(message: str) -> None:
    _logger().info(message)


def load_config() -> dict:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"配置读取失败：{exc}")
        return json.loads(json.dumps(DEFAULT_CONFIG))
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    if isinstance(data, dict):
        merged.update(data)
    return merged


def _client_settings(platform: str) -> dict:
    if platform != "mango":
        config = load_config()
        try:
            port = int(config.get("debug_port") or 9222)
        except (TypeError, ValueError):
            port = 9222
        return {
            "platform": "bilibili",
            "label": "哔哩哔哩",
            "process": str(config.get("process") or "哔哩哔哩.exe"),
            "debug_port": port,
            "client_path": str(config.get("client_path") or ""),
            "keys": config.get("keys") if isinstance(config.get("keys"), dict) else {},
        }
    base = json.loads(json.dumps(DEFAULT_CONFIG["mango"]))
    custom = load_config().get("mango")
    if isinstance(custom, dict):
        keys = dict(base.get("keys") or {})
        if isinstance(custom.get("keys"), dict):
            keys.update(custom["keys"])
        base.update(custom)
        base["keys"] = keys
    try:
        base["debug_port"] = int(base.get("debug_port") or 9333)
    except (TypeError, ValueError):
        base["debug_port"] = 9333
    base["platform"] = "mango"
    base["label"] = "芒果TV"
    base["process"] = str(base.get("process") or "芒果TV.exe")
    return base


def _process_name() -> str:
    return _client_settings("bilibili")["process"]


def _debug_port() -> int:
    return int(_client_settings("bilibili")["debug_port"])


def client_port(platform: str = "bilibili") -> int:
    return int(_client_settings(platform)["debug_port"])


def client_keys(platform: str = "bilibili") -> dict:
    keys = _client_settings(platform).get("keys")
    return keys if isinstance(keys, dict) else {}


def _pids_named(image_name: str) -> list[int]:
    kernel = ctypes.windll.kernel32
    snapshot = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    found: list[int] = []
    try:
        has_next = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while has_next:
            if entry.szExeFile.lower() == image_name.lower():
                found.append(int(entry.th32ProcessID))
            has_next = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return found


def _executable_path(pid: int) -> str:
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel.CloseHandle(handle)


def _has_main_window(pids: set[int]) -> bool:
    if not pids:
        return False
    user32 = ctypes.windll.user32
    found = False

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        nonlocal found
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        if not (user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd)):
            return True
        if user32.GetWindowTextLengthW(hwnd) > 0:
            found = True
            return False
        return True

    user32.EnumWindows(visit, 0)
    return found


def _port_open(port: int) -> bool:
    url = f"http://127.0.0.1:{port}/json"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            payload = response.read(4096)
        return payload.lstrip().startswith(b"[")
    except OSError:
        return False


def client_status(platform: str = "bilibili") -> dict:
    settings = _client_settings(platform)
    name = settings["process"]
    pids = _pids_named(name)
    window = _has_main_window(set(pids))
    port = _port_open(int(settings["debug_port"]))
    return {"running": bool(pids), "window": window, "port": port}


def check_client(platform: str = "bilibili") -> dict:
    settings = _client_settings(platform)
    status = client_status(platform)
    ok = status["window"] and status["port"]
    label = settings["label"]
    if ok:
        log("客户端检查通过" if platform != "mango" else "芒果TV客户端检查通过")
    elif not status["running"]:
        log(f"客户端检查失败：{label}没有启动")
    elif not status["window"]:
        log(f"客户端检查失败：没有找到{label}窗口")
    else:
        log("客户端检查失败：调试端口不可连接" if platform != "mango" else "芒果TV客户端检查失败：调试端口不可连接")
    return {"ok": ok, **status}


def _configured_client_path(platform: str = "bilibili") -> Path | None:
    raw = str(_client_settings(platform).get("client_path") or "").strip()
    if raw and Path(raw).is_file():
        return Path(raw)
    return None


def _installed_client_path(platform: str = "bilibili") -> Path | None:
    configured = _configured_client_path(platform)
    if configured:
        return configured
    settings = _client_settings(platform)
    name = settings["process"]
    for pid in _pids_named(name):
        path = _executable_path(pid)
        if path and Path(path).is_file():
            return Path(path)
    if platform == "mango":
        roots = [
            Path(r"F:\mangguoTV\MGTVPCC"),
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "MGTVPCC",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "MGTVPCC",
            Path(os.environ.get("LOCALAPPDATA", "")) / "MGTVPCC",
        ]
    else:
        roots = [
            Path(r"E:\Program Files\bilibili"),
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "bilibili",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "bilibili",
            Path(os.environ.get("LOCALAPPDATA", "")) / "bilibili",
        ]
    for root in roots:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _close_client(platform: str = "bilibili") -> None:
    name = _client_settings(platform)["process"]
    subprocess.run(["taskkill", "/IM", name, "/T"], capture_output=True, timeout=8)
    deadline = time.time() + 4
    while time.time() < deadline and _pids_named(name):
        time.sleep(0.3)
    if _pids_named(name):
        subprocess.run(["taskkill", "/IM", name, "/T", "/F"], capture_output=True, timeout=8)
        time.sleep(0.6)


def _wait_until_ready(platform: str = "bilibili", timeout: float = 20) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client_status(platform)
        if status["window"] and status["port"]:
            return True
        time.sleep(0.4)
    return False


def restart_client(platform: str = "bilibili") -> dict:
    settings = _client_settings(platform)
    label = settings["label"]
    exe = _installed_client_path(platform)
    if exe is None:
        log(f"重启{label}失败：没有找到客户端程序")
        return {"ok": False}
    port = int(settings["debug_port"])
    if client_status(platform)["running"]:
        log(f"关闭当前{label}")
        _close_client(platform)
    log(f"通过调试端口启动{label}：{exe}")
    arguments = [str(exe)]
    if platform == "mango":
        arguments.append("src:10")
    arguments.append(f"--remote-debugging-port={port}")
    subprocess.Popen(arguments, cwd=str(exe.parent))
    ok = _wait_until_ready(platform)
    log(f"重启{label}成功" if ok else f"重启{label}失败：窗口或调试端口没有就绪")
    return {"ok": ok}


def _input_devices() -> list[dict]:
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        pass
    devices = sd.query_devices()
    return [item for item in devices if int(item.get("max_input_channels") or 0) > 0]


def check_microphone() -> dict:
    try:
        inputs = _input_devices()
    except Exception as exc:
        log(f"麦克风设备检查失败：{exc}")
        return {"ok": False}
    ok = len(inputs) > 0
    log("麦克风设备检查通过" if ok else "麦克风设备检查失败：没有识别到输入设备")
    return {"ok": ok}


def check_permission() -> dict:
    if not check_microphone()["ok"]:
        log("麦克风权限检查失败：没有可用的输入设备")
        return {"ok": False}
    try:
        stream = sd.RawInputStream(samplerate=16000, channels=1, dtype="float32", blocksize=800)
        stream.start()
        time.sleep(0.05)
        stream.stop()
        stream.close()
    except Exception as exc:
        log(f"麦克风权限检查失败：{exc}")
        return {"ok": False}
    log("麦克风权限检查通过")
    return {"ok": True}


def open_microphone_settings() -> dict:
    os.startfile("ms-settings:privacy-microphone")
    log("已打开系统麦克风权限设置")
    return check_permission()


def _large_enough(path: Path, minimum: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= minimum
    except OSError:
        return False


def _missing_models() -> list[str]:
    missing = []
    if not _large_enough(MODEL_DIR / "silero_vad.onnx", 500_000):
        missing.append("silero_vad.onnx")
    for name, minimum in KWS_FILES.items():
        if not _large_enough(MODEL_DIR / KWS_DIR / name, minimum):
            missing.append(f"{KWS_DIR}/{name}")
    if not _large_enough(MODEL_DIR / SENSE_DIR / "model.int8.onnx", 50_000_000):
        missing.append(f"{SENSE_DIR}/model.int8.onnx")
    if not _large_enough(MODEL_DIR / SENSE_DIR / "tokens.txt", 1000):
        missing.append(f"{SENSE_DIR}/tokens.txt")
    return missing


def check_model() -> dict:
    missing = _missing_models()
    if missing:
        log("语音模型检查失败：缺少 " + "、".join(missing))
        return {"ok": False}
    log("语音模型检查通过")
    return {"ok": True}


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "VideoAssist"})
    with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as handle:
        shutil.copyfileobj(response, handle, length=1024 * 256)
    temporary.replace(destination)


def _download_first(urls: list[str], destination: Path) -> None:
    errors: list[str] = []
    for url in urls:
        try:
            _download(url, destination)
            return
        except Exception as exc:
            errors.append(f"{url} ({exc})")
    raise OSError("；".join(errors))


def _github_urls(asset_url: str) -> list[str]:
    return [
        "https://gh-proxy.com/" + asset_url,
        asset_url,
    ]


def _modelscope_kws(filename: str) -> str:
    return (
        "https://www.modelscope.cn/models/pkufool/"
        f"{KWS_DIR}/resolve/master/{filename}"
    )


def download_models() -> dict:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if not _large_enough(MODEL_DIR / "silero_vad.onnx", 500_000):
            log("开始下载 silero_vad.onnx")
            _download_first(
                _github_urls(
                    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
                ),
                MODEL_DIR / "silero_vad.onnx",
            )
        for name, minimum in KWS_FILES.items():
            target = MODEL_DIR / KWS_DIR / name
            if _large_enough(target, minimum):
                continue
            log(f"开始下载 {name}")
            _download_first([_modelscope_kws(name)], target)
        sense_model = MODEL_DIR / SENSE_DIR / "model.int8.onnx"
        sense_tokens = MODEL_DIR / SENSE_DIR / "tokens.txt"
        if not (_large_enough(sense_model, 50_000_000) and _large_enough(sense_tokens, 1000)):
            archive = MODEL_DIR / f"{SENSE_DIR}.tar.bz2"
            log("开始下载语音识别模型")
            _download_first(
                _github_urls(
                    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                    f"{SENSE_DIR}.tar.bz2"
                ),
                archive,
            )
            with tarfile.open(archive, "r:bz2") as package:
                package.extractall(MODEL_DIR)
            archive.unlink(missing_ok=True)
    except Exception as exc:
        log(f"语音模型下载失败：{exc}")
        return {"ok": False}
    return check_model()


def check_item(item_id: str, platform: str = "bilibili") -> dict:
    if item_id == "client":
        return check_client(platform)
    if item_id == "mic":
        return check_microphone()
    if item_id == "perm":
        return check_permission()
    if item_id == "model":
        return check_model()
    return {"ok": False}


def repair_item(item_id: str, platform: str = "bilibili") -> dict:
    if item_id == "client":
        restart_client(platform)
        return check_client(platform)
    if item_id == "mic":
        return check_microphone()
    if item_id == "perm":
        return open_microphone_settings()
    if item_id == "model":
        return download_models()
    return {"ok": False}
