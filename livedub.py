"""
LiveDub - live speech translation and dubbing for Windows.

1) Listens to whatever plays in your headphones (a call, a stream, a video),
   recognizes the speech locally with Whisper, translates it and shows
   subtitles over the screen. Optionally it also reads the translation aloud.
2) Voice mode: hold a NumPad key, speak your own language, release - the
   translation is spoken into a virtual microphone (VB-Cable), so the people
   in a call hear it.

Lives in the system tray. Subtitles sleep until you press Num1.

NumPad controls (NumLock must be on):
  Num1 - subtitles on / off
  Num2 - move subtitles to the next monitor
  Num3 - show / hide the original text
  Num4 / Num5 - smaller / bigger text
  Num6 - voice mode (microphone through the virtual cable) on / off
  Num7 - HOLD and speak -> translation into PTT_LANGS["7"]
  Num8 - HOLD and speak -> translation into PTT_LANGS["8"]
  Num0 - read the other person's speech aloud on / off
  Num. - repeat the last phrase aloud
  Num9 - quit
The same actions are in the tray icon menu.
"""

import ctypes
import itertools
import os
import queue
import re
import site
import sys
import threading
import time
import tkinter as tk
import warnings
from collections import deque
from ctypes import wintypes

warnings.filterwarnings("ignore", message="data discontinuity in recording")

# ---------- папки и лог ----------
FROZEN = getattr(sys, "frozen", False)
BASE_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))

# Без консоли (exe или pythonw) пишем сообщения в файл livedub.log рядом с программой
if FROZEN or sys.stdout is None or sys.stderr is None:
    try:
        _log = open(os.path.join(BASE_DIR, "livedub.log"), "w", encoding="utf-8", buffering=1)
        sys.stdout = _log
        sys.stderr = _log
    except Exception:
        pass

MODELS_DIR = os.path.join(BASE_DIR, "models")
os.makedirs(MODELS_DIR, exist_ok=True)
os.environ["HF_HOME"] = MODELS_DIR
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

# ====================== НАСТРОЙКИ ======================
# --- распознавание и субтитры ---
SOURCE_LANG = "nl"        # на каком языке говорят другие ("auto" - любой язык, кроме твоего)
TARGET_LANG = "ru"        # твой язык: на него идут субтитры и озвучка, на нём говоришь ты
PTT_LANGS = {"7": "nl", "8": "en"}   # Num7 / Num8 (зажать): на какой язык переводить твою речь
USE_GPU = True            # пробует видеокарту NVIDIA, если не выйдет - процессор
GPU_MODEL = "medium"      # модель для видеокарты ("small" - меньше нагрузка на видеокарту в играх)
CPU_MODEL = "small"       # модель для процессора
SPEAKER_NAME = ""         # часть названия наушников; пусто = устройство по умолчанию
PAUSE_SECONDS = 0.5       # пауза в речи, после которой фраза уходит на перевод
MAX_SECONDS = 6           # максимальная длина одной фразы
SILENCE_LEVEL = 0.003     # ниже этого уровня звук считается тишиной
SHOW_SECONDS = 12         # сколько секунд каждая строка висит на экране
MAX_LINES = 3             # сколько строк истории на экране
SHOW_ORIGINAL = False     # показывать оригинальный текст в скобках
FONT_SIZE = 20
WRAP_WIDTH = 1000
BOTTOM_MARGIN = 90
HOTKEY_MODIFIER = "none"  # "none" - голый NumPad, или "ctrl", "alt", "ctrl+alt"

# --- голосовой режим ---
VOICE_AUTOSTART = True               # голосовой режим включается сам при запуске программы
VIRTUAL_CABLE_NAME = "CABLE Input"   # часть названия виртуального кабеля VB-Cable
MIC_NAME = ""                        # часть названия твоего микрофона; пусто = по умолчанию
MIC_PASSTHROUGH = True               # скрипт сам пересылает твой микрофон в кабель (и глушит его на время перевода)
MONITOR_OWN_TRANSLATION = True       # ты тоже слышишь свой перевод в наушниках
VOICES = {                           # голоса озвучки (edge-tts), по коду языка
    "ru": "ru-RU-SvetlanaNeural",    # мужской вариант: ru-RU-DmitryNeural
    "nl": "nl-NL-MaartenNeural",     # женские: nl-NL-FennaNeural, nl-NL-ColetteNeural
    "en": "en-US-GuyNeural",         # женский: en-US-JennyNeural
    "de": "de-DE-ConradNeural",
    "fr": "fr-FR-HenriNeural",
    "es": "es-ES-AlvaroNeural",
    "it": "it-IT-DiegoNeural",
    "pl": "pl-PL-MarekNeural",
    "uk": "uk-UA-OstapNeural",
    "pt": "pt-BR-AntonioNeural",
    "tr": "tr-TR-AhmetNeural",
    "ja": "ja-JP-KeitaNeural",
}
TTS_RATE = "+10%"                    # скорость речи твоего перевода
DUB_RATE = "+20%"                    # скорость озвучки собеседника (быстрее - меньше отставание)
BEEPS = True                         # короткие сигналы при начале/конце записи
PTT_MAX_SECONDS = 30                 # максимум одной записи
# ========================================================

