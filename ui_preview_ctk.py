"""CustomTkinter UI preview — a mockup of the Video Transcriber layout.
Run to see what the real app could look like. No logic, just visuals."""

import customtkinter as ctk

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("green")

root = ctk.CTk()
root.title("Video Transcriber — Preview")
root.geometry("1040x720")
root.minsize(900, 620)

# --- Header ---
header = ctk.CTkFrame(root, fg_color="transparent")
header.pack(fill="x", padx=24, pady=(20, 8))
ctk.CTkLabel(header, text="Video Transcriber", font=ctk.CTkFont(size=22, weight="bold")).pack(side="left")
status_label = ctk.CTkLabel(header, text="● Ready", text_color="#3F6B4C",
                             font=ctk.CTkFont(size=12, weight="bold"))
status_label.pack(side="right")

# --- Top input bar (card) ---
top = ctk.CTkFrame(root, corner_radius=14)
top.pack(fill="x", padx=24, pady=(4, 12))
entry = ctk.CTkEntry(top, placeholder_text="Paste a video or audio link...",
                     height=42, font=ctk.CTkFont(size=13))
entry.pack(side="left", fill="x", expand=True, padx=(14, 10), pady=14)
ctk.CTkButton(top, text="Transcribe", width=110, height=38).pack(side="left", padx=(0, 8))
ctk.CTkButton(top, text="AI Polish", width=90, height=38,
              fg_color="transparent", border_width=1, text_color=("gray10", "gray90")).pack(side="left", padx=(0, 8))
ctk.CTkButton(top, text="Settings", width=84, height=38,
              fg_color="transparent", border_width=1, text_color=("gray10", "gray90")).pack(side="left", padx=(0, 8))
ctk.CTkButton(top, text="Folder", width=74, height=38,
              fg_color="transparent", border_width=1, text_color=("gray10", "gray90")).pack(side="left")

# --- Progress card ---
prog = ctk.CTkFrame(root, corner_radius=14)
prog.pack(fill="x", padx=24, pady=(0, 12))
ctk.CTkLabel(prog, text="Paste a link, choose a model, then start.",
             text_color="gray50", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=16, pady=(14, 4))
ctk.CTkProgressBar(prog, progress=0.0).pack(fill="x", padx=16, pady=(0, 16))

# --- Content: history + transcript ---
content = ctk.CTkFrame(root, fg_color="transparent")
content.pack(fill="both", expand=True, padx=24, pady=(0, 20))

history = ctk.CTkFrame(content, width=250, corner_radius=14)
history.pack(side="left", fill="y", padx=(0, 12))
history.pack_propagate(False)
ctk.CTkLabel(history, text="History", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=16, pady=(16, 8))
hist_items = [
    "06-22 00:30  北大女生聚会 [润色]",
    "06-22 00:22  北大女生聚会",
    "06-21 23:33  全嘻嘻-同学聚会",
    "06-12 22:48  全英Talk-资源劣势",
]
for it in hist_items:
    ctk.CTkLabel(history, text=it, anchor="w", justify="left",
                 font=ctk.CTkFont(size=11), text_color="gray30").pack(anchor="w", padx=16, pady=3)

transcript = ctk.CTkFrame(content, corner_radius=14)
transcript.pack(side="left", fill="both", expand=True)
ctk.CTkLabel(transcript, text="Transcript", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=20, pady=(16, 6))
text = ctk.CTkTextbox(transcript, wrap="word", font=ctk.CTkFont(size=13))
text.pack(fill="both", expand=True, padx=16, pady=(0, 16))
text.insert("1.0", "这里显示转写或润色后的文稿内容。\n\n双击桌面图标启动后，点 Transcribe 开始；完成后点 AI Polish 润色。\n\n这是一个 UI 预览，没有实际功能。")
text.configure(state="disabled")

root.mainloop()
