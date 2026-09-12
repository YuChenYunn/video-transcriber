"""Download a large file from the official PyTorch index with resume support,
then install it into the GPU venv with pip.

Run with the venv's python so pip targets the right environment:

    E:\\AI\\whisper-gpu\\venv\\Scripts\\python.exe fetch_cuda_torch.py

It downloads to the OS temp dir, resumes on interruption, and retries up to
N times. After a complete download it runs `pip install <wheel>`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

URL = "https://download.pytorch.org/whl/cu128/torch-2.8.0%2Bcu128-cp312-cp312-win_amd64.whl"
DEST = Path(os.environ.get("TEMP", str(Path.home()))) / "torch-2.8.0+cu128-cp312-cp312-win_amd64.whl"
MAX_RETRIES = 30
TIMEOUT_CONNECT = 60
TIMEOUT_READ = 120


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def get_remote_size() -> int:
    req = urllib.request.Request(URL, method="HEAD")
    with urllib.request.urlopen(req, timeout=TIMEOUT_CONNECT) as resp:
        return int(resp.headers.get("Content-Length", 0))


def download():
    total = get_remote_size()
    existing = DEST.stat().st_size if DEST.exists() else 0
    print(f"Remote size: {fmt_size(total)}", flush=True)
    print(f"Already have: {fmt_size(existing)}", flush=True)

    if existing >= total and total > 0:
        print("File already complete.", flush=True)
        return

    for attempt in range(1, MAX_RETRIES + 1):
        existing = DEST.stat().st_size if DEST.exists() else 0
        if existing >= total and total > 0:
            print("File complete.", flush=True)
            return
        mode = "ab" if existing else "wb"
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        req = urllib.request.Request(URL, headers=headers)
        print(f"[attempt {attempt}] resuming from {fmt_size(existing)} / {fmt_size(total)} ...", flush=True)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_CONNECT) as resp, open(DEST, mode) as fh:
                start = time.time()
                last_print = start
                downloaded_this_run = 0
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    downloaded_this_run += len(chunk)
                    now = time.time()
                    if now - last_print >= 5:
                        done = existing + downloaded_this_run
                        pct = (done / total * 100) if total else 0
                        speed = downloaded_this_run / (now - start + 0.001)
                        print(f"  {fmt_size(done)} / {fmt_size(total)} ({pct:.1f}%) @ {fmt_size(speed)}/s", flush=True)
                        last_print = now
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            print(f"  interrupted: {exc}; retrying in 3s...", flush=True)
            time.sleep(3)
            continue
        # reached end of stream; verify size
        existing = DEST.stat().st_size
        if existing >= total:
            print(f"File complete: {fmt_size(existing)}", flush=True)
            return
        print(f"  stream ended early at {fmt_size(existing)}; retrying...", flush=True)

    raise RuntimeError(f"Failed to complete download after {MAX_RETRIES} attempts")


def install():
    print(f"Installing {DEST.name} with pip...", flush=True)
    cmd = [sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", str(DEST)]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"pip install failed with exit code {result.returncode}")
    print("Installed.", flush=True)


def main() -> int:
    try:
        download()
        install()
        print("OK", flush=True)
        return 0
    except Exception as exc:
        print(f"FAILED: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