SAMPLE_RATE = 16000
MIC_RATE = 48000
BLOCK_SECONDS = 0.2

COLORS = {0: "#ffffff", 1: "#b0b0b0", 2: "#7d7d7d"}
SIZE_STEP = {0: 0, 1: 4, 2: 6}
MINE_COLOR = "#8fd3ff"               # цвет твоих переведённых фраз

HOTKEY_ACTIONS = {                   # код клавиши NumPad -> команда
    0x61: "toggle",        # Num1
    0x62: "monitor_next",  # Num2
    0x63: "orig",          # Num3
    0x64: "font-",         # Num4
    0x65: "font+",         # Num5
    0x66: "voice",         # Num6
    0x60: "dub",           # Num0
    0x6E: "replay",        # Num.
    0x69: "quit",          # Num9
}
PTT_KEYS = {0x67: PTT_LANGS.get("7", "nl"), 0x68: PTT_LANGS.get("8", "en")}  # Num7, Num8
HOTKEY_MODS = {"none": 0x0, "alt": 0x1, "ctrl": 0x2, "ctrl+alt": 0x3}


def add_cuda_paths():
    bases = [os.path.join(BASE_DIR, "cuda_libs")]
    for base in list(site.getsitepackages()) + [site.getusersitepackages()]:
        for sub in ("cublas", "cudnn"):
            bases.append(os.path.join(base, "nvidia", sub, "bin"))
    for p in bases:
        if os.path.isdir(p):
            try:
                os.add_dll_directory(p)
            except Exception:
                pass
            os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")


add_cuda_paths()

import numpy as np
import soundcard as sc
from deep_translator import GoogleTranslator, MyMemoryTranslator
from faster_whisper import WhisperModel

try:
    import edge_tts
except ImportError:
    edge_tts = None

# ---------- общее состояние ----------
awake = threading.Event()        # субтитры включены
voice_on = threading.Event()     # голосовой режим включён
ready = threading.Event()        # модель загружена
mic_ready = threading.Event()    # микрофон открыт и готов к записи
tts_active = threading.Event()   # сейчас звучит озвучка у тебя в наушниках
model_lock = threading.Lock()    # модель одна на всех
S = {"font": FONT_SIZE, "orig": SHOW_ORIGINAL, "monitor": 0, "dub": False, "last_tr": ""}
audio_q = queue.Queue()
text_q = queue.Queue()
cmd_q = queue.Queue()
voice_q = queue.Queue()
dub_q = queue.Queue()
play_q = queue.Queue(maxsize=3)
BAD_VOICES = set()
tray = {"icon": None}
id_counter = itertools.count(1)


def notify(msg):
    try:
        if tray["icon"]:
            tray["icon"].notify(msg, "LiveDub")
    except Exception:
        pass


def drain(q):
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        pass


def beep(freq):
    if not BEEPS:
        return
    try:
        import winsound
        threading.Thread(target=winsound.Beep, args=(freq, 70), daemon=True).start()
    except Exception:
        pass


# ---------- модель ----------
def load_model():
    if USE_GPU:
        try:
            print(f"Пробую видеокарту, модель {GPU_MODEL}...")
            m = WhisperModel(GPU_MODEL, device="cuda", compute_type="float16",
                             download_root=MODELS_DIR)
            list(m.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))[0])
            print("Работаю на видеокарте.")
            return m
        except Exception as e:
            print("Видеокарта не подошла, использую процессор. Причина:", str(e)[:200])
    print(f"Загружаю модель {CPU_MODEL} (процессор)...")
    return WhisperModel(CPU_MODEL, device="cpu", compute_type="int8", download_root=MODELS_DIR)


