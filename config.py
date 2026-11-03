"""Настройки LiveDub: значения по умолчанию, чтение и сохранение settings.json."""

import json
import os
import sys

APP_NAME = "LiveDub"
APP_VERSION = "1.0.0"

FROZEN = getattr(sys, "frozen", False)
BASE_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")

# Языки перевода (код -> название на самом языке)
LANG_NAMES = {
    "nl": "Nederlands", "ru": "Русский", "en": "English", "de": "Deutsch",
    "fr": "Français", "es": "Español", "it": "Italiano", "pl": "Polski",
    "uk": "Українська", "pt": "Português", "tr": "Türkçe", "ja": "日本語",
}

MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v3"]

# Голоса озвучки (Edge TTS). Первый в списке - голос по умолчанию,
# остальные используются как запасные.
VOICE_CHOICES = {
    "ru": ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"],
    "nl": ["nl-NL-MaartenNeural", "nl-NL-FennaNeural", "nl-NL-ColetteNeural"],
    "en": ["en-US-GuyNeural", "en-US-JennyNeural", "en-GB-RyanNeural", "en-GB-SoniaNeural"],
    "de": ["de-DE-ConradNeural", "de-DE-KatjaNeural"],
    "fr": ["fr-FR-HenriNeural", "fr-FR-DeniseNeural"],
    "es": ["es-ES-AlvaroNeural", "es-ES-ElviraNeural"],
    "it": ["it-IT-DiegoNeural", "it-IT-ElsaNeural"],
    "pl": ["pl-PL-MarekNeural", "pl-PL-ZofiaNeural"],
    "uk": ["uk-UA-OstapNeural", "uk-UA-PolinaNeural"],
    "pt": ["pt-BR-AntonioNeural", "pt-BR-FranciscaNeural"],
    "tr": ["tr-TR-AhmetNeural", "tr-TR-EmelNeural"],
    "ja": ["ja-JP-KeitaNeural", "ja-JP-NanamiNeural"],
}

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
