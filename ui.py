"""Интерфейс LiveDub: окно настроек (customtkinter), субтитры поверх экрана, значок в трее."""

import ctypes
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser

import customtkinter as ctk

import config
import engine
import updater
from config import APP_NAME, APP_VERSION, LANG_NAMES, MODEL_SIZES, VOICE_CHOICES, cfg
from i18n import LANGUAGES, set_language, t

COLORS = {0: "#ffffff", 1: "#b0b0b0", 2: "#7d7d7d"}   # новая строка ярче, старые тусклее
SIZE_STEP = {0: 0, 1: 4, 2: 6}                          # старые строки мельче
MINE_COLOR = "#8fd3ff"                                  # твои переведённые фразы
MAX_LABELS = 5
GRAY = ("gray40", "gray60")
PRESETS = [(0.3, 4.0), (0.5, 6.0), (0.8, 9.0)]       # быстро / баланс / плавно: пауза, макс. длина фразы


def resolve_language():
    code = cfg["ui_language"]
    return config.detect_ui_language() if code == "auto" else code


def make_tray_image(active):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (46, 204, 113, 255) if active else (140, 140, 140, 255)
    d.rounded_rectangle((2, 8, 62, 56), radius=10, fill=color)
    d.rectangle((14, 22, 50, 28), fill="white")
    d.rectangle((14, 36, 38, 42), fill="white")
    return img


