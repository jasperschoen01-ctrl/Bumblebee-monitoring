# file: validate_returns_sample.py
# Purpose: build a manually-labelled validation sample for the v3 hive-RETURN
#          classifier, so a real precision figure (with a confidence interval)
#          can be reported instead of an eyeballed spot-check.
#
# Two modes
# ---------
#   1) DOWNLOAD (default):
#        python validate_returns_sample.py
#      - reads the per-track output (per_track_indicators.csv)
#      - keeps only confirmed returns (hive_return_v3 == True)
#      - draws a FIXED-SEED random sample of N_SAMPLE of them (balanced across
#        the two systems where possible, so both cameras are represented)
#      - downloads each detection's PATS-C video via the PATS API
#      - writes a label sheet (manifest.csv) with a blank `is_correct` column
#
#   2) SCORE:
#        python validate_returns_sample.py --score
#      - reads manifest.csv after you have filled in `is_correct`
#        (1 = real return, 0 = false positive; y/n and true/false also accepted)
#      - prints precision and a Wilson 95% confidence interval
#
# Credentials are read from the repo .env (pats_user / pats_passw), exactly like
# fetch_flight_data_20260415.py.

import argparse
import math
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv

import logger
from pats_service import PatsService

logger.init_logger(logger=logger.logger)
load_dotenv()

# ---- Config ----------------------------------------------------------------
PER_TRACK_CSV = Path("../data/multi_day_v3/per_track_indicators.csv")
OUTPUT_DIR    = Path("../data/validation_returns")
VIDEO_DIR     = OUTPUT_DIR / "videos"
MANIFEST_CSV  = OUTPUT_DIR / "manifest.csv"

RETURN_FLAG   = "hive_return_v3"   # set to "hive_exit_v3" to validate exits instead
N_SAMPLE      = 50
RANDOM_SEED   = 20                 # fixed -> reproducible sample
BALANCE_BY_SYSTEM = True           # split the sample evenly over the systems present
# A manual override if section auto-resolution fails (e.g. {900: 123, 939: 123}).
SECTION_ID_OVERRIDE: Dict[int, int] = {}
# ----------------------------------------------------------------------------


def read_credentials() -> Tuple[str, str]:
    user  = os.getenv("pats_user")
    passw = os.getenv("pats_passw")
    if not user or not passw:
        raise SystemExit("Missing pats_user / pats_passw in environment (.env)")
    return user, passw


