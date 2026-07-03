#!/usr/bin/env python3
"""
Fetch historical VATSIM pilot sessions and write them to a CSV file.

Usage:
  python vatsim_flights_to_csv.py --cid 1234567 --user-agent "your-name-vatsim-export/1.0 contact:you@example.com"

Optional environment variables:
  $env:VATSIM_USER_AGENT="your-name-vatsim-export/1.0 contact:you@example.com"
  $env:VATSIM_API_KEY="YOUR_KEY"  # Not required for this endpoint, but supported if you have one.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api.vatsim.net/v2"
DEFAULT_PAGE_LIMIT = 100
DEFAULT_SLEEP_SECONDS = 0.5
CSV_COLUMNS = [
    "session_id",
    "vatsim_id",
    "callsign",
    "start_utc",
    "end_utc",
    "duration",
    "duration_minutes",
    "server",
    "type",
    "rating",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch all previous VATSIM pilot sessions and save them as a tidy CSV."
    )
    parser.add_argument("--cid", required=True, help="Your VATSIM CID/member ID.")
    parser.add_argument(
        "--user-agent",
        default=os.getenv("VATSIM_USER_AGENT"),
        help="Required. Identifies your script to VATSIM, preferably with contact info. Can also be set with VATSIM_USER_AGENT.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("VATSIM_API_KEY"),
        help="Optional VATSIM API key. Not required for member history if you respect rate limits.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="CSV output path. Defaults to vatsim_flights_<CID>.csv in this folder.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_PAGE_LIMIT,
        help=f"Rows to fetch per API request. Default: {DEFAULT_PAGE_LIMIT}.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help=f"Seconds to wait between paginated requests. Default: {DEFAULT_SLEEP_SECONDS}.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"VATSIM API base URL. Default: {DEFAULT_BASE_URL}.",
    )
    return parser.parse_args()


def api_get_json(url: str, user_agent: str, api_key: str | None) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": user_agent,
    }
    if api_key:
        headers["X-API-Key"] = api_key

    request = Request(url, headers=headers)

    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"VATSIM API returned HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach VATSIM API: {exc.reason}") from exc


def fetch_all_sessions(
    cid: str,
    user_agent: str,
    api_key: str | None,
    base_url: str,
    limit: int,
    sleep_seconds: float,
) -> list[dict[str, Any]]:
    offset = 0
    sessions: list[dict[str, Any]] = []
    total_count: int | None = None

    while True:
        query = urlencode({"limit": limit, "offset": offset})
        url = f"{base_url.rstrip('/')}/members/{cid}/history?{query}"
        data = api_get_json(url, user_agent=user_agent, api_key=api_key)

        items = data.get("items")
        if not isinstance(items, list):
            raise RuntimeError("Unexpected API response: missing 'items' list.")

        if total_count is None:
            count = data.get("count")
            total_count = count if isinstance(count, int) else None

        sessions.extend(items)
        print(
            f"Fetched {len(sessions)}"
            + (f"/{total_count}" if total_count is not None else "")
            + " sessions...",
            file=sys.stderr,
        )

        offset += len(items)
        if not items or (total_count is not None and offset >= total_count):
            break

        time.sleep(sleep_seconds)

    return sessions


def parse_api_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_datetime(value: Any) -> str:
    parsed = parse_api_datetime(value)
    if parsed is None:
        return ""
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def duration_fields(start_value: Any, end_value: Any) -> tuple[str, str]:
    start = parse_api_datetime(start_value)
    end = parse_api_datetime(end_value)
    if start is None or end is None or end < start:
        return "", ""

    total_minutes = int((end - start).total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}", str(total_minutes)


def normalize_session(session: dict[str, Any]) -> dict[str, str]:
    duration, duration_minutes = duration_fields(session.get("start"), session.get("end"))
    return {
        "session_id": str(session.get("id", "")),
        "vatsim_id": str(session.get("vatsim_id", "")),
        "callsign": str(session.get("callsign", "")),
        "start_utc": format_datetime(session.get("start")),
        "end_utc": format_datetime(session.get("end")),
        "duration": duration,
        "duration_minutes": duration_minutes,
        "server": str(session.get("server", "")),
        "type": str(session.get("type", "")),
        "rating": str(session.get("rating", "")),
    }


def write_csv(sessions: list[dict[str, Any]], output_path: Path) -> None:
    rows = [normalize_session(session) for session in sessions]
    rows.sort(key=lambda row: row["start_utc"], reverse=True)

    with output_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()

    if not args.user_agent:
        print(
            "Missing user-agent. Pass --user-agent or set the VATSIM_USER_AGENT environment variable.",
            file=sys.stderr,
        )
        print(
            "Example: --user-agent \"vatsim-export/1.0 contact:you@example.com\"",
            file=sys.stderr,
        )
        return 2

    if args.limit < 1:
        print("--limit must be 1 or higher.", file=sys.stderr)
        return 2

    if args.sleep < 0:
        print("--sleep cannot be negative.", file=sys.stderr)
        return 2

    script_dir = Path(__file__).resolve().parent
    output_path = Path(args.output) if args.output else script_dir / f"vatsim_flights_{args.cid}.csv"
    sessions = fetch_all_sessions(
        cid=args.cid,
        user_agent=args.user_agent,
        api_key=args.api_key,
        base_url=args.base_url,
        limit=args.limit,
        sleep_seconds=args.sleep,
    )
    write_csv(sessions, output_path)

    print(f"Wrote {len(sessions)} sessions to {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
