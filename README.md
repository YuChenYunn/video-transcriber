# Video Transcriber

Paste a YouTube, Bilibili, Xiaoyuzhou, or direct audio/video link, then transcribe it locally — either with Whisper or WhisperX (with speaker diarization). Optionally polish the raw transcript into a clean speech draft with an LLM (DeepSeek by default). Optionally, a daily mailer can email new transcripts to your mailbox every evening (Windows).

## What you need

- Python
- `yt-dlp`
- `ffmpeg`
- A transcription engine, one of:
  - `openai-whisper` (lightweight), or
  - `whisperX` installed in a separate GPU venv for GPU acceleration + speaker diarization
- `openai` Python package (for the AI polish feature)

This folder does not bundle those tools. It calls the local copies already installed on your machine.

## Start

Double-click `Start Video Transcriber.bat`, or run:

```powershell
python video_transcriber.py
```

All settings are saved to `settings.json` next to the scripts. This file is gitignored because it holds your API keys, so a fresh clone only contains `settings.example.json` — copy it to `settings.json` and fill in your own values, or just edit everything in the app UI (it saves back to `settings.json`). The same applies to `mailer_config.json` / `mailer_config.example.json` for the mailer below.

## Settings

- Paste each source link into the main window. The link field starts blank every time.
- `Whisper model`: `base` is a good first test. Use `small`, `medium`, or `large-v3` for better accuracy.
- `Language`: use `en` for English, `zh` for Chinese. Leave blank to auto-detect.
- `yt-dlp path`: optional. Pick `yt-dlp.exe` if it is not available globally.
- `ffmpeg path/bin`: optional. Pick `ffmpeg.exe` or the folder that contains it.
- `Whisper path`: optional. Used only when Engine = `whisper`.
- `Cookies file`: optional. Useful for links that need your login state.
- `Cookies from browser`: optional. Pick `edge` or `chrome` if the site needs your browser login state.

### Transcription engine (whisperX)

- `Engine`: `whisperx` (GPU, recommended) or `whisper` (CPU/CLI fallback).
- `whisperX python.exe`: path to the Python executable of the GPU venv that has `whisperx` installed, e.g. `E:\AI\whisper-gpu\venv\Scripts\python.exe`.
- `HuggingFace token`: required for speaker diarization. Create one at https://huggingface.co/settings/tokens and first accept the conditions for both https://huggingface.co/pyannote/segmentation-3.0 and https://huggingface.co/pyannote/speaker-diarization-3.1.
- `Speaker diarization`: tag segments with `[SPEAKER_xx]`. Needs an HF token.
- `compute_type`: `auto` picks `float16` for small/medium and `int8` for large models (fits an 8GB card).
- `batch_size`: lower to 8 if you run out of GPU memory.

### AI polish (LLM)

- `LLM base URL`: `https://api.deepseek.com/v1` by default; any OpenAI-compatible endpoint works.
- `LLM API key`: your DeepSeek (or other provider) API key.
- `LLM model`: `deepseek-v4-flash` by default.

After a transcript finishes (or after loading one from History), click **AI Polish** to rewrite the raw transcript into a clean speech draft, saved as `{name}_draft.md`. The prompt fixes homophone/terminology errors, joins spoken fragments into full sentences, removes filler words, and preserves `[SPEAKER_xx]` tags so you can rename speakers afterwards.

### Daily transcript mailer (optional)

`daily_mailer.py` emails finished transcripts to your own mailbox once a day so you can read them on your phone. It scans the configured folder for new or updated `.txt` / `.md` / `.docx` files and sends them as attachments in one email (standard library only, nothing extra to install).

1. Copy `mailer_config.example.json` to `mailer_config.json` and fill in your mailbox and SMTP authorization code (how to get a QQ authorization code is written in the template's comment).
2. Verify SMTP with a test mail: `python daily_mailer.py --test`.
3. Double-click `install_schedule.bat` and pick `1. Install` to register a Windows scheduled task that runs `run_daily_mail.bat` every day at 21:00 (the same menu can also run it now, show status, or uninstall).
4. Already-sent files are remembered in `mailer_state.json` by modification time, so nothing is sent twice; a failed send is retried the next day. Output is logged to `mailer.log`.

`python daily_mailer.py --force-resend` re-sends every file in the folder, ignoring the state file (useful for backfilling or archiving).

## Notes

- Some Bilibili or Xiaoyuzhou links may require login cookies. Select your browser in `Cookies from browser`, or export a `cookies.txt` file and select it in the app.
- The first Whisper/whisperX run may be slow because the model has to be downloaded or loaded.
- Long videos can take a while, especially with larger models.
- whisperX diarization downloads the pyannote models on first run (one-time).

