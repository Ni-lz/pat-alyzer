from __future__ import annotations

import json
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests


BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
PROCESSED_DIR = BASE_DIR / "data" / "processed"
DOCS_DIR = BASE_DIR / "docs"

START_YEAR = 2004
CURRENT_YEAR = datetime.now(timezone.utc).year

GAME_URL_TEMPLATE = (
    "https://prdlnboppreportsst.blob.core.windows.net/"
    "legal-reports/euromillions-gamedata-NL-{year}.csv"
)

FINANCIAL_URL_TEMPLATE = (
    "https://prdlnboppreportsst.blob.core.windows.net/"
    "legal-reports/euromillions-financialdata-NL-{year}.csv"
)


def ensure_folders() -> None:
    for folder in [
        RAW_DIR,
        RAW_DIR / "gamedata",
        RAW_DIR / "financialdata",
        PROCESSED_DIR,
        DOCS_DIR,
    ]:
        folder.mkdir(parents=True, exist_ok=True)


def download_file(url: str, destination: Path) -> bool:
    response = requests.get(url, timeout=30)

    if response.status_code == 404:
        print(f"Not found: {url}")
        return False

    response.raise_for_status()
    destination.write_bytes(response.content)
    print(f"Downloaded: {destination}")
    return True


def fetch_official_csvs() -> None:
    for year in range(START_YEAR, CURRENT_YEAR + 1):
        download_file(
            GAME_URL_TEMPLATE.format(year=year),
            RAW_DIR / "gamedata" / f"euromillions-gamedata-NL-{year}.csv",
        )

        download_file(
            FINANCIAL_URL_TEMPLATE.format(year=year),
            RAW_DIR / "financialdata" / f"euromillions-financialdata-NL-{year}.csv",
        )


def read_csv_flexible(path: Path) -> pd.DataFrame:
    for encoding in ["utf-8-sig", "latin1"]:
        for separator in [";", ","]:
            try:
                df = pd.read_csv(path, sep=separator, dtype=str, encoding=encoding)
                if df.shape[1] > 1:
                    return df
            except Exception:
                continue

    raise ValueError(f"Could not parse CSV: {path}")


