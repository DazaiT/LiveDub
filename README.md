# LiveDub

**Live speech translation and dubbing for Windows.** LiveDub listens to whatever plays on your PC (a call, a stream, a YouTube video), recognizes the speech, translates it and shows subtitles on top of your screen. It can also read the translation aloud, and in the other direction it can speak *your* words, translated, into a virtual microphone so the people in a call hear you in their language.

[Русская версия](README.ru.md)

<!-- Add a screenshot or a short GIF here, e.g. ![demo](docs/demo.gif) -->

## Features

- **Live subtitles** over any app (borderless-window games, browsers, players). The last 3 phrases stay on screen: the newest is big and bright, older ones shrink and fade, so you have time to finish reading.
- **Dubbing**: reads the translated speech aloud in your headphones.
- **Voice mode**: hold a key, speak your language, release, and the translation is spoken into a virtual microphone (works in Discord, Zoom, Teams, OBS, games...). While the key is held, your real voice is muted for the other side.
- **A real app window with settings**: light or dark theme that follows Windows, interface in English, Russian, Dutch and Ukrainian, choice of languages, Whisper model size, voices, audio devices and subtitle look. Everything applies on the fly and is saved to `settings.json`.
- **Runs in the tray** and is fully controllable from the NumPad. Subtitles can be moved between monitors.
- **Speech recognition is local** (Whisper via `faster-whisper`, on an NVIDIA GPU or on the CPU). Only short pieces of *text* are sent to online services for translation and voice synthesis.
- Any source language Whisper knows (about 99), or `auto` to translate everything that is not your language.

## How it works

```
 Everything you hear (WASAPI loopback)
          |
   energy-based phrase detection (pause 0.5 s / max 6 s)
          |
   faster-whisper (local) -> language check
          |
   translation: Google (gtx) -> deep-translator -> MyMemory
          |
          +--> subtitle overlay (Tkinter, always on top, 3-line history)
          +--> edge-tts -> your headphones            (optional dubbing)

 Your microphone --> LiveDub --> VB-Cable --> "microphone" of Discord/Zoom/OBS
   hold Num7/Num8: mic is muted for the other side, speech is recorded
   -> Whisper (your language) -> translation -> edge-tts -> VB-Cable
```

The code is split into a few small modules:

| File | What it does |
|---|---|
| [`livedub.py`](livedub.py) | entry point: logging, model folder, starts everything |
| [`engine.py`](engine.py) | all the logic, no UI: audio, recognition, translation, voices, hotkeys |
| [`ui.py`](ui.py) | settings window (customtkinter), subtitle overlay (Tkinter), tray icon (`pystray`) |
| [`config.py`](config.py) | default settings, `settings.json`, language and voice lists |
| [`i18n.py`](i18n.py) | interface translations |

`engine.py` runs a few threads that talk through queues:

| Thread / function | What it does |
|---|---|
| `capture_loop` / `record_session` | records system audio, cuts it into phrases |
| `translate_loop` | Whisper transcription, language check, translation |
| `dub_synth_loop`, `dub_play_loop` | synthesizes the next phrase while the previous one is playing |
| `mic_loop` / `mic_session` | passes your microphone to the virtual cable, mutes it while you hold the key |
| `voice_out_loop` | your speech -> translation -> synthesized voice into the cable |
| `hotkey_thread` | global NumPad hotkeys (`RegisterHotKey`), detects key release for push-to-talk |
| `model_worker` | loads or reloads the Whisper model in the background |

Only the main thread touches Tkinter: hotkeys, tray and the engine put commands and messages into queues (`cmd_q`, `text_q`), and `ui.py` processes them.

## Requirements

