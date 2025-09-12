import pathlib
import subprocess
import os
import sys

INPUT_DIR = "input"
OUT_ROOT  = "out"
DEVICE    = "cpu"
LANGUAGE  = None

def main():
    in_dir  = pathlib.Path(INPUT_DIR)
    out_root = pathlib.Path(OUT_ROOT)
    out_root.mkdir(parents=True, exist_ok=True)

    exts = {".mp3", ".wav", ".m4a", ".mp4", ".flac", ".mkv", ".aac", ".ogg", ".wma", ".webm", ".mov"}
    files = [p for p in in_dir.glob("*") if p.suffix.lower() in exts and p.is_file()]

    if not files:
        print(f"No audio/video files found in: {in_dir.resolve()}")
        sys.exit(0)

    for f in files:
        stem_out_dir = out_root / f.stem
        stem_out_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n🎙️  Transcribing: {f.name}")
        cmd = [
            "whisper",
            str(f),
            "--device", DEVICE,
            "--output_dir", str(stem_out_dir),
            "--task", "transcribe",
        ]
        if LANGUAGE:
            cmd += ["--language", LANGUAGE]

        try:
            subprocess.run(cmd, check=True)
            print(f"✅ Saved to: {stem_out_dir.resolve()}")
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed on {f.name}: {e}")

    print("\nAll done!")

if __name__ == "__main__":
    main()