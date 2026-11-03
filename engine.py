"""Движок LiveDub: звук, распознавание, перевод, озвучка, микрофон, горячие клавиши.

Здесь нет интерфейса. Движок общается с окном через очереди:
  text_q - субтитры и сообщения для экрана  (kind, a, b, c)
  cmd_q  - команды от горячих клавиш и трея ("toggle", "voice", ...)
"""

import ctypes
import itertools
import queue
import re
import threading
import time
import warnings
from collections import deque
from ctypes import wintypes

import numpy as np
import soundcard as sc
from deep_translator import GoogleTranslator, MyMemoryTranslator
from faster_whisper import WhisperModel

try:
    import edge_tts
except ImportError:
    edge_tts = None

from config import MODELS_DIR, VOICE_CHOICES, cfg
from i18n import t

warnings.filterwarnings("ignore", message="data discontinuity in recording")

SAMPLE_RATE = 16000
MIC_RATE = 48000
BLOCK_SECONDS = 0.2

HOTKEY_ACTIONS = {           # код клавиши NumPad -> команда
    0x61: "toggle",          # Num1
    0x62: "monitor_next",    # Num2
    0x63: "orig",            # Num3
    0x64: "font-",           # Num4
    0x65: "font+",           # Num5
    0x66: "voice",           # Num6
    0x60: "dub",             # Num0
    0x6E: "replay",          # Num.
    0x69: "quit",            # Num9
}
PTT_VK = {0x67: "7", 0x68: "8"}      # Num7, Num8 - зажать и говорить
HOTKEY_MODS = {"none": 0x0, "alt": 0x1, "ctrl": 0x2, "ctrl+alt": 0x3}

# ---------- общее состояние ----------
awake = threading.Event()          # субтитры включены
voice_on = threading.Event()       # голосовой режим включён
ready = threading.Event()          # модель загружена
mic_ready = threading.Event()      # микрофон открыт
tts_active = threading.Event()     # сейчас звучит озвучка в твоих наушниках
hotkeys_reload = threading.Event()
model_lock = threading.Lock()
model_ref = {"m": None}
model_state = {"status": "idle", "name": "", "device": ""}
S = {"last_tr": "", "dub": False}

audio_q = queue.Queue()
text_q = queue.Queue()
cmd_q = queue.Queue()
voice_q = queue.Queue()
dub_q = queue.Queue()
play_q = queue.Queue(maxsize=3)
model_req_q = queue.Queue()
id_counter = itertools.count(1)
BAD_VOICES = set()


def post(kind, a="", b="", c=""):
    text_q.put((kind, a, b, c))


def say(text):
    """Короткое сообщение поверх экрана."""
    post("msg", text)


def notify(text):
    """Всплывающее уведомление из трея."""
    post("notify", text)


def drain(q):
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        pass


def beep(freq):
    if not cfg["beeps"]:
        return
    try:
        import winsound
        threading.Thread(target=winsound.Beep, args=(freq, 70), daemon=True).start()
    except Exception:
        pass


# ---------- Windows: DPI, мониторы ----------
def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def get_monitors():
    """Список мониторов (left, top, right, bottom); основной - первым."""
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


# ---------- модель распознавания ----------
def load_model(name, use_gpu):
    if use_gpu:
        try:
            print(f"Пробую видеокарту, модель {name}...")
            m = WhisperModel(name, device="cuda", compute_type="float16", download_root=MODELS_DIR)
            list(m.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))[0])
            print("Работаю на видеокарте.")
            return m, "GPU"
        except Exception as e:
            print("Видеокарта не подошла, использую процессор. Причина:", str(e)[:200])
    print(f"Загружаю модель {name} (процессор)...")
    return WhisperModel(name, device="cpu", compute_type="int8", download_root=MODELS_DIR), "CPU"


def model_worker():
    announced = False
    while True:
        name, use_gpu = model_req_q.get()
        while not model_req_q.empty():          # берём самый свежий запрос
            name, use_gpu = model_req_q.get_nowait()
        model_state.update(status="loading", name=name, device="")
        try:
            m, device = load_model(name, use_gpu)
        except Exception as e:
            print("Не удалось загрузить модель:", e)
            model_state.update(status="error")
            notify(t("note_model_fail"))
            continue
        with model_lock:
            old = model_ref["m"]
            model_ref["m"] = m
        del old
        model_state.update(status="ready", name=name, device=device)
        ready.set()
        print("Модель готова.")
        if not announced:
            announced = True
            notify(t("note_model_ready"))


def request_model():
    model_req_q.put((cfg["model"], cfg["use_gpu"]))


# ---------- устройства ----------
def list_speakers():
    try:
        return [s.name for s in sc.all_speakers()]
    except Exception:
        return []


def list_microphones():
    try:
        return [m.name for m in sc.all_microphones(include_loopback=False)]
    except Exception:
        return []