def draw_sample() -> pd.DataFrame:
    if not PER_TRACK_CSV.exists():
        raise SystemExit(f"Cannot find {PER_TRACK_CSV} (run from the pats/ folder).")

    df = pd.read_csv(PER_TRACK_CSV)
    returns = df[df[RETURN_FLAG] == True].copy()  # noqa: E712  (pandas mask)
    if returns.empty:
        raise SystemExit(f"No rows with {RETURN_FLAG} == True in {PER_TRACK_CSV}.")

    print(f"Confirmed {RETURN_FLAG}: {len(returns)} tracks "
          f"across systems {sorted(returns['system_id'].unique())}")

    if BALANCE_BY_SYSTEM:
        systems = sorted(returns["system_id"].unique())
        per_sys = max(1, N_SAMPLE // len(systems))
        parts = []
        for sys_id in systems:
            pool = returns[returns["system_id"] == sys_id]
            parts.append(pool.sample(min(per_sys, len(pool)), random_state=RANDOM_SEED))
        sample = pd.concat(parts, ignore_index=True)
        # top up to N_SAMPLE if integer division left a remainder
        if len(sample) < N_SAMPLE:
            remaining = returns.drop(index=sample.index, errors="ignore")
            extra = remaining.sample(min(N_SAMPLE - len(sample), len(remaining)),
                                     random_state=RANDOM_SEED)
            sample = pd.concat([sample, extra], ignore_index=True)
    else:
        sample = returns.sample(min(N_SAMPLE, len(returns)), random_state=RANDOM_SEED)

    sample = sample.sample(frac=1.0, random_state=RANDOM_SEED).reset_index(drop=True)
    return sample[["date", "system_id", "uid", "ts"]]


def build_system_to_section(pats: PatsService) -> Dict[int, int]:
    """Map each PATS-C system_id to the section_id it lives in, via the API."""
    mapping: Dict[int, int] = {}
    for section in pats.download_sections():
        section_id = section["id"]
        try:
            spots = pats.download_spots(section_id=section_id, snapping_mode="disabled")
        except Exception as e:  # noqa: BLE001
            print(f"  spots download failed for section {section_id}: {e}")
            continue
        for spot in spots.get("c", []):
            sys_id = spot.get("system_id")
            if sys_id is not None:
                mapping[int(sys_id)] = section_id
    return mapping


def resolve_section(system_id: int, mapping: Dict[int, int]) -> Optional[int]:
    if int(system_id) in SECTION_ID_OVERRIDE:
        return SECTION_ID_OVERRIDE[int(system_id)]
    return mapping.get(int(system_id))


def download_mode() -> None:
    sample = draw_sample()
    print(f"Sampled {len(sample)} returns (seed={RANDOM_SEED}).")

    user, passw = read_credentials()
    pats = PatsService(user=user, passw=passw)

    sys_to_section = {**build_system_to_section(pats), **SECTION_ID_OVERRIDE}
    print(f"system -> section map: {sys_to_section}")

    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    for i, r in sample.iterrows():
        system_id = int(r["system_id"])
        uid       = int(r["uid"])
        section_id = resolve_section(system_id, sys_to_section)
        record = {
            "order_idx":  i + 1,
            "date":       r["date"],
            "system_id":  system_id,
            "uid":        uid,
            "ts":         r["ts"],
            "section_id": section_id,
            "predicted":  "return",
            "video_file": "",
            "is_correct": "",     # <-- you fill this: 1 = real return, 0 = false positive
            "notes":      "",
        }
        if section_id is None:
            print(f"  [{i+1}/{len(sample)}] uid={uid} system={system_id}: "
                  f"no section_id -> set SECTION_ID_OVERRIDE; skipping download")
            rows.append(record)
            continue
        try:
            video = pats.download_c_video(section_id=section_id, detection_uid=uid)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i+1}/{len(sample)}] uid={uid} video FAILED: {e}")
            rows.append(record)
            continue
        fname = f"{system_id}_{r['date']}_{uid}.mp4"
        (VIDEO_DIR / fname).write_bytes(video)
        record["video_file"] = fname
        rows.append(record)
        print(f"  [{i+1}/{len(sample)}] uid={uid} system={system_id} -> {fname}")

    manifest = pd.DataFrame(rows)
    manifest.to_csv(MANIFEST_CSV, index=False)
    n_ok = (manifest["video_file"] != "").sum()
    print(f"\n-> {n_ok}/{len(manifest)} videos in {VIDEO_DIR}")
    print(f"-> label sheet: {MANIFEST_CSV}")
    print("   Fill the `is_correct` column (1 = real return, 0 = false positive),")
    print("   then run:  python validate_returns_sample.py --score")


def _to_bool(v) -> Optional[bool]:
    s = str(v).strip().lower()
    if s in ("1", "y", "yes", "true", "t"):
        return True
    if s in ("0", "n", "no", "false", "f"):
        return False
    return None  # blank / unlabelled


def wilson_ci(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = (z / den) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - half, centre + half)


def score_mode() -> None:
    if not MANIFEST_CSV.exists():
        raise SystemExit(f"{MANIFEST_CSV} not found - run download mode first.")
    m = pd.read_csv(MANIFEST_CSV)
    labels = m["is_correct"].map(_to_bool)
    labelled = labels.dropna()
    n = len(labelled)
    k = int(labelled.sum())
    if n == 0:
        raise SystemExit("No labels found in `is_correct`. Fill 1/0 and retry.")
    p = k / n
    lo, hi = wilson_ci(k, n)
    print(f"Labelled: {n} / {len(m)} sampled returns")
    print(f"True returns (TP): {k}   False positives (FP): {n - k}")
    print(f"Precision = {k}/{n} = {p*100:.1f}%")
    print(f"Wilson 95% CI = [{lo*100:.1f}%, {hi*100:.1f}%]")


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate v3 return classifier on a labelled video sample.")
    ap.add_argument("--score", action="store_true",
                    help="Compute precision + Wilson CI from a filled-in manifest.csv")
    args = ap.parse_args()
    if args.score:
        score_mode()
    else:
        download_mode()


if __name__ == "__main__":
    main()
