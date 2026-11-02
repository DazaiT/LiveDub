"""Собирает livedub.py в программу LiveDub.exe (папка dist\\LiveDub)."""

import glob
import os
import shutil
import site
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "LiveDub"

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean", "--windowed",
    "--name", NAME,
    "--collect-all", "faster_whisper",
    "--collect-all", "ctranslate2",
    "--collect-all", "onnxruntime",
    "--collect-all", "av",
    "--collect-all", "tokenizers",
    "--collect-all", "soundcard",
    "--collect-all", "pystray",
    "--collect-all", "edge_tts",
    "--collect-all", "certifi",
    "--hidden-import", "pystray._win32",
    "livedub.py",
]
print("Собираю exe, это займёт несколько минут...")
subprocess.check_call(cmd, cwd=HERE)

dist = os.path.join(HERE, "dist", NAME)

# CUDA-библиотеки для видеокарты
cuda_target = os.path.join(dist, "cuda_libs")
os.makedirs(cuda_target, exist_ok=True)
copied = 0
for base in list(site.getsitepackages()) + [site.getusersitepackages()]:
    for sub in ("cublas", "cudnn"):
        for dll in glob.glob(os.path.join(base, "nvidia", sub, "bin", "*.dll")):
            shutil.copy(dll, cuda_target)
            copied += 1
print(f"CUDA-библиотек скопировано: {copied}")

# уже скачанные модели, чтобы не качать заново
models = os.path.join(HERE, "models")
if os.path.isdir(models):
    print("Копирую уже скачанные модели...")
    shutil.copytree(models, os.path.join(dist, "models"), dirs_exist_ok=True)

print()
print("ГОТОВО. Программа лежит здесь:")
print(os.path.join(dist, NAME + ".exe"))