# ---------- устройства ----------
def pick_speaker():
    if SPEAKER_NAME:
        for s in sc.all_speakers():
            if SPEAKER_NAME.lower() in s.name.lower():
                return s
        print("Устройство вывода с таким названием не найдено, беру по умолчанию.")
    return sc.default_speaker()


def pick_microphone():
    if MIC_NAME:
        for m in sc.all_microphones(include_loopback=False):
            if MIC_NAME.lower() in m.name.lower():
                return m
        print("Микрофон с таким названием не найден, беру по умолчанию.")
    mic = sc.default_microphone()
    if "cable" in mic.name.lower():
        print("ВНИМАНИЕ: микрофоном по умолчанию в Windows выбран кабель. "
              "Укажи свой настоящий микрофон в MIC_NAME.")
    return mic


def find_cable():
    for s in sc.all_speakers():
        if VIRTUAL_CABLE_NAME.lower() in s.name.lower():
            return s
    return None


def play_audio(speaker, mono, sr):
    stereo = np.column_stack([mono, mono]).astype(np.float32)
    speaker.play(stereo, samplerate=sr, channels=2)


# ---------- захват звука собеседника ----------
def record_session():
    speaker = pick_speaker()
    print(f"Слушаю устройство: {speaker.name}")
    mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
    block_frames = int(SAMPLE_RATE * BLOCK_SECONDS)
    pre_roll = deque(maxlen=2)
    buf = []
    speaking = False
    silent_blocks = 0
    max_blocks = int(MAX_SECONDS / BLOCK_SECONDS)
    pause_blocks = max(1, int(PAUSE_SECONDS / BLOCK_SECONDS))

    with mic.recorder(samplerate=SAMPLE_RATE, channels=2) as rec:
        while awake.is_set():
            data = rec.record(numframes=block_frames)
            if tts_active.is_set():
                # пока звучит озвучка, не слушаем, иначе переводчик услышит сам себя
                buf, speaking, silent_blocks = [], False, 0
                pre_roll.clear()
                continue
            mono = data.mean(axis=1).astype(np.float32)
            loud = float(np.sqrt(np.mean(mono ** 2))) > SILENCE_LEVEL

            if loud:
                if not speaking:
                    speaking = True
                    buf = list(pre_roll)
                buf.append(mono)
                silent_blocks = 0
            elif speaking:
                buf.append(mono)
                silent_blocks += 1
            else:
                pre_roll.append(mono)

            if speaking and (silent_blocks >= pause_blocks or len(buf) >= max_blocks):
                if len(buf) * BLOCK_SECONDS >= 0.7:
                    audio_q.put(np.concatenate(buf))
                buf, speaking, silent_blocks = [], False, 0


def capture_loop():
    while True:
        if not awake.is_set():
            time.sleep(0.2)
            continue
        try:
            record_session()
        except Exception as e:
            print("Ошибка захвата звука:", e)
            time.sleep(2)


# ---------- перевод ----------
class GtxTranslator:
    """Бесплатный endpoint Google, лучше переносит частые запросы."""

    def __init__(self, source, target):
        self.source = source
        self.target = target

    def translate(self, text):
        import requests
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": self.source, "tl": self.target, "dt": "t", "q": text},
            timeout=6,
        )
        r.raise_for_status()
        data = r.json()
        return "".join(part[0] for part in data[0] if part and part[0]).strip()


class Translator:
    """Google, а если он ругается на лимиты - запасной MyMemory."""
    NAMES = {"nl": "dutch", "ru": "russian", "en": "english", "de": "german", "fr": "french",
             "es": "spanish", "it": "italian", "pl": "polish", "uk": "ukrainian",
             "pt": "portuguese", "tr": "turkish", "ja": "japanese"}

    def __init__(self, source, target):
        self.engines = [["gtx", GtxTranslator(source, target), 0.0],
                        ["google", GoogleTranslator(source=source, target=target), 0.0]]
        try:
            self.engines.append(["mymemory", MyMemoryTranslator(
                source=self.NAMES[source], target=self.NAMES[target]), 0.0])
        except Exception:
            pass

    def translate(self, text):
        for eng in self.engines:
            name, tr, bad_until = eng
            if time.time() < bad_until:
                continue
            try:
                out = tr.translate(text)
                if out:
                    return out
            except Exception as e:
                print(f"[{name}] ошибка перевода: {str(e)[:120]}")
                eng[2] = time.time() + 60
        return None


