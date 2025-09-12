import pathlib
import subprocess
import json
import os
import sys

INPUT_DIR = "input"
OUT_ROOT = "out"
DEVICE = "cpu"

def fmt(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h*3600 - m*60
    if h:
        return f"{h:02d}:{m:02d}:{s:06.3f}"
    else:
        return f"{m:02d}:{s:06.3f}"

def main():
    in_dir = pathlib.Path(INPUT_DIR)
    out_root = pathlib.Path(OUT_ROOT)
    out_root.mkdir(parents=True, exist_ok=True)

    exts = {".mp3",".wav",".m4a",".mp4",".flac",".mkv",".aac",".ogg",".wma",".webm",".mov"}
    files = [p for p in in_dir.glob("*") if p.suffix.lower() in exts and p.is_file()]
    if not files:
        print(f"No audio/video files found in: {in_dir.resolve()}")
        sys.exit(0)

    for f in files:
        stem_out_dir = out_root / f.stem
        stem_out_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "whisper",
            str(f),
            "--device", DEVICE,
            "--output_dir", str(stem_out_dir),
            "--language", "English",
        ]

        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Failed on {f.name}: {e}")
            continue

        json_path = stem_out_dir / (f.stem + ".json")
        if json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as jf:
                    data = json.load(jf)
                lines_path = stem_out_dir / (f.stem + ".lines.txt")
                with open(lines_path, "w", encoding="utf-8") as out:
                    for seg in data.get("segments", []):
                        start = seg.get("start", 0.0)
                        end = seg.get("end", 0.0)
                        text = (seg.get("text") or "").strip()
                        out.write(f"[{fmt(start)} → {fmt(end)}] {text}\n")
                print(f"Wrote {lines_path}")
            except Exception as e:
                print(f"Post-process failed for {f.name}: {e}")
        else:
            print(f"No JSON found for {f.name}; skipping one-line export.")

    print("Done.")

if __name__ == "__main__":
    main()
