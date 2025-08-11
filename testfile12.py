#!/usr/bin/env python3
import argparse
import sys
import time
import json
from datetime import datetime
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs

import requests
from requests.adapters import HTTPAdapter, Retry
import urllib3
import pandas as pd

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def find_record_list(obj):

    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                return v
    return []


def build_url_with_params(base_url: str, date_from: str, date_to: str, page_number: int) -> str:
    
    parsed = urlparse(base_url)
    q = parse_qs(parsed.query)
    q["dateFrom"] = [date_from]
    q["dateTo"] = [date_to]
    q["pageNumber"] = [str(page_number)]
    new_query = urlencode({k: v[0] for k, v in q.items()})
    return urlunparse(parsed._replace(query=new_query))


def make_session(retries: int, backoff: float) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"])
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def pull_avaya_data(
    date_from: str,
    date_to: str,
    output_filename: str | None = None,
    base_url: str = "https://10.10.10.10:2000/avaya-data-export/Avaya1_ECHI",
    start_page: int = 1,
    page_size_hint: int = 100,
    sleep: float = 0.15,
    max_pages: int = 0,
    timeout: int = 30,
    insecure_tls: bool = True,
    retries: int = 2,
    backoff: float = 0.5,
    verbose: bool = True,
):

    session = make_session(retries=retries, backoff=backoff)

    if not output_filename:
        date_part = date_from.split("T")[0].replace("-", "")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"avaya_data_{date_part}_{ts}.csv"

    total_rows = 0
    page = start_page
    wrote_header = False
    first_columns = None

    if verbose:
        print(f"Starting data pull from {date_from} to {date_to}")

    while True:
        url = build_url_with_params(base_url, date_from, date_to, page)
        if verbose:
            print(f"Fetching page {page}...")

        try:
            r = session.get(url, verify=not insecure_tls is True, timeout=timeout)
            r.raise_for_status()
            try:
                payload = r.json()
            except json.JSONDecodeError:
                payload = json.loads(r.text)

            records = find_record_list(payload)
            if not records:
                if verbose:
                    print(f"No data found on page {page}. Stopping.")
                break

            df = pd.json_normalize(records, sep=".")

            # Keep a stable CSV schema based on first page
            if not wrote_header:
                first_columns = list(df.columns)
                df.to_csv(output_filename, index=False, mode="w", header=True)
                wrote_header = True
            else:
                # Reindex to first-page columns; drop unseen columns later.
                df = df.reindex(columns=first_columns)
                df.to_csv(output_filename, index=False, mode="a", header=False)

            n = len(df)
            total_rows += n
            if verbose:
                print(f"Page {page}: {n} records written (total {total_rows})")

            # Likely last page if short; otherwise continue
            if n < page_size_hint:
                if verbose:
                    print("Last page reached (less than page_size_hint records).")
                break

            page += 1
            if max_pages and (page - start_page + 1) > max_pages:
                if verbose:
                    print(f"Reached max_pages={max_pages}.")
                break

            time.sleep(sleep)

        except requests.RequestException as e:
            print(f"Error fetching page {page}: {e}", file=sys.stderr)
            break
        except Exception as e:
            print(f"Unexpected error on page {page}: {e}", file=sys.stderr)
            break

    if wrote_header:
        if verbose:
            print("\nData export complete!")
            print(f"Total records: {total_rows}")
            print(f"Total pages: {page if total_rows else 0}")
            print(f"Output file: {output_filename}")
        return output_filename, total_rows, page
    else:
        if verbose:
            print("No data retrieved. Please check the date range and API health.", file=sys.stderr)
        return None, 0, page


def parse_args():
    ap = argparse.ArgumentParser(
        description="Grab Avaya ECHI report and save to a single CSV file (streaming)."
    )
    ap.add_argument("--date-from", required=True, help="ISO8601 start, e.g. 2025-08-11T00:00:00")
    ap.add_argument("--date-to", required=True, help="ISO8601 end,   e.g. 2025-08-11T23:59:59")
    ap.add_argument("--output", "-o", default=None, help="Output CSV path (default auto-named)")
    ap.add_argument("--base-url", default="https://10.10.10.10:2000/avaya-data-export/Avaya1_ECHI",
                    help="Base endpoint or example URL; extra query params will be preserved")
    ap.add_argument("--start-page", type=int, default=1, help="Page number to start from (default 1)")
    ap.add_argument("--page-size-hint", type=int, default=100, help="Records per page (hint for last-page detection)")
    ap.add_argument("--sleep", type=float, default=0.15, help="Seconds to sleep between requests")
    ap.add_argument("--max-pages", type=int, default=0, help="Hard cap on pages (0 = no cap)")
    ap.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds")
    ap.add_argument("--retries", type=int, default=2, help="Total retries on 429/5xx")
    ap.add_argument("--backoff", type=float, default=0.5, help="Backoff factor between retries")
    ap.add_argument("--insecure", action="store_true", help="Allow self-signed TLS (sets verify=False)")
    ap.add_argument("--quiet", action="store_true", help="Reduce console output")
    return ap.parse_args()


def main():
    args = parse_args()
    insecure_tls = True if args.insecure else False

    pull_avaya_data(
        date_from=args.date_from,
        date_to=args.date_to,
        output_filename=args.output,
        base_url=args.base_url,
        start_page=args.start_page,
        page_size_hint=args.page_size_hint,
        sleep=args.sleep,
        max_pages=args.max_pages,
        timeout=args.timeout,
        insecure_tls=insecure_tls,
        retries=args.retries,
        backoff=args.backoff,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