def translate_loop(model):
    translator = Translator(SOURCE_LANG, TARGET_LANG)
    while True:
        audio = audio_q.get()
        while audio_q.qsize() > 2:
            audio = audio_q.get()
        if not awake.is_set():
            continue
        try:
            with model_lock:
                segments, info = model.transcribe(
                    audio, language=None, vad_filter=True, beam_size=1,
                    condition_on_previous_text=False, without_timestamps=True,
                )
                if SOURCE_LANG == "auto":
                    wrong_language = info.language == TARGET_LANG
                else:
                    wrong_language = info.language != SOURCE_LANG
                if wrong_language or info.language_probability < 0.5:
                    continue
                text = " ".join(s.text.strip() for s in segments).strip()
            if not text or not awake.is_set():
                continue
            eid = next(id_counter)
            text_q.put(("orig", eid, text, ""))
            translated = translator.translate(text)
            text_q.put(("tr", eid, text, translated or "(перевод недоступен)"))
            if translated:
                S["last_tr"] = translated
                if S["dub"]:
                    dub_q.put(translated)
        except Exception as e:
            print("Ошибка распознавания:", e)


# ---------- озвучка ----------
def tts_audio(text, voice, rate=None):
    """Текст -> звук (edge-tts, нужен интернет). Возвращает (float32 моно, частота)."""
    import asyncio
    import io
    import av

    async def synth():
        comm = edge_tts.Communicate(text, voice, rate=rate or TTS_RATE)
        buf = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    data = b""
    for attempt in range(2):
        try:
            data = asyncio.run(synth())
            if data:
                break
        except Exception:
            if attempt == 1:
                raise
            time.sleep(0.5)
    if not data:
        raise RuntimeError("синтезатор вернул пустой звук")
    container = av.open(io.BytesIO(data))
    resampler = av.AudioResampler(format="flt", layout="mono", rate=24000)
    parts = []
    for frame in container.decode(audio=0):
        for f in resampler.resample(frame):
            parts.append(f.to_ndarray().reshape(-1))
    try:
        for f in resampler.resample(None):
            parts.append(f.to_ndarray().reshape(-1))
    except Exception:
        pass
    container.close()
    return np.concatenate(parts).astype(np.float32), 24000


VOICE_FALLBACKS = {
    "ru": ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"],
    "de": ["de-DE-KatjaNeural", "de-DE-ConradNeural"],
    "fr": ["fr-FR-DeniseNeural", "fr-FR-HenriNeural"],
    "es": ["es-ES-ElviraNeural", "es-ES-AlvaroNeural"],
    "uk": ["uk-UA-PolinaNeural", "uk-UA-OstapNeural"],
    "nl": ["nl-NL-FennaNeural", "nl-NL-ColetteNeural", "nl-NL-MaartenNeural"],
    "en": ["en-US-JennyNeural", "en-US-GuyNeural"],
}


def synth_with_fallback(text, lang, rate=None):
    """Озвучка с запасными голосами. Голос, который сломался, больше не используется."""
    if lang not in VOICES:
        raise RuntimeError(f"нет голоса для языка {lang!r}, добавь его в VOICES")
    candidates = [VOICES[lang]] + [v for v in VOICE_FALLBACKS.get(lang, []) if v != VOICES[lang]]
    candidates = [v for v in candidates if v not in BAD_VOICES] or candidates
    failed = []
    last_error = None
    for voice in candidates:
        try:
            result = tts_audio(text, voice, rate)
            for v in failed:                 # запасной сработал - значит, прошлый голос сломан
                BAD_VOICES.add(v)
                print(f"Голос {v} не работает, дальше использую {voice}.")
            return result
        except Exception as e:
            last_error = e
            failed.append(voice)
            print(f"Ошибка озвучки (голос {voice}): {e}; текст: {text[:80]!r}")
    raise last_error


def dub_synth_loop():
    """Готовит озвучку заранее, пока предыдущая фраза ещё играет."""
    while True:
        text = dub_q.get()
        # накопившиеся фразы склеиваем в одну: меньше пауз и обрывов
        while not dub_q.empty() and len(text) < 400:
            try:
                text += " " + dub_q.get_nowait()
            except queue.Empty:
                break
        if edge_tts is None:
            notify("Для озвучки нужен edge-tts: pip install edge-tts")
            continue
        if not re.search(r"[A-Za-zА-Яа-яЁё]", text):
            continue
        try:
            mono, sr = synth_with_fallback(text, TARGET_LANG, DUB_RATE)
            play_q.put((mono, sr))
        except Exception as e:
            print("Не удалось озвучить фразу:", e)


