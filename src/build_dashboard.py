from __future__ import annotations

import html
import itertools
import json
import math
import random
from collections import Counter, defaultdict
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

MAIN_COLS = ["n1", "n2", "n3", "n4", "n5"]
STAR_COLS = ["s1", "s2"]


def classify_euromillions_era(draw_date: str) -> dict:
    date = pd.to_datetime(draw_date).date()

    if date <= pd.to_datetime("2011-05-06").date():
        return {
            "era": "Era 1: 2004-2011",
            "era_code": "era_1",
            "main_pool": 50,
            "star_pool": 9,
            "era_weight": 0.25,
        }

    if date <= pd.to_datetime("2016-09-23").date():
        return {
            "era": "Era 2: 2011-2016",
            "era_code": "era_2",
            "main_pool": 50,
            "star_pool": 11,
            "era_weight": 0.50,
        }

    return {
        "era": "Era 3: 2016-current",
        "era_code": "era_3",
        "main_pool": 50,
        "star_pool": 12,
        "era_weight": 1.00,
    }


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
    try:
        response = requests.get(url, timeout=30)
    except requests.RequestException as exc:
        print(f"Download failed: {url} ({exc})")
        return False

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

    # ISO date or ISO timestamp from official source.
    if pd.Series([text]).str.match(r"^\d{4}-\d{2}-\d{2}$").iloc[0]:
        parsed = pd.to_datetime(text, format="%Y-%m-%d", errors="coerce")
    elif pd.Series([text]).str.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}").iloc[0]:
        parsed = pd.to_datetime(text, errors="coerce")
    else:
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
            if pd.isna(parsed_draw_date) or parsed_draw_date.date() > today:
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


def normalize_financialdata() -> tuple[pd.DataFrame, pd.DataFrame]:
    raw_rows: list[dict] = []
    summary_rows: list[dict] = []

    for file in sorted((RAW_DIR / "financialdata").glob("*.csv")):
        source_year = int(file.stem.split("-")[-1])
        try:
            df = read_csv_flexible(file)
        except Exception as exc:
            summary_rows.append(
                {"source_year": source_year, "rows": 0, "columns": 0, "source_file": file.name, "status": str(exc)}
            )
            continue

        summary_rows.append(
            {"source_year": source_year, "rows": len(df), "columns": len(df.columns), "source_file": file.name, "status": "ok"}
        )
        for _, row in df.iterrows():
            item = {"source_year": source_year, "source_file": file.name}
            item.update({str(k): v for k, v in row.to_dict().items()})
            raw_rows.append(item)

    raw = pd.DataFrame(raw_rows)
    summary = pd.DataFrame(summary_rows)
    raw.to_csv(PROCESSED_DIR / "financial_raw.csv", index=False)
    summary.to_csv(PROCESSED_DIR / "financial_summary.csv", index=False)
    return raw, summary


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


def star_zone(star: int) -> int:
    if 1 <= star <= 4:
        return 1
    if 5 <= star <= 8:
        return 2
    if 9 <= star <= 12:
        return 3
    raise ValueError(f"Invalid EuroMillions star: {star}")


def star_zone_signature(stars: list[int]) -> str:
    counts = Counter(star_zone(star) for star in stars)
    return "-".join(str(counts.get(zone, 0)) for zone in range(1, 4))


def add_pattern_columns(draws: pd.DataFrame) -> pd.DataFrame:
    enriched = draws.copy()

    era_data = enriched["draw_date"].apply(classify_euromillions_era)
    enriched["era"] = era_data.apply(lambda item: item["era"])
    enriched["era_code"] = era_data.apply(lambda item: item["era_code"])
    enriched["main_pool"] = era_data.apply(lambda item: item["main_pool"])
    enriched["star_pool"] = era_data.apply(lambda item: item["star_pool"])
    enriched["era_weight"] = era_data.apply(lambda item: item["era_weight"])

    enriched[MAIN_COLS] = enriched[MAIN_COLS].astype(int)
    enriched[STAR_COLS] = enriched[STAR_COLS].astype(int)
    enriched["main_numbers"] = enriched[MAIN_COLS].values.tolist()
    enriched["star_numbers"] = enriched[STAR_COLS].values.tolist()
    enriched["zone_signature"] = enriched["main_numbers"].apply(zone_signature)
    enriched["star_zone_signature"] = enriched["star_numbers"].apply(star_zone_signature)
    enriched["sum"] = enriched[MAIN_COLS].sum(axis=1)
    enriched["odd_count"] = enriched[MAIN_COLS].apply(lambda row: sum(number % 2 for number in row), axis=1)
    enriched["even_count"] = 5 - enriched["odd_count"]
    enriched["low_count"] = enriched[MAIN_COLS].apply(lambda row: sum(number <= 25 for number in row), axis=1)
    enriched["high_count"] = 5 - enriched["low_count"]
    enriched.to_csv(PROCESSED_DIR / "draws_enriched.csv", index=False)
    return enriched


