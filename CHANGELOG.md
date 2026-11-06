# Changelog

## 1.1.0
- Languages are now data files: add translation languages in `languages.json` and interface languages in `locales/xx.json`, no code changes or rebuild needed.
- 23 built-in translation languages (added Danish, Swedish, Norwegian, Czech, Finnish, Romanian, Greek, Hungarian, Bulgarian, Korean, Chinese); Danish interface included as an example.
- *App* tab: **Update language packs** (downloads validated data files from this repository) and **Check for new version**.
- Speed preset (Fast / Balanced / Smooth) in the *Model* tab.
- Lower delay: faster phrase-end detection, reused connection for translation, faster recognition of your own speech.
- `livedub.log` now prints the time of every stage (`[задержка] ...`).
- Fixed the light theme: the window is rebuilt when the theme changes, so it no longer shows dark panels with dark text.

## 1.0.0
- Settings window (customtkinter) with light/dark theme, interface in English, Russian, Dutch and Ukrainian.
- Choice of languages, Whisper model size, voices, audio devices and subtitle look; settings saved to `settings.json`.
- Code split into modules: `engine.py`, `ui.py`, `config.py`, `i18n.py`.

## 0.1.0-alpha
- First working version: live subtitles, dubbing, push-to-talk voice translation, tray icon, NumPad control.