def dub_play_loop():
    """Проигрывает озвучку по очереди, без пауз между готовыми фразами."""
    while True:
        mono, sr = play_q.get()
        tts_active.set()
        try:
            play_audio(pick_speaker(), mono, sr)
        except Exception as e:
            print("Ошибка проигрывания озвучки:", e)
        finally:
            if play_q.empty():
                time.sleep(0.3)
                if play_q.empty():
                    tts_active.clear()


def speak_to_friend(text, lang):
    """Проигрывает перевод в виртуальный микрофон (и тебе в наушники)."""
    mono, sr = synth_with_fallback(text, lang)
    cable = find_cable()
    cable_thread = None
    if cable:
        cable_thread = threading.Thread(target=play_audio, args=(cable, mono, sr), daemon=True)
        cable_thread.start()
    else:
        notify("VB-Cable не найден, перевод слышишь только ты.")
    if MONITOR_OWN_TRANSLATION or not cable:
        tts_active.set()
        try:
            play_audio(pick_speaker(), mono, sr)
        finally:
            time.sleep(0.4)
            tts_active.clear()
    if cable_thread:
        cable_thread.join()


# ---------- голосовой режим: микрофон и "говорю -> перевод" ----------
class Ptt:
    def __init__(self):
        self.active = False
        self.lang = None
        self.chunks = []
        self.lock = threading.Lock()


ptt = Ptt()


def mic_session():
    mic = pick_microphone()
    cable = find_cable() if MIC_PASSTHROUGH else None
    if MIC_PASSTHROUGH and cable is None:
        notify("Не найден виртуальный кабель VB-Cable. Голосовой режим выключен.")
        print("VB-Cable не найден. Доступные устройства вывода:")
        for sp in sc.all_speakers():
            print("  -", sp.name)
        voice_on.clear()
        return
    block = int(MIC_RATE * 0.03)
    print(f"Микрофон: {mic.name}; кабель: {cable.name if cable else 'не используется'}")
    silence = np.zeros((block, 2), dtype=np.float32)
    out_q = queue.Queue(maxsize=8)
    stop = threading.Event()

    def writer():
        try:
            with cable.player(samplerate=MIC_RATE, channels=2) as player:
                print("Микрофон передаётся в кабель.")
                while not stop.is_set():
                    try:
                        blk = out_q.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    player.play(blk)
        except Exception as e:
            print("Ошибка передачи микрофона в кабель:", e)

    if cable:
        threading.Thread(target=writer, daemon=True).start()
    try:
        with mic.recorder(samplerate=MIC_RATE, channels=1) as rec:
            mic_ready.set()
            while voice_on.is_set():
                data = rec.record(numframes=block)
                mono = data[:, 0].astype(np.float32)
                if ptt.active:
                    with ptt.lock:
                        ptt.chunks.append(mono.copy())
                    blk = silence               # пока держишь клавишу, друг тебя не слышит
                else:
                    blk = np.column_stack([mono, mono])
                if cable:
                    try:
                        out_q.put_nowait(blk)
                    except queue.Full:
                        pass
    finally:
        mic_ready.clear()
        stop.set()


def mic_loop():
    while True:
        if not voice_on.is_set():
            time.sleep(0.2)
            continue
        try:
            mic_session()
        except Exception as e:
            print("Ошибка микрофона:", e)
            time.sleep(2)


def ptt_start(lang):
    if not voice_on.is_set():
        if edge_tts is None:
            cmd_q.put("voice_hint")
            return False
        voice_on.set()               # включаем голосовой режим сами
        cmd_q.put("voice_auto")
    if not mic_ready.wait(2.0):
        cmd_q.put("voice_hint")
        return False
    if ptt.active:
        return False
    with ptt.lock:
        ptt.chunks = []
        ptt.lang = lang
        ptt.active = True
    beep(1000)
    return True


def ptt_stop():
    with ptt.lock:
        ptt.active = False
        chunks, ptt.chunks = ptt.chunks, []
        lang = ptt.lang
    beep(600)
    if chunks:
        audio = np.concatenate(chunks)
        if len(audio) / MIC_RATE >= 0.4:
            voice_q.put((audio, lang))


def watch_release(vk):
    u = ctypes.windll.user32
    t0 = time.time()
    while (u.GetAsyncKeyState(vk) & 0x8000) and time.time() - t0 < PTT_MAX_SECONDS:
        time.sleep(0.02)
    ptt_stop()


