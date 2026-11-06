"""Обновление языковых пакетов и проверка новой версии через GitHub.

Скачиваются ТОЛЬКО файлы с данными (locales/*.json и languages.json). Каждый файл проверяется
и перезаписывается очищенным содержимым, поэтому код программы такие обновления не меняют.
"""

import json
import os
import re

import requests

import config
import i18n

HEADERS = {"User-Agent": "LiveDub"}
API = f"https://api.github.com/repos/{config.GITHUB_REPO}"
RAW = f"https://raw.githubusercontent.com/{config.GITHUB_REPO}/main"
LOCALE_FILE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?\.json$")


def _get_json(url):
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()


def update_language_packs():
    """Возвращает (сколько файлов обновлено, текст ошибки или None)."""
    count = 0
    try:
        folder = os.path.join(config.BASE_DIR, "locales")
        os.makedirs(folder, exist_ok=True)
        for item in _get_json(f"{API}/contents/locales"):
            name = item.get("name", "") if isinstance(item, dict) else ""
            if not LOCALE_FILE.match(name):
                continue
            data = _get_json(f"{RAW}/locales/{name}")
            strings = i18n.validate_pack(data.get("strings")) if isinstance(data, dict) else {}
            if not strings:
                continue
            clean = {"name": str(data.get("name", name[:-5]))[:40], "strings": strings}
            with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                json.dump(clean, f, ensure_ascii=False, indent=2)
            count += 1

        # languages.json: добавляем новые языки, свои записи пользователя не трогаем
        try:
            downloaded = config.validate_languages(_get_json(f"{RAW}/languages.json"))
            try:
                with open(config.LANGUAGES_PATH, encoding="utf-8") as f:
                    merged = config.validate_languages(json.load(f))
            except Exception:
                merged = {}
            merged.update(downloaded)
            with open(config.LANGUAGES_PATH, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
            count += 1
        except Exception as e:
            print("languages.json не обновлён:", e)

        config.load_languages()
        i18n.load_external()
        return count, None
    except Exception as e:
        return count, str(e)


def _version(text):
    return tuple(int(x) for x in re.findall(r"\d+", text.split("-")[0])[:3])


def is_newer(latest, current):
    try:
        return _version(latest) > _version(current)
    except Exception:
        return False


def latest_release():
    """Возвращает (версия, ссылка на страницу релиза, текст ошибки или None)."""
    try:
        data = _get_json(f"{API}/releases/latest")
        url = data.get("html_url") or f"https://github.com/{config.GITHUB_REPO}/releases"
        if not str(url).startswith("https://github.com/"):
            url = f"https://github.com/{config.GITHUB_REPO}/releases"
        return str(data.get("tag_name", "")).lstrip("vV"), url, None
    except Exception as e:
        return "", "", str(e)
