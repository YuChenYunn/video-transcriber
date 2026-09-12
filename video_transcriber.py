import json
import locale
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
import tkinter as tk
import customtkinter as ctk
from tkinter import BooleanVar, DoubleVar, StringVar, filedialog, messagebox
from urllib.parse import urlparse


# --- CustomTkinter global theme (warm retro palette to match the gramophone icon) ---
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("green")

# Shared color constants.
BG = "#F2EBD9"          # warm cream backdrop
CARD = "#FBF7EE"        # ivory card
CARD_EDGE = "#E4D9BE"   # soft card border
PRIMARY = "#3F6B4C"     # deep green
PRIMARY_DK = "#33593F"  # darker green (pressed)
BRASS = "#B07A39"       # brass accent
INK = "#3A2E1F"         # dark brown ink
MUTED = "#7A6A4F"       # muted brown


def read_docx_text(path: Path) -> str:
    """Read plain text from a .docx file (paragraphs joined by newlines)."""
    try:
        from docx import Document
    except ImportError:
        return ""
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs).strip()


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "settings.json"
WHISPERX_RUNNER = APP_DIR / "whisperx_runner.py"
POLISH_SCRIPT = APP_DIR / "polish.py"
DEFAULT_OUTPUT_DIR = Path(r"E:\AI\transcripts")
MODELS = ["tiny", "base", "small", "medium", "large-v3", "large"]
BROWSERS = ["", "edge", "chrome", "firefox", "brave", "opera", "vivaldi"]
ENGINES = ["whisperx", "whisper"]
COMPUTE_TYPES = ["auto", "float16", "int8", "int8_float16", "float32"]
DEFAULT_LLM_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_LLM_MODEL = "deepseek-v4-flash"

DEFAULT_PROMPT = """\
你是一名专业的文字编辑。下面给你一段语音转写稿，大多是多人对话或访谈的录音，少数是单人发言。请把它整理成一篇通顺、可阅读的文稿。

要求：
1. 纠正明显的同音字、错别字、术语错误（结合上下文判断，不确定的保留原意，不要凭空编造内容）。
2. 把口语碎片、断句拼成完整、通顺的句子，保留原意和说话人的语气立场；适度保留口语感，不要改成生硬的纯书面语。
3. 删掉「嗯」「啊」「那个」「就是说」「然后」等无意义的语气词和重复啰嗦的部分。
4. 梳理对话的来回：如果转写稿里带有 [SPEAKER_xx] 之类的说话人标签，原样保留在对应段落开头，不要删改标签、不要合并不同说话人的话；如果没有标签，就按说话人的切换合理分段（一段一个说话人的连续发言），不必猜测具体人名。
5. 只输出整理后的文稿正文，不要加解释、不要加标题、不要加「以下是整理结果」之类的说明。
"""


def load_settings():
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(settings):
    CONFIG_PATH.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def sanitize_name(value):
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in value)
    cleaned = " ".join(cleaned.split()).strip(" ._")
    return cleaned[:120] or f"transcript-{int(time.time())}"


def run_command(command, log, env=None):
    log("$ " + " ".join(f'"{part}"' if " " in str(part) else str(part) for part in command))
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding=locale.getpreferredencoding(False),
        errors="replace",
        env=env,
    )
    assert process.stdout is not None
    for line in process.stdout:
        log(line.rstrip())
    code = process.wait()
    if code != 0:
        raise RuntimeError(f"Command failed with exit code {code}")


def yt_dlp_command(yt_dlp_path):
    if yt_dlp_path:
        return [yt_dlp_path]
    found = shutil.which("yt-dlp")
    if found:
        return [found]
    return [sys.executable, "-m", "yt_dlp"]


def whisper_command(whisper_path):
    if whisper_path:
        return [whisper_path]
    found = shutil.which("whisper")
    if found:
        return [found]
    return [sys.executable, "-m", "whisper.transcribe"]


def whisperx_command(python_path, audio, output_dir, settings, stem=""):
    python = python_path or sys.executable
    command = [
        python,
        str(WHISPERX_RUNNER),
        "--audio", str(audio),
        "--output_dir", str(output_dir),
        "--model", settings["model"],
        "--compute_type", settings.get("compute_type", "auto"),
        "--batch_size", str(settings.get("batch_size", 16)),
    ]
    if stem:
        # Name outputs after the sanitized stem instead of the raw audio
        # filename, whose characters (e.g. U+29F8 "⧸" in a video title) are
        # unprintable on the GBK log pipe; also makes final_txt below match
        # what the runner actually writes.
        command += ["--stem", stem]
    if settings.get("language"):
        command += ["--language", settings["language"]]
    if settings.get("diarize") and settings.get("hf_token"):
        command += ["--diarize", "--hf_token", settings["hf_token"]]
    elif settings.get("diarize") and not settings.get("hf_token"):
        # No token: the runner will warn and skip diarization, but still transcribe.
        command += ["--diarize"]
    return command