def current_era_draws(enriched: pd.DataFrame) -> pd.DataFrame:
    return enriched[enriched["era_code"] == "era_3"].copy()


def load_machine_metadata() -> pd.DataFrame:
    metadata_file = BASE_DIR / "data" / "external" / "euromillions_machine_metadata.csv"

    if not metadata_file.exists():
        return pd.DataFrame()

    metadata = pd.read_csv(metadata_file, dtype=str)

    if metadata.empty or "draw_date" not in metadata.columns:
        return pd.DataFrame()

    metadata["draw_date"] = metadata["draw_date"].apply(normalize_date)
    metadata = metadata.dropna(subset=["draw_date"])

    return metadata


def analyze_eras(enriched: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for era, group in enriched.groupby("era"):
        rows.append(
            {
                "era": era,
                "draw_count": len(group),
                "date_from": group["draw_date"].min(),
                "date_to": group["draw_date"].max(),
                "star_pool": int(group["star_pool"].max()),
                "most_common_zone": group["zone_signature"].value_counts().idxmax(),
                "avg_sum": round(float(group["sum"].mean()), 2),
            }
        )

    result = pd.DataFrame(rows).sort_values("date_from")
    result.to_csv(PROCESSED_DIR / "era_summary.csv", index=False)

    return result


def analyze_machine_metadata(enriched: pd.DataFrame) -> pd.DataFrame:
    if "ball_machine" not in enriched.columns:
        result = pd.DataFrame(
            [
                {
                    "status": "No machine metadata loaded yet",
                    "note": "Add data/external/euromillions_machine_metadata.csv to enable machine analysis.",
                }
            ]
        )
        result.to_csv(PROCESSED_DIR / "machine_metadata_summary.csv", index=False)
        return result

    machine_data = enriched[
        enriched["ball_machine"].astype(str).str.strip() != ""
    ].copy()

    if machine_data.empty:
        result = pd.DataFrame(
            [
                {
                    "status": "No machine metadata loaded yet",
                    "note": "Add data/external/euromillions_machine_metadata.csv to enable machine analysis.",
                }
            ]
        )
        result.to_csv(PROCESSED_DIR / "machine_metadata_summary.csv", index=False)
        return result

    rows = []

    for machine, group in machine_data.groupby("ball_machine"):
        rows.append(
            {
                "ball_machine": machine,
                "draw_count": len(group),
                "sample_warning": "OK" if len(group) >= 30 else "LOW SAMPLE",
                "avg_main_sum": round(float(group["sum"].mean()), 2),
                "avg_low_count": round(float(group["low_count"].mean()), 2),
                "avg_high_count": round(float(group["high_count"].mean()), 2),
                "most_common_zone": group["zone_signature"].value_counts().idxmax(),
            }
        )

    result = pd.DataFrame(rows).sort_values("draw_count", ascending=False)
    result.to_csv(PROCESSED_DIR / "machine_metadata_summary.csv", index=False)

    return result


def analyze_missing_zone_patterns(enriched: pd.DataFrame, recent_window: int = 50) -> pd.DataFrame:
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
        hybrid_score = common_weight * historical_rate + missing_weight * missing_score
        rows.append(
            {
                "zone_signature": signature,
                "historical_rate": round(historical_rate, 4),
                "recent_rate": round(recent_rate, 4),
                "missing_score": round(missing_score, 4),
                "hybrid_score": round(hybrid_score, 6),
            }
        )
    result = pd.DataFrame(rows).sort_values("hybrid_score", ascending=False).reset_index(drop=True)
    result.to_csv(PROCESSED_DIR / "hybrid_zone_patterns.csv", index=False)
    return result.head(top_n)


def all_pairs(numbers: list[int]) -> list[tuple[int, int]]:
    return [tuple(sorted(pair)) for pair in itertools.combinations(numbers, 2)]


def all_triplets(numbers: list[int]) -> list[tuple[int, int, int]]:
    return [tuple(sorted(triplet)) for triplet in itertools.combinations(numbers, 3)]


def build_context(enriched: pd.DataFrame) -> dict:
    main_lists = [list(map(int, row)) for row in enriched[MAIN_COLS].values.tolist()]
    star_lists = [list(map(int, row)) for row in enriched[STAR_COLS].values.tolist()]

    pair_counts = Counter()
    triplet_counts = Counter()
    exact_draws = set()
    number_counts = Counter()
    star_zone_counts = Counter()
    last_seen_index = {number: -1 for number in range(1, 51)}

    for index, numbers in enumerate(main_lists):
        numbers = sorted(numbers)
        exact_draws.add(tuple(numbers))
        number_counts.update(numbers)
        pair_counts.update(all_pairs(numbers))
        triplet_counts.update(all_triplets(numbers))
        for number in numbers:
            last_seen_index[number] = index

    for stars in star_lists:
        star_zone_counts[star_zone_signature(sorted(stars))] += 1

    current_index = len(enriched) - 1
    gaps = {number: current_index - last_seen_index[number] if last_seen_index[number] >= 0 else current_index + 1 for number in range(1, 51)}

    return {
        "pair_counts": pair_counts,
        "triplet_counts": triplet_counts,
        "exact_draws": exact_draws,
        "number_counts": number_counts,
        "star_zone_counts": star_zone_counts,
        "gaps": gaps,
        "draw_count": len(enriched),
    }


def band_score(value: float, ideal_min: float, ideal_max: float, hard_min: float = 0, hard_max: float = 100) -> int:
    if ideal_min <= value <= ideal_max:
        return 100
    distance = ideal_min - value if value < ideal_min else value - ideal_max
    spread = max(ideal_max - ideal_min, 1)
    score = max(hard_min, 100 - (distance / spread) * 35)
    return int(round(min(hard_max, score)))


def build_wheeling_pool(ctx: dict, pool_size: int = 22) -> list[int]:
    hot = [number for number, _ in ctx["number_counts"].most_common(14)]
    overdue = sorted(ctx["gaps"], key=lambda number: ctx["gaps"][number], reverse=True)[:14]
    combined = []
    for number in overdue + hot:
        if number not in combined:
            combined.append(number)
    # Ensure every rainbow zone has enough candidates.
    for zone_start in [1, 11, 21, 31, 41]:
        zone_numbers = [n for n in range(zone_start, zone_start + 10) if n not in combined]
        combined.extend(zone_numbers[:2])
    return sorted(combined[:pool_size])


def make_ticket_from_signature(signature: str, rng: random.Random, pool: list[int]) -> list[int] | None:
    counts = [int(part) for part in signature.split("-")]
    selected: list[int] = []
    for zone_index, count in enumerate(counts, start=1):
        zone_numbers = [n for n in pool if zone_for_number(n) == zone_index]
        if len(zone_numbers) < count:
            zone_numbers = list(range((zone_index - 1) * 10 + 1, zone_index * 10 + 1))
        if len(zone_numbers) < count:
            return None
        selected.extend(rng.sample(zone_numbers, count))
    if len(set(selected)) != 5:
        return None
    return sorted(selected)


def score_ticket(nums: list[int], stars: list[int], hybrid_lookup: dict, ctx: dict) -> dict:
    nums = sorted(nums)
    stars = sorted(stars)
    sig = zone_signature(nums)
    hybrid_row = hybrid_lookup.get(sig, {})
    hybrid_score = min(100, int(round(float(hybrid_row.get("hybrid_score", 0)) * 2500)))

    odd = sum(number % 2 for number in nums)
    low = sum(number <= 25 for number in nums)
    total_sum = sum(nums)
    odd_even_score = 100 if odd in [2, 3] else 70 if odd in [1, 4] else 35
    low_high_score = 100 if low in [2, 3] else 70 if low in [1, 4] else 35
    sum_score = band_score(total_sum, 95, 165)
    pair_score = band_score(sum(ctx["pair_counts"].get(pair, 0) for pair in all_pairs(nums)) / 10, 2, 10)
    triplet_score = 100 - min(60, sum(ctx["triplet_counts"].get(triplet, 0) for triplet in all_triplets(nums)) * 15)
    avg_gap = sum(ctx["gaps"].get(number, 0) for number in nums) / len(nums)
    gap_score = band_score(avg_gap, 12, 35)
    star_sig = star_zone_signature(stars)
    star_pattern_score = band_score(ctx["star_zone_counts"].get(star_sig, 0), 60, 400)
    anti_crowd_score = min(100, 35 + sum(1 for number in nums if number > 31) * 15)
    duplicate_penalty = 100 if tuple(nums) in ctx["exact_draws"] else 0

    final = int(
        round(
            0.20 * hybrid_score
            + 0.10 * odd_even_score
            + 0.10 * low_high_score
            + 0.10 * sum_score
            + 0.10 * pair_score
            + 0.08 * triplet_score
            + 0.12 * gap_score
            + 0.10 * star_pattern_score
            + 0.10 * anti_crowd_score
            - 0.25 * duplicate_penalty
        )
    )

    why = [
        f"Hybrid 60/40 zone {sig}",
        f"Odd/even {odd}/{5 - odd}",
        f"Low/high {low}/{5 - low}",
        f"Sum {total_sum}",
        f"Avg gap {avg_gap:.1f}",
        f"Star zone {star_sig}",
        "No exact historical duplicate" if duplicate_penalty == 0 else "Exact historical duplicate penalty",
    ]

    return {
        "numbers": ", ".join(map(str, nums)),
        "stars": ", ".join(map(str, stars)),
        "zone_signature": sig,
        "hybrid_score": hybrid_score,
        "odd_even_score": odd_even_score,
        "low_high_score": low_high_score,
        "sum_score": sum_score,
        "pair_score": pair_score,
        "triplet_score": triplet_score,
        "gap_overdue_score": gap_score,
        "star_pattern_score": star_pattern_score,
        "anti_crowd_score": anti_crowd_score,
        "duplicate_penalty": duplicate_penalty,
        "final_strategy_score": max(0, min(100, final)),
        "why_selected": " | ".join(why),
    }


def generate_tickets(
    enriched: pd.DataFrame,
    hybrid: pd.DataFrame,
    amount: int = 10,
    sample_size: int = 2500,
    seed: str | int | None = None,
    strategy: str = "v2_scored",
) -> pd.DataFrame:
    rng = random.Random(str(seed or "pat-alyzer"))
    ctx = build_context(enriched)
    hybrid_lookup = hybrid.set_index("zone_signature").to_dict("index")
    targets = hybrid["zone_signature"].tolist()
    pool = build_wheeling_pool(ctx)
    scored_tickets: list[dict] = []
    seen: set[str] = set()

    for index in range(sample_size):
        target = targets[index % max(1, min(len(targets), 5))]
        if strategy == "random":
            nums = sorted(rng.sample(range(1, 51), 5))
        else:
            nums = make_ticket_from_signature(target, rng, pool)
            if nums is None:
                continue
        stars = sorted(rng.sample(range(1, 13), 2))
        key = f"{nums}|{stars}"
        if key in seen:
            continue
        seen.add(key)
        scored = score_ticket(nums, stars, hybrid_lookup, ctx)
        if strategy == "hybrid_zone_only":
            scored["final_strategy_score"] = scored["hybrid_score"]
        elif strategy == "random":
            scored["final_strategy_score"] = 0
        if scored["duplicate_penalty"] >= 100:
            continue
        scored_tickets.append(scored)

    df = pd.DataFrame(scored_tickets)
    if df.empty:
        return df
    df = df.sort_values(["final_strategy_score", "gap_overdue_score", "pair_score"], ascending=False).head(amount)
    if strategy == "v2_scored":
        df.to_csv(PROCESSED_DIR / "candidate_tickets.csv", index=False)
    return df.reset_index(drop=True)


def common_zone_patterns(enriched: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    rates = enriched["zone_signature"].value_counts(normalize=True).head(top_n)
    return pd.DataFrame(
        {
            "zone_signature": rates.index,
            "historical_rate": rates.values,
            "recent_rate": 0,
            "missing_score": 0,
            "hybrid_score": rates.values,
        }
    )


def backtest_zone_strategy(
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
                score_row[hybrid_name] = common_weight * historical_rate + missing_weight * missing_score
            score_rows.append(score_row)
        scores_df = pd.DataFrame(score_rows)
        actual_signature = actual["zone_signature"]
        unique_signature_count = max(1, history["zone_signature"].nunique())
        result_row = {
            "draw_date": actual["draw_date"],
            "actual_zone_signature": actual_signature,
            "Missing-pattern top zone signatures": actual_signature in scores_df.sort_values("missing_score", ascending=False).head(top_n)["zone_signature"].tolist(),
            "Most-common top zone signatures": actual_signature in scores_df.sort_values("historical_rate", ascending=False).head(top_n)["zone_signature"].tolist(),
            "Random signature baseline estimate": min(1.0, top_n / unique_signature_count),
        }
        for hybrid_name in hybrid_weights:
            result_row[hybrid_name] = actual_signature in scores_df.sort_values(hybrid_name, ascending=False).head(top_n)["zone_signature"].tolist()
        rows.append(result_row)
    result = pd.DataFrame(rows)
    result.to_csv(PROCESSED_DIR / "backtest_results.csv", index=False)
    summary_rows = []
    for name in [
        "Hybrid 60 common / 40 missing",
        "Hybrid 70 common / 30 missing",
        "Most-common top zone signatures",
        "Hybrid 50 common / 50 missing",
        "Missing-pattern top zone signatures",
    ]:
        summary_rows.append({"test_name": name, "draws_tested": len(result), "hit_rate": round(float(result[name].mean()), 4)})
    summary_rows.append(
        {
            "test_name": "Random signature baseline estimate",
            "draws_tested": len(result),
            "hit_rate": round(float(result["Random signature baseline estimate"].mean()), 4),
        }
    )
    summary = pd.DataFrame(summary_rows).sort_values("hit_rate", ascending=False).reset_index(drop=True)
    summary.to_csv(PROCESSED_DIR / "backtest_summary.csv", index=False)
    return summary


def evaluate_ticket_set(tickets: pd.DataFrame, actual_nums: set[int], actual_stars: set[int]) -> dict:
    best_main = 0
    best_star = 0
    hit_2_main = False
    hit_3_main = False
    hit_2_main_1_star = False
    for _, ticket in tickets.iterrows():
        nums = {int(x.strip()) for x in str(ticket["numbers"]).split(",")}
        stars = {int(x.strip()) for x in str(ticket["stars"]).split(",")}
        main_matches = len(nums & actual_nums)
        star_matches = len(stars & actual_stars)
        best_main = max(best_main, main_matches)
        best_star = max(best_star, star_matches)
        hit_2_main = hit_2_main or main_matches >= 2
        hit_3_main = hit_3_main or main_matches >= 3
        hit_2_main_1_star = hit_2_main_1_star or (main_matches >= 2 and star_matches >= 1)
    return {
        "best_main_matches": best_main,
        "best_star_matches": best_star,
        "hit_2_main_or_better": hit_2_main,
        "hit_3_main_or_better": hit_3_main,
        "hit_2_main_plus_1_star_or_better": hit_2_main_1_star,
    }


def backtest_generated_ticket_strategies(
    enriched: pd.DataFrame,
    test_window: int = 60,
    tickets_per_draw: int = 10,
    sample_size: int = 1600,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    start = max(250, len(enriched) - test_window)
    strategies = {
        "Pat-alyzer v2 scored": "v2_scored",
        "Hybrid-zone only": "hybrid_zone_only",
        "Most-common-zone only": "common_zone_only",
        "Pure random": "random",
    }
    for index in range(start, len(enriched)):
        history = enriched.iloc[:index]
        actual = enriched.iloc[index]
        actual_nums = {int(actual[col]) for col in MAIN_COLS}
        actual_stars = {int(actual[col]) for col in STAR_COLS}
        hybrid = get_hybrid_target_signatures(history)
        common = common_zone_patterns(history)
        for display_name, strategy in strategies.items():
            pattern_source = common if strategy == "common_zone_only" else hybrid
            tickets = generate_tickets(
                history,
                pattern_source,
                amount=tickets_per_draw,
                sample_size=sample_size,
                seed=f"{actual['draw_date']}:{display_name}",
                strategy="hybrid_zone_only" if strategy == "common_zone_only" else strategy,
            )
            metrics = evaluate_ticket_set(tickets, actual_nums, actual_stars)
            rows.append({"draw_date": actual["draw_date"], "strategy": display_name, **metrics})
    results = pd.DataFrame(rows)
    results.to_csv(PROCESSED_DIR / "generated_ticket_backtest_results.csv", index=False)
    summary_rows = []
    for strategy_name, subset in results.groupby("strategy"):
        summary_rows.append(
            {
                "strategy": strategy_name,
                "draws_tested": int(subset["draw_date"].nunique()),
                "avg_best_main_matches": round(float(subset["best_main_matches"].mean()), 4),
                "avg_best_star_matches": round(float(subset["best_star_matches"].mean()), 4),
                "hit_2_main_or_better_rate": round(float(subset["hit_2_main_or_better"].mean()), 4),
                "hit_3_main_or_better_rate": round(float(subset["hit_3_main_or_better"].mean()), 4),
                "hit_2_main_plus_1_star_or_better_rate": round(float(subset["hit_2_main_plus_1_star_or_better"].mean()), 4),
            }
        )
    summary = pd.DataFrame(summary_rows).sort_values(
        ["hit_2_main_plus_1_star_or_better_rate", "hit_3_main_or_better_rate", "hit_2_main_or_better_rate"],
        ascending=False,
    )
    summary.to_csv(PROCESSED_DIR / "generated_ticket_backtest_summary.csv", index=False)
    return results, summary


def table_html(df: pd.DataFrame, classes: str = "") -> str:
    if df is None or df.empty:
        return "<p class='muted'>No data available.</p>"
    return df.to_html(index=False, classes=f"data-table {classes}", escape=True)


def metric_cards(items: list[tuple[str, str]]) -> str:
    return "".join(
        f"<div class='metric'><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>" for label, value in items
    )


def generate_html_dashboard(
    enriched: pd.DataFrame,
    missing_patterns: pd.DataFrame,
    hybrid_patterns: pd.DataFrame,
    tickets: pd.DataFrame,
    zone_backtest: pd.DataFrame,
    generated_backtest_summary: pd.DataFrame,
    financial_summary: pd.DataFrame,
    era_summary: pd.DataFrame,
    machine_summary: pd.DataFrame,
) -> None:
    latest = enriched.tail(1).iloc[0]
    hot_numbers = (
        pd.Series(enriched[MAIN_COLS].values.flatten()).value_counts().head(10).rename_axis("number").reset_index(name="times_drawn")
    )
    hot_stars = (
        pd.Series(enriched[STAR_COLS].values.flatten()).value_counts().head(5).rename_axis("star").reset_index(name="times_drawn")
    )
    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    best_generated = generated_backtest_summary.head(1)
    best_strategy = best_generated.iloc[0]["strategy"] if not best_generated.empty else "n/a"

    css = """
    :root {
      --bg: #0b1020; --panel: rgba(255,255,255,.08); --panel2: rgba(255,255,255,.12);
      --text: #eef2ff; --muted: #aeb8d7; --line: rgba(255,255,255,.14);
      --accent: #7c3aed; --accent2: #06b6d4; --good: #22c55e; --warn: #f59e0b;
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Inter, Segoe UI, Arial, sans-serif; background: radial-gradient(circle at top left, #26346f 0, transparent 32%), radial-gradient(circle at top right, #3b0764 0, transparent 32%), var(--bg); color: var(--text); }
    .shell { width: min(1400px, calc(100% - 32px)); margin: 0 auto; padding: 36px 0 60px; }
    .hero { padding: 34px; border: 1px solid var(--line); border-radius: 28px; background: linear-gradient(135deg, rgba(124,58,237,.38), rgba(6,182,212,.16)); box-shadow: 0 24px 70px rgba(0,0,0,.32); margin-bottom: 22px; }
    .hero h1 { margin: 0; font-size: clamp(34px, 5vw, 64px); letter-spacing: -.04em; }
    .hero p { color: var(--muted); max-width: 900px; line-height: 1.6; }
    .badge-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 18px; }
    .badge { padding: 8px 12px; border-radius: 999px; background: rgba(255,255,255,.12); border: 1px solid var(--line); color: #fff; font-size: 13px; }
    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 18px; }
    .card { grid-column: span 12; padding: 22px; border: 1px solid var(--line); border-radius: 22px; background: var(--panel); backdrop-filter: blur(12px); box-shadow: 0 12px 40px rgba(0,0,0,.22); overflow: hidden; }
    .half { grid-column: span 6; } .third { grid-column: span 4; }
    @media (max-width: 900px) { .half, .third { grid-column: span 12; } }
    h2 { margin: 0 0 12px; font-size: 22px; letter-spacing: -.02em; }
    .muted, .note { color: var(--muted); line-height: 1.55; }
    .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-top: 18px; }
    .metric { padding: 16px; border-radius: 18px; background: var(--panel2); border: 1px solid var(--line); }
    .metric span { display:block; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
    .metric strong { display:block; margin-top: 8px; font-size: 24px; }
    .table-wrap { overflow-x: auto; border-radius: 16px; border: 1px solid var(--line); }
    table.data-table { width: 100%; border-collapse: collapse; min-width: 850px; background: rgba(5,10,25,.55); }
    .data-table th { position: sticky; top: 0; background: rgba(20,30,60,.96); color: #dbeafe; font-size: 12px; text-transform: uppercase; letter-spacing: .06em; }
    .data-table th, .data-table td { padding: 10px 12px; border-bottom: 1px solid rgba(255,255,255,.08); text-align: left; vertical-align: top; }
    .data-table tr:hover td { background: rgba(255,255,255,.05); }
    .warning { color: #fde68a; border-left: 4px solid var(--warn); padding-left: 12px; }
    .footer { color: var(--muted); text-align: center; padding: 30px 0 0; }
    """

    hero_metrics = metric_cards(
        [
            ("Latest draw", str(latest["draw_date"])),
            ("Numbers", f"{latest['n1']}, {latest['n2']}, {latest['n3']}, {latest['n4']}, {latest['n5']} + {latest['s1']}, {latest['s2']}"),
            ("Rainbow zone", str(latest["zone_signature"])),
            ("Draws analyzed", str(len(enriched))),
            ("Best generated backtest", str(best_strategy)),
            ("Updated", updated_at),
        ]
    )

    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pat-alyzer - EuroMillions Pattern Analyzer</title>
  <style>{css}</style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <h1>Pat-alyzer</h1>
      <p>EuroMillions pattern analysis with rainbow-zone modelling, hybrid 60/40 scoring, wheeling-based ticket generation, and historical backtesting.</p>
      <p class="warning">This analyzes historical structures and creates strategy-based candidate tickets. It does not guarantee or truly predict winning numbers.</p>
      <div class="badge-row">
        <span class="badge">Hybrid 60/40</span><span class="badge">Rainbow zones</span><span class="badge">Deterministic tickets</span><span class="badge">Backtested baselines</span><span class="badge">Official CSV source</span>
      </div>
      <div class="metrics">{hero_metrics}</div>
    </section>

    <section class="grid">
      <div class="card">
        <h2>Generated candidate tickets</h2>
        <p class="note">Stable per latest draw date. Uses wheeling pool, hybrid zones, pair/triplet scoring, gap scoring, star scoring, anti-crowd scoring, and duplicate avoidance.</p>
        <div class="table-wrap">{table_html(tickets)}</div>
      </div>

      <div class="card half">
        <h2>Strategy comparison backtest</h2>
        <p class="note">Compares Pat-alyzer v2 against random, hybrid-zone-only, and most-common-zone ticket generation over recent historical draws.</p>
        <div class="table-wrap">{table_html(generated_backtest_summary)}</div>
      </div>

      <div class="card half">
        <h2>Zone-signature backtest</h2>
        <p class="note">Checks whether the next historical draw matched one of the top predicted rainbow-zone signatures.</p>
        <div class="table-wrap">{table_html(zone_backtest)}</div>
      </div>

      <div class="card half">
        <h2>Hybrid 60/40 target patterns</h2>
        <p class="note">Score = 60% historical commonness + 40% recent underrepresentation.</p>
        <div class="table-wrap">{table_html(hybrid_patterns)}</div>
      </div>

      <div class="card half">
        <h2>Missing rainbow-zone patterns</h2>
        <div class="table-wrap">{table_html(missing_patterns.head(10))}</div>
      </div>

      <div class="card half">
        <h2>Financial data import summary</h2>
        <div class="table-wrap">{table_html(financial_summary.tail(10))}</div>
      </div>

      <div class="card half">
        <h2>EuroMillions rule eras</h2>
        <p class="note">Splits historical data into rule eras so old Lucky Star formats do not distort current-era analysis.</p>
        <div class="table-wrap">{table_html(era_summary)}</div>
      </div>

      <div class="card half">
        <h2>Machine / ball-set metadata</h2>
        <p class="note">Optional post-draw analysis. Machine or ball-set claims require enough samples before being trusted.</p>
        <div class="table-wrap">{table_html(machine_summary)}</div>
      </div>

      <div class="card third">
        <h2>Hot main numbers</h2>
        <div class="table-wrap">{table_html(hot_numbers)}</div>
      </div>

      <div class="card third">
        <h2>Hot stars</h2>
        <div class="table-wrap">{table_html(hot_stars)}</div>
      </div>

      <div class="card third">
        <h2>Model notes</h2>
        <p class="note">The score is a strategy-fit score, not a winning probability. Higher means the ticket matches the current tested rules better.</p>
      </div>
    </section>
    <div class="footer">Pat-alyzer · generated from official public EuroMillions CSV data · {updated_at}</div>
  </main>
</body>
</html>"""
    (DOCS_DIR / "index.html").write_text(html_text, encoding="utf-8")


def main() -> None:
    ensure_folders()
    fetch_official_csvs()

    _, financial_summary = normalize_financialdata()

    draws = normalize_gamedata()

    if draws.empty:
        raise RuntimeError("No draw data was normalized. CSV parsing needs adjustment.")

    enriched = add_pattern_columns(draws)

    machine_metadata = load_machine_metadata()

    if not machine_metadata.empty:
        enriched = enriched.merge(machine_metadata, on="draw_date", how="left")
    else:
        enriched["ball_machine"] = ""
        enriched["ball_set"] = ""

    enriched.to_csv(PROCESSED_DIR / "draws_enriched.csv", index=False)

    missing_patterns = analyze_missing_zone_patterns(enriched)
    hybrid_patterns = get_hybrid_target_signatures(enriched)

    era_summary = analyze_eras(enriched)
    machine_summary = analyze_machine_metadata(enriched)

    latest_date = str(enriched.tail(1).iloc[0]["draw_date"])

    tickets = generate_tickets(
        enriched,
        hybrid_patterns,
        amount=10,
        seed=latest_date,
        strategy="v2_scored",
    )

    zone_backtest = backtest_zone_strategy(enriched)

    _, generated_backtest_summary = backtest_generated_ticket_strategies(enriched)

    generate_html_dashboard(
        enriched,
        missing_patterns,
        hybrid_patterns,
        tickets,
        zone_backtest,
        generated_backtest_summary,
        financial_summary,
        era_summary,
        machine_summary,
    )

    summary = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "draw_count": int(len(enriched)),
        "latest_draw_date": latest_date,
        "version": "v4-draw-system-aware",
    }

    (DOCS_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    print("Build completed.")
    print(summary)

if __name__ == "__main__":
    main()
