# explore_pats.py
# Run with: python explore_pats.py
# Goal: find the detection around 13-04-2026 14:00:19 and inspect it
import os
from datetime import datetime, timedelta
from typing import Tuple

import matplotlib.pyplot as plt
from dotenv import load_dotenv

import logger
from pats_service import PatsService, PatsServiceError
from plot_examples import ExamplePlots

logger.init_logger(logger=logger.logger)
load_dotenv()

# --- Fill these in ---
USERNAME    = "18119301@student.hhs.nl"
PASSWORD    = "korrit-Giksod-2cajpe"
SECTION_ID  = 900          # find this via download_sections()
ROW_ID      = 6         # fill in once you know it
POST_ID     = 1         # fill in once you know it
DETECTION_CLASS_ID = 1     # bumblebee class id — check via download_detection_classes()

# The moment we are looking for
TARGET_DATETIME = datetime(2026, 4, 13, 14, 0, 19)


def main():
    pats = PatsService(USERNAME, PASSWORD)

    # ----------------------------------------------------------------
    # Step 1: find your section_id and detection_class_id if unknown
    # ----------------------------------------------------------------
    print("=== Available sections ===")
    sections = pats.download_sections()
    for s in sections:
        print(f"  id={s['id']:5d}  name={s['name']}  greenhouse={s['greenhouse_name']}")

    print("\n=== Available detection classes ===")
    classes = pats.download_detection_classes()
    for cid, c in classes.items():
        print(f"  id={cid:3s}  label={c['label']}  short={c['short_name']}")

    # ----------------------------------------------------------------
    # Step 2: download a narrow time window around the target moment
    # ----------------------------------------------------------------
    # Pull a 1-hour window centred on the target so we don't miss it
    from datetime import timedelta
    start = TARGET_DATETIME - timedelta(minutes=30)
    end   = TARGET_DATETIME + timedelta(minutes=30)

    print(f"\n=== Downloading detections {start} → {end} ===")
    df = pats.download_c_detection_features(
        section_id         = SECTION_ID,
        row_id             = ROW_ID,
        post_id            = POST_ID,
        system_id          = None,
        detection_class_id = DETECTION_CLASS_ID,
        start_date         = start,
        end_date           = end,
    )

    if df.empty:
        print("No detections returned — check section_id, row_id, post_id, detection_class_id")
        return

    # Parse the datetime column (format from API: "%Y%m%d_%H%M%S")
    df["dt"] = pd.to_datetime(df["datetime"], format="%Y%m%d_%H%M%S", errors="coerce")
    df = df.sort_values("dt").reset_index(drop=True)

    print(f"\nFound {len(df)} detections in window")
    print(df[["dt", "uid", "duration", "dist_traject", "vel_mean", "vel_max"]].to_string())

    # ----------------------------------------------------------------
    # Step 3: find the detection closest to our target timestamp
    # ----------------------------------------------------------------
    target_ts = pd.Timestamp(TARGET_DATETIME)
    df["delta_s"] = (df["dt"] - target_ts).abs().dt.total_seconds()
    closest = df.loc[df["delta_s"].idxmin()]

    print(f"\n=== Closest detection to {TARGET_DATETIME} ===")
    print(closest.to_string())

    # ----------------------------------------------------------------
    # Step 4: download the full frame-by-frame flight track for it
    # ----------------------------------------------------------------
    uid = int(closest["uid"])
    print(f"\n=== Flight track for uid={uid} ===")
    track = pats.download_c_flight_track(
        section_id    = SECTION_ID,
        detection_uid = uid,
    )

    print(f"Track shape: {track.shape}")
    print(f"Columns: {list(track.columns)}")
    print("\nFirst 5 frames:")
    print(track.head().to_string())
    print("\nLast 5 frames:")
    print(track.tail().to_string())

    # Quick XYZ summary
    if "posX_insect" in track.columns:
        print(f"\nXYZ range:")
        print(f"  X: {track['posX_insect'].min():.3f} → {track['posX_insect'].max():.3f} m")
        print(f"  Y: {track['posY_insect'].min():.3f} → {track['posY_insect'].max():.3f} m")
        print(f"  Z: {track['posZ_insect'].min():.3f} → {track['posZ_insect'].max():.3f} m")
        print(f"\n  T0 (first frame): X={track['posX_insect'].iloc[0]:.3f}  "
              f"Y={track['posY_insect'].iloc[0]:.3f}  Z={track['posZ_insect'].iloc[0]:.3f}")
        print(f"  T1 (last frame):  X={track['posX_insect'].iloc[-1]:.3f}  "
              f"Y={track['posY_insect'].iloc[-1]:.3f}  Z={track['posZ_insect'].iloc[-1]:.3f}")


if __name__ == "__main__":
    main()