def to_16k(x):
    n = len(x) // 3 * 3
    a = x[:n].reshape(-1, 3).mean(axis=1).astype(np.float32)
    peak = float(np.abs(a).max()) if len(a) else 0.0
    if 0.001 < peak < 0.3:
        a = a * (0.3 / peak)
    return a


def voice_out_loop(model):
    translators = {lang: Translator(TARGET_LANG, lang) for lang in set(PTT_KEYS.values())}
    while True:
        audio48, lang = voice_q.get()
        try:
            audio = to_16k(audio48)
            with model_lock:
                segments, info = model.transcribe(
                    audio, language=TARGET_LANG, vad_filter=True, beam_size=3,
                    condition_on_previous_text=False, without_timestamps=True,
                )
                ru = " ".join(s.text.strip() for s in segments).strip()
            if not ru:
                continue
            out = translators[lang].translate(ru)
            if not out:
                notify("Не удалось перевести, попробуй ещё раз.")
                continue
            text_q.put(("out", next(id_counter), ru, f"{lang.upper()} ▶ {out}"))
            if edge_tts is None:
                notify("Для озвучки нужен edge-tts: pip install edge-tts")
                continue
            speak_to_friend(out, lang)
        except Exception as e:
            print("Ошибка голосового перевода:", e)


# ---------- Windows: мониторы, горячие клавиши, окно ----------
def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def get_monitors():
    mons = []
    try:
        proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(wintypes.RECT), ctypes.c_void_p,
        )

        def cb(hmon, hdc, lprect, lparam):
            r = lprect.contents
            mons.append((r.left, r.top, r.right, r.bottom))
            return 1

        ctypes.windll.user32.EnumDisplayMonitors(None, None, proc_type(cb), 0)
    except Exception as e:
        print("Не удалось получить список мониторов:", e)
    mons.sort(key=lambda m: (0 if (m[0], m[1]) == (0, 0) else 1, m[0]))
    return mons


def hotkey_thread():
    try:
        u = ctypes.windll.user32
        mods = HOTKEY_MODS.get(HOTKEY_MODIFIER, 0) | 0x4000   # MOD_NOREPEAT
        for vk in list(HOTKEY_ACTIONS) + list(PTT_KEYS):
            if not u.RegisterHotKey(None, vk, mods, vk):
                print(f"Не удалось занять клавишу с кодом {hex(vk)} (занята другой программой?)")
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312:   # WM_HOTKEY
                vk = int(msg.wParam)
                if vk in PTT_KEYS:
                    if ptt_start(PTT_KEYS[vk]):
                        threading.Thread(target=watch_release, args=(vk,), daemon=True).start()
                elif vk in HOTKEY_ACTIONS:
                    cmd_q.put(HOTKEY_ACTIONS[vk])
    except Exception as e:
        print("Ошибка горячих клавиш:", e)


def make_overlay_window(root):
    try:
        root.update_idletasks()
        u = ctypes.windll.user32
        hwnd = u.GetParent(root.winfo_id()) or root.winfo_id()
        style = u.GetWindowLongW(hwnd, -20)
        style |= 0x08000000 | 0x00000080 | 0x00000008   # NOACTIVATE | TOOLWINDOW | TOPMOST
        u.SetWindowLongW(hwnd, -20, style)
    except Exception:
        pass


def make_tray_image(active):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (46, 204, 113, 255) if active else (140, 140, 140, 255)
    d.rounded_rectangle((2, 8, 62, 56), radius=10, fill=color)
    d.rectangle((14, 22, 50, 28), fill="white")
    d.rectangle((14, 36, 38, 42), fill="white")
    return img


