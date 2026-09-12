"""Standalone whisperX runner. Run inside the GPU venv.

Usage:
    python whisperx_runner.py --audio audio.mp3 --output_dir out --model medium \
        --language zh --diarize --hf_token HF_TOKEN [--compute_type auto] [--batch_size 16] \
        [--min_speakers 2] [--max_speakers 4]

Outputs `{stem}.txt` (with [SPEAKER_xx] tags when diarized) and `{stem}.srt`
next to the audio in --output_dir. Prints progress lines so the parent
process can show them in its log panel.

This file intentionally has no third-party imports at module top-level that
the main app needs; it is only ever executed by the GPU venv's python.
"""

from __future__ import annotations

import argparse
import gc
import os
import sys
import time
from pathlib import Path

# The stdout pipe uses the locale code page (GBK on zh-CN Windows), which cannot
# encode every character that is legal in a filename (e.g. U+29F8 "⧸" from a
# video title) — printing such a path used to kill the run mid-transcription.
# Keep the locale encoding itself (the parent GUI decodes with the same locale)
# and only replace unencodable characters.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

# Use the HF mirror as the endpoint (huggingface.co itself times out in mainland
# China) and default to OFFLINE mode so already-cached models are used directly
# without any network call. Without this, huggingface_hub does a HEAD request to
# check for updates on every run, which blocks for a long time when the network
# is flaky. The cache was populated by a previous successful run.
# If a model is NOT yet cached, the code below temporarily disables offline mode
# to allow one download attempt through the mirror.
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_OFFLINE", "1")


def log(message: str) -> None:
    print(message, flush=True)


def make_chunk_progress_hook(chunk_index: int, n_chunks: int):
    """whisperX calls the hook with a 0..1 fraction for the current chunk.

    Maps it to overall progress across all chunks and emits a "Transcribing:
    NN%" log line every ~2% so the parent GUI can drive its progress bar.
    """
    last_bucket = [-1]

    def hook(fraction: float) -> None:
        try:
            frac = max(0.0, min(1.0, float(fraction)))
        except (TypeError, ValueError):
            return
        percent = int(round((chunk_index + frac) / n_chunks * 100))
        bucket = percent - percent % 2
        if bucket > last_bucket[0]:
            last_bucket[0] = bucket
            log(f"Transcribing: {bucket}%")

    return hook


def load_with_offline_fallback(stage_name: str, loader):
    """Run a model loader. Try offline first (uses the HF cache, no network).
    If that fails because the model isn't cached, retry online via the mirror.

    `loader` is a zero-arg callable that performs the load and returns the model.
    """
    try:
        return loader()
    except Exception as exc:
        log(f"{stage_name}: offline load failed ({type(exc).__name__}); trying online via mirror...")
        os.environ["HF_HUB_OFFLINE"] = "0"
        try:
            result = loader()
            # Back to offline for subsequent stages.
            os.environ["HF_HUB_OFFLINE"] = "1"
            return result
        except Exception as exc2:
            os.environ["HF_HUB_OFFLINE"] = "1"
            raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="whisperX transcription + diarization")
    parser.add_argument("--audio", required=True, help="Path to audio/video file")
    parser.add_argument("--output_dir", required=True, help="Directory to write .txt/.srt into")
    parser.add_argument("--model", default="medium", help="whisper model name")
    parser.add_argument("--language", default="", help="language code, e.g. zh, en; empty = auto")
    parser.add_argument("--diarize", action="store_true", help="run speaker diarization")
    parser.add_argument("--hf_token", default="", help="HuggingFace token for pyannote models")
    parser.add_argument("--compute_type", default="auto",
                        help="faster-whisper compute_type, or 'auto'")
    parser.add_argument("--batch_size", type=int, default=16, help="whisperX batch size")
    parser.add_argument("--min_speakers", type=int, default=0, help="hint: minimum speakers")
    parser.add_argument("--max_speakers", type=int, default=0, help="hint: maximum speakers")
    parser.add_argument("--stem", default="", help="output filename stem; defaults to audio stem")
    return parser.parse_args()


def resolve_compute_type(requested: str, model: str) -> str:
    if requested and requested != "auto":
        return requested
    # On an 8GB card, large models need int8 to fit; smaller ones are fine on fp16.
    large_models = {"large", "large-v1", "large-v2", "large-v3"}
    if model.lower() in large_models:
        return "int8"
    return "float16"


