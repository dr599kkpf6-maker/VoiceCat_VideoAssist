"""收音、唤醒、断句、转文字。不判断这句话要做什么。"""

from __future__ import annotations

import queue
import threading
from collections import deque

import numpy as np
import sherpa_onnx
import sounddevice as sd

import startup_checks

WAKE_KEYWORDS = "\n".join((
    "m āo sh ī f ù @猫师傅",
    "g āo sh ī f ù @高师傅",
    "m áo sh ī f ù @毛师傅",
    "m iāo sh ī f ù @喵师傅",
    "x iǎo m āo @小猫",
    "m āo m āo @猫猫",
))
SAMPLE_RATE = 16000


class Listener:
    def __init__(self, on_text):
        self._on_text = on_text
        self.level = 0.0
        self.alive = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream = None
        self._audio: queue.Queue = queue.Queue(maxsize=48)
        self._kws = None
        self._vad = None
        self._asr = None

    def start(self) -> bool:
        if self.alive:
            return True
        try:
            self._load()
            self._stop.clear()
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=1600,
                callback=self._callback,
            )
            self._stream.start()
            self.alive = True
            self._thread = threading.Thread(target=self._loop, name="voice-rec", daemon=True)
            self._thread.start()
        except Exception as exc:
            startup_checks.log(f"开始监听失败：{exc}")
            self.stop()
            return False
        return True

    def stop(self) -> None:
        self._stop.set()
        self.alive = False
        self.level = 0.0
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _load(self) -> None:
        model_dir = startup_checks.MODEL_DIR
        kws_dir = model_dir / startup_checks.KWS_DIR
        sense_dir = model_dir / startup_checks.SENSE_DIR
        keywords = startup_checks.APP_DIR / "wake_keywords.txt"
        keywords.write_text(WAKE_KEYWORDS + "\n", encoding="utf-8")
        self._kws = sherpa_onnx.KeywordSpotter(
            tokens=str(kws_dir / "tokens.txt"),
            encoder=str(kws_dir / "encoder-epoch-12-avg-2-chunk-16-left-64.onnx"),
            decoder=str(kws_dir / "decoder-epoch-12-avg-2-chunk-16-left-64.onnx"),
            joiner=str(kws_dir / "joiner-epoch-12-avg-2-chunk-16-left-64.onnx"),
            keywords_file=str(keywords),
            num_threads=2,
            keywords_score=2.2,
            keywords_threshold=0.08,
            provider="cpu",
        )
        config = startup_checks.load_config()
        silence = max(0.2, float(config.get("endpoint_silence_ms") or 700) / 1000)
        longest = max(2.0, float(config.get("max_utterance_ms") or 8000) / 1000)
        vad_config = sherpa_onnx.VadModelConfig()
        vad_config.sample_rate = SAMPLE_RATE
        vad_config.silero_vad.model = str(model_dir / "silero_vad.onnx")
        vad_config.silero_vad.threshold = 0.5
        vad_config.silero_vad.min_silence_duration = silence
        vad_config.silero_vad.min_speech_duration = 0.2
        vad_config.silero_vad.max_speech_duration = longest
        self._vad = sherpa_onnx.VoiceActivityDetector(vad_config, buffer_size_in_seconds=longest + 2)
        self._asr = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(sense_dir / "model.int8.onnx"),
            tokens=str(sense_dir / "tokens.txt"),
            num_threads=2,
            language="zh",
            use_itn=True,
            provider="cpu",
        )
        self._silence_samples = int(SAMPLE_RATE * silence)
        self._max_samples = int(SAMPLE_RATE * longest)

    def _callback(self, indata, frames, time_info, status) -> None:
        del frames, time_info
        if status:
            self._port_status = str(status)
        samples = np.asarray(indata, dtype=np.float32).reshape(-1).copy()
        if samples.size == 0:
            return
        rms = float(np.sqrt(np.mean(np.square(samples))))
        instant = min(1.0, max(0.0, (rms - 0.008) / 0.05))
        self.level = self.level * 0.62 + instant * 0.38
        try:
            self._audio.put_nowait(samples)
        except queue.Full:
            try:
                self._audio.get_nowait()
            except queue.Empty:
                pass
            try:
                self._audio.put_nowait(samples)
            except queue.Full:
                pass

    def _loop(self) -> None:
        ring: deque[float] = deque(maxlen=int(SAMPLE_RATE * 1.5))
        kws_stream = self._kws.create_stream()
        collecting = False
        uttered: list[float] = []
        collected = 0
        saw_speech = False
        silent = 0
        try:
            while not self._stop.is_set():
                try:
                    chunk = self._audio.get(timeout=0.2)
                except queue.Empty:
                    if self._stop.is_set():
                        continue
                    stream = self._stream
                    if stream is not None and not stream.active:
                        startup_checks.log("监听中断：麦克风已断开")
                        break
                    continue
                values = chunk.tolist()
                if not collecting:
                    ring.extend(values)
                kws_stream.accept_waveform(SAMPLE_RATE, chunk)
                while self._kws.is_ready(kws_stream):
                    self._kws.decode_stream(kws_stream)
                heard_wake = bool(self._kws.get_result(kws_stream))
                if heard_wake:
                    self._kws.reset_stream(kws_stream)
                if heard_wake and not collecting:
                    collecting = True
                    uttered = list(ring)
                    collected = 0
                    saw_speech = False
                    silent = 0
                    self._vad.reset()
                if not collecting:
                    continue
                uttered.extend(values)
                collected += len(values)
                self._vad.accept_waveform(chunk)
                if self._vad.is_speech_detected():
                    saw_speech = True
                    silent = 0
                elif saw_speech:
                    silent += len(values)
                finished = False
                if saw_speech and silent >= self._silence_samples:
                    finished = True
                elif not saw_speech and collected >= int(SAMPLE_RATE * 1.2):
                    finished = True
                elif collected >= self._max_samples:
                    finished = True
                if not finished:
                    continue
                audio = np.asarray(uttered, dtype=np.float32)
                collecting = False
                uttered = []
                text = self._transcribe(audio)
                if text:
                    try:
                        self._on_text(text)
                    except Exception as exc:
                        startup_checks.log(f"指令处理失败：{exc}")
        except Exception as exc:
            startup_checks.log(f"监听中断：{exc}")
        finally:
            self.alive = False
            self.level = 0.0

    def _transcribe(self, audio: np.ndarray) -> str:
        if audio.size < SAMPLE_RATE * 0.2:
            return ""
        stream = self._asr.create_stream()
        stream.accept_waveform(SAMPLE_RATE, audio)
        self._asr.decode_stream(stream)
        return str(stream.result.text or "").strip()
