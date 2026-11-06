# Interface languages / Языки интерфейса

Every `xx.json` file in this folder is an interface language (`xx` = language code, e.g. `da`).
Drop a new file here and restart LiveDub: it appears in **App -> App language**. No rebuild needed.

1. Copy `_template.json` to `xx.json`.
2. Set `"name"` (how the language is shown in the menu) and translate the values under `"strings"`.
   Keep `{placeholders}` such as `{model}` unchanged. Missing keys fall back to English.
3. Restart the program.

Файл `xx.json` в этой папке - язык интерфейса. Скопируй `_template.json` в `xx.json`, переведи значения,
перезапусти программу. Пересобирать `.exe` не нужно.
