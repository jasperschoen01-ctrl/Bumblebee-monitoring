# file: fetch_flight_data_20260415.py
# Purpose: retrieve ALL PATS-C flight data (detection summaries + per-frame
# flight tracks) for 2026-04-15. Videos are NOT downloaded.
#
# Output (written to ./data/flight_data_2026-04-15/):
#   _detection_classes.json  — catalog of insect classes for reference
#   _sections.json           — catalog of accessible greenhouse sections
#   detections.csv           — one row per detection (summary features)
#   flight_tracks.csv        — one row per video frame across all detections
#                              (detection_uid column links back to detections.csv)

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Tuple

import pandas as pd
from dotenv import load_dotenv

import logger
from pats_service import PatsService

logger.init_logger(logger=logger.logger)
load_dotenv()

# ---- Config ----------------------------------------------------------------
TARGET_DATE = datetime(2026, 4, 15)
OUTPUT_DIR  = Path("../data/flight_data_2026-04-15")
# ----------------------------------------------------------------------------


def read_credentials() -> Tuple[str, str]:
    user  = os.getenv("pats_user")
    passw = os.getenv("pats_passw")
    if not user or not passw:
        raise SystemExit("Missing pats_user / pats_passw in environment (.env)")
    return user, passw


def main() -> None:
    user, passw = read_credentials()
    pats = PatsService(user=user, passw=passw)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    classes  = pats.download_detection_classes()
    sections = pats.download_sections()

    print(f"Accessible sections     : {len(sections)}")
    print(f"Known detection classes : {len(classes)}")

    with open(OUTPUT_DIR / "_detection_classes.json", "w") as f:
        json.dump(classes, f, indent=2)
    with open(OUTPUT_DIR / "_sections.json", "w") as f:
        json.dump(sections, f, indent=2)

    day_start = TARGET_DATE.replace(hour=0,  minute=0,  second=0)
    day_end   = TARGET_DATE.replace(hour=23, minute=59, second=59)

    all_detections = []
    all_tracks     = []

    for section in sections:
        section_id   = section["id"]
        section_name = section.get("name", f"section_{section_id}")
        print(f"\n=== Section {section_id} ({section_name}) ===")

        try:
            spots = pats.download_spots(section_id=section_id, snapping_mode="disabled")
        except Exception as e:
            print(f"  spots download failed: {e}")
            continue

        c_spots = spots.get("c", [])
        if not c_spots:
            print("  (no PATS-C sensors in this section — skipping)")
            continue
        print(f"  {len(c_spots)} PATS-C sensor(s)")

        # Insect classes the PATS-C units can actually see in this section.
        c_insects = [cls for cls in section.get("detection_classes", [])
                     if cls.get("available_in_c")]
        if not c_insects:
            print("  (no C-available detection classes — skipping)")
            continue

        for spot in c_spots:
            row_id    = spot.get("row_id")
            post_id   = spot.get("post_id")
            system_id = spot.get("system_id") if row_id is None or post_id is None else None

            for insect in c_insects:
                try:
                    df = pats.download_c_detection_features(
                        section_id         = section_id,
                        row_id             = row_id,
                        post_id            = post_id,
                        system_id          = system_id,
                        detection_class_id = insect["id"],
                        start_date         = day_start,
                        end_date           = day_end,
                    )
                except Exception as e:
                    print(f"    row={row_id} post={post_id} insect={insect['short_name']} "
                          f"features FAILED: {e}")
                    continue

                if df.empty:
                    continue

                df["section_id"]  = section_id
                df["insect_id"]   = insect["id"]
                df["insect_name"] = insect["short_name"]
                all_detections.append(df)
                print(f"    row={row_id} post={post_id} insect={insect['short_name']}: "
                      f"{len(df)} detection(s)")

                for uid in df["uid"].tolist():
                    try:
                        track = pats.download_c_flight_track(
                            section_id    = section_id,
                            detection_uid = int(uid),
                        )
                    except Exception as e:
                        print(f"      uid={uid} track FAILED: {e}")
                        continue
                    if track.empty:
                        continue
                    track["detection_uid"] = int(uid)
                    track["section_id"]    = section_id
                    track["insect_id"]     = insect["id"]
                    all_tracks.append(track)

    # ----- write outputs ----------------------------------------------------
    if all_detections:
        det_df = pd.concat(all_detections, ignore_index=True)
        det_path = OUTPUT_DIR / "detections.csv"
        det_df.to_csv(det_path, index=False)
        print(f"\n-> {len(det_df)} detection(s) written to {det_path}")
    else:
        print("\n-> no detections found for 2026-04-15")

    if all_tracks:
        trk_df = pd.concat(all_tracks, ignore_index=True)
        trk_path = OUTPUT_DIR / "flight_tracks.csv"
        trk_df.to_csv(trk_path, index=False)
        print(f"-> {len(trk_df)} track frame(s) written to {trk_path}")
    else:
        print("-> no flight tracks found for 2026-04-15")


if __name__ == "__main__":
    main()