def main():
    enable_dpi_awareness()
    entries = []
    state = {"visible": False, "drag_anchor": None, "dx": 0, "dy": 0}

    def worker():
        try:
            model = load_model()
        except Exception as e:
            print("Не удалось загрузить модель:", e)
            notify("Не удалось загрузить модель, смотри livedub.log")
            return
        ready.set()
        print("Модель готова.")
        notify("Модель загружена. Num1 - включить субтитры.")
        threading.Thread(target=voice_out_loop, args=(model,), daemon=True).start()
        translate_loop(model)

    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=capture_loop, daemon=True).start()
    threading.Thread(target=dub_synth_loop, daemon=True).start()
    threading.Thread(target=dub_play_loop, daemon=True).start()
    threading.Thread(target=mic_loop, daemon=True).start()
    threading.Thread(target=hotkey_thread, daemon=True).start()

    if VOICE_AUTOSTART and edge_tts is not None:
        voice_on.set()

    # ---------- окно субтитров ----------
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.88)
    root.configure(bg="black")
    labels = []
    for _ in range(MAX_LINES):
        labels.append(tk.Label(root, text="", font=("Segoe UI", FONT_SIZE), fg="white", bg="black",
                               wraplength=WRAP_WIDTH, justify="center", padx=14, pady=3))
    make_overlay_window(root)

    def default_anchor():
        mons = get_monitors()
        if mons:
            idx = min(S["monitor"], len(mons) - 1)
            l, t, r, b = mons[idx]
            return (l + r) // 2, b - BOTTOM_MARGIN
        return root.winfo_screenwidth() // 2, root.winfo_screenheight() - BOTTOM_MARGIN

    def render():
        vis = entries[-MAX_LINES:]
        for lbl in labels:
            lbl.pack_forget()
        if not vis:
            root.withdraw()
            state["visible"] = False
            return
        for idx, e in enumerate(vis):
            rank = len(vis) - 1 - idx
            lbl = labels[idx]
            size = max(10, S["font"] - SIZE_STEP[rank])
            if e["tr"] is None:
                text = e["orig"]
                color = "#9a9a9a" if rank == 0 else COLORS[rank]
                font = ("Segoe UI", size, "italic")
            else:
                if e.get("mine"):
                    text = e["tr"] + f"\n({e['orig']})"
                    color = MINE_COLOR if rank == 0 else COLORS[rank]
                else:
                    text = e["tr"] + (f"\n({e['orig']})" if S["orig"] else "")
                    color = COLORS[rank]
                font = ("Segoe UI", size, "bold" if rank == 0 else "normal")
            lbl.config(text=text, fg=color, font=font, wraplength=WRAP_WIDTH)
            lbl.pack(fill="x")
        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        cx, bottom = state["drag_anchor"] or default_anchor()
        root.geometry(f"+{cx - w // 2}+{bottom - h}")
        if not state["visible"]:
            root.deiconify()
            state["visible"] = True

    def preview(text="Так выглядят субтитры"):
        entries.append({"id": -1, "orig": text, "tr": text, "t": time.time()})
        del entries[:-MAX_LINES]

    def update_tray():
        ic = tray["icon"]
        if not ic:
            return
        try:
            ic.icon = make_tray_image(awake.is_set() or voice_on.is_set())
            parts = []
            if awake.is_set():
                parts.append("субтитры")
            if voice_on.is_set():
                parts.append("голос")
            ic.title = "LiveDub: " + (", ".join(parts) if parts else "спит")
            ic.update_menu()
        except Exception:
            pass

    def set_awake(flag):
        if flag:
            drain(audio_q)
            drain(text_q)
            awake.set()
            if not ready.is_set():
                notify("Модель ещё загружается, подожди немного...")
        else:
            awake.clear()
            entries.clear()
            drain(audio_q)
            drain(text_q)
        update_tray()

    def quit_app():
        try:
            if tray["icon"]:
                tray["icon"].stop()
        except Exception:
            pass
        os._exit(0)

    def handle(cmd):
        if cmd == "toggle":
            set_awake(not awake.is_set())
        elif cmd == "monitor_next":
            n = len(get_monitors()) or 1
            S["monitor"] = (S["monitor"] + 1) % n
            state["drag_anchor"] = None
            preview("Субтитры теперь здесь")
        elif cmd.startswith("monitor:"):
            S["monitor"] = int(cmd.split(":")[1])
            state["drag_anchor"] = None
            preview("Субтитры теперь здесь")
        elif cmd == "orig":
            S["orig"] = not S["orig"]
        elif cmd == "font+":
            S["font"] = min(40, S["font"] + 2)
            preview()
        elif cmd == "font-":
            S["font"] = max(12, S["font"] - 2)
            preview()
        elif cmd == "voice":
            if voice_on.is_set():
                voice_on.clear()
                preview("Голосовой режим выключен")
            elif edge_tts is None:
                preview("Нужна озвучка: pip install edge-tts")
            else:
                voice_on.set()
                preview(f"Голосовой режим включён: Num7 -> {PTT_KEYS[0x67]}, Num8 -> {PTT_KEYS[0x68]}")
        elif cmd == "voice_hint":
            preview("Голосовой режим не запустился, смотри livedub.log")
        elif cmd == "voice_auto":
            preview("Голосовой режим включён")
        elif cmd == "dub":
            S["dub"] = not S["dub"]
            if not S["dub"]:
                drain(dub_q)
                drain(play_q)
            if S["dub"] and not awake.is_set():
                set_awake(True)
            preview("Озвучка собеседника: " + ("включена" if S["dub"] else "выключена"))
        elif cmd == "replay":
            if S["last_tr"]:
                dub_q.put(S["last_tr"])
        elif cmd == "quit":
            quit_app()
        update_tray()

    def poll():
        changed = False
        try:
            while True:
                handle(cmd_q.get_nowait())
                changed = True
        except queue.Empty:
            pass
        try:
            while True:
                kind, eid, orig, tr = text_q.get_nowait()
                now = time.time()
                if kind == "out":
                    entries.append({"id": eid, "orig": orig, "tr": tr, "t": now, "mine": True})
                    del entries[:-MAX_LINES]
                    changed = True
                    continue
                if not awake.is_set():
                    continue
                if kind == "orig":
                    entries.append({"id": eid, "orig": orig, "tr": None, "t": now})
                    del entries[:-MAX_LINES]
                else:
                    for e in entries:
                        if e["id"] == eid:
                            e["tr"] = tr
                            e["t"] = now
                            break
                    else:
                        entries.append({"id": eid, "orig": orig, "tr": tr, "t": now})
                        del entries[:-MAX_LINES]
                changed = True
        except queue.Empty:
            pass
        now = time.time()
        before = len(entries)
        entries[:] = [e for e in entries if now - e["t"] < SHOW_SECONDS]
        if len(entries) != before:
            changed = True
        if changed:
            render()
        if state["visible"]:
            root.attributes("-topmost", True)
            root.lift()
        root.after(150, poll)

    def start_drag(e):
        state["dx"], state["dy"] = e.x, e.y

    def do_drag(e):
        x = root.winfo_x() + e.x - state["dx"]
        y = root.winfo_y() + e.y - state["dy"]
        root.geometry(f"+{x}+{y}")
        state["drag_anchor"] = (x + root.winfo_width() // 2, y + root.winfo_height())

    def clear_subs(_e=None):
        entries.clear()
        render()

    for lbl in labels:
        lbl.bind("<Button-1>", start_drag)
        lbl.bind("<B1-Motion>", do_drag)
        lbl.bind("<Button-3>", clear_subs)

    # ---------- трей ----------
    try:
        import pystray

        def monitor_items():
            for i, (l, t, r, b) in enumerate(get_monitors()):
                yield pystray.MenuItem(
                    f"Монитор {i + 1} ({r - l}x{b - t})",
                    (lambda idx: lambda icon, item: cmd_q.put(f"monitor:{idx}"))(i),
                    checked=(lambda idx: lambda item: S["monitor"] == idx)(i),
                    radio=True,
                )

        def put(cmd):
            return lambda icon, item: cmd_q.put(cmd)

        menu = pystray.Menu(
            pystray.MenuItem("Субтитры вкл / выкл (Num1)", put("toggle"),
                             checked=lambda item: awake.is_set(), default=True),
            pystray.MenuItem("Монитор для субтитров (Num2)", pystray.Menu(monitor_items)),
            pystray.MenuItem("Показывать оригинал (Num3)", put("orig"),
                             checked=lambda item: S["orig"]),
            pystray.MenuItem("Текст мельче (Num4)", put("font-")),
            pystray.MenuItem("Текст крупнее (Num5)", put("font+")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Голосовой режим / микрофон через кабель (Num6)", put("voice"),
                             checked=lambda item: voice_on.is_set()),
            pystray.MenuItem("Озвучивать собеседника по-русски (Num0)", put("dub"),
                             checked=lambda item: S["dub"]),
            pystray.MenuItem("Повторить последнюю фразу голосом (Num.)", put("replay")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход (Num9)", put("quit")),
        )
        tray["icon"] = pystray.Icon("livedub",
                                    make_tray_image(awake.is_set() or voice_on.is_set()),
                                    "LiveDub: голос" if voice_on.is_set() else "LiveDub: спит",
                                    menu)
        tray["icon"].run_detached()
    except Exception as e:
        print("Трей недоступен (установи pystray и pillow):", e)

    root.withdraw()
    print("Программа запущена. Num1 - субтитры; зажми Num7 или Num8 и говори на своём языке.")
    poll()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    quit_app()


if __name__ == "__main__":
    main()
