import pathlib
import subprocess
import shlex
import os

INPUT_DIR = "input"
OUT_DIR = "out"


DEVICE = "cpu"

os.makedirs(OUT_DIR, exist_ok=True)

extensions = [".mp3", ".wav", ".m4a", ".mp4", ".flac", ".mkv"]
files = [p for p in pathlib.Path(INPUT_DIR).glob("*") if p.suffix.lower() in extensions]

for f in files:
    print(f"🎙️  Transcribing: {f.name}")
    cmd = f'whisper {shlex.quote(str(f))} --device {DEVICE} --output_dir {shlex.quote(OUT_DIR)} --output_format srt'
    subprocess.run(cmd, shell=True)

print("✅ Done! Transcripts saved in:", OUT_DIR)
