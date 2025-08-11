#!/usr/bin/env python3
"""
Avaya ECHI pull (interactive)
- Prompts for start/end datetimes, then fetches all pages and writes ONE CSV.
- Auto-names the CSV using the date range.
- Uses Table_Details.totalPages and Table_Details.data.

Accepted input formats (examples):
  2025-08-11T00:00:00
  2025-08-11 00:00
  2025-08-11            (start -> 00:00:00, end -> 23:59:59)
  08/11/2025 00:00
"""

import json
import sys
import time
from datetime import datetime
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs

import requests
import pandas as pd
import urllib3

#######
BASE_URL     = "https://10.10.10.10:2000/avaya-data-export/Avaya1_ECHI"
BEARER_TOKEN = "REPLACE_WITH_YOUR_TOKEN"
INSECURE     = True
HOST_HEADER  = None
TIMEOUT_SECS = 30
SLEEP_BETWEEN_PAGES = 0.15
# ===================================================

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def parse_datetime_user(s: str, is_end: bool = False) -> datetime:
    """
    Parse common date/time inputs. If only a date is given:
    - start gets 00:00:00
    - end   gets 23:59:59
    """
    s = s.strip()
    fmts = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%d",
        "%m/%d/%Y",
    ]
    for f in fmts:
        try:
            dt = datetime.strptime(s, f)
            if f in ("%Y-%m-%d", "%m/%d/%Y"):
                if is_end:
                    return dt.replace(hour=23, minute=59, second=59)
                else:
                    return dt.replace(hour=0, minute=0, second=0)
            return dt
        except ValueError:
            continue
    raise ValueError(f"Could not parse date/time: {s!r}. Try e.g. 2025-08-11T00:00:00")

def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")

def build_url(base_url: str, date_from_iso: str, date_to_iso: str, page_number: int) -> str:
    parsed = urlparse(base_url)
    q = parse_qs(parsed.query)
    q["dateFrom"] = [date_from_iso]
    q["dateTo"]   = [date_to_iso]
    q["pageNumber"] = [str(page_number)]
    new_query = urlencode({k: v[0] for k, v in q.items()})
    return urlunparse(parsed._replace(query=new_query))

def get_page(session: requests.Session, url: str, headers: dict) -> dict:
    r = session.get(url, headers=headers, verify=(False if INSECURE else True), timeout=TIMEOUT_SECS)
    if r.status_code in (401, 403):
        raise RuntimeError(f"Auth failed ({r.status_code}). Check/refresh your bearer token.")
    r.raise_for_status()
    text = (r.text or "").strip()
    if not text or not (text.startswith("{") or text.startswith("[")):
        ctype = r.headers.get("content-type", "")
        preview = text[:200].replace("\n", " ")
        raise RuntimeError(f"Non-JSON/empty response (status={r.status_code}, content-type={ctype}). Body: {preview!r}")
    try:
        return r.json()
    except json.JSONDecodeError as ex:
        raise RuntimeError(f"Failed to parse JSON: {ex}") from ex

def extract_table_details(payload: dict):
    """
    Returns (total_pages:int, records:list) from a Table_Details-shaped payload.
    Handles light key-casing differences.
    """
    td = (payload.get("Table_Details") or payload.get("table_details") or {})
    if not isinstance(td, dict):
        return 0, []
    lower = { (k.lower() if isinstance(k, str) else k): v for k, v in td.items() }
    total_pages = lower.get("totalpages")
    if total_pages is None and "totalPages" in td:
        total_pages = td["totalPages"]
    try:
        total_pages = int(total_pages) if total_pages is not None else 0
    except Exception:
        total_pages = 0
    data = lower.get("data") or td.get("data") or []
    return total_pages, data

def auto_filename(start_dt: datetime, end_dt: datetime) -> str:
    s = start_dt.strftime("%Y%m%d_%H%M%S")
    e = end_dt.strftime("%Y%m%d_%H%M%S")
    return f"avaya_{s}_to_{e}.csv"

def main():
    if BEARER_TOKEN == "REPLACE_WITH_YOUR_TOKEN":
        print("ERROR: Set BEARER_TOKEN at the top of the script.", file=sys.stderr)
        sys.exit(2)

##Enter the date and time range
    try:
        start_in = input("Enter START datetime (e.g. 2025-08-11T00:00:00 or 2025-08-11 00:00): ").strip()
        end_in   = input("Enter END   datetime (e.g. 2025-08-11T02:00:00 or 2025-08-11 02:00): ").strip()
        start_dt = parse_datetime_user(start_in, is_end=False)
        end_dt   = parse_datetime_user(end_in,   is_end=True)
    except Exception as ex:
        print(f"Invalid input: {ex}", file=sys.stderr)
        sys.exit(2)

    if end_dt < start_dt:
        print("END datetime must be after START datetime.", file=sys.stderr)
        sys.exit(2)

    date_from_iso = iso(start_dt)
    date_to_iso   = iso(end_dt)
    output_csv    = auto_filename(start_dt, end_dt)

    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "Accept": "application/json",
    }
    if HOST_HEADER:
        headers["Host"] = HOST_HEADER

    session = requests.Session()

    print(f"\nFetching page 1 for {date_from_iso} → {date_to_iso} ...")
    page1_url = build_url(BASE_URL, date_from_iso, date_to_iso, 1)
    payload = get_page(session, page1_url, headers)
    total_pages, data = extract_table_details(payload)

    if total_pages <= 0:
        total_pages = 10**9

    frames = []
    if data:
        frames.append(pd.json_normalize(data, sep="."))
        print(f"Page 1: {len(data)} records")
    else:
        print("No data on page 1.")

    for page in range(2, total_pages + 1):
        url = build_url(BASE_URL, date_from_iso, date_to_iso, page)
        print(f"Fetching page {page}{'' if total_pages >= 10**9 else f' of {total_pages}'} ...")
        try:
            payload = get_page(session, url, headers)
        except Exception as e:
            print(f"Error on page {page}: {e}", file=sys.stderr)
            break

        _, data = extract_table_details(payload)
        if not data:
            print(f"Page {page}: no data; stopping.")
            break

        frames.append(pd.json_normalize(data, sep="."))
        print(f"Page {page}: {len(data)} records")
        time.sleep(SLEEP_BETWEEN_PAGES)

    if not frames:
        print("\nNo data returned for the given range.")
        sys.exit(0)

    df = pd.concat(frames, ignore_index=True)
    df.to_csv(output_csv, index=False)
    print(f"\nDone. Wrote {len(df)} rows to {output_csv}")

if __name__ == "__main__":
    main()
