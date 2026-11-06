"""LiveDub - живой перевод и озвучка речи для Windows. Точка входа."""

import os
import site
import sys

import config

# --- без консоли (exe или pythonw) пишем сообщения в livedub.log ---
if config.FROZEN or sys.stdout is None or sys.stderr is None:
    try:
        _log = open(os.path.join(config.BASE_DIR, "livedub.log"), "w", encoding="utf-8", buffering=1)
        sys.stdout = _log
        sys.stderr = _log
    except Exception:
        pass

# --- модели хранятся рядом с программой, а не в профиле пользователя ---
os.makedirs(config.MODELS_DIR, exist_ok=True)
os.environ["HF_HOME"] = config.MODELS_DIR
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"


def add_cuda_paths():
    """Подключает CUDA-библиотеки (cublas/cudnn), если они есть рядом или установлены через pip."""
    paths = [os.path.join(config.BASE_DIR, "cuda_libs")]
    for base in list(site.getsitepackages()) + [site.getusersitepackages()]:
        for sub in ("cublas", "cudnn"):
            paths.append(os.path.join(base, "nvidia", sub, "bin"))
    for p in paths:
        if os.path.isdir(p):
            try:
                os.add_dll_directory(p)
            except Exception:
                pass
            os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")


add_cuda_paths()

config.load()

import i18n  # noqa: E402

i18n.load_external()   # locales/*.json рядом с программой
code = config.cfg["ui_language"]
i18n.set_language(config.detect_ui_language() if code == "auto" else code)

import engine  # noqa: E402  (импорт после настройки окружения)
import ui  # noqa: E402


def main():
    engine.enable_dpi_awareness()
    engine.start()
    ui.App().run()


if __name__ == "__main__":
    main()
