#!/usr/bin/env python3
"""
Batch OFF-LINE transcription + redaction on Voicegain EDGE.

For each .wav file in INPUTFOLDER (from config.ini):
  1) Upload audio via /data/file  -> get dataStore uuid
  2) Start OFF-LINE /asr/transcribe/async with:
        - acousticModelNonRealTime: "Voicegain-omega"
        - languages: ["en-US"]
        - audioChannelSelector: "two-channel"
        - formatters: digits, enhanced (CC, EMAIL),
                      spelling (EMAIL, lang=en-us),
                      redact (SSN, CC, CVV)
  3) Poll until result.final == true
  4) Extract result.transcript (already redacted)
  5) Save to OUTPUTFOLDER/<audio_stem>_redacted.txt
"""

import os
import time
import configparser
import requests

# ---------------------------------------------------------------------
# Load config.ini 
# ---------------------------------------------------------------------

cfg = configparser.ConfigParser()
cfg.read("config.ini")

config_section = cfg.get("DEFAULT", "CONFIG")  # e.g. EDGE or CLOUD-PROD
protocol       = cfg.get(config_section, "PROTOCOL")
host_port      = cfg.get(config_section, "HOSTPORT")
jwt            = cfg.get(config_section, "JWT")
url_prefix     = cfg.get(config_section, "URLPREFIX")

input_folder   = cfg.get("DEFAULT", "INPUTFOLDER")
output_folder  = cfg.get("DEFAULT", "OUTPUTFOLDER")

base = f"{protocol}://{host_port}/{url_prefix}".rstrip("/")

DATA_URL         = f"{base}/data/file"
ASYNC_TRANSCRIBE = f"{base}/asr/transcribe/async"

print(f"Using config section: {config_section}")
print(f"Base URL: {base}")
print(f"Input folder: {input_folder}")
print(f"Output folder: {output_folder}")

os.makedirs(output_folder, exist_ok=True)

AUTH_HEADER = {"Authorization": jwt}

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def upload_audio_get_uuid(path: str) -> str | None:
    """Upload local WAV file via /data/file and return dataStore uuid."""
    print(f"\n📤 Uploading audio: {path}")
    with open(path, "rb") as fh:
        audio_bytes = fh.read()

    headers = AUTH_HEADER.copy()
    headers["Content-Type"] = "audio/wav"

    resp = requests.post(DATA_URL, headers=headers, data=audio_bytes)
    if resp.status_code != 200:
        print(f"❌ /data/file HTTP {resp.status_code} for {path}: {resp.text[:300]}")
        return None

    data = resp.json()
    uuid = data.get("uuid")
    if not uuid:
        print(f"❌ No uuid in /data/file response for {path}: {data}")
        return None

    print(f" Uploaded, dataStore uuid = {uuid}")
    return uuid


def start_offline_job(data_uuid: str) -> str | None:
    """Start OFF-LINE async job on a given dataStore uuid, return poll URL."""
    print("▶️  Starting OFF-LINE transcription job...")

    body = {
        "sessions": [
            {
                "asyncMode": "OFF-LINE",
                "audioChannelSelector": "two-channel",
                "poll": {
                    "persist": 299999
                },
                "content": {
                    "incremental": ["progress"],
                    "full": ["transcript", "words"]
                },
            }
        ],
        "audio": {
            "source": {
                "dataStore": {
                    "uuid": data_uuid
                }
            }
        },
        "settings": {
            "asr": {
                "acousticModelNonRealTime": "Voicegain-omega",
                "confidenceThreshold": 0.01,
                "sensitivity": 0.5,
                "languages": ["en-US"],
            },
            "formatters": [
                {
                    "type": "digits"
                },
                {
                    "type": "enhanced",
                    "parameters": {
                        "CC": True,
                        "EMAIL": True
                    }
                },
                {
                    "type": "spelling",
                    "parameters": {
                        "lang": "en-us",
                        "EMAIL": True
                    }
                },
                {
                    "type": "redact",
                    "parameters": {
                        "SSN": "partial:4",
                        "CC": "partial:4",
                        "CVV": "full"
                    }
                },
            ],
        },
    }

    headers = AUTH_HEADER.copy()
    resp = requests.post(ASYNC_TRANSCRIBE, headers=headers, json=body)
    if resp.status_code != 200:
        print(f"❌ /asr/transcribe/async HTTP {resp.status_code}: {resp.text[:400]}")
        return None

    data = resp.json()
    # https://support.voicegain.ai/.../Example-of-OFF-LINE-transcription-using-API
    try:
        poll_url = data["sessions"][0]["poll"]["url"]
    except Exception as e:
        print(f"❌ Could not find poll.url in response: {data} ({e})")
        return None

    print(f"✅ OFF-LINE session started. Poll URL: {poll_url}")
    return poll_url