def pick_speaker():
    name = cfg["speaker_name"]
    if name:
        for s in sc.all_speakers():
            if name.lower() in s.name.lower():
                return s
        print("Устройство вывода не найдено, беру по умолчанию.")
    return sc.default_speaker()


def pick_microphone():
    name = cfg["mic_name"]
    if name:
        for m in sc.all_microphones(include_loopback=False):
            if name.lower() in m.name.lower():
                return m
        print("Микрофон не найден, беру по умолчанию.")
    mic = sc.default_microphone()
    if "cable" in mic.name.lower():
        print("ВНИМАНИЕ: микрофоном по умолчанию выбран кабель. Выбери свой микрофон в настройках.")
    return mic


def find_cable():
    needle = cfg["cable_name"].lower()
    for s in sc.all_speakers():
        if needle in s.name.lower():
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

    with mic.recorder(samplerate=SAMPLE_RATE, channels=2) as rec:
        while awake.is_set():
            data = rec.record(numframes=block_frames)
            pause_blocks = max(1, int(cfg["pause_seconds"] / BLOCK_SECONDS))
            max_blocks = max(2, int(cfg["max_seconds"] / BLOCK_SECONDS))
            if tts_active.is_set():
                # пока звучит озвучка, не слушаем, иначе услышим сами себя
                buf, speaking, silent_blocks = [], False, 0
                pre_roll.clear()
                continue
            mono = data.mean(axis=1).astype(np.float32)
            loud = float(np.sqrt(np.mean(mono ** 2))) > cfg["silence_level"]

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
    """Google (gtx) -> deep-translator -> MyMemory. Сломавшийся сервис отключается на минуту."""
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


_translators = {}


def get_translator(source, target):
    key = (source, target)
    if key not in _translators:
        _translators[key] = Translator(source, target)
    return _translators[key]


def translate_loop():
    while True:
        audio = audio_q.get()
        while audio_q.qsize() > 2:      # не успеваем - пропускаем старое
            audio = audio_q.get()
        if not awake.is_set():
            continue
        try:
            src, tgt = cfg["source_lang"], cfg["target_lang"]
            with model_lock:
                m = model_ref["m"]
                if m is None:
                    continue
                segments, info = m.transcribe(
                    audio, language=None, vad_filter=True, beam_size=1,
                    condition_on_previous_text=False, without_timestamps=True,
                )
                if src == "auto":
                    wrong_language = info.language == tgt
                else:
                    wrong_language = info.language != src
                if wrong_language or info.language_probability < 0.5:
                    continue
                text = " ".join(s.text.strip() for s in segments).strip()
            if not text or not awake.is_set():
                continue
            eid = next(id_counter)
            post("orig", eid, text)
            translated = get_translator(src, tgt).translate(text)
            post("tr", eid, text, translated or t("no_translation"))
            if translated:
                S["last_tr"] = translated
                if S["dub"]:
                    dub_q.put(translated)
        except Exception as e:
            print("Ошибка распознавания:", e)


# ---------- озвучка ----------
def rate_str(percent):
    return f"{int(round(percent)):+d}%"


def tts_audio(text, voice, rate):
    """Текст -> звук (edge-tts, нужен интернет). Возвращает (float32 моно, частота)."""
    import asyncio
    import io
    import av

    async def synth():
        comm = edge_tts.Communicate(text, voice, rate=rate)
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


def voice_candidates(lang):
    options = VOICE_CHOICES.get(lang, [])
    chosen = cfg["voices"].get(lang)
    first = [chosen] if chosen else []
    return first + [v for v in options if v != chosen]


def synth_with_fallback(text, lang, rate):
    """Озвучка с запасными голосами. Голос, который сломался, больше не используется."""
    candidates = voice_candidates(lang)
    if not candidates:
        raise RuntimeError(f"нет голоса для языка {lang!r}, добавь его в config.VOICE_CHOICES")
    candidates = [v for v in candidates if v not in BAD_VOICES] or candidates
    failed = []
    last_error = None
    for voice in candidates:
        try:
            result = tts_audio(text, voice, rate)
            for v in failed:             # запасной сработал - значит, прошлый голос сломан
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
        while not dub_q.empty() and len(text) < 400:     # накопившиеся фразы склеиваем
            try:
                text += " " + dub_q.get_nowait()
            except queue.Empty:
                break
        if edge_tts is None:
            say(t("msg_need_edge"))
            continue
        if not re.search(r"\w", text):
            continue
        try:
            mono, sr = synth_with_fallback(text, cfg["target_lang"], rate_str(cfg["dub_rate"]))
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
    mono, sr = synth_with_fallback(text, lang, rate_str(cfg["own_rate"]))
    cable = find_cable()
    cable_thread = None
    if cable:
        cable_thread = threading.Thread(target=play_audio, args=(cable, mono, sr), daemon=True)
        cable_thread.start()
    else:
        notify(t("note_cable_play"))
    if cfg["monitor_own"] or not cable:
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
    cable = find_cable() if cfg["mic_passthrough"] else None
    if cfg["mic_passthrough"] and cable is None:
        notify(t("note_cable_missing"))
        print("VB-Cable не найден. Доступные устройства вывода:")
        for name in list_speakers():
            print("  -", name)
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
                    blk = silence               # пока держишь клавишу, собеседник тебя не слышит
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
            say(t("msg_need_edge"))
            return False
        voice_on.set()               # включаем голосовой режим сами
        say(t("msg_voice_on", l7=cfg["ptt_lang_7"], l8=cfg["ptt_lang_8"]))
    if not mic_ready.wait(2.0):
        say(t("msg_voice_fail"))
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
    while (u.GetAsyncKeyState(vk) & 0x8000) and time.time() - t0 < 30:
        time.sleep(0.02)
    ptt_stop()


