from __future__ import annotations

import itertools
import json
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
PROCESSED_DIR = BASE_DIR / "data" / "processed"
DOCS_DIR = BASE_DIR / "docs"
START_YEAR = 2004
CURRENT_YEAR = datetime.now(timezone.utc).year
MAIN_COLS = ["n1", "n2", "n3", "n4", "n5"]
STAR_COLS = ["s1", "s2"]
GAME_URL = "https://prdlnboppreportsst.blob.core.windows.net/legal-reports/euromillions-gamedata-NL-{year}.csv"
FIN_URL = "https://prdlnboppreportsst.blob.core.windows.net/legal-reports/euromillions-financialdata-NL-{year}.csv"


def ensure_folders() -> None:
    for folder in [RAW_DIR / "gamedata", RAW_DIR / "financialdata", PROCESSED_DIR, DOCS_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


def download_file(url: str, dest: Path) -> None:
    r = requests.get(url, timeout=30)
    if r.status_code == 404:
        print(f"Not found: {url}")
        return
    r.raise_for_status()
    dest.write_bytes(r.content)
    print(f"Downloaded: {dest}")


def fetch_official_csvs() -> None:
    for year in range(START_YEAR, CURRENT_YEAR + 1):
        download_file(GAME_URL.format(year=year), RAW_DIR / "gamedata" / f"euromillions-gamedata-NL-{year}.csv")
        download_file(FIN_URL.format(year=year), RAW_DIR / "financialdata" / f"euromillions-financialdata-NL-{year}.csv")


def read_csv_flexible(path: Path) -> pd.DataFrame:
    for enc in ["utf-8-sig", "latin1"]:
        for sep in [";", ","]:
            try:
                df = pd.read_csv(path, sep=sep, dtype=str, encoding=enc)
                if df.shape[1] > 1:
                    return df
            except Exception:
                pass
    raise ValueError(f"Could not parse CSV: {path}")


def normalize_date(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if pd.Series([text]).str.match(r"^\d{4}-\d{2}-\d{2}$").iloc[0]:
        parsed = pd.to_datetime(text, format="%Y-%m-%d", errors="coerce")
    elif pd.Series([text]).str.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}").iloc[0]:
        parsed = pd.to_datetime(text, errors="coerce")
    else:
        parsed = pd.to_datetime(text, dayfirst=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.strftime("%Y-%m-%d")


def extract_date(row: pd.Series) -> str | None:
    for val in row.values:
        dt = normalize_date(val)
        if dt:
            return dt
    return None


def extract_numbers(row: pd.Series) -> list[int]:
    nums: list[int] = []
    for val in row.values:
        text = str(val).strip()
        if text.isdigit():
            n = int(text)
            if 1 <= n <= 50:
                nums.append(n)
    return nums


def valid_draw(nums: list[int], stars: list[int]) -> bool:
    return len(nums) == 5 and len(stars) == 2 and len(set(nums)) == 5 and len(set(stars)) == 2 and all(1 <= n <= 50 for n in nums) and all(1 <= s <= 12 for s in stars)


def normalize_gamedata() -> pd.DataFrame:
    rows: list[dict] = []
    today = datetime.now(timezone.utc).date()
    for file in sorted((RAW_DIR / "gamedata").glob("*.csv")):
        year = int(file.stem.split("-")[-1])
        df = read_csv_flexible(file)
        for _, row in df.iterrows():
            dt = extract_date(row)
            raw_nums = extract_numbers(row)
            if not dt or len(raw_nums) < 7:
                continue
            parsed_dt = pd.to_datetime(dt, errors="coerce")
            if pd.isna(parsed_dt) or parsed_dt.date() > today:
                continue
            nums = sorted(raw_nums[:5])
            stars = sorted(raw_nums[5:7])
            if valid_draw(nums, stars):
                rows.append({"draw_date": dt, "n1": nums[0], "n2": nums[1], "n3": nums[2], "n4": nums[3], "n5": nums[4], "s1": stars[0], "s2": stars[1], "source_year": year})
    draws = pd.DataFrame(rows)
    if draws.empty:
        return draws
    draws = draws.drop_duplicates(subset=["draw_date"]).sort_values("draw_date").reset_index(drop=True)
    draws.to_csv(PROCESSED_DIR / "draws.csv", index=False)
    return draws


def normalize_financialdata() -> pd.DataFrame:
    summaries = []
    frames = []
    for file in sorted((RAW_DIR / "financialdata").glob("*.csv")):
        year = int(file.stem.split("-")[-1])
        try:
            df = read_csv_flexible(file)
        except Exception:
            continue
        df["source_year"] = year
        df["source_file"] = file.name
        frames.append(df)
        summaries.append({"source_year": year, "rows": len(df), "columns": len(df.columns), "source_file": file.name})
    if frames:
        pd.concat(frames, ignore_index=True, sort=False).to_csv(PROCESSED_DIR / "financial_raw.csv", index=False)
    summary = pd.DataFrame(summaries).sort_values("source_year") if summaries else pd.DataFrame()
    summary.to_csv(PROCESSED_DIR / "financial_summary.csv", index=False)
    return summary


def zone_for_number(n: int) -> int:
    return 1 if n <= 10 else 2 if n <= 20 else 3 if n <= 30 else 4 if n <= 40 else 5


def zone_signature(nums: Iterable[int]) -> str:
    c = Counter(zone_for_number(int(n)) for n in nums)
    return "-".join(str(c.get(z, 0)) for z in range(1, 6))


def star_zone_signature(stars: Iterable[int]) -> str:
    c = Counter(1 if int(s) <= 4 else 2 if int(s) <= 8 else 3 for s in stars)
    return "-".join(str(c.get(z, 0)) for z in range(1, 4))


def combo_key(nums: Iterable[int], stars: Iterable[int]) -> str:
    return ",".join(map(str, sorted(map(int, nums)))) + "+" + ",".join(map(str, sorted(map(int, stars))))


def add_pattern_columns(draws: pd.DataFrame) -> pd.DataFrame:
    e = draws.copy()
    e[MAIN_COLS] = e[MAIN_COLS].astype(int)
    e[STAR_COLS] = e[STAR_COLS].astype(int)
    e["main_numbers"] = e[MAIN_COLS].values.tolist()
    e["stars"] = e[STAR_COLS].values.tolist()
    e["zone_signature"] = e["main_numbers"].apply(zone_signature)
    e["star_zone_signature"] = e["stars"].apply(star_zone_signature)
    e["sum"] = e[MAIN_COLS].sum(axis=1)
    e["odd_count"] = e[MAIN_COLS].apply(lambda r: sum(int(n) % 2 for n in r), axis=1)
    e["even_count"] = 5 - e["odd_count"]
    e["low_count"] = e[MAIN_COLS].apply(lambda r: sum(int(n) <= 25 for n in r), axis=1)
    e["high_count"] = 5 - e["low_count"]
    e["exact_combo_key"] = e.apply(lambda r: combo_key(r["main_numbers"], r["stars"]), axis=1)
    e["main_combo_key"] = e["main_numbers"].apply(lambda ns: ",".join(map(str, sorted(ns))))
    e.to_csv(PROCESSED_DIR / "draws_enriched.csv", index=False)
    return e


def analyze_missing_zone_patterns(e: pd.DataFrame, recent_window: int = 50) -> pd.DataFrame:
    hist = e["zone_signature"].value_counts(normalize=True)
    recent = e.tail(recent_window)["zone_signature"].value_counts(normalize=True)
    rows = []
    for sig in sorted(set(hist.index).union(recent.index)):
        h, r = float(hist.get(sig, 0)), float(recent.get(sig, 0))
        rows.append({"zone_signature": sig, "historical_rate": round(h, 4), "recent_rate": round(r, 4), "missing_score": round(max(0, h - r), 4)})
    out = pd.DataFrame(rows).sort_values("missing_score", ascending=False)
    out.to_csv(PROCESSED_DIR / "missing_zone_patterns.csv", index=False)
    return out


def get_hybrid_target_signatures(e: pd.DataFrame, recent_window: int = 50, top_n: int = 10, common_weight: float = 0.60, missing_weight: float = 0.40) -> pd.DataFrame:
    hist = e["zone_signature"].value_counts(normalize=True)
    recent = e.tail(recent_window)["zone_signature"].value_counts(normalize=True)
    rows = []
    for sig in sorted(set(hist.index).union(recent.index)):
        h, r = float(hist.get(sig, 0)), float(recent.get(sig, 0))
        missing = max(0, h - r)
        rows.append({"zone_signature": sig, "historical_rate": round(h, 4), "recent_rate": round(r, 4), "missing_score": round(missing, 4), "hybrid_score": round(common_weight * h + missing_weight * missing, 6)})
    out = pd.DataFrame(rows).sort_values("hybrid_score", ascending=False)
    out.to_csv(PROCESSED_DIR / "hybrid_zone_patterns.csv", index=False)
    return out.head(top_n)


def all_pairs(nums: Iterable[int]) -> list[tuple[int, int]]:
    return list(itertools.combinations(sorted(map(int, nums)), 2))


def all_triplets(nums: Iterable[int]) -> list[tuple[int, int, int]]:
    return list(itertools.combinations(sorted(map(int, nums)), 3))


def build_context(e: pd.DataFrame) -> dict:
    pair_counts, triplet_counts, star_pair_counts = Counter(), Counter(), Counter()
    for _, r in e.iterrows():
        nums = [int(r[c]) for c in MAIN_COLS]
        stars = [int(r[c]) for c in STAR_COLS]
        pair_counts.update(all_pairs(nums))
        triplet_counts.update(all_triplets(nums))
        star_pair_counts.update([tuple(sorted(stars))])
    last_seen = {}
    for n in range(1, 51):
        idx = e.index[e[MAIN_COLS].eq(n).any(axis=1)].tolist()
        last_seen[n] = len(e) - 1 - idx[-1] if idx else len(e)
    star_last_seen = {}
    for s in range(1, 13):
        idx = e.index[e[STAR_COLS].eq(s).any(axis=1)].tolist()
        star_last_seen[s] = len(e) - 1 - idx[-1] if idx else len(e)
    return {"main_counts": Counter(e[MAIN_COLS].values.flatten()), "star_counts": Counter(e[STAR_COLS].values.flatten()), "pair_counts": pair_counts, "triplet_counts": triplet_counts, "star_pair_counts": star_pair_counts, "last_seen": last_seen, "star_last_seen": star_last_seen, "historical_exact_keys": set(e["exact_combo_key"]), "historical_main_keys": set(e["main_combo_key"])}


def band_score(avg: float, low: float, high: float) -> int:
    if low <= avg <= high:
        return 100
    if avg < low:
        return max(0, int(100 * avg / max(low, 1)))
    return max(0, int(100 - min(100, ((avg - high) / max(high, 1)) * 100)))


def score_ticket(nums: list[int], stars: list[int], hybrid: pd.DataFrame, ctx: dict) -> dict:
    nums, stars = sorted(nums), sorted(stars)
    sig = zone_signature(nums)
    star_sig = star_zone_signature(stars)
    hybrid_row = hybrid.set_index("zone_signature").to_dict("index").get(sig, {})
    hybrid_score = min(100, int(round(float(hybrid_row.get("hybrid_score", 0)) * 2500)))
    odd = sum(n % 2 for n in nums)
    low = sum(n <= 25 for n in nums)
    total = sum(nums)
    odd_even_score = 100 if odd in [2, 3] else 60 if odd in [1, 4] else 20
    low_high_score = 100 if low in [2, 3] else 60 if low in [1, 4] else 20
    sum_score = 100 if 90 <= total <= 170 else 70 if 75 <= total <= 185 else 30
    pair_score = band_score(sum(ctx["pair_counts"].get(p, 0) for p in all_pairs(nums)) / 10, 2, 10)
    triplet_score = band_score(sum(ctx["triplet_counts"].get(t, 0) for t in all_triplets(nums)) / 10, 0, 3)
    avg_gap = sum(ctx["last_seen"].get(n, 0) for n in nums) / 5
    gap_score = int(min(100, max(20, avg_gap * 4)))
    star_pair_frequency = ctx["star_pair_counts"].get(tuple(stars), 0)
    star_gap_avg = sum(ctx["star_last_seen"].get(s, 0) for s in stars) / 2
    star_pattern_score = int(min(100, (30 if star_pair_frequency else 45) + min(35, star_gap_avg * 5) + 20))
    anti_crowd_score = min(100, sum(1 for n in nums if n > 31) * 25)
    exact = combo_key(nums, stars)
    main = ",".join(map(str, nums))
    duplicate_penalty = 100 if exact in ctx["historical_exact_keys"] else 60 if main in ctx["historical_main_keys"] else 0
    final = int(round(0.20 * hybrid_score + 0.10 * odd_even_score + 0.10 * low_high_score + 0.10 * sum_score + 0.10 * pair_score + 0.08 * triplet_score + 0.12 * gap_score + 0.10 * star_pattern_score + 0.10 * anti_crowd_score - 0.25 * duplicate_penalty))
    final = max(0, min(100, final))
    why = [f"Hybrid 60/40 zone {sig}", f"Odd/even {odd}/{5 - odd}", f"Low/high {low}/{5 - low}", f"Sum {total}", f"Avg gap {avg_gap:.1f}", f"Star zone {star_sig}", "No exact historical duplicate" if duplicate_penalty == 0 else "Duplicate/main-history penalty applied"]
    return {"numbers": ", ".join(map(str, nums)), "stars": ", ".join(map(str, stars)), "zone_signature": sig, "hybrid_score": hybrid_score, "odd_even_score": odd_even_score, "low_high_score": low_high_score, "sum_score": sum_score, "pair_score": pair_score, "triplet_score": triplet_score, "gap_overdue_score": gap_score, "star_pattern_score": star_pattern_score, "anti_crowd_score": anti_crowd_score, "duplicate_penalty": duplicate_penalty, "final_strategy_score": final, "why_selected": " | ".join(why)}


def build_wheeling_pool(ctx: dict, pool_size: int = 18) -> list[int]:
    hot = [int(n) for n, _ in ctx["main_counts"].most_common(15)]
    overdue = sorted(range(1, 51), key=lambda n: ctx["last_seen"].get(n, 0), reverse=True)[:15]
    pool = []
    for n in hot + overdue + list(range(1, 51)):
        if n not in pool:
            pool.append(n)
        if len(pool) >= pool_size:
            break
    return sorted(pool)


def generate_tickets(e: pd.DataFrame, hybrid: pd.DataFrame, amount: int = 10, sample_size: int = 50000) -> pd.DataFrame:
    random.seed(f"pat-alyzer-{e.tail(1).iloc[0]['draw_date']}")
    ctx = build_context(e)
    targets = set(hybrid["zone_signature"])
    pool = build_wheeling_pool(ctx)
    combos = list(itertools.combinations(pool, 5))
    random.shuffle(combos)
    candidates = []
    seen = set()
    for nums_tuple in combos[:sample_size]:
        nums = sorted(nums_tuple)
        if zone_signature(nums) not in targets:
            continue
        stars = sorted(random.sample(range(1, 13), 2))
        key = combo_key(nums, stars)
        if key in seen:
            continue
        seen.add(key)
        scored = score_ticket(nums, stars, hybrid, ctx)
        if scored["duplicate_penalty"] >= 100:
            continue
        candidates.append(scored)
    candidates = sorted(candidates, key=lambda r: r["final_strategy_score"], reverse=True)
    out = pd.DataFrame(candidates[:amount])
    out.to_csv(PROCESSED_DIR / "candidate_tickets.csv", index=False)
    return out


def backtest_zone_strategy(e: pd.DataFrame, training_window: int = 200, recent_window: int = 50, top_n: int = 10) -> pd.DataFrame:
    weights = {"Hybrid 70 common / 30 missing": (0.70, 0.30), "Hybrid 60 common / 40 missing": (0.60, 0.40), "Hybrid 50 common / 50 missing": (0.50, 0.50)}
    rows = []
    for i in range(training_window, len(e)):
        hist, actual = e.iloc[:i], e.iloc[i]
        hist_rates = hist["zone_signature"].value_counts(normalize=True)
        recent_rates = hist.tail(recent_window)["zone_signature"].value_counts(normalize=True)
        sigs = sorted(set(hist_rates.index).union(recent_rates.index))
        score_rows = []
        for sig in sigs:
            h, r = float(hist_rates.get(sig, 0)), float(recent_rates.get(sig, 0))
            missing = max(0, h - r)
            row = {"zone_signature": sig, "historical_rate": h, "missing_score": missing}
            for name, (cw, mw) in weights.items():
                row[name] = cw * h + mw * missing
            score_rows.append(row)
        scores = pd.DataFrame(score_rows)
        actual_sig = actual["zone_signature"]
        result = {"draw_date": actual["draw_date"], "actual_zone_signature": actual_sig, "Missing-pattern top zone signatures": actual_sig in scores.sort_values("missing_score", ascending=False).head(top_n)["zone_signature"].tolist(), "Most-common top zone signatures": actual_sig in scores.sort_values("historical_rate", ascending=False).head(top_n)["zone_signature"].tolist(), "Random signature baseline estimate": min(1.0, top_n / max(1, hist["zone_signature"].nunique()))}
        for name in weights:
            result[name] = actual_sig in scores.sort_values(name, ascending=False).head(top_n)["zone_signature"].tolist()
        rows.append(result)
    result_df = pd.DataFrame(rows)
    result_df.to_csv(PROCESSED_DIR / "backtest_results.csv", index=False)
    summary = []
    for col in ["Hybrid 70 common / 30 missing", "Hybrid 60 common / 40 missing", "Hybrid 50 common / 50 missing", "Most-common top zone signatures", "Missing-pattern top zone signatures"]:
        summary.append({"test_name": col, "draws_tested": len(result_df), "hit_rate": round(float(result_df[col].mean()), 4)})
    summary.append({"test_name": "Random signature baseline estimate", "draws_tested": len(result_df), "hit_rate": round(float(result_df["Random signature baseline estimate"].mean()), 4)})
    out = pd.DataFrame(summary).sort_values("hit_rate", ascending=False).reset_index(drop=True)
    out.to_csv(PROCESSED_DIR / "backtest_summary.csv", index=False)
    return out


def parse_num_text(text: str) -> list[int]:
    return [int(v.strip()) for v in str(text).split(",") if v.strip().isdigit()]


def backtest_generated_tickets(e: pd.DataFrame, test_window: int = 250, tickets_per_draw: int = 10) -> pd.DataFrame:
    rows = []
    start = max(250, len(e) - test_window)
    for i in range(start, len(e)):
        hist, actual = e.iloc[:i].copy(), e.iloc[i]
        hybrid = get_hybrid_target_signatures(hist)
        tickets = generate_tickets(hist, hybrid, amount=tickets_per_draw, sample_size=10000)
        actual_nums = [int(actual[c]) for c in MAIN_COLS]
        actual_stars = [int(actual[c]) for c in STAR_COLS]
        best_main, best_stars, best_ticket = 0, 0, ""
        for _, t in tickets.iterrows():
            tn, ts = parse_num_text(t["numbers"]), parse_num_text(t["stars"])
            mm, sm = len(set(tn) & set(actual_nums)), len(set(ts) & set(actual_stars))
            if (mm, sm) > (best_main, best_stars):
                best_main, best_stars, best_ticket = mm, sm, f'{t["numbers"]} + {t["stars"]}'
        rows.append({"draw_date": actual["draw_date"], "best_generated_ticket": best_ticket, "best_main_matches": best_main, "best_star_matches": best_stars, "hit_2_main_or_better": best_main >= 2, "hit_3_main_or_better": best_main >= 3, "hit_2_main_plus_1_star_or_better": best_main >= 2 and best_stars >= 1})
    result = pd.DataFrame(rows)
    result.to_csv(PROCESSED_DIR / "generated_ticket_backtest_results.csv", index=False)
    out = pd.DataFrame([
        {"metric": "draws_tested", "value": len(result)},
        {"metric": "avg_best_main_matches", "value": round(float(result["best_main_matches"].mean()), 4)},
        {"metric": "avg_best_star_matches", "value": round(float(result["best_star_matches"].mean()), 4)},
        {"metric": "hit_2_main_or_better_rate", "value": round(float(result["hit_2_main_or_better"].mean()), 4)},
        {"metric": "hit_3_main_or_better_rate", "value": round(float(result["hit_3_main_or_better"].mean()), 4)},
        {"metric": "hit_2_main_plus_1_star_or_better_rate", "value": round(float(result["hit_2_main_plus_1_star_or_better"].mean()), 4)},
    ])
    out.to_csv(PROCESSED_DIR / "generated_ticket_backtest_summary.csv", index=False)
    return out


def render_page(e: pd.DataFrame, missing: pd.DataFrame, hybrid: pd.DataFrame, tickets: pd.DataFrame, zone_backtest: pd.DataFrame, ticket_backtest: pd.DataFrame, financial: pd.DataFrame) -> None:
    latest = e.tail(1).iloc[0]
    hot_nums = pd.Series(e[MAIN_COLS].values.flatten()).value_counts().head(10).rename_axis("number").reset_index(name="times_drawn")
    hot_stars = pd.Series(e[STAR_COLS].values.flatten()).value_counts().head(5).rename_axis("star").reset_index(name="times_drawn")
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = f"""<!doctype html><html><head><meta charset='utf-8'><title>Pat-alyzer</title><style>body{{font-family:Arial,sans-serif;margin:40px;background:#f7f7f7;color:#222}}.card{{background:#fff;padding:20px;margin-bottom:20px;border-radius:10px;box-shadow:0 1px 4px #bbb}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:8px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}th{{background:#eee}}.warning{{color:#8a4b00;font-weight:bold}}code{{background:#eee;padding:2px 4px}}</style></head><body>
<h1>Pat-alyzer</h1><h2>EuroMillions Pattern Analyzer</h2>
<div class='card'><h2>Important</h2><p class='warning'>This analyzes historical structures and creates strategy-based candidate tickets. It does not guarantee or truly predict winning numbers.</p></div>
<div class='card'><h2>Latest draw</h2><p><b>Date:</b> {latest['draw_date']}</p><p><b>Numbers:</b> {latest['n1']}, {latest['n2']}, {latest['n3']}, {latest['n4']}, {latest['n5']} + {latest['s1']}, {latest['s2']}</p><p><b>Rainbow zone:</b> <code>{latest['zone_signature']}</code></p><p><b>Draws analyzed:</b> {len(e)}</p><p><b>Updated:</b> {updated}</p></div>
<div class='card'><h2>Missing rainbow-zone patterns</h2>{missing.head(10).to_html(index=False)}</div>
<div class='card'><h2>Hybrid 60/40 target patterns</h2><p>60% historical commonness + 40% recent underrepresentation.</p>{hybrid.to_html(index=False)}</div>
<div class='card'><h2>Generated candidate tickets</h2><p>Stable per latest draw date. Uses wheeling pool, hybrid zones, pair/triplet scoring, gap scoring, star scoring, anti-crowd scoring, and duplicate avoidance.</p>{tickets.to_html(index=False)}</div>
<div class='card'><h2>Zone-signature backtest</h2>{zone_backtest.to_html(index=False)}</div>
<div class='card'><h2>Actual generated-ticket backtest</h2>{ticket_backtest.to_html(index=False)}</div>
<div class='card'><h2>Financial data import summary</h2>{financial.tail(10).to_html(index=False) if not financial.empty else '<p>No financial data imported.</p>'}</div>
<div class='card'><h2>Hot main numbers</h2>{hot_nums.to_html(index=False)}</div>
<div class='card'><h2>Hot stars</h2>{hot_stars.to_html(index=False)}</div>
</body></html>"""
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")


def main() -> None:
    ensure_folders()
    fetch_official_csvs()
    draws = normalize_gamedata()
    if draws.empty:
        raise RuntimeError("No draw data was normalized. CSV parsing needs adjustment.")
    financial = normalize_financialdata()
    enriched = add_pattern_columns(draws)
    missing = analyze_missing_zone_patterns(enriched)
    hybrid = get_hybrid_target_signatures(enriched)
    tickets = generate_tickets(enriched, hybrid)
    zone_backtest = backtest_zone_strategy(enriched)
    ticket_backtest = backtest_generated_tickets(enriched)
    render_page(enriched, missing, hybrid, tickets, zone_backtest, ticket_backtest, financial)
    summary = {"updated_at": datetime.now(timezone.utc).isoformat(), "draw_count": int(len(enriched)), "latest_draw_date": str(enriched.tail(1).iloc[0]["draw_date"])}
    (DOCS_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("Build completed.")
    print(summary)


if __name__ == "__main__":
    main()