class App:
    def __init__(self):
        self.entries = []                    # строки субтитров
        self.ov = {"visible": False, "anchor": None, "dx": 0, "dy": 0}
        self.tray = None
        self.sync = {}                       # ключ настройки -> функция обновления виджета
        self.model_labels = []
        self.voices_frame = None
        self.tabs = None
        self.tab_names = []
        self.save_job = None
        self.last_status = None
        self.hidden_notified = False

        ctk.set_default_color_theme("blue")
        self.apply_theme()
        self.root = ctk.CTk()
        self.root.title(APP_NAME)
        self.root.geometry("880x680")
        self.root.minsize(780, 580)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.last_mode = ctk.get_appearance_mode()
        self.build_overlay()
        self.build_ui()
        self.build_tray()
        if cfg["start_minimized"] and self.tray is not None:
            self.root.withdraw()
        self.poll()

    # ------------------------------------------------------------ тема
    def apply_theme(self):
        mode = {"system": "System", "light": "Light", "dark": "Dark"}.get(cfg["theme"], "System")
        ctk.set_appearance_mode(mode)

    def check_theme(self):
        """Тема сменилась (в настройках или в самой Windows): пересоздаём окно с нуля.

        customtkinter не перекрашивает вложенные блоки на лету, из-за этого светлая тема
        выглядела сломанной (тёмные плашки и тёмный текст).
        """
        mode = ctk.get_appearance_mode()
        if mode != self.last_mode:
            self.last_mode = mode
            self.root.after(250, self.rebuild_all)

    # ------------------------------------------------------------ субтитры поверх экрана
    def build_overlay(self):
        self.overlay = tk.Toplevel(self.root)
        self.overlay.overrideredirect(True)
        self.overlay.attributes("-topmost", True)
        self.overlay.attributes("-alpha", cfg["opacity"])
        self.overlay.configure(bg="black")
        self.labels = []
        for _ in range(MAX_LABELS):
            lbl = tk.Label(self.overlay, text="", font=("Segoe UI", int(cfg["font_size"])),
                           fg="white", bg="black", wraplength=int(cfg["wrap_width"]),
                           justify="center", padx=14, pady=3)
            lbl.bind("<Button-1>", self.start_drag)
            lbl.bind("<B1-Motion>", self.do_drag)
            lbl.bind("<Button-3>", self.clear_subs)
            self.labels.append(lbl)
        # окно не забирает фокус, не показывается в панели задач и всегда сверху
        try:
            self.overlay.update_idletasks()
            u = ctypes.windll.user32
            hwnd = u.GetParent(self.overlay.winfo_id()) or self.overlay.winfo_id()
            style = u.GetWindowLongW(hwnd, -20)
            style |= 0x08000000 | 0x00000080 | 0x00000008   # NOACTIVATE | TOOLWINDOW | TOPMOST
            u.SetWindowLongW(hwnd, -20, style)
        except Exception:
            pass
        self.overlay.withdraw()

    def default_anchor(self):
        mons = engine.get_monitors()
        if mons:
            idx = min(int(cfg["monitor"]), len(mons) - 1)
            left, top, right, bottom = mons[idx]
            return (left + right) // 2, bottom - int(cfg["bottom_margin"])
        return (self.overlay.winfo_screenwidth() // 2,
                self.overlay.winfo_screenheight() - int(cfg["bottom_margin"]))

    def render(self):
        n = max(1, min(MAX_LABELS, int(cfg["max_lines"])))
        vis = self.entries[-n:]
        for lbl in self.labels:
            lbl.pack_forget()
        if not vis:
            self.overlay.withdraw()
            self.ov["visible"] = False
            return
        for idx, e in enumerate(vis):
            rank = min(len(vis) - 1 - idx, 2)
            size = max(10, int(cfg["font_size"]) - SIZE_STEP[rank])
            if e["tr"] is None:
                text = e["orig"]
                color = "#9a9a9a" if rank == 0 else COLORS[rank]
                font = ("Segoe UI", size, "italic")
            else:
                if e.get("mine"):
                    text = e["tr"] + f"\n({e['orig']})"
                    color = MINE_COLOR if rank == 0 else COLORS[rank]
                else:
                    text = e["tr"] + (f"\n({e['orig']})" if cfg["show_original"] else "")
                    color = COLORS[rank]
                font = ("Segoe UI", size, "bold" if rank == 0 else "normal")
            lbl = self.labels[idx]
            lbl.config(text=text, fg=color, font=font, wraplength=int(cfg["wrap_width"]))
            lbl.pack(fill="x")
        self.overlay.update_idletasks()
        w, h = self.overlay.winfo_reqwidth(), self.overlay.winfo_reqheight()
        cx, bottom = self.ov["anchor"] or self.default_anchor()
        self.overlay.geometry(f"+{cx - w // 2}+{bottom - h}")
        if not self.ov["visible"]:
            self.overlay.deiconify()
            self.ov["visible"] = True

    def preview(self, text):
        self.entries = [e for e in self.entries if e["id"] != -1]
        self.entries.append({"id": -1, "orig": text, "tr": text, "t": time.time()})
        del self.entries[:-MAX_LABELS]
        self.render()

    def start_drag(self, e):
        self.ov["dx"], self.ov["dy"] = e.x, e.y

    def do_drag(self, e):
        x = self.overlay.winfo_x() + e.x - self.ov["dx"]
        y = self.overlay.winfo_y() + e.y - self.ov["dy"]
        self.overlay.geometry(f"+{x}+{y}")
        self.ov["anchor"] = (x + self.overlay.winfo_width() // 2, y + self.overlay.winfo_height())

    def clear_subs(self, _e=None):
        self.entries.clear()
        self.render()

    # ------------------------------------------------------------ трей
    def build_tray(self):
        try:
            import pystray
        except Exception as e:
            print("Трей недоступен (установи pystray и pillow):", e)
            return

        def put(cmd):
            return lambda icon, item: engine.cmd_q.put(cmd)

        menu = pystray.Menu(
            pystray.MenuItem(lambda item: t("tray_open"), put("open_ui"), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: t("tray_subtitles"), put("toggle"),
                             checked=lambda item: engine.awake.is_set()),
            pystray.MenuItem(lambda item: t("tray_voice"), put("voice"),
                             checked=lambda item: engine.voice_on.is_set()),
            pystray.MenuItem(lambda item: t("tray_dub"), put("dub"),
                             checked=lambda item: engine.S["dub"]),
            pystray.MenuItem(lambda item: t("tray_replay"), put("replay")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: t("tray_quit"), put("quit")),
        )
        try:
            self.tray = pystray.Icon("livedub", make_tray_image(False), APP_NAME, menu)
            self.tray.run_detached()
        except Exception as e:
            print("Не удалось создать значок в трее:", e)
            self.tray = None

    def update_tray(self):
        if self.tray is None:
            return
        try:
            active = engine.awake.is_set() or engine.voice_on.is_set()
            self.tray.icon = make_tray_image(active)
            self.tray.title = APP_NAME
            self.tray.update_menu()
        except Exception:
            pass

    def notify(self, text):
        if self.tray is None:
            return
        try:
            self.tray.notify(text, APP_NAME)
        except Exception:
            pass

    # ------------------------------------------------------------ окно: показать, скрыть, выйти
    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def on_close(self):
        if self.tray is not None:
            self.root.withdraw()
            if not self.hidden_notified:
                self.hidden_notified = True
                self.notify(t("tray_hidden"))
        else:
            self.quit()

    def quit(self):
        config.save()
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        os._exit(0)

    # ------------------------------------------------------------ настройки
    def set(self, key, value):
        cfg[key] = value
        cb = self.sync.get(key)
        if cb:
            try:
                cb(value)
            except Exception:
                pass
        self.schedule_save()
        self.after_change(key)

    def schedule_save(self):
        if self.save_job is not None:
            try:
                self.root.after_cancel(self.save_job)
            except Exception:
                pass
        self.save_job = self.root.after(600, self.do_save)

    def do_save(self):
        self.save_job = None
        config.save()

    def restart_audio(self, which):
        """Перезапускает захват звука или микрофон, чтобы подхватить новое устройство."""
        ev = engine.awake if which == "capture" else engine.voice_on
        if ev.is_set():
            ev.clear()
            self.root.after(500, ev.set)

    def after_change(self, key):
        if key == "theme":
            self.apply_theme()
        elif key == "ui_language":
            set_language(resolve_language())
            self.rebuild_all()
        elif key == "monitor":
            self.ov["anchor"] = None
            self.preview(t("msg_monitor"))
        elif key in ("font_size", "max_lines", "wrap_width", "show_original", "show_seconds", "bottom_margin"):
            self.preview(t("msg_test"))
        elif key == "opacity":
            self.overlay.attributes("-alpha", float(cfg["opacity"]))
        elif key == "hotkey_modifier":
            engine.reload_hotkeys()
        elif key in ("target_lang", "ptt_lang_7", "ptt_lang_8"):
            self.build_voice_rows()
        elif key == "speaker_name":
            self.restart_audio("capture")
        elif key in ("mic_name", "cable_name", "mic_passthrough"):
            self.restart_audio("mic")

    def rebuild_all(self):
        idx = 0
        try:
            idx = self.tab_names.index(self.tabs.get())
        except Exception:
            pass
        self.build_ui(select=idx)
        self.update_tray()

    # ------------------------------------------------------------ команды (клавиши, трей, переключатели)
    def handle(self, cmd):
        if cmd == "toggle":
            engine.set_awake(not engine.awake.is_set())
            if not engine.awake.is_set():
                self.entries = [e for e in self.entries if e.get("mine") or e["id"] == -1]
                self.render()
        elif cmd == "monitor_next":
            n = len(engine.get_monitors()) or 1
            self.set("monitor", (int(cfg["monitor"]) + 1) % n)
        elif cmd == "orig":
            self.set("show_original", not cfg["show_original"])
        elif cmd == "font+":
            self.set("font_size", min(40, int(cfg["font_size"]) + 2))
        elif cmd == "font-":
            self.set("font_size", max(12, int(cfg["font_size"]) - 2))
        elif cmd == "voice":
            engine.toggle_voice()
        elif cmd == "dub":
            engine.toggle_dub()
        elif cmd == "replay":
            engine.replay()
        elif cmd == "open_ui":
            self.show_window()
        elif cmd == "rebuild_ui":
            self.rebuild_all()
        elif cmd == "quit":
            self.quit()
        self.last_status = None      # заставляем переключатели обновиться

    def poll(self):
        changed = False
        try:
            while True:
                self.handle(engine.cmd_q.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                kind, a, b, c = engine.text_q.get_nowait()
                if kind == "msg":
                    self.preview(a)
                    continue
                if kind == "notify":
                    self.notify(a)
                    continue
                now = time.time()
                if kind == "out":
                    self.entries.append({"id": a, "orig": b, "tr": c, "t": now, "mine": True})
                    del self.entries[:-MAX_LABELS]
                    changed = True
                    continue
                if not engine.awake.is_set():
                    continue
                if kind == "orig":
                    self.entries.append({"id": a, "orig": b, "tr": None, "t": now})
                    del self.entries[:-MAX_LABELS]
                else:   # "tr"
                    for e in self.entries:
                        if e["id"] == a:
                            e["tr"] = c
                            e["t"] = now
                            break
                    else:
                        self.entries.append({"id": a, "orig": b, "tr": c, "t": now})
                        del self.entries[:-MAX_LABELS]
                changed = True
        except queue.Empty:
            pass
        now = time.time()
        before = len(self.entries)
        self.entries = [e for e in self.entries if now - e["t"] < float(cfg["show_seconds"])]
        if len(self.entries) != before:
            changed = True
        if changed:
            self.render()
        if self.ov["visible"]:
            self.overlay.attributes("-topmost", True)
            self.overlay.lift()
        self.refresh_status()
        self.check_theme()
        self.root.after(150, self.poll)

    def model_text(self):
        st = engine.model_state
        if st["status"] == "loading":
            return t("model_loading", model=st["name"])
        if st["status"] == "ready":
            return t("model_ready", model=st["name"], device=st["device"])
        if st["status"] == "error":
            return t("model_error")
        return t("model_idle")

    def refresh_status(self):
        st = (engine.awake.is_set(), engine.voice_on.is_set(), engine.S["dub"],
              engine.model_state["status"], engine.model_state["name"], engine.model_state["device"])
        if st == self.last_status:
            return
        self.last_status = st
        for name, value in (("sub", st[0]), ("voice", st[1]), ("dub", st[2])):
            var = getattr(self, "var_" + name, None)
            try:
                if var is not None and var.get() != value:
                    var.set(value)
            except Exception:
                pass
        text = self.model_text()
        for lbl in list(self.model_labels):
            try:
                lbl.configure(text=text)
            except Exception:
                pass
        self.update_tray()

    # ------------------------------------------------------------ обновления и пресеты
    def apply_preset(self, display):
        names = [t("preset_fast"), t("preset_balanced"), t("preset_smooth")]
        if display in names:
            pause, longest = PRESETS[names.index(display)]
            self.set("pause_seconds", pause)
            self.set("max_seconds", longest)

    def do_update_packs(self):
        def work():
            count, err = updater.update_language_packs()
            if err:
                print("Обновление языковых пакетов:", err)
                engine.say(t("upd_packs_fail"))
            else:
                engine.say(t("upd_packs_ok", n=count))
                engine.cmd_q.put("rebuild_ui")
        threading.Thread(target=work, daemon=True).start()

    def do_check_updates(self):
        def work():
            version, url, err = updater.latest_release()
            if err:
                print("Проверка обновлений:", err)
                engine.say(t("upd_fail"))
            elif updater.is_newer(version, APP_VERSION):
                engine.say(t("upd_new", v=version))
                webbrowser.open(url)
            else:
                engine.say(t("upd_latest"))
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ виджеты-помощники
    def scroll(self, frame):
        sc = ctk.CTkScrollableFrame(frame, fg_color="transparent")
        sc.pack(fill="both", expand=True)
        return sc

    def heading(self, parent, text):
        ctk.CTkLabel(parent, text=text, anchor="w",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(fill="x", padx=8, pady=(14, 2))

    def hint(self, parent, text):
        ctk.CTkLabel(parent, text=text, anchor="w", justify="left", wraplength=640,
                     text_color=GRAY).pack(fill="x", padx=8, pady=(2, 6))

    def row(self, parent, label):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", pady=4, padx=4)
        ctk.CTkLabel(f, text=label, anchor="w", justify="left", wraplength=340).pack(side="left", padx=(4, 10))
        return f

    @staticmethod
    def display_for(options, value):
        for v, d in options:
            if v == value:
                return d
        if isinstance(value, str) and value:
            for v, d in options:
                if isinstance(v, str) and value.lower() in v.lower():
                    return d
        return options[0][1] if options else ""

    def option(self, parent, label, options, key, width=290):
        rev = {d: v for v, d in options}
        row = self.row(parent, label)
        menu = ctk.CTkOptionMenu(row, values=[d for _, d in options] or [""], width=width,
                                 command=lambda choice: self.set(key, rev.get(choice, choice)))
        menu.set(self.display_for(options, cfg[key]))
        menu.pack(side="right", padx=4)
        self.sync[key] = lambda v: menu.set(self.display_for(options, v))
        return menu

    def switch(self, parent, label, key):
        row = self.row(parent, label)
        var = ctk.BooleanVar(value=bool(cfg[key]))
        sw = ctk.CTkSwitch(row, text="", variable=var, width=46,
                           command=lambda: self.set(key, var.get()))
        sw.pack(side="right", padx=4)
        self.sync[key] = lambda v: var.set(bool(v))

    def slider(self, parent, label, key, lo, hi, steps, fmt="{:.0f}", cast=float):
        row = self.row(parent, label)
        value_label = ctk.CTkLabel(row, width=56, text=fmt.format(cast(cfg[key])))
        value_label.pack(side="right", padx=4)
        slider = ctk.CTkSlider(row, from_=lo, to=hi, number_of_steps=steps, width=210)
        slider.set(cfg[key])

        def on_move(v):
            v = cast(v)
            value_label.configure(text=fmt.format(v))
            self.set(key, v)

        slider.configure(command=on_move)
        slider.pack(side="right", padx=4)

        def sync(v):
            slider.set(v)
            value_label.configure(text=fmt.format(cast(v)))

        self.sync[key] = sync

    def card(self, parent, title, hint, var, command):
        f = ctk.CTkFrame(parent)
        f.pack(fill="x", pady=6, padx=4)
        left = ctk.CTkFrame(f, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, padx=14, pady=12)
        ctk.CTkLabel(left, text=title, anchor="w",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(fill="x")
        ctk.CTkLabel(left, text=hint, anchor="w", justify="left", wraplength=500,
                     text_color=GRAY).pack(fill="x")
        ctk.CTkSwitch(f, text="", variable=var, command=command, width=46).pack(side="right", padx=16)

    # ------------------------------------------------------------ построение окна
    def build_ui(self, select=0):
        if self.tabs is not None:
            self.tabs.destroy()
        self.sync = {}
        self.model_labels = []
        self.voices_frame = None
        self.tabs = ctk.CTkTabview(self.root)
        self.tabs.pack(fill="both", expand=True, padx=14, pady=14)
        builders = [("tab_main", self.tab_main), ("tab_languages", self.tab_languages),
                    ("tab_audio", self.tab_audio), ("tab_subtitles", self.tab_subtitles),
                    ("tab_model", self.tab_model), ("tab_app", self.tab_app),
                    ("tab_about", self.tab_about)]
        self.tab_names = []
        for key, builder in builders:
            name = t(key)
            self.tab_names.append(name)
            builder(self.scroll(self.tabs.add(name)))
        self.tabs.set(self.tab_names[min(select, len(self.tab_names) - 1)])
        self.last_status = None

    def tab_main(self, p):
        self.var_sub = ctk.BooleanVar(value=engine.awake.is_set())
        self.var_voice = ctk.BooleanVar(value=engine.voice_on.is_set())
        self.var_dub = ctk.BooleanVar(value=engine.S["dub"])
        self.card(p, t("sw_subtitles"), t("sw_subtitles_hint"), self.var_sub,
                  lambda: self.on_switch("sub"))
        self.card(p, t("sw_voice"), t("sw_voice_hint"), self.var_voice,
                  lambda: self.on_switch("voice"))
        self.card(p, t("sw_dub"), t("sw_dub_hint"), self.var_dub,
                  lambda: self.on_switch("dub"))
        lbl = ctk.CTkLabel(p, text=self.model_text(), anchor="w")
        lbl.pack(fill="x", padx=10, pady=(10, 4))
        self.model_labels.append(lbl)
        buttons = ctk.CTkFrame(p, fg_color="transparent")
        buttons.pack(fill="x", padx=6, pady=6)
        ctk.CTkButton(buttons, text=t("btn_replay"),
                      command=lambda: engine.cmd_q.put("replay")).pack(side="left", padx=4)
        ctk.CTkButton(buttons, text=t("btn_monitor_next"),
                      command=lambda: engine.cmd_q.put("monitor_next")).pack(side="left", padx=4)
        self.heading(p, t("hotkeys_title"))
        ctk.CTkLabel(p, text=t("hotkeys_text"), anchor="w", justify="left",
                     font=ctk.CTkFont(family="Consolas", size=13)).pack(fill="x", padx=10, pady=4)

    def on_switch(self, which):
        var = getattr(self, "var_" + which)
        actual = {"sub": engine.awake.is_set(), "voice": engine.voice_on.is_set(),
                  "dub": engine.S["dub"]}[which]
        if var.get() != actual:
            engine.cmd_q.put({"sub": "toggle", "voice": "voice", "dub": "dub"}[which])

    def tab_languages(self, p):
        langs = list(LANG_NAMES.items())
        self.option(p, t("lang_source"), [("auto", t("lang_auto"))] + langs, "source_lang")
        self.option(p, t("lang_target"), langs, "target_lang")
        self.option(p, t("lang_ptt7"), langs, "ptt_lang_7")
        self.option(p, t("lang_ptt8"), langs, "ptt_lang_8")
        self.heading(p, t("voices_title"))
        self.voices_frame = ctk.CTkFrame(p, fg_color="transparent")
        self.voices_frame.pack(fill="x")
        self.build_voice_rows()
        self.slider(p, t("rate_dub"), "dub_rate", -30, 60, 18, "{:+.0f}", lambda v: int(round(v)))
        self.slider(p, t("rate_own"), "own_rate", -30, 60, 18, "{:+.0f}", lambda v: int(round(v)))

    def build_voice_rows(self):
        frame = self.voices_frame
        if frame is None:
            return
        for w in frame.winfo_children():
            w.destroy()
        codes = []
        for code in (cfg["target_lang"], cfg["ptt_lang_7"], cfg["ptt_lang_8"]):
            if code in VOICE_CHOICES and code not in codes:
                codes.append(code)
        for code in codes:
            options = [(v, v.replace("Neural", "")) for v in VOICE_CHOICES[code]]
            rev = {d: v for v, d in options}
            current = cfg["voices"].get(code) or VOICE_CHOICES[code][0]
            row = self.row(frame, t("voice_for", lang=LANG_NAMES.get(code, code)))

            def pick(choice, code=code, rev=rev):
                cfg["voices"][code] = rev[choice]
                engine.BAD_VOICES.discard(rev[choice])
                self.schedule_save()

            menu = ctk.CTkOptionMenu(row, values=[d for _, d in options], command=pick, width=290)
            menu.set(self.display_for(options, current))
            menu.pack(side="right", padx=4)

    def tab_audio(self, p):
        speakers = [(n, n) for n in engine.list_speakers()]
        mics = [(n, n) for n in engine.list_microphones()]
        default = [("", t("audio_default"))]
        self.option(p, t("audio_output"), default + speakers, "speaker_name")
        self.option(p, t("audio_mic"), default + mics, "mic_name")
        self.option(p, t("audio_cable"), speakers or [(cfg["cable_name"], cfg["cable_name"])],
                    "cable_name")
        self.switch(p, t("audio_passthrough"), "mic_passthrough")
        self.switch(p, t("audio_monitor"), "monitor_own")
        self.switch(p, t("audio_beeps"), "beeps")
        self.switch(p, t("audio_autostart"), "voice_autostart")
        ctk.CTkButton(p, text=t("audio_refresh"), command=self.rebuild_all).pack(anchor="w", padx=8, pady=8)
        self.hint(p, t("audio_help"))

    def tab_subtitles(self, p):
        self.slider(p, t("sub_font"), "font_size", 12, 40, 28, "{:.0f}", lambda v: int(round(v)))
        self.slider(p, t("sub_lines"), "max_lines", 1, 5, 4, "{:.0f}", lambda v: int(round(v)))
        self.slider(p, t("sub_width"), "wrap_width", 500, 1600, 22, "{:.0f}", lambda v: int(round(v)))
        self.slider(p, t("sub_seconds"), "show_seconds", 4, 30, 26, "{:.0f}", lambda v: int(round(v)))
        self.slider(p, t("sub_opacity"), "opacity", 0.4, 1.0, 12, "{:.2f}", float)
        self.switch(p, t("sub_original"), "show_original")
        mons = engine.get_monitors()
        options = [(i, f"{i + 1}: {right - left}x{bottom - top}")
                   for i, (left, top, right, bottom) in enumerate(mons)] or [(0, "1")]
        self.option(p, t("sub_monitor"), options, "monitor")
        ctk.CTkButton(p, text=t("sub_test"),
                      command=lambda: self.preview(t("msg_test"))).pack(anchor="w", padx=8, pady=10)

    def tab_model(self, p):
        self.option(p, t("model_size"), [(m, m) for m in MODEL_SIZES], "model")
        self.option(p, t("model_device"),
                    [(True, t("device_gpu")), (False, t("device_cpu"))], "use_gpu")
        lbl = ctk.CTkLabel(p, text=self.model_text(), anchor="w")
        lbl.pack(fill="x", padx=10, pady=(8, 2))
        self.model_labels.append(lbl)
        ctk.CTkButton(p, text=t("model_apply"), command=engine.request_model).pack(anchor="w", padx=8, pady=6)
        self.hint(p, t("model_hint"))
        row = self.row(p, t("preset_label"))
        names = [t("preset_fast"), t("preset_balanced"), t("preset_smooth")]
        seg = ctk.CTkSegmentedButton(row, values=names, command=self.apply_preset)
        seg.pack(side="right", padx=4)
        for i, (pause, longest) in enumerate(PRESETS):
            if abs(cfg["pause_seconds"] - pause) < 0.05 and abs(cfg["max_seconds"] - longest) < 0.5:
                seg.set(names[i])
        self.hint(p, t("latency_hint"))
        self.slider(p, t("timing_pause"), "pause_seconds", 0.2, 1.5, 13, "{:.1f}", float)
        self.slider(p, t("timing_max"), "max_seconds", 3, 15, 12, "{:.0f}", float)

    def tab_app(self, p):
        self.option(p, t("app_language"), [("auto", "Auto")] + list(LANGUAGES.items()), "ui_language")
        self.option(p, t("app_theme"), [("system", t("theme_system")), ("light", t("theme_light")),
                                        ("dark", t("theme_dark"))], "theme")
        self.switch(p, t("app_minimized"), "start_minimized")
        self.option(p, t("app_modifier"), [("none", t("mod_none")), ("ctrl", t("mod_ctrl")),
                                           ("alt", t("mod_alt")), ("ctrl+alt", t("mod_ctrl_alt"))],
                    "hotkey_modifier")
        self.heading(p, t("upd_title"))
        buttons = ctk.CTkFrame(p, fg_color="transparent")
        buttons.pack(fill="x", padx=6, pady=4)
        ctk.CTkButton(buttons, text=t("upd_packs_btn"), command=self.do_update_packs).pack(side="left", padx=4)
        ctk.CTkButton(buttons, text=t("upd_check_btn"), command=self.do_check_updates).pack(side="left", padx=4)
        self.hint(p, t("upd_hint"))

    def tab_about(self, p):
        ctk.CTkLabel(p, text=APP_NAME, anchor="w",
                     font=ctk.CTkFont(size=26, weight="bold")).pack(fill="x", padx=10, pady=(14, 0))
        ctk.CTkLabel(p, text=t("about_version", version=APP_VERSION), anchor="w",
                     text_color=GRAY).pack(fill="x", padx=10)
        self.hint(p, t("about_text"))
        self.hint(p, t("about_privacy"))

    # ------------------------------------------------------------ запуск
    def run(self):
        self.root.mainloop()
        self.quit()