def to_16k(x):
    n = len(x) // 3 * 3
    a = x[:n].reshape(-1, 3).mean(axis=1).astype(np.float32)
    peak = float(np.abs(a).max()) if len(a) else 0.0
    if 0.001 < peak < 0.3:
        a = a * (0.3 / peak)
    return a


def voice_out_loop():
    while True:
        audio48, lang = voice_q.get()
        try:
            me = cfg["target_lang"]
            audio = to_16k(audio48)
            with model_lock:
                m = model_ref["m"]
                if m is None:
                    continue
                segments, info = m.transcribe(
                    audio, language=me, vad_filter=True, beam_size=3,
                    condition_on_previous_text=False, without_timestamps=True,
                )
                said = " ".join(s.text.strip() for s in segments).strip()
            if not said:
                continue
            out = get_translator(me, lang).translate(said)
            if not out:
                notify(t("note_translate_fail"))
                continue
            post("out", next(id_counter), said, f"{lang.upper()} ▶ {out}")
            if edge_tts is None:
                say(t("msg_need_edge"))
                continue
            speak_to_friend(out, lang)
        except Exception as e:
            print("Ошибка голосового перевода:", e)


# ---------- горячие клавиши ----------
def hotkey_thread():
    try:
        u = ctypes.windll.user32
        registered = []

        def register_all():
            for vk in registered:
                u.UnregisterHotKey(None, vk)
            registered.clear()
            mods = HOTKEY_MODS.get(cfg["hotkey_modifier"], 0) | 0x4000   # MOD_NOREPEAT
            for vk in list(HOTKEY_ACTIONS) + list(PTT_VK):
                if u.RegisterHotKey(None, vk, mods, vk):
                    registered.append(vk)
                else:
                    print(f"Не удалось занять клавишу с кодом {hex(vk)} (занята другой программой?)")

        register_all()
        msg = wintypes.MSG()
        while True:
            if hotkeys_reload.is_set():
                hotkeys_reload.clear()
                register_all()
            if u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):      # PM_REMOVE
                if msg.message == 0x0312:                              # WM_HOTKEY
                    vk = int(msg.wParam)
                    if vk in PTT_VK:
                        lang = cfg["ptt_lang_" + PTT_VK[vk]]
                        if ptt_start(lang):
                            threading.Thread(target=watch_release, args=(vk,), daemon=True).start()
                    elif vk in HOTKEY_ACTIONS:
                        cmd_q.put(HOTKEY_ACTIONS[vk])
            else:
                time.sleep(0.01)
    except Exception as e:
        print("Ошибка горячих клавиш:", e)


def reload_hotkeys():
    hotkeys_reload.set()


# ---------- управление (вызывается из интерфейса) ----------
def set_awake(flag):
    if flag:
        drain(audio_q)
        awake.set()
        if not ready.is_set():
            notify(t("note_model_loading"))
    else:
        awake.clear()
        drain(audio_q)


def toggle_voice():
    if voice_on.is_set():
        voice_on.clear()
        say(t("msg_voice_off"))
    elif edge_tts is None:
        say(t("msg_need_edge"))
    else:
        voice_on.set()
        say(t("msg_voice_on", l7=cfg["ptt_lang_7"], l8=cfg["ptt_lang_8"]))


def toggle_dub():
    S["dub"] = not S["dub"]
    if S["dub"]:
        if not awake.is_set():
            set_awake(True)
    else:
        drain(dub_q)
        drain(play_q)
    say(t("msg_dub_on") if S["dub"] else t("msg_dub_off"))


def replay():
    if S["last_tr"]:
        dub_q.put(S["last_tr"])


def start():
    """Запускает все фоновые потоки."""
    for target in (model_worker, capture_loop, translate_loop, dub_synth_loop, dub_play_loop,
                   mic_loop, voice_out_loop, hotkey_thread):
        threading.Thread(target=target, daemon=True).start()
    request_model()
    if cfg["voice_autostart"] and edge_tts is not None:
        voice_on.set()