def wait_for_final_transcript(poll_url: str, poll_interval: float = 5.0) -> str | None:
    """
    Poll the async session until result.final == true,
    then return result.transcript (already formatted/redacted).
    """
    print("⏱  Polling for completion...")

    # First: loop with full=false to check progress
    while True:
        try:
            resp = requests.get(poll_url, headers=AUTH_HEADER, params={"full": "false"})
        except Exception as e:
            print(f"❌ Polling error: {e}")
            return None

        if resp.status_code != 200:
            print(f"❌ Poll HTTP {resp.status_code}: {resp.text[:300]}")
            return None

        data = resp.json()
        result = data.get("result", {})
        is_final = result.get("final", False)

        phase = (data.get("progress") or {}).get("phase")
        print(f"  - phase={phase}, final={is_final}")
        if is_final:
            break

        time.sleep(poll_interval)

    # Now retrieve full result (same endpoint, full=true)
    resp = requests.get(poll_url, headers=AUTH_HEADER, params={"full": "true"})
    if resp.status_code != 200:
        print(f"❌ Final poll HTTP {resp.status_code}: {resp.text[:300]}")
        return None

    data = resp.json()
    result = data.get("result", {})
    status = result.get("status")

    if status != "MATCH":
        print(f"❌ Final status is {status}, no transcript.")
        return None

    transcript = result.get("transcript", "")
    print(f"✅ Got transcript ({len(transcript)} chars).")
    return transcript


# ---------------------------------------------------------------------
# Main batch loop
# ---------------------------------------------------------------------

def main():
    wav_files = [
        f for f in sorted(os.listdir(input_folder))
        if f.lower().endswith(".wav")
    ]

    if not wav_files:
        print("⚠️ No .wav files found in INPUTFOLDER.")
        return

    print(f"\nFound {len(wav_files)} wav file(s):")
    for f in wav_files:
        print(" -", f)

    for fname in wav_files:
        in_path = os.path.join(input_folder, fname)
        base_name = os.path.splitext(fname)[0]
        out_path = os.path.join(output_folder, base_name + "_redacted.txt")

        print("\n" + "=" * 70)
        print(f"🎧 Processing {fname}")

        data_uuid = upload_audio_get_uuid(in_path)
        if not data_uuid:
            print(f"Skipping {fname} (upload failed).")
            continue

        poll_url = start_offline_job(data_uuid)
        if not poll_url:
            print(f"Skipping {fname} (async start failed).")
            continue

        transcript = wait_for_final_transcript(poll_url)
        if transcript is None:
            print(f"Skipping {fname} (no final transcript).")
            continue

        # Save to text file
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(transcript)

        print(f"💾 Saved redacted transcript to {out_path}")

    print("\n✅ Done.")


if __name__ == "__main__":
    main()



#######
[DEFAULT]
CONFIG=CLOUD-PROD
INPUTFOLDER=../data/audio_in       ; folder with your .wav files
OUTPUTFOLDER=../data/audio_out     ; where text files will go

[CLOUD-PROD]
PROTOCOL=https
HOSTPORT=api.voicegain.ai
URLPREFIX=v1
JWT=Bearer <your-token-here>
