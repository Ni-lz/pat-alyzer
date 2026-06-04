from __future__ import annotations

import argparse
import html
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DRAWS = BASE_DIR / "data" / "processed" / "draws.csv"
OUTPUT = BASE_DIR / "data" / "external" / "euromillions_machine_metadata.csv"

HEADERS = {
    "User-Agent": "Pat-alyzer metadata research; contact: repository owner",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

INVALID = {"", "nan", "none", "null", "not_found", "unknown", "n/a"}


def slug_date(date_text: str) -> str:
    date = pd.to_datetime(date_text).date()
    return date.strftime("%d-%m-%Y")


def strip_html(raw_html: str) -> str:
    without_scripts = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw_html)
    text = re.sub(r"(?s)<[^>]+>", " ", without_scripts)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_number(value: str | None) -> str:
    if value is None:
        return ""
    value = str(value).strip()
    return value if value.isdigit() else ""


def extract_labeled_int(labels: Iterable[str], text: str) -> str:
    for label in labels:
        patterns = [
            rf"{re.escape(label)}\s*(?:Used)?\s*[:#-]?\s*(\d+)",
            rf"{re.escape(label)}\s+Used\s*[:#-]?\s*(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return clean_number(match.group(1))
    return ""


def valid_ball_sequence(values: list[int]) -> bool:
    if len(values) != 7:
        return False
    mains = values[:5]
    stars = values[5:]
    return (
        all(1 <= number <= 50 for number in mains)
        and all(1 <= star <= 12 for star in stars)
        and len(set(mains)) == 5
        and len(set(stars)) == 2
    )


def extract_drawn_order(text: str) -> list[str]:
    """Extract 5 main balls + 2 stars in drawn order.

    Both euro-millions.com and lottery.co.uk place a sorted result and a drawn-order
    result close to text such as "View numbers in drawn order" or
    "Display balls in drawn order". Taking the final 7 valid ball numbers before
    that phrase is the most reliable no-dependency parser found so far.
    """
    markers = [
        "View numbers in drawn order",
        "Display balls in drawn order",
        "drawn order",
    ]

    for marker in markers:
        marker_match = re.search(re.escape(marker), text, flags=re.IGNORECASE)
        if not marker_match:
            continue

        window = text[max(0, marker_match.start() - 450) : marker_match.start()]
        values = [int(item) for item in re.findall(r"\b(?:[1-9]|[1-4]\d|50)\b", window)]

        # Walk backwards through possible 7-number windows and return the first valid one.
        for start in range(len(values) - 7, -1, -1):
            candidate = values[start : start + 7]
            if valid_ball_sequence(candidate):
                return [str(item) for item in candidate]

    return [""] * 7


def fetch_url(url: str) -> tuple[int, str]:
    response = requests.get(url, timeout=25, headers=HEADERS)
    return response.status_code, response.text


def source_urls(draw_date: str) -> list[str]:
    slug = slug_date(draw_date)
    return [
        f"https://www.euro-millions.com/results/{slug}",
        f"https://www.lottery.co.uk/euromillions/results-{slug}",
    ]


def fetch_one(draw_date: str, pause_seconds: float = 0.15) -> dict:
    base_row = {
        "draw_date": draw_date,
        "ball_machine": "",
        "ball_set": "",
        "draw_order_1": "",
        "draw_order_2": "",
        "draw_order_3": "",
        "draw_order_4": "",
        "draw_order_5": "",
        "star_order_1": "",
        "star_order_2": "",
        "source_url": "",
        "metadata_status": "not_fetched",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    for url in source_urls(draw_date):
        row = dict(base_row)
        row["source_url"] = url
        try:
            status_code, raw_html = fetch_url(url)
            if status_code != 200:
                row["metadata_status"] = f"http_{status_code}"
                continue

            text = strip_html(raw_html)
            row["ball_machine"] = extract_labeled_int(["Ball Machine", "Ball Machine Used"], text)
            row["ball_set"] = extract_labeled_int(["Ball Set", "Ball Set Used"], text)

            order = extract_drawn_order(text)
            for idx in range(5):
                row[f"draw_order_{idx + 1}"] = order[idx]
            row["star_order_1"] = order[5]
            row["star_order_2"] = order[6]

            has_machine = row["ball_machine"] not in INVALID
            has_set = row["ball_set"] not in INVALID
            has_order = all(row[f"draw_order_{idx}"] for idx in range(1, 6)) and row["star_order_1"] and row["star_order_2"]

            if has_machine or has_set or has_order:
                row["metadata_status"] = "ok"
                return row

            row["metadata_status"] = "parsed_no_machine_fields"
        except Exception as exc:
            row["metadata_status"] = f"error: {type(exc).__name__}: {exc}"
        finally:
            if pause_seconds > 0:
                time.sleep(pause_seconds)

    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch EuroMillions machine/set/drawn-order metadata.")
    parser.add_argument("--limit", type=int, default=250, help="Number of latest Era 3 draws to fetch. Use 0 for all Era 3 draws.")
    parser.add_argument("--pause", type=float, default=0.15, help="Pause in seconds between requests.")
    args = parser.parse_args()

    if not RAW_DRAWS.exists():
        raise SystemExit("Run python src/build_dashboard.py first so data/processed/draws.csv exists.")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    draws = pd.read_csv(RAW_DRAWS, dtype=str).sort_values("draw_date")
    draws = draws[draws["draw_date"] >= "2016-09-27"].copy()

    if args.limit and args.limit > 0:
        draws = draws.tail(args.limit)

    rows = []
    for position, draw_date in enumerate(draws["draw_date"].tolist(), start=1):
        print(f"[{position}/{len(draws)}] Fetching metadata for {draw_date}")
        rows.append(fetch_one(draw_date, pause_seconds=args.pause))

    result = pd.DataFrame(rows)
    result.to_csv(OUTPUT, index=False)

    ok_count = int((result["metadata_status"] == "ok").sum()) if not result.empty else 0
    machine_count = int(result["ball_machine"].fillna("").astype(str).str.strip().ne("").sum()) if not result.empty else 0
    set_count = int(result["ball_set"].fillna("").astype(str).str.strip().ne("").sum()) if not result.empty else 0
    order_count = int(result["draw_order_1"].fillna("").astype(str).str.strip().ne("").sum()) if not result.empty else 0

    print(f"Wrote {OUTPUT} with {len(result)} rows")
    print(f"OK rows: {ok_count}; ball_machine rows: {machine_count}; ball_set rows: {set_count}; drawn_order rows: {order_count}")


if __name__ == "__main__":
    main()
