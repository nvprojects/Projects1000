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
from typing import Optional  # <-- needed for Python 3.6 type hints

# ---------------------------------------------------------------------
# Load config.ini
# ---------------------------------------------------------------------

cfg = configparser.ConfigParser()
cfg.read("config.ini")

config_section = cfg.get("DEFAULT", "CONFIG")  # e.g. EDGE or CLOUD-PROD
protocol = cfg.get(config_section, "PROTOCOL")
host_port = cfg.get(config_section, "HOSTPORT")
jwt = cfg.get(config_section, "JWT")
url_prefix = cfg.get(config_section, "URLPREFIX")

input_folder = cfg.get("DEFAULT", "INPUTFOLDER")
output_folder = cfg.get("DEFAULT", "OUTPUTFOLDER")

base = "{}://{}/{}".format(protocol, host_port, url_prefix).rstrip("/")

DATA_URL = base + "/data/file"
ASYNC_TRANSCRIBE = base + "/asr/transcribe/async"

print("Using config section: {}".format(config_section))
print("Base URL: {}".format(base))
print("Input folder: {}".format(input_folder))
print("Output folder: {}".format(output_folder))

if not os.path.exists(output_folder):
    os.makedirs(output_folder)

AUTH_HEADER = {"Authorization": jwt}

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def upload_audio_get_uuid(path: str) -> Optional[str]:
    """Upload local WAV file via /data/file and return dataStore uuid."""
    print("\n📤 Uploading audio: {}".format(path))
    with open(path, "rb") as fh:
        audio_bytes = fh.read()

    headers = AUTH_HEADER.copy()
    headers["Content-Type"] = "audio/wav"

    resp = requests.post(DATA_URL, headers=headers, data=audio_bytes)
    if resp.status_code != 200:
        print("❌ /data/file HTTP {} for {}: {}".format(
            resp.status_code, path, resp.text[:300]
        ))
        return None

    try:
        data = resp.json()
    except Exception as e:
        print("❌ Failed to parse JSON from /data/file response: {}".format(e))
        return None

    uuid = data.get("uuid")
    if not uuid:
        print("❌ No uuid in /data/file response for {}: {}".format(path, data))
        return None

    print("✅ Uploaded, dataStore uuid = {}".format(uuid))
    return uuid


def start_offline_job(data_uuid: str) -> Optional[str]:
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
        print("❌ /asr/transcribe/async HTTP {}: {}".format(
            resp.status_code, resp.text[:400]
        ))
        return None

    try:
        data = resp.json()
    except Exception as e:
        print("❌ Failed to parse JSON from async start: {}".format(e))
        return None

    try:
        poll_url = data["sessions"][0]["poll"]["url"]
    except Exception as e:
        print("❌ Could not find poll.url in response: {} ({})".format(data, e))
        return None

    print("✅ OFF-LINE session started. Poll URL: {}".format(poll_url))
    return poll_url


def wait_for_final_transcript(poll_url: str, poll_interval: float = 5.0) -> Optional[str]:
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
            print("❌ Polling error: {}".format(e))
            return None

        if resp.status_code != 200:
            print("❌ Poll HTTP {}: {}".format(resp.status_code, resp.text[:300]))
            return None

        try:
            data = resp.json()
        except Exception as e:
            print("❌ Failed to parse JSON from poll response: {}".format(e))
            return None

        result = data.get("result", {})
        is_final = result.get("final", False)
        progress = data.get("progress") or {}
        phase = progress.get("phase")

        print("  - phase={}, final={}".format(phase, is_final))
        if is_final:
            break

        time.sleep(poll_interval)

    # Now retrieve full result (same endpoint, full=true)
    resp = requests.get(poll_url, headers=AUTH_HEADER, params={"full": "true"})
    if resp.status_code != 200:
        print("❌ Final poll HTTP {}: {}".format(resp.status_code, resp.text[:300]))
        return None

    try:
        data = resp.json()
    except Exception as e:
        print("❌ Failed to parse JSON from final poll: {}".format(e))
        return None

    result = data.get("result", {})
    status = result.get("status")

    if status != "MATCH":
        print("❌ Final status is {}, no transcript.".format(status))
        return None

    transcript = result.get("transcript", "")
    print("✅ Got transcript ({} chars).".format(len(transcript)))
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
        print("⚠️ No .wav files found in INPUTFOLDER: {}".format(input_folder))
        return

    print("\nFound {} wav file(s):".format(len(wav_files)))
    for f in wav_files:
        print(" - {}".format(f))

    for fname in wav_files:
        in_path = os.path.join(input_folder, fname)
        base_name = os.path.splitext(fname)[0]
        out_path = os.path.join(output_folder, base_name + "_redacted.txt")

        print("\n" + "=" * 70)
        print("🎧 Processing {}".format(fname))

        data_uuid = upload_audio_get_uuid(in_path)
        if not data_uuid:
            print("Skipping {} (upload failed).".format(fname))
            continue

        poll_url = start_offline_job(data_uuid)
        if not poll_url:
            print("Skipping {} (async start failed).".format(fname))
            continue

        transcript = wait_for_final_transcript(poll_url)
        if transcript is None:
            print("Skipping {} (no final transcript).".format(fname))
            continue

        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(transcript)

        print("💾 Saved redacted transcript to {}".format(out_path))

    print("\n✅ Done.")


if __name__ == "__main__":
    main()