def fmt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(segments: list, path: Path) -> None:
    lines = []
    for index, seg in enumerate(segments, start=1):
        start = seg.get("start", 0.0)
        end = seg.get("end", 0.0)
        speaker = seg.get("speaker", "")
        text = (seg.get("text", "") or "").strip()
        prefix = f"[{speaker}] " if speaker else ""
        lines.append(str(index))
        lines.append(f"{fmt_timestamp(start)} --> {fmt_timestamp(end)}")
        lines.append(prefix + text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_txt(segments: list, path: Path) -> None:
    parts = []
    current_speaker = None
    buffer = []
    for seg in segments:
        speaker = seg.get("speaker", "")
        text = (seg.get("text", "") or "").strip()
        if not text:
            continue
        if speaker != current_speaker:
            if buffer:
                parts.append(f"[{current_speaker}] " + " ".join(buffer) if current_speaker else " ".join(buffer))
                buffer = []
            current_speaker = speaker
        buffer.append(text)
    if buffer:
        parts.append(f"[{current_speaker}] " + " ".join(buffer) if current_speaker else " ".join(buffer))
    path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    audio_path = Path(args.audio)
    if not audio_path.is_file():
        log(f"ERROR: audio file not found: {audio_path}")
        return 2
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.stem or audio_path.stem
    compute_type = resolve_compute_type(args.compute_type, args.model)

    try:
        import torch
        import whisperx
        from whisperx.diarize import DiarizationPipeline
    except ImportError as exc:
        log(f"ERROR: missing dependency in this Python environment: {exc}")
        log("Install whisperX in the GPU venv first.")
        return 3

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log(f"Device: {device} | model: {args.model} | compute_type: {compute_type} | batch_size: {args.batch_size}")

    log("Loading whisper model...")
    model_loaded = load_with_offline_fallback(
        "whisper model",
        lambda: whisperx.load_model(
            args.model,
            device,
            compute_type=compute_type,
            language=(args.language or None),
        ),
    )

    log("Loading audio...")
    audio = whisperx.load_audio(str(audio_path))

    # whisperX has no streaming API — transcribe() only returns when the whole
    # audio is done, so a crash mid-run loses everything. Instead, transcribe in
    # chunks and rewrite the checkpoint file after each chunk: killing the
    # process at any point keeps everything transcribed so far in
    # {stem}.partial.txt. Trade-off: a sentence spanning a chunk boundary can
    # come out slightly garbled; 4-minute chunks keep that rare.
    partial_path = output_dir / f"{stem}.partial.txt"
    chunk_seconds = 240
    sample_rate = 16000  # whisperx.load_audio resamples to 16 kHz mono
    step = chunk_seconds * sample_rate
    n_chunks = max(1, -(-len(audio) // step))
    language = args.language or None
    segments = []
    log("Transcribing...")
    for index in range(n_chunks):
        start = index * step
        chunk = audio[start:start + step]
        offset = start / sample_rate
        transcribe_kwargs = {
            "batch_size": args.batch_size,
            "progress_callback": make_chunk_progress_hook(index, n_chunks),
        }
        if language:
            transcribe_kwargs["language"] = language
        chunk_result = model_loaded.transcribe(chunk, **transcribe_kwargs)
        if not language:
            language = chunk_result.get("language") or language
        for seg in chunk_result.get("segments", []):
            seg["start"] = seg.get("start", 0.0) + offset
            seg["end"] = seg.get("end", 0.0) + offset
            segments.append(seg)
        write_txt(segments, partial_path)
        log(f"Checkpoint saved: chunk {index + 1}/{n_chunks} -> {partial_path.name}")
    detected = language or "auto"
    log(f"Detected/used language: {detected}")
    result = {"segments": segments, "language": detected}

    gc.collect()
    if device == "cuda":
        torch.cuda.empty_cache()

    # Word-level alignment (improves timestamps, needed before diarization).
    # Runs through the HF mirror set globally at the top of this file.
    log("Aligning timestamps...")
    try:
        align_model, align_meta = load_with_offline_fallback(
            "alignment model",
            lambda: whisperx.load_align_model(language_code=detected, device=device),
        )
        result = whisperx.align(
            result["segments"],
            align_model,
            align_meta,
            audio,
            device,
            return_char_alignments=False,
        )
        # Checkpoint the aligned timestamps too, so a crash during diarization
        # still leaves the fully-aligned text on disk.
        write_txt(result["segments"], partial_path)
    except Exception as exc:
        log(f"WARNING: alignment failed ({exc}); continuing with segment timestamps.")

    if args.diarize:
        if not args.hf_token:
            log("WARNING: --diarize requested but no --hf_token provided; skipping diarization.")
        else:
            log("Diarizing (loading pyannote model)...")
            diarize_kwargs = {}
            if args.min_speakers > 0:
                diarize_kwargs["min_speakers"] = args.min_speakers
            if args.max_speakers > 0:
                diarize_kwargs["max_speakers"] = args.max_speakers
            try:
                diarize_pipeline = load_with_offline_fallback(
                    "diarization model",
                    lambda: DiarizationPipeline(token=args.hf_token, device=device),
                )
                diarize_segments = diarize_pipeline(audio, **diarize_kwargs)
                result = whisperx.assign_word_speakers(diarize_segments, result)
                speakers = sorted({s.get("speaker", "") for s in result["segments"] if s.get("speaker")})
                log(f"Speakers found: {', '.join(speakers) or 'none'}")
            except Exception as exc:
                log(f"WARNING: diarization failed ({exc}); continuing without speaker labels.")

    txt_path = output_dir / f"{stem}.txt"
    segments = result.get("segments", [])
    write_txt(segments, txt_path)
    log(f"Saved transcript: {txt_path}")
    partial_path.unlink(missing_ok=True)
    log("Finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
