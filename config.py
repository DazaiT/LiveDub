"""Настройки LiveDub: значения по умолчанию, чтение и сохранение settings.json."""

import json
import os
import re
import sys

APP_NAME = "LiveDub"
APP_VERSION = "1.1.0"

FROZEN = getattr(sys, "frozen", False)
BASE_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")

# Языки перевода. Встроенные языки лежат здесь, а новые можно добавлять БЕЗ правки кода:
# положи файл languages.json рядом с программой (формат смотри в README).
BUILTIN_LANGUAGES = {
    "nl": {"name": "Nederlands", "voices": ["nl-NL-MaartenNeural", "nl-NL-FennaNeural", "nl-NL-ColetteNeural"], "mymemory": "dutch"},
    "ru": {"name": "Русский", "voices": ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"], "mymemory": "russian"},
    "en": {"name": "English", "voices": ["en-US-GuyNeural", "en-US-JennyNeural", "en-GB-RyanNeural", "en-GB-SoniaNeural"], "mymemory": "english"},
    "de": {"name": "Deutsch", "voices": ["de-DE-ConradNeural", "de-DE-KatjaNeural"], "mymemory": "german"},
    "fr": {"name": "Français", "voices": ["fr-FR-HenriNeural", "fr-FR-DeniseNeural"], "mymemory": "french"},
    "es": {"name": "Español", "voices": ["es-ES-AlvaroNeural", "es-ES-ElviraNeural"], "mymemory": "spanish"},
    "it": {"name": "Italiano", "voices": ["it-IT-DiegoNeural", "it-IT-ElsaNeural"], "mymemory": "italian"},
    "pl": {"name": "Polski", "voices": ["pl-PL-MarekNeural", "pl-PL-ZofiaNeural"], "mymemory": "polish"},
    "uk": {"name": "Українська", "voices": ["uk-UA-OstapNeural", "uk-UA-PolinaNeural"], "mymemory": "ukrainian"},
    "pt": {"name": "Português", "voices": ["pt-BR-AntonioNeural", "pt-BR-FranciscaNeural"], "mymemory": "portuguese"},
    "tr": {"name": "Türkçe", "voices": ["tr-TR-AhmetNeural", "tr-TR-EmelNeural"], "mymemory": "turkish"},
    "ja": {"name": "日本語", "voices": ["ja-JP-KeitaNeural", "ja-JP-NanamiNeural"], "mymemory": "japanese"},
    "da": {"name": "Dansk", "voices": ["da-DK-JeppeNeural", "da-DK-ChristelNeural"], "mymemory": "danish"},
    "sv": {"name": "Svenska", "voices": ["sv-SE-MattiasNeural", "sv-SE-SofieNeural"], "mymemory": "swedish"},
    "no": {"name": "Norsk", "voices": ["nb-NO-FinnNeural", "nb-NO-PernilleNeural"], "mymemory": "norwegian"},
    "cs": {"name": "Čeština", "voices": ["cs-CZ-AntoninNeural", "cs-CZ-VlastaNeural"], "mymemory": "czech"},
    "fi": {"name": "Suomi", "voices": ["fi-FI-HarriNeural", "fi-FI-NooraNeural"], "mymemory": "finnish"},
    "ro": {"name": "Română", "voices": ["ro-RO-EmilNeural", "ro-RO-AlinaNeural"], "mymemory": "romanian"},
    "el": {"name": "Ελληνικά", "voices": ["el-GR-NestorasNeural", "el-GR-AthinaNeural"], "mymemory": "greek"},
    "hu": {"name": "Magyar", "voices": ["hu-HU-TamasNeural", "hu-HU-NoemiNeural"], "mymemory": "hungarian"},
    "bg": {"name": "Български", "voices": ["bg-BG-BorislavNeural", "bg-BG-KalinaNeural"], "mymemory": "bulgarian"},
    "ko": {"name": "한국어", "voices": ["ko-KR-InJoonNeural", "ko-KR-SunHiNeural"], "mymemory": "korean"},
    "zh": {"name": "中文", "voices": ["zh-CN-YunxiNeural", "zh-CN-XiaoxiaoNeural"], "google": "zh-CN"},
}

GITHUB_REPO = "DazaiT/livedub"
LANGUAGES_PATH = os.path.join(BASE_DIR, "languages.json")

LANG_NAMES = {}       # код -> название (заполняется в load_languages)
VOICE_CHOICES = {}    # код -> список голосов Edge TTS, первый - по умолчанию
GOOGLE_CODES = {}     # код Whisper -> код Google Translate, если они отличаются
MYMEMORY = {}         # код -> название языка для MyMemory (запасной переводчик)

MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v3"]

_CODE_RE = re.compile(r"^[a-z]{2,3}$")
_VOICE_RE = re.compile(r"^[a-z]{2,3}-[A-Za-z]{2,4}-[A-Za-z0-9]+Neural$")


def validate_languages(data):
    """Оставляет только корректные записи языков (защита от битых и чужих файлов)."""
    clean = {}
    if not isinstance(data, dict):
        return clean
    for code, entry in data.items():
        try:
            if not isinstance(code, str) or not _CODE_RE.match(code) or not isinstance(entry, dict):
                continue
            name, voices = entry.get("name"), entry.get("voices")
            if not isinstance(name, str) or not name.strip() or len(name) > 40:
                continue
            if not isinstance(voices, list) or not voices:
                continue
            if not all(isinstance(v, str) and _VOICE_RE.match(v) for v in voices):
                continue
            item = {"name": name.strip(), "voices": list(voices)}
            google = entry.get("google")
            if isinstance(google, str) and re.match(r"^[A-Za-z-]{2,8}$", google):
                item["google"] = google
            mymemory = entry.get("mymemory")
            if isinstance(mymemory, str) and re.match(r"^[a-z ]{3,30}$", mymemory):
                item["mymemory"] = mymemory
            clean[code] = item
        except Exception:
            continue
    return clean


def load_languages():
    """Встроенные языки + languages.json рядом с программой."""
    data = dict(BUILTIN_LANGUAGES)
    try:
        with open(LANGUAGES_PATH, encoding="utf-8") as f:
            data.update(validate_languages(json.load(f)))
    except FileNotFoundError:
        pass
    except Exception as e:
        print("Не удалось прочитать languages.json:", e)
    for d in (LANG_NAMES, VOICE_CHOICES, GOOGLE_CODES, MYMEMORY):
        d.clear()
    for code, entry in data.items():
        LANG_NAMES[code] = entry["name"]
        VOICE_CHOICES[code] = list(entry["voices"])
        if entry.get("google"):
            GOOGLE_CODES[code] = entry["google"]
        if entry.get("mymemory"):
            MYMEMORY[code] = entry["mymemory"]


def google_code(lang):
    return GOOGLE_CODES.get(lang, lang)


load_languages()

DEFAULTS = {
    # приложение
    "ui_language": "auto",        # "auto" = язык Windows
    "theme": "system",            # system / light / dark
    "start_minimized": False,
    "hotkey_modifier": "none",    # none / ctrl / alt / ctrl+alt
    # языки
    "source_lang": "nl",          # на каком языке говорят другие ("auto" = любой, кроме твоего)
    "target_lang": "ru",          # твой язык
    "ptt_lang_7": "nl",           # Num7 (зажать) -> на какой язык переводить твою речь
    "ptt_lang_8": "en",           # Num8 (зажать)
    "voices": {},                 # выбранные голоса: код языка -> имя голоса
    "dub_rate": 20,               # скорость озвучки собеседника, %
    "own_rate": 10,               # скорость озвучки твоего перевода, %
    # распознавание
    "model": "medium",
    "use_gpu": True,
    "pause_seconds": 0.5,
    "max_seconds": 6.0,
    "silence_level": 0.003,
    # звук
    "speaker_name": "",           # пусто = устройство вывода по умолчанию
    "mic_name": "",               # пусто = микрофон по умолчанию
    "cable_name": "CABLE Input",
    "mic_passthrough": True,
    "monitor_own": True,
    "beeps": True,
    "voice_autostart": True,
    # субтитры
    "font_size": 20,
    "max_lines": 3,
    "wrap_width": 1000,
    "show_seconds": 12,
    "show_original": False,
    "opacity": 0.88,
    "bottom_margin": 90,
    "monitor": 0,
}

cfg = dict(DEFAULTS)


def _compatible(default, value):
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, dict):
        return isinstance(value, dict)
    return True


def load():
    """Читает settings.json поверх значений по умолчанию."""
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for key, value in data.items():
            if key in DEFAULTS and _compatible(DEFAULTS[key], value):
                cfg[key] = value
    except FileNotFoundError:
        pass
    except Exception as e:
        print("Не удалось прочитать settings.json:", e)


def save():
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Не удалось сохранить settings.json:", e)


def detect_ui_language():
    """Язык интерфейса Windows -> код языка приложения."""
    try:
        import ctypes
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
        return {0x19: "ru", 0x09: "en", 0x13: "nl", 0x22: "uk"}.get(lang_id, "en")
    except Exception:
        return "en"