- Windows 10/11
- Python 3.10+ (tick **Add Python to PATH** in the installer)
- Internet (translation and voice synthesis are online)
- Optional: NVIDIA GPU for faster recognition
- For voice mode: [VB-Cable](https://vb-audio.com/Cable/) (free virtual audio cable)

## Install and run

```
git clone https://github.com/DazaiT/livedub.git
cd livedub
pip install -r requirements.txt
python livedub.py
```

The first start downloads the Whisper model (about 1.5 GB for `medium`) into the `models` folder next to the script. For a silent start without a console window, use `start.bat`.

The settings window opens and the subtitles start asleep. Turn them on with the switch in the window or press **Num1** (NumLock on). Closing the window keeps the program running in the tray.

### Settings

Open the **Languages**, **Audio**, **Subtitles**, **Model** and **App** tabs of the window. Changes apply immediately and are saved to `settings.json` next to the program (delete it to reset everything). The main ones:

- **Languages**: what the other people speak (or auto-detect), your own language, and the languages Num7 / Num8 translate your speech into. The voices are chosen here too.
- **Model**: `tiny`, `base`, `small`, `medium`, `large-v3`, on GPU or CPU. Press *Apply* to reload. `small` is lighter on the GPU in games.
- **App**: interface language, theme (system / light / dark), start minimized to the tray, hotkey modifier.

### NVIDIA GPU

The script tries the GPU first and falls back to the CPU by itself. If GPU mode complains about missing libraries, run `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`.

### Voice mode setup (one time)

1. Install VB-Cable and reboot.
2. In your call app (Discord, Zoom, ...) choose **CABLE Output** as the microphone.
3. Keep your real microphone as the Windows default (or pick it in the **Audio** tab).
4. Hold **Num7** or **Num8**, speak, release.

Voice mode starts together with the program, because the call app now listens to the virtual cable. Press **Num6** to turn it off.

## Controls (NumLock on)

| Key | Action |
|---|---|
| Num1 | subtitles on / off (sleep) |
| Num2 | move subtitles to the next monitor |
| Num3 | show / hide the original text |
| Num4 / Num5 | smaller / bigger text |
| Num6 | voice mode on / off |
| Num7 / Num8 (hold) | speak -> translation into the language set in the **Languages** tab |
| Num0 | dubbing of the other person on / off |
| Num. | repeat the last phrase aloud |
| Num9 | quit |

Plain NumPad keys are taken by the program while it runs. In the **App** tab you can switch the hotkeys to Ctrl+Num (or Alt, or Ctrl+Alt) instead. Subtitles can be dragged with the mouse; right click clears them. The tray icon has the same actions.

## Build an .exe (optional)

Run `build.bat`. The result is in `dist\LiveDub`. This build is experimental, running `start.bat` is the reliable way.

## Limitations

- Windows only (WASAPI loopback, global hotkeys, tray).
- While dubbing is playing in your headphones, recognition is paused, otherwise the program would hear and translate its own voice.
- Phrases are cut by pauses, so very fast speech is translated in pieces. In the **Model** tab raise the pause and maximum phrase length for more complete sentences, at the cost of delay.
- Exclusive-fullscreen games hide the overlay; use borderless windowed mode.
- Code comments are in Russian (the interface itself has 4 languages, see `i18n.py` to add more).

## Privacy and consent

Audio is processed locally. Recognized **text** is sent to Google Translate (unofficial web endpoint), MyMemory as a fallback, and Microsoft Edge text-to-speech. These are unofficial, free services: they can change or stop working, and they are meant for personal use. If you capture other people's voices, tell them, and follow the rules of the service you use.

## Third-party software

LiveDub is licensed under MIT, but it depends on other projects with their own licenses, notably [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and OpenAI Whisper models (MIT), [soundcard](https://github.com/bastibe/SoundCard), [deep-translator](https://github.com/nidhaloff/deep-translator), [pystray](https://github.com/moses-palmer/pystray) and [edge-tts](https://github.com/rany2/edge-tts) (GPL-3.0). Dependencies are installed by the user through `pip` and are not included in this repository. If you distribute a built `.exe` that bundles them, check their licenses first.

## License

[MIT](LICENSE)
