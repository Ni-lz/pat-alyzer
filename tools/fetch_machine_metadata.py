from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DRAWS = BASE_DIR / "data" / "processed" / "draws.csv"
OUTPUT = BASE_DIR / "data" / "external" / "euromillions_machine_metadata.csv"


def slug_date(date_text: str) -> str:
    date = pd.to_datetime(date_text).date()
    return date.strftime("%d-%m-%Y")


def extract_int_after(label: str, text: str) -> str:
    patterns = [
        rf"{label}\s*(?:Used)?\s*[:#-]?\s*(\d+)",
        rf"{label.lower()}\s*(?:used)?\s*[:#-]?\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def extract_drawn_order(text: str) -> list[str]:
    # Best-effort fallback. Many result pages render drawn order in HTML/JS differently.
    # Keep blanks rather than guessing when the page cannot be parsed reliably.
    candidates = re.findall(r"\b(?:drawn order|display balls in drawn order)\b(.{0,500})", text, flags=re.I | re.S)
    if not candidates:
        return [""] * 7
    nums = re.findall(r"\b(?:[1-9]|[1-4]\d|50)\b", candidates[0])
    nums = nums[:7]
    return nums + [""] * (7 - len(nums))


def fetch_one(draw_date: str) -> dict:
    url = f"https://www.lottery.co.uk/euromillions/results-{slug_date(draw_date)}"
    row = {
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
        "source_url": url,
        "metadata_status": "not_fetched",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": "Pat-alyzer metadata research"})
        if response.status_code != 200:
            row["metadata_status"] = f"http_{response.status_code}"
            return row
        text = response.text
        row["ball_machine"] = extract_int_after("Ball Machine", text)
        row["ball_set"] = extract_int_after("Ball Set", text)
        order = extract_drawn_order(text)
        for idx in range(5):
            row[f"draw_order_{idx + 1}"] = order[idx]
        row["star_order_1"] = order[5]
        row["star_order_2"] = order[6]
        row["metadata_status"] = "ok" if row["ball_machine"] or row["ball_set"] else "parsed_no_machine_fields"
    except Exception as exc:
        row["metadata_status"] = f"error: {exc}"
    return row


def main(limit: int | None = 250) -> None:
    if not RAW_DRAWS.exists():
        raise SystemExit("Run python src/build_dashboard.py first so data/processed/draws.csv exists.")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    draws = pd.read_csv(RAW_DRAWS, dtype=str).sort_values("draw_date")
    draws = draws[draws["draw_date"] >= "2016-09-27"].tail(limit) if limit else draws
    rows = [fetch_one(date) for date in draws["draw_date"].tolist()]
    pd.DataFrame(rows).to_csv(OUTPUT, index=False)
    print(f"Wrote {OUTPUT} with {len(rows)} rows")


if __name__ == "__main__":
    main()