def polish_command(settings, input_path, out_path):
    if not POLISH_SCRIPT.is_file():
        raise RuntimeError(f"polish.py not found: {POLISH_SCRIPT}")
    command = [
        sys.executable,
        str(POLISH_SCRIPT),
        str(input_path),
        "--api_key", settings.get("llm_api_key", ""),
        "--base_url", settings.get("llm_base_url", DEFAULT_LLM_BASE_URL),
        "--model", settings.get("llm_model", DEFAULT_LLM_MODEL),
        "--out", str(out_path),
    ]
    return command


def build_env(ffmpeg_path):
    env = os.environ.copy()
    if ffmpeg_path:
        ffmpeg = Path(ffmpeg_path)
        ffmpeg_dir = ffmpeg.parent if ffmpeg.is_file() else ffmpeg
        env["PATH"] = str(ffmpeg_dir) + os.pathsep + env.get("PATH", "")
    return env


def site_download_args(url):
    host = urlparse(url).netloc.lower()
    if "bilibili.com" in host or "b23.tv" in host:
        return [
            "--referer",
            "https://www.bilibili.com/",
            "--user-agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
        ]
    return []


class TranscriberApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Video Transcriber")
        self.root.geometry("1180x780")
        self.root.minsize(1000, 660)

        self.settings = load_settings()
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        self.url = StringVar(value="")
        self.model = StringVar(value=self.settings.get("model", "base"))
        self.language = StringVar(value=self.settings.get("language", "en"))
        self.output_dir = StringVar(value=self.settings.get("output_dir", str(DEFAULT_OUTPUT_DIR)))
        self.yt_dlp_path = StringVar(value=self.settings.get("yt_dlp_path", ""))
        self.ffmpeg_path = StringVar(value=self.settings.get("ffmpeg_path", ""))
        self.whisper_path = StringVar(value=self.settings.get("whisper_path", ""))
        self.cookies_path = StringVar(value=self.settings.get("cookies_path", ""))
        self.cookies_browser = StringVar(value=self.settings.get("cookies_browser", ""))
        self.keep_audio = BooleanVar(value=bool(self.settings.get("keep_audio", False)))
        # whisperX engine
        self.engine = StringVar(value=self.settings.get("engine", "whisperx"))
        self.whisperx_python = StringVar(value=self.settings.get(
            "whisperx_python", r"E:\AI\whisper-gpu\venv\Scripts\python.exe"))
        self.hf_token = StringVar(value=self.settings.get("hf_token", ""))
        self.diarize = BooleanVar(value=bool(self.settings.get("diarize", True)))
        self.compute_type = StringVar(value=self.settings.get("compute_type", "auto"))
        self.batch_size = StringVar(value=str(self.settings.get("batch_size", 16)))
        # AI polish (DeepSeek default)
        self.llm_base_url = StringVar(value=self.settings.get("llm_base_url", DEFAULT_LLM_BASE_URL))
        self.llm_api_key = StringVar(value=self.settings.get("llm_api_key", ""))
        self.llm_model = StringVar(value=self.settings.get("llm_model", DEFAULT_LLM_MODEL))
        self.status = StringVar(value="Ready")
        self.detail_status = StringVar(value="Paste a link, choose a model, then start.")
        self.progress = DoubleVar(value=0)
        self.log_queue = queue.Queue()
        self.log_lines = []
        self.history_items = []
        self.worker = None
        self.last_transcript_path = None
        self.polisher = None
        # Live "Working on the transcript... MM:SS" ticker state. working_since is
        # a monotonic timestamp while a transcription runs, None when idle.
        self.working_since = None
        self.working_stage = ""
        self.working_percent = None
        # CTkProgressBar has no `variable=`, so we bridge the DoubleVar (0-100)
        # to progress_bar.set (0-1) via a trace callback.
        self.progress.trace_add("write", self._on_progress_changed)
        self.polisher = None

        self.configure_style()
        self.build_ui()
        self.root.after(100, self.flush_log_queue)

    def configure_style(self):
        # CustomTkinter handles most theming via widget parameters; here we only
        # set the root window background to the warm cream backdrop.
        self.root.configure(fg_color=BG)

    def build_ui(self):
        # Shared secondary-button style: transparent with a soft border.
        secondary = dict(fg_color="transparent", border_width=1, border_color=CARD_EDGE,
                         text_color=INK, hover_color="#F6F0DF")

        shell = ctk.CTkFrame(self.root, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=20, pady=20)

        # --- Header ---
        header = ctk.CTkFrame(shell, fg_color="transparent")
        header.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(header, text="Video Transcriber",
                     font=ctk.CTkFont(size=22, weight="bold"), text_color=INK).pack(side="left")
        ctk.CTkLabel(header, textvariable=self.status, text_color=PRIMARY,
                     font=ctk.CTkFont(size=12, weight="bold")).pack(side="right")

        # --- Top input bar (card) ---
        top = ctk.CTkFrame(shell, corner_radius=14, fg_color=CARD)
        top.pack(fill="x", pady=(0, 10))
        top.grid_columnconfigure(1, weight=1)
        top.grid_rowconfigure(0, weight=1)
        ctk.CTkButton(top, text="📁 File", width=84, height=42, command=self.pick_local_file,
                      font=ctk.CTkFont(size=12), **secondary).grid(row=0, column=0, padx=(14, 8), pady=14)
        ctk.CTkEntry(top, textvariable=self.url, height=42,
                     font=ctk.CTkFont(size=13), fg_color=CARD, border_color=CARD_EDGE,
                     text_color=INK).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=14)
        self.start_button = ctk.CTkButton(top, text="Transcribe", width=110, height=38,
                                          command=self.start,
                                          font=ctk.CTkFont(size=13, weight="bold"),
                                          fg_color=PRIMARY, hover_color=PRIMARY_DK, text_color="#FBF7EE")
        self.start_button.grid(row=0, column=2, padx=(0, 8), pady=14)
        self.polish_button = ctk.CTkButton(top, text="AI Polish", width=96, height=38,
                                           command=self.start_polish, **secondary)
        self.polish_button.grid(row=0, column=3, padx=(0, 8), pady=14)
        self.polish_button.configure(state="disabled")
        ctk.CTkButton(top, text="Settings", width=84, height=38, command=self.open_settings, **secondary).grid(row=0, column=4, padx=(0, 8), pady=14)
        ctk.CTkButton(top, text="Folder", width=74, height=38, command=self.open_output_dir, **secondary).grid(row=0, column=5, pady=14)

        # --- Progress card (status + progress bar + live log) ---
        progress_card = ctk.CTkFrame(shell, corner_radius=14, fg_color=CARD)
        progress_card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(progress_card, textvariable=self.detail_status, text_color=MUTED,
                     font=ctk.CTkFont(size=11), anchor="w").pack(anchor="w", padx=16, pady=(14, 6))
        self.progress_bar = ctk.CTkProgressBar(progress_card,
                                               fg_color=CARD_EDGE, progress_color=BRASS, height=12)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", padx=16, pady=(0, 10))
        # Live log panel: shows yt-dlp / whisperX output so the user can see progress
        # instead of staring at a frozen "Working" status.
        self.log_text = ctk.CTkTextbox(progress_card, height=110,
                                       font=ctk.CTkFont(family="Consolas", size=11),
                                       fg_color="#F2EBD9", text_color=MUTED,
                                       border_width=1, border_color=CARD_EDGE)
        self.log_text.pack(fill="x", padx=16, pady=(0, 14))
        self.log_text.configure(state="disabled")

        # --- Content: history + transcript ---
        content = ctk.CTkFrame(shell, fg_color="transparent")
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        history_card = ctk.CTkFrame(content, corner_radius=14, fg_color=CARD, width=250)
        history_card.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        history_card.grid_propagate(False)
        history_card.grid_columnconfigure(0, weight=1)
        history_card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(history_card, text="History",
                     font=ctk.CTkFont(size=12, weight="bold"), text_color=INK).grid(row=0, column=0, sticky="w", padx=16, pady=(16, 8))
        # Scrollable list of history items (replaces the Listbox).
        self.history_list = ctk.CTkScrollableFrame(history_card, fg_color="transparent")
        self.history_list.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        transcript_card = ctk.CTkFrame(content, corner_radius=14, fg_color=CARD)
        transcript_card.grid(row=0, column=1, sticky="nsew")
        transcript_card.grid_rowconfigure(1, weight=1)
        transcript_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(transcript_card, text="Transcript",
                     font=ctk.CTkFont(size=12, weight="bold"), text_color=INK).grid(row=0, column=0, sticky="w", padx=20, pady=(16, 6))
        self.transcript_text = ctk.CTkTextbox(transcript_card, wrap="word",
                                              font=ctk.CTkFont(size=13),
                                              fg_color=CARD, text_color=INK)
        self.transcript_text.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.set_transcript("Paste a link above and start a transcription. Completed transcripts will appear here.")
        self.refresh_history()

    def open_settings(self):
        window = ctk.CTkToplevel(self.root, fg_color=BG)
        window.title("Settings")
        window.geometry("920x820")
        window.minsize(820, 700)
        window.transient(self.root)
        window.grab_set()

        # Panel inside a scrollable frame so the Save button is always reachable
        # even when the content is taller than the window.
        outer = ctk.CTkFrame(window, corner_radius=14, fg_color=CARD)
        outer.pack(fill="both", expand=True, padx=16, pady=16)
        panel = ctk.CTkScrollableFrame(outer, fg_color="transparent")
        panel.pack(fill="both", expand=True, padx=16, pady=16)
        panel.grid_columnconfigure(1, weight=1)

        self.add_combo(panel, 0, "Whisper model", self.model, MODELS)
        self.add_entry(panel, 1, "Language", self.language)
        self.add_path(panel, 2, "Output folder", self.output_dir, self.pick_folder)
        self.add_path(panel, 3, "yt-dlp path", self.yt_dlp_path, lambda: self.pick_file(self.yt_dlp_path))
        self.add_path(panel, 4, "ffmpeg path/bin", self.ffmpeg_path, lambda: self.pick_file_or_folder(self.ffmpeg_path))
        self.add_path(panel, 5, "Whisper path", self.whisper_path, lambda: self.pick_file(self.whisper_path))
        self.add_path(panel, 6, "Cookies file", self.cookies_path, lambda: self.pick_file(self.cookies_path))
        self.add_combo(panel, 7, "Cookies from browser", self.cookies_browser, BROWSERS)
        ctk.CTkCheckBox(panel, text="Keep downloaded audio", variable=self.keep_audio,
                        fg_color=PRIMARY, hover_color=PRIMARY_DK, text_color=INK).grid(row=8, column=1, sticky="w", padx=(0, 0), pady=(8, 4))

        # --- whisperX / engine section ---
        self._separator(panel, 9)
        ctk.CTkLabel(panel, text="Transcription engine", text_color=MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=10, column=0, columnspan=3, sticky="w", padx=(8, 0))
        self.add_combo(panel, 11, "Engine", self.engine, ENGINES)
        self.add_path(panel, 12, "whisperX python.exe", self.whisperx_python, lambda: self.pick_file(self.whisperx_python))
        self.add_path(panel, 13, "HuggingFace token", self.hf_token, lambda: self.pick_file(self.hf_token))
        ctk.CTkCheckBox(panel, text="Speaker diarization (needs HF token)", variable=self.diarize,
                        fg_color=PRIMARY, hover_color=PRIMARY_DK, text_color=INK).grid(row=14, column=1, sticky="w", pady=4)
        self.add_combo(panel, 15, "compute_type", self.compute_type, COMPUTE_TYPES)
        self.add_entry(panel, 16, "batch_size", self.batch_size)

        # --- AI polish section ---
        self._separator(panel, 17)
        ctk.CTkLabel(panel, text="AI polish (DeepSeek / OpenAI-compatible)", text_color=MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=18, column=0, columnspan=3, sticky="w", padx=(8, 0))
        self.add_entry(panel, 19, "LLM base URL", self.llm_base_url)
        self.add_entry(panel, 20, "LLM API key", self.llm_api_key)
        self.add_entry(panel, 21, "LLM model", self.llm_model)

        buttons = ctk.CTkFrame(panel, fg_color="transparent")
        buttons.grid(row=22, column=0, columnspan=3, sticky="ew")
        buttons.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(buttons, text="Save", width=100, height=36,
                      command=lambda: self.save_settings_and_close(window),
                      font=ctk.CTkFont(size=13, weight="bold"),
                      fg_color=PRIMARY, hover_color=PRIMARY_DK, text_color="#FBF7EE").grid(row=0, column=1, padx=(0, 8), pady=(10, 4))
        ctk.CTkButton(buttons, text="Cancel", width=100, height=36, command=window.destroy,
                      fg_color="transparent", border_width=1, border_color=CARD_EDGE,
                      text_color=INK, hover_color="#F6F0DF").grid(row=0, column=2, pady=(10, 4))

    def _separator(self, parent, row):
        ctk.CTkFrame(parent, height=2, fg_color=CARD_EDGE).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(12, 4))

    def save_settings_and_close(self, window):
        self.persist_settings_silent()
        self.refresh_history()
        window.destroy()

    def add_entry(self, parent, row, label, variable):
        ctk.CTkLabel(parent, text=label, text_color=INK,
                     font=ctk.CTkFont(size=11)).grid(row=row, column=0, sticky="w", padx=(8, 12), pady=4)
        ctk.CTkEntry(parent, textvariable=variable, fg_color=CARD, border_color=CARD_EDGE,
                     text_color=INK).grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)

    def add_combo(self, parent, row, label, variable, values):
        ctk.CTkLabel(parent, text=label, text_color=INK,
                     font=ctk.CTkFont(size=11)).grid(row=row, column=0, sticky="w", padx=(8, 12), pady=4)
        ctk.CTkComboBox(parent, variable=variable, values=values, fg_color=CARD,
                        border_color=CARD_EDGE, text_color=INK, button_color=BRASS,
                        button_hover_color=PRIMARY_DK, dropdown_fg_color=CARD,
                        dropdown_text_color=INK, dropdown_hover_color="#F6F0DF").grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)

    def add_path(self, parent, row, label, variable, command):
        ctk.CTkLabel(parent, text=label, text_color=INK,
                     font=ctk.CTkFont(size=11)).grid(row=row, column=0, sticky="w", padx=(8, 12), pady=4)
        ctk.CTkEntry(parent, textvariable=variable, fg_color=CARD, border_color=CARD_EDGE,
                     text_color=INK).grid(row=row, column=1, sticky="ew", pady=4)
        ctk.CTkButton(parent, text="Browse", width=80, height=28, command=command,
                      fg_color="transparent", border_width=1, border_color=CARD_EDGE,
                      text_color=INK, hover_color="#F6F0DF").grid(row=row, column=2, sticky="ew", padx=(8, 8), pady=4)

    def pick_folder(self):
        value = filedialog.askdirectory(initialdir=self.output_dir.get() or str(APP_DIR))
        if value:
            self.output_dir.set(value)

    def pick_file(self, variable):
        value = filedialog.askopenfilename(initialdir=str(APP_DIR))
        if value:
            variable.set(value)

    def pick_file_or_folder(self, variable):
        value = filedialog.askopenfilename(title="Pick ffmpeg.exe, or cancel to pick a folder")
        if not value:
            value = filedialog.askdirectory(title="Pick folder containing ffmpeg.exe")
        if value:
            variable.set(value)

    def persist_settings(self):
        save_settings(self.current_settings())
        messagebox.showinfo("Saved", "Settings saved.")

    def current_settings(self, include_url=True):
        return {
            "url": self.url.get().strip() if include_url else "",
            "model": self.model.get().strip() or "base",
            "language": self.language.get().strip(),
            "output_dir": self.output_dir.get().strip(),
            "yt_dlp_path": self.yt_dlp_path.get().strip(),
            "ffmpeg_path": self.ffmpeg_path.get().strip(),
            "whisper_path": self.whisper_path.get().strip(),
            "cookies_path": self.cookies_path.get().strip(),
            "cookies_browser": self.cookies_browser.get().strip(),
            "keep_audio": self.keep_audio.get(),
            "engine": self.engine.get().strip() or "whisperx",
            "whisperx_python": self.whisperx_python.get().strip(),
            "hf_token": self.hf_token.get().strip(),
            "diarize": self.diarize.get(),
            "compute_type": self.compute_type.get().strip() or "auto",
            "batch_size": int(self.batch_size.get().strip() or "16"),
            "llm_base_url": self.llm_base_url.get().strip() or DEFAULT_LLM_BASE_URL,
            "llm_api_key": self.llm_api_key.get().strip(),
            "llm_model": self.llm_model.get().strip() or DEFAULT_LLM_MODEL,
        }

    def _on_progress_changed(self, *_args):
        # Bridge self.progress (DoubleVar, 0-100) -> progress_bar (CTk, 0-1).
        bar = getattr(self, "progress_bar", None)
        if bar is None:
            return
        value = self.progress.get()
        bar.set(max(0.0, min(100.0, value)) / 100.0)

    def log(self, message):
        self.log_queue.put(message)

    def flush_log_queue(self):
        new_messages = []
        try:
            while True:
                message = self.log_queue.get_nowait()
                self.log_lines.append(message)
                new_messages.append(message)
                self.consume_status_message(message)
        except queue.Empty:
            pass
        # Append new log lines to the live log panel.
        if new_messages and hasattr(self, "log_text"):
            self.log_text.configure(state="normal")
            for message in new_messages:
                self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(100, self.flush_log_queue)

    def consume_status_message(self, message):
        if not message:
            return
        if message.startswith("ERROR:"):
            self.detail_status.set(message)
            return
        if message.startswith("Saved transcript:"):
            self.detail_status.set("Transcript saved.")
            return
        stage_names = {
            "Reading media information...": "Reading media information",
            "Downloading audio...": "Downloading audio",
            "Loading whisper model...": "Loading whisper model",
            "Loading audio...": "Loading audio",
            "Transcribing...": "Transcribing",
            "Aligning timestamps...": "Aligning timestamps",
        }
        if message in stage_names:
            self.working_stage = stage_names[message]
            self.working_percent = None
            self.detail_status.set(message)
            return
        if message.startswith("Diarizing"):
            self.working_stage = "Speaker diarization"
            self.working_percent = None
            self.detail_status.set("Diarizing...")
            return
        if message == "Finished.":
            self.working_stage = ""
            self.detail_status.set(message)
            return
        if message.startswith("Transcribing:"):
            # Progress line from whisperx_runner ("Transcribing: 45%").
            self.working_stage = "Transcribing"
            match = re.search(r"(\d{1,3})%", message)
            if match:
                percent = max(0, min(100, int(match.group(1))))
                self.progress.set(percent)
                self.working_percent = percent
                self.detail_status.set(f"Transcribing audio... {percent}%")
            return
        # yt-dlp download lines like "[download]  26.4% of  36.70MiB".
        match = re.search(r"(\d{1,3})%", message)
        if match:
            percent = max(0, min(100, int(match.group(1))))
            self.progress.set(percent)
            self.working_percent = percent
            if percent < 100:
                self.detail_status.set(f"Working... {percent}%")

    def tick_working_status(self):
        # Update the "Working on the transcript..." placeholder once per second
        # with elapsed time (+ stage/percent when known) so long silent phases
        # don't look like a hang. Stops itself when working_since is cleared.
        if self.working_since is None:
            return
        elapsed = int(time.monotonic() - self.working_since)
        text = f"Working on the transcript... {elapsed // 60:02d}:{elapsed % 60:02d}"
        extras = []
        if self.working_stage:
            extras.append(self.working_stage)
        if self.working_percent is not None:
            extras.append(f"{self.working_percent}%")
        if extras:
            text += "  ·  " + " ".join(extras)
        self.set_transcript(text)
        self.root.after(1000, self.tick_working_status)

    def set_transcript(self, text):
        self.transcript_text.configure(state="normal")
        self.transcript_text.delete("1.0", "end")
        self.transcript_text.insert("1.0", text)
        self.transcript_text.configure(state="disabled")

    def refresh_history(self):
        if not hasattr(self, "history_list"):
            return
        output_dir = Path(self.output_dir.get().strip() or DEFAULT_OUTPUT_DIR)
        self.history_items = []
        # Clear existing children (CTkScrollableFrame replaces Listbox semantics).
        for child in self.history_list.winfo_children():
            child.destroy()
        if not output_dir.exists():
            ctk.CTkLabel(self.history_list, text="No transcripts yet",
                         text_color=MUTED, font=ctk.CTkFont(size=11)).pack(anchor="w", padx=4, pady=4)
            return
        files = sorted(
            [p for p in output_dir.glob("*")
             if p.is_file() and p.suffix.lower() in {".txt", ".docx"}
             and not p.name.endswith(".partial.txt")],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not files:
            ctk.CTkLabel(self.history_list, text="No transcripts yet",
                         text_color=MUTED, font=ctk.CTkFont(size=11)).pack(anchor="w", padx=4, pady=4)
            return
        for index, path in enumerate(files):
            stamp = time.strftime("%m-%d %H:%M", time.localtime(path.stat().st_mtime))
            tag = " [润色]" if path.suffix.lower() == ".docx" else ""
            label = f"{stamp}  {path.stem[:30]}{tag}"
            self.history_items.append(path)
            item = ctk.CTkButton(
                self.history_list,
                text=label,
                anchor="w",
                height=30,
                fg_color="transparent",
                hover_color="#F6F0DF",
                text_color=INK,
                border_width=0,
                font=ctk.CTkFont(size=11),
                command=lambda i=index: self.load_selected_history_index(i),
            )
            item.pack(fill="x", pady=1)

    def load_selected_history_index(self, index):
        """Open a history item by its index (replaces Listbox curselection flow)."""
        if index >= len(self.history_items):
            return
        path = self.history_items[index]
        # Visually highlight the selected item, reset the others.
        for i, child in enumerate(self.history_list.winfo_children()):
            if not isinstance(child, ctk.CTkButton):
                continue
            if i == index:
                child.configure(fg_color=BRASS, text_color="#FBF7EE", hover_color="#9A6830")
            else:
                child.configure(fg_color="transparent", text_color=INK, hover_color="#F6F0DF")
        try:
            if path.suffix.lower() == ".docx":
                text = read_docx_text(path)
            else:
                text = path.read_text(encoding="utf-8", errors="replace").strip()
            self.set_transcript(text or "Transcript file is empty.")
            self.last_transcript_path = path
            self.detail_status.set(f"Viewing {path.name}")
            self.status.set("Ready")
            self.progress.set(0)
            self.polish_button.configure(state="normal")
        except Exception as exc:
            messagebox.showerror("Could not open transcript", str(exc))

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        url = self.url.get().strip()
        if not url:
            messagebox.showwarning("Missing input", "Paste a link or pick a local file first.")
            return
        self.persist_settings_silent()
        self.start_button.configure(state="disabled")
        self.status.set("Working")
        self.detail_status.set("Starting...")
        self.progress.set(0)
        self.log_lines.clear()
        if hasattr(self, "log_text"):
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
        self.working_since = time.monotonic()
        self.working_stage = ""
        self.working_percent = None
        self.set_transcript("Working on the transcript... 00:00")
        self.tick_working_status()
        self.worker = threading.Thread(target=self.transcribe, daemon=True)
        self.worker.start()

    def persist_settings_silent(self):
        save_settings(self.current_settings(include_url=False))

    def start_polish(self):
        if self.polisher and self.polisher.is_alive():
            return
        if not self.last_transcript_path or not self.last_transcript_path.exists():
            messagebox.showwarning("Nothing to polish", "Transcribe something first, or load one from History.")
            return
        settings = self.current_settings(include_url=False)
        if not settings.get("llm_api_key"):
            messagebox.showwarning("Missing API key", "Set your DeepSeek/LLM API key in Settings first.")
            self.open_settings()
            return
        self.persist_settings_silent()
        self.polish_button.configure(state="disabled")
        self.working_since = None  # stop the transcription ticker; polish has its own status
        self.status.set("Polishing")
        self.detail_status.set("Polishing with LLM...")
        self.progress.set(0)
        self.set_transcript("Polishing the transcript with the LLM...")
        self.polisher = threading.Thread(target=self.polish, daemon=True)
        self.polisher.start()

    def polish(self):
        try:
            settings = self.current_settings(include_url=False)
            src = self.last_transcript_path
            out = src.with_name(f"{src.stem}_draft.docx")
            self.log("AI polish: starting...")
            run_command(polish_command(settings, src, out), self.log)
            self.root.after(0, lambda: self.progress.set(100))
            if out.exists():
                text = read_docx_text(out)
                self.log(f"Saved draft: {out}")
                self.root.after(0, lambda text=text: self.set_transcript(text or "Draft is empty."))
                self.root.after(0, self.refresh_history)
            self.root.after(0, lambda: self.status.set("Done"))
            self.log("Polish finished.")
        except Exception as exc:
            self.root.after(0, lambda: self.status.set("Error"))
            self.log(f"ERROR: {exc}")
            details = "\n".join(self.log_lines[-18:])
            message = f"Polish failed:\n\n{exc}"
            if details:
                message += f"\n\nLast messages:\n{details}"
            self.root.after(0, lambda message=message: self.set_transcript(message))
        finally:
            self.root.after(0, lambda: self.polish_button.configure(state="normal"))

    def transcribe(self):
        try:
            settings = self.current_settings()
            output_dir = Path(settings["output_dir"]).expanduser()
            output_dir.mkdir(parents=True, exist_ok=True)
            env = build_env(settings["ffmpeg_path"])

            with tempfile.TemporaryDirectory(prefix="video-transcriber-") as temp:
                temp_dir = Path(temp)
                info_path = temp_dir / "info.json"
                audio_template = str(temp_dir / "%(title).160s.%(ext)s")

                # Local file mode: if the input is an existing file path, use it
                # directly and skip yt-dlp entirely (covers platforms yt-dlp can't
                # handle, e.g. WeChat Channels). Otherwise, download via yt-dlp.
                local_input = Path(settings["url"])
                if local_input.is_file():
                    audio_file = local_input
                    self.log(f"Using local file: {audio_file.name}")
                    self.root.after(0, lambda: self.progress.set(35))
                else:
                    self.log("Reading media information...")
                    common_download_args = ["--no-playlist"] + site_download_args(settings["url"])
                    if settings["cookies_path"]:
                        common_download_args += ["--cookies", settings["cookies_path"]]
                    elif settings["cookies_browser"]:
                        common_download_args += ["--cookies-from-browser", settings["cookies_browser"]]
                    run_command(
                        yt_dlp_command(settings["yt_dlp_path"]) + common_download_args + ["--get-title", settings["url"]],
                        self.log,
                        env=env,
                    )
                    self.root.after(0, lambda: self.progress.set(15))

                    self.log("Downloading audio...")
                    run_command(
                        yt_dlp_command(settings["yt_dlp_path"]) + common_download_args + [
                            "-f",
                            "bestaudio/best",
                            "-o",
                            audio_template,
                            settings["url"],
                        ],
                        self.log,
                        env=env,
                    )
                    self.root.after(0, lambda: self.progress.set(35))

                    audio_files = [path for path in temp_dir.iterdir() if path.is_file() and path.name != info_path.name]
                    if not audio_files:
                        raise RuntimeError("No audio file was downloaded.")
                    audio_file = max(audio_files, key=lambda path: path.stat().st_size)
                stem = sanitize_name(audio_file.stem)

                self.log(f"Transcribing: {audio_file.name}")
                engine = settings.get("engine", "whisperx")
                if engine == "whisperx":
                    if not WHISPERX_RUNNER.is_file():
                        raise RuntimeError(f"whisperx_runner.py not found: {WHISPERX_RUNNER}")
                    if settings.get("diarize") and not settings.get("hf_token"):
                        self.log("WARNING: diarization enabled but no HF token; speaker labels will be skipped.")
                    wx_args = whisperx_command(settings["whisperx_python"], audio_file, output_dir, settings, stem)
                    run_command(wx_args, self.log, env=env)
                    final_txt = output_dir / f"{stem}.txt"
                else:
                    whisper_args = whisper_command(settings["whisper_path"]) + [
                        str(audio_file),
                        "--model",
                        settings["model"],
                        "--output_dir",
                        str(output_dir),
                        "--output_format",
                        "txt",
                        "--verbose",
                        "False",
                    ]
                    if settings["language"]:
                        whisper_args += ["--language", settings["language"]]
                    run_command(whisper_args, self.log, env=env)
                    final_txt = None
                    txt_files = sorted(
                        (p for p in output_dir.glob("*.txt") if not p.name.endswith(".partial.txt")),
                        key=lambda path: path.stat().st_mtime, reverse=True)
                    if txt_files:
                        final_txt = output_dir / f"{stem}.txt"
                        if txt_files[0] != final_txt and not final_txt.exists():
                            txt_files[0].replace(final_txt)
                self.root.after(0, lambda: self.progress.set(95))

                # Locate the transcript we just produced. Prefer a file matching the
                # sanitized stem; otherwise fall back to the newest .txt in the folder.
                # (whisperX names output after the audio stem, which may differ from our
                # sanitized stem when the title has full-width punctuation/spaces.)
                if not final_txt or not final_txt.exists():
                    recent_txts = sorted(
                        (p for p in output_dir.glob("*.txt")
                         if p.is_file() and not p.name.endswith(".partial.txt")),
                        key=lambda path: path.stat().st_mtime,
                        reverse=True,
                    )
                    if recent_txts:
                        final_txt = recent_txts[0]

                if final_txt and final_txt.exists():
                    self.last_transcript_path = final_txt
                    self.log(f"Saved transcript: {final_txt}")
                    transcript = final_txt.read_text(encoding="utf-8", errors="replace").strip()
                    self.root.after(0, lambda text=transcript: self.set_transcript(text or "Transcript file is empty."))
                    self.root.after(0, self.refresh_history)

                if settings["keep_audio"]:
                    kept_audio = output_dir / audio_file.name
                    shutil.copy2(audio_file, kept_audio)
                    self.log(f"Saved audio: {kept_audio}")

            # Stop the elapsed-time ticker before the final content is written,
            # so it can't overwrite the finished transcript.
            self.working_since = None
            self.root.after(0, lambda: self.status.set("Done"))
            self.root.after(0, lambda: self.progress.set(100))
            self.log("Finished.")
            self.root.after(0, lambda: self.polish_button.configure(state="normal"))
        except Exception as exc:
            self.working_since = None
            self.root.after(0, lambda: self.status.set("Error"))
            self.log(f"ERROR: {exc}")
            details = "\n".join(self.log_lines[-18:])
            message = f"Something went wrong:\n\n{exc}"
            if details:
                message += f"\n\nLast messages:\n{details}"
            self.root.after(0, lambda message=message: self.set_transcript(message))
        finally:
            self.root.after(0, lambda: self.start_button.configure(state="normal"))

    def open_output_dir(self):
        path = Path(self.output_dir.get().strip() or DEFAULT_OUTPUT_DIR)
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def pick_local_file(self):
        """Pick a local audio/video file and put its path in the URL field,
        so transcribe() can use it directly without yt-dlp."""
        value = filedialog.askopenfilename(
            title="Pick a video or audio file",
            filetypes=[
                ("Media files", "*.mp4 *.m4a *.mp3 *.wav *.webm *.mkv *.mov *.flac *.aac *.ogg *.wma"),
                ("All files", "*.*"),
            ],
        )
        if value:
            self.url.set(value)


if __name__ == "__main__":
    root = ctk.CTk()
    app = TranscriberApp(root)
    root.mainloop()