def normalize_date(value: object) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    # Official/source-safe ISO date: 2026-05-12
    if pd.Series([text]).str.match(r"^\d{4}-\d{2}-\d{2}$").iloc[0]:
        parsed = pd.to_datetime(text, format="%Y-%m-%d", errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.strftime("%Y-%m-%d")

    # ISO datetime: 2026-05-12 00:00:00.000
    if pd.Series([text]).str.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}").iloc[0]:
        parsed = pd.to_datetime(text, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.strftime("%Y-%m-%d")

    # Belgian date format: 12/05/2026 or 12-05-2026
    parsed = pd.to_datetime(text, dayfirst=True, errors="coerce")

    if pd.isna(parsed):
        return None

    return parsed.strftime("%Y-%m-%d")


def extract_date_from_row(row: pd.Series) -> str | None:
    for value in row.values:
        normalized = normalize_date(value)
        if normalized:
            return normalized

    return None


def extract_numbers_from_row(row: pd.Series) -> list[int]:
    numbers: list[int] = []

    for value in row.values:
        text = str(value).strip()

        if text.isdigit():
            number = int(text)

            if 1 <= number <= 50:
                numbers.append(number)

    return numbers


def is_valid_draw(main_numbers: list[int], stars: list[int]) -> bool:
    return (
        len(main_numbers) == 5
        and len(stars) == 2
        and all(1 <= number <= 50 for number in main_numbers)
        and all(1 <= star <= 12 for star in stars)
        and len(set(main_numbers)) == 5
        and len(set(stars)) == 2
    )


def normalize_gamedata() -> pd.DataFrame:
    rows: list[dict] = []
    today = datetime.now(timezone.utc).date()

    for file in sorted((RAW_DIR / "gamedata").glob("*.csv")):
        source_year = int(file.stem.split("-")[-1])
        df = read_csv_flexible(file)

        for _, row in df.iterrows():
            draw_date = extract_date_from_row(row)
            numbers = extract_numbers_from_row(row)

            if not draw_date or len(numbers) < 7:
                continue

            parsed_draw_date = pd.to_datetime(draw_date, errors="coerce")

            if pd.isna(parsed_draw_date):
                continue

            if parsed_draw_date.date() > today:
                continue

            main_numbers = sorted(numbers[:5])
            stars = sorted(numbers[5:7])

            if not is_valid_draw(main_numbers, stars):
                continue

            rows.append(
                {
                    "draw_date": draw_date,
                    "n1": main_numbers[0],
                    "n2": main_numbers[1],
                    "n3": main_numbers[2],
                    "n4": main_numbers[3],
                    "n5": main_numbers[4],
                    "s1": stars[0],
                    "s2": stars[1],
                    "source_year": source_year,
                }
            )

    draws = pd.DataFrame(rows)

    if draws.empty:
        return draws

    draws = draws.drop_duplicates(subset=["draw_date"])
    draws = draws.sort_values("draw_date").reset_index(drop=True)
    draws.to_csv(PROCESSED_DIR / "draws.csv", index=False)

    return draws


def zone_for_number(number: int) -> int:
    if 1 <= number <= 10:
        return 1
    if 11 <= number <= 20:
        return 2
    if 21 <= number <= 30:
        return 3
    if 31 <= number <= 40:
        return 4
    if 41 <= number <= 50:
        return 5

    raise ValueError(f"Invalid EuroMillions number: {number}")


def zone_signature(numbers: list[int]) -> str:
    counts = Counter(zone_for_number(number) for number in numbers)
    return "-".join(str(counts.get(zone, 0)) for zone in range(1, 6))


def add_pattern_columns(draws: pd.DataFrame) -> pd.DataFrame:
    enriched = draws.copy()
    main_cols = ["n1", "n2", "n3", "n4", "n5"]

    enriched["main_numbers"] = enriched[main_cols].values.tolist()
    enriched["zone_signature"] = enriched["main_numbers"].apply(zone_signature)
    enriched["sum"] = enriched[main_cols].sum(axis=1)
    enriched["odd_count"] = enriched[main_cols].apply(
        lambda row: sum(number % 2 for number in row),
        axis=1,
    )
    enriched["even_count"] = 5 - enriched["odd_count"]
    enriched["low_count"] = enriched[main_cols].apply(
        lambda row: sum(number <= 25 for number in row),
        axis=1,
    )
    enriched["high_count"] = 5 - enriched["low_count"]

    enriched.to_csv(PROCESSED_DIR / "draws_enriched.csv", index=False)

    return enriched


def analyze_missing_zone_patterns(
    enriched: pd.DataFrame,
    recent_window: int = 50,
) -> pd.DataFrame:
    historical = enriched["zone_signature"].value_counts(normalize=True)
    recent = enriched.tail(recent_window)["zone_signature"].value_counts(normalize=True)

    signatures = sorted(set(historical.index).union(set(recent.index)))
    rows = []

    for signature in signatures:
        historical_rate = float(historical.get(signature, 0))
        recent_rate = float(recent.get(signature, 0))
        missing_score = historical_rate - recent_rate

        rows.append(
            {
                "zone_signature": signature,
                "historical_rate": round(historical_rate, 4),
                "recent_rate": round(recent_rate, 4),
                "missing_score": round(missing_score, 4),
            }
        )

    result = pd.DataFrame(rows).sort_values("missing_score", ascending=False)
    result.to_csv(PROCESSED_DIR / "missing_zone_patterns.csv", index=False)

    return result


def get_hybrid_target_signatures(
    enriched: pd.DataFrame,
    recent_window: int = 50,
    top_n: int = 10,
    common_weight: float = 0.60,
    missing_weight: float = 0.40,
) -> pd.DataFrame:
    historical_rates = enriched["zone_signature"].value_counts(normalize=True)
    recent_rates = enriched.tail(recent_window)["zone_signature"].value_counts(normalize=True)

    signatures = sorted(set(historical_rates.index).union(set(recent_rates.index)))

    rows = []

    for signature in signatures:
        historical_rate = float(historical_rates.get(signature, 0))
        recent_rate = float(recent_rates.get(signature, 0))
        missing_score = max(0.0, historical_rate - recent_rate)

        hybrid_score = (
            common_weight * historical_rate
            + missing_weight * missing_score
        )

        rows.append(
            {
                "zone_signature": signature,
                "historical_rate": round(historical_rate, 4),
                "recent_rate": round(recent_rate, 4),
                "missing_score": round(missing_score, 4),
                "hybrid_score": round(hybrid_score, 6),
            }
        )

    result = pd.DataFrame(rows).sort_values("hybrid_score", ascending=False)
    result.to_csv(PROCESSED_DIR / "hybrid_zone_patterns.csv", index=False)

    return result.head(top_n)

def generate_candidate_ticket(target_signatures: list[str]) -> dict:
    for _ in range(20000):
        main_numbers = sorted(random.sample(range(1, 51), 5))
        stars = sorted(random.sample(range(1, 13), 2))

        signature = zone_signature(main_numbers)
        odd_count = sum(number % 2 for number in main_numbers)
        low_count = sum(number <= 25 for number in main_numbers)
        total_sum = sum(main_numbers)

        if signature not in target_signatures:
            continue

        if odd_count not in [2, 3]:
            continue

        if low_count not in [2, 3]:
            continue

        if not 90 <= total_sum <= 170:
            continue

        anti_crowd_score = sum(1 for number in main_numbers if number > 31) * 10

        return {
            "numbers": ", ".join(str(number) for number in main_numbers),
            "stars": ", ".join(str(star) for star in stars),
            "zone_signature": signature,
            "odd_count": odd_count,
            "even_count": 5 - odd_count,
            "low_count": low_count,
            "high_count": 5 - low_count,
            "sum": total_sum,
            "anti_crowd_score": anti_crowd_score,
        }

    raise RuntimeError("Could not generate a candidate ticket with current constraints.")


def generate_tickets(missing_patterns: pd.DataFrame, amount: int = 10) -> pd.DataFrame:
    target_signatures = missing_patterns.head(10)["zone_signature"].tolist()

    tickets = []
    seen = set()

    while len(tickets) < amount:
        ticket = generate_candidate_ticket(target_signatures)
        key = ticket["numbers"] + " + " + ticket["stars"]

        if key in seen:
            continue

        seen.add(key)
        ticket["strategy_score"] = (
            50
            + ticket["anti_crowd_score"]
            + 10
            + 10
        )
        tickets.append(ticket)

    df = pd.DataFrame(tickets)
    df.to_csv(PROCESSED_DIR / "candidate_tickets.csv", index=False)

    return df


def backtest_missing_pattern_strategy(
    enriched: pd.DataFrame,
    training_window: int = 200,
    recent_window: int = 50,
    top_n: int = 10,
) -> pd.DataFrame:
    rows = []

    if len(enriched) <= training_window + 1:
        return pd.DataFrame()

    hybrid_weights = {
        "Hybrid 70 common / 30 missing": (0.70, 0.30),
        "Hybrid 60 common / 40 missing": (0.60, 0.40),
        "Hybrid 50 common / 50 missing": (0.50, 0.50),
    }

    for index in range(training_window, len(enriched)):
        history = enriched.iloc[:index]
        actual = enriched.iloc[index]

        historical_rates = history["zone_signature"].value_counts(normalize=True)
        recent_rates = history.tail(recent_window)["zone_signature"].value_counts(normalize=True)

        signatures = sorted(set(historical_rates.index).union(set(recent_rates.index)))

        score_rows = []

        for signature in signatures:
            historical_rate = float(historical_rates.get(signature, 0))
            recent_rate = float(recent_rates.get(signature, 0))
            missing_score = max(0.0, historical_rate - recent_rate)

            score_row = {
                "zone_signature": signature,
                "historical_rate": historical_rate,
                "recent_rate": recent_rate,
                "missing_score": missing_score,
            }

            for hybrid_name, (common_weight, missing_weight) in hybrid_weights.items():
                score_row[hybrid_name] = (
                    common_weight * historical_rate
                    + missing_weight * missing_score
                )

            score_rows.append(score_row)

        scores_df = pd.DataFrame(score_rows)

        predicted_missing_signatures = (
            scores_df.sort_values("missing_score", ascending=False)
            .head(top_n)["zone_signature"]
            .tolist()
        )

        common_signatures = (
            scores_df.sort_values("historical_rate", ascending=False)
            .head(top_n)["zone_signature"]
            .tolist()
        )

        hybrid_predictions = {}

        for hybrid_name in hybrid_weights:
            hybrid_predictions[hybrid_name] = (
                scores_df.sort_values(hybrid_name, ascending=False)
                .head(top_n)["zone_signature"]
                .tolist()
            )

        actual_signature = actual["zone_signature"]

        unique_signature_count = max(1, history["zone_signature"].nunique())
        random_baseline_probability = min(1.0, top_n / unique_signature_count)

        result_row = {
            "draw_date": actual["draw_date"],
            "actual_zone_signature": actual_signature,
            "missing_strategy_hit": actual_signature in predicted_missing_signatures,
            "common_strategy_hit": actual_signature in common_signatures,
            "random_signature_baseline": random_baseline_probability,
        }

        for hybrid_name, predictions in hybrid_predictions.items():
            result_row[hybrid_name] = actual_signature in predictions

        rows.append(result_row)

    result = pd.DataFrame(rows)
    result.to_csv(PROCESSED_DIR / "backtest_results.csv", index=False)

    summary_rows = [
        {
            "test_name": "Missing-pattern top zone signatures",
            "draws_tested": len(result),
            "hit_rate": round(float(result["missing_strategy_hit"].mean()), 4),
        },
        {
            "test_name": "Most-common top zone signatures",
            "draws_tested": len(result),
            "hit_rate": round(float(result["common_strategy_hit"].mean()), 4),
        },
    ]

    for hybrid_name in hybrid_weights:
        summary_rows.append(
            {
                "test_name": hybrid_name,
                "draws_tested": len(result),
                "hit_rate": round(float(result[hybrid_name].mean()), 4),
            }
        )

    summary_rows.append(
        {
            "test_name": "Random signature baseline estimate",
            "draws_tested": len(result),
            "hit_rate": round(float(result["random_signature_baseline"].mean()), 4),
        }
    )

    summary = pd.DataFrame(summary_rows)
    summary = summary.sort_values("hit_rate", ascending=False).reset_index(drop=True)

    summary.to_csv(PROCESSED_DIR / "backtest_summary.csv", index=False)

    return summary

def generate_html_dashboard(
    enriched: pd.DataFrame,
    missing_patterns: pd.DataFrame,
    tickets: pd.DataFrame,
    backtest_summary: pd.DataFrame,
    hybrid_patterns: pd.DataFrame,
) -> None:
    latest = enriched.tail(1).iloc[0]
    top_patterns = missing_patterns.head(10)

    hot_numbers = (
        pd.Series(enriched[["n1", "n2", "n3", "n4", "n5"]].values.flatten())
        .value_counts()
        .head(10)
        .rename_axis("number")
        .reset_index(name="times_drawn")
    )

    hot_stars = (
        pd.Series(enriched[["s1", "s2"]].values.flatten())
        .value_counts()
        .head(5)
        .rename_axis("star")
        .reset_index(name="times_drawn")
    )

    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Pat-alyzer - EuroMillions Pattern Analyzer</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 40px; background: #f7f7f7; color: #222; }}
    .card {{ background: white; padding: 20px; margin-bottom: 20px; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,0.1); }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ padding: 8px 10px; border-bottom: 1px solid #ddd; text-align: left; }}
    th {{ background: #eee; }}
    .warning {{ color: #8a4b00; font-weight: bold; }}
    code {{ background: #eee; padding: 2px 4px; }}
  </style>
</head>
<body>
  <h1>Pat-alyzer</h1>
  <h2>EuroMillions Pattern Analyzer</h2>

  <div class="card">
    <h2>Important</h2>
    <p class="warning">
      This system analyzes historical structures and generates strategy-based candidate tickets.
      It does not guarantee or truly predict winning numbers.
    </p>
  </div>

  <div class="card">
    <h2>Latest draw in dataset</h2>
    <p><b>Date:</b> {latest["draw_date"]}</p>
    <p><b>Numbers:</b> {latest["n1"]}, {latest["n2"]}, {latest["n3"]}, {latest["n4"]}, {latest["n5"]} + {latest["s1"]}, {latest["s2"]}</p>
    <p><b>Rainbow / zone signature:</b> <code>{latest["zone_signature"]}</code></p>
    <p><b>Total draws analyzed:</b> {len(enriched)}</p>
    <p><b>Updated:</b> {updated_at}</p>
  </div>

  <div class="card">
    <h2>Missing / underrepresented rainbow-zone patterns</h2>
    {top_patterns.to_html(index=False)}
  </div>

  <div class="card">
    <h2>Generated candidate tickets</h2>
    {tickets.to_html(index=False)}
  </div>

  <div class="card">
    <h2>Backtest summary</h2>
    <p>
      This checks whether the next historical draw matched one of the top predicted rainbow-zone signatures.
      It validates the structure strategy, not jackpot-winning ticket accuracy.
    </p>
    {backtest_summary.to_html(index=False)}
  </div>

  <div class="card">
    <h2>Hot main numbers</h2>
    {hot_numbers.to_html(index=False)}
  </div>

  <div class="card">
    <h2>Hot stars</h2>
    {hot_stars.to_html(index=False)}
  </div>
</body>
</html>
"""

    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")


def main() -> None:
    ensure_folders()
    fetch_official_csvs()

    draws = normalize_gamedata()

    if draws.empty:
        raise RuntimeError("No draw data was normalized. CSV parsing needs adjustment.")

    enriched = add_pattern_columns(draws)
    missing_patterns = analyze_missing_zone_patterns(enriched)
    hybrid_patterns = get_hybrid_target_signatures(enriched)
    tickets = generate_tickets(hybrid_patterns)

    backtest_summary = backtest_missing_pattern_strategy(enriched)

    generate_html_dashboard(enriched, missing_patterns, tickets, backtest_summary, hybrid_patterns)

    summary = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "draw_count": int(len(enriched)),
        "latest_draw_date": str(enriched.tail(1).iloc[0]["draw_date"]),
    }

    (DOCS_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    print("Build completed.")
    print(summary)


if __name__ == "__main__":
    main()









