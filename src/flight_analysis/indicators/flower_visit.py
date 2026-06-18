"""
flower_visit.py
================

Flower-visit detection in PATS-C flight tracks.

A flower visit appears as a *spatial collision* between two consecutive tracks:
when the bee lands on a flower it becomes (mostly) stationary and PATS-C loses
the track.  A short while later, when the bee departs, a new track is started
right next to where the previous one ended.  We therefore detect a visit as a
pair of consecutive tracks whose end- and start-points are spatially close,
inside the flower canopy z-range, separated in time by an inter-track interval
that fits the expected dwell-time window.

The thresholds are calibrated from a manually confirmed visit on
13 April 2026 (PATS-C system 900, ≈13:59:32–13:59:37 Europe/Amsterdam,
detection uids 1002164 → 1002167).  See ``calibrate_from_ground_truth`` and
the notebook ``exposure_analysis.ipynb`` Section P for the calibration details.

Public API
----------
build_track_summary(ft, det)
    Convert raw per-frame ``flight_tracks`` + ``detections`` tables into a
    one-row-per-track summary table with first/last positions and wall times.

compute(tracks, system_id, thresholds)
    Detect flower visits for one system.  Returns one row per visit.

condition_for(date_like)
    Map a date to the experimental condition ("BASELINE" / "ON" / "OFF") used
    in the rest of the project.

Notes
-----
The pair (1002164 → 1002167) on 13 April actually has a *negative* gap
between Track A's end and Track B's start (PATS-C kept the lock on the
approach track through the brief landing while a new track was initialised
at departure).  We therefore allow dwell down to ~−3 s so that this kind of
overlap-style visit is still captured.  The biological interpretation of the
dwell is preserved by also reporting `start_to_start_s` (≈ time between the
two appearances of the bee), which on 13 April equals 5.0 s and matches the
hand-annotated dwell.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Experimental condition (re-used from exposure_analysis.ipynb)               #
# --------------------------------------------------------------------------- #
CYCLE_ANCHOR    = pd.Timestamp("2026-04-23")
CYCLE_ON_DAYS   = 3
CYCLE_OFF_DAYS  = 3
CYCLE_LEN       = CYCLE_ON_DAYS + CYCLE_OFF_DAYS


def condition_for(date_like) -> str:
    """ON / OFF / BASELINE label for a given date."""
    d = pd.Timestamp(date_like)
    if d.tzinfo is not None:
        d = d.tz_convert("Europe/Amsterdam").tz_localize(None)
    d = d.normalize()
    if d < CYCLE_ANCHOR:
        return "BASELINE"
    days_since = (d - CYCLE_ANCHOR).days
    return "ON" if (days_since % CYCLE_LEN) < CYCLE_ON_DAYS else "OFF"


# --------------------------------------------------------------------------- #
# Track-endpoint table                                                        #
# --------------------------------------------------------------------------- #
def build_track_summary(ft: pd.DataFrame, det: pd.DataFrame) -> pd.DataFrame:
    """
    Build a one-row-per-track summary table.

    Parameters
    ----------
    ft  : raw flight_tracks.csv (per-frame positions)
    det : raw detections.csv     (per-track wall-time + duration)

    Returns
    -------
    DataFrame with columns:
        detection_uid, system_id,
        wall_start, wall_end, duration_s,
        x_first, y_first, z_first,   ← entry point of the track
        x_last,  y_last,  z_last     ← exit  point of the track
    """
    if ft.empty or det.empty:
        return pd.DataFrame(columns=[
            "detection_uid", "system_id", "wall_start", "wall_end", "duration_s",
            "x_first", "y_first", "z_first", "x_last", "y_last", "z_last",
        ])

    ft_sorted = ft.sort_values(["detection_uid", "elapsed"])

    first = (ft_sorted.groupby("detection_uid", as_index=False).first()
                 [["detection_uid", "posX_insect", "posY_insect", "posZ_insect"]])
    first.columns = ["detection_uid", "x_first", "y_first", "z_first"]

    last  = (ft_sorted.groupby("detection_uid", as_index=False).last()
                 [["detection_uid", "posX_insect", "posY_insect", "posZ_insect"]])
    last.columns  = ["detection_uid", "x_last", "y_last", "z_last"]

    det_ = det.rename(columns={"uid": "detection_uid"}).copy()
    det_["wall_start"] = pd.to_datetime(det_["start_datetime"], utc=True)
    det_["duration_s"] = det_["duration"].astype(float)
    det_["wall_end"]   = det_["wall_start"] + pd.to_timedelta(det_["duration_s"], unit="s")

    out = (first
           .merge(last, on="detection_uid")
           .merge(det_[["detection_uid", "system_id", "wall_start", "wall_end", "duration_s"]],
                  on="detection_uid", how="inner"))
    return out.sort_values("wall_start").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# The detector                                                                #
# --------------------------------------------------------------------------- #
def compute(tracks: pd.DataFrame,
            system_id: int,
            thresholds: dict) -> pd.DataFrame:
    """
    Detect flower visits from PATS-C track pairs.

    A flower visit is defined as a gap between two consecutive tracks where
    the end-point of track N and start-point of track N+1 are spatially close
    (within ``SPATIAL_TOLERANCE`` m), the inter-track gap (dwell time) falls
    between ``MIN_DWELL`` and ``MAX_DWELL`` seconds, and both points lie
    within the flower-canopy z-range (if specified).

    Parameters
    ----------
    tracks      : DataFrame from ``build_track_summary`` (any number of systems)
    system_id   : integer system identifier (e.g. 900 or 939)
    thresholds  : dict with keys
        SPATIAL_TOLERANCE       – maximum 3-D end-to-start distance (m)
        MIN_DWELL               – minimum inter-track gap (s, may be negative)
        MAX_DWELL               – maximum inter-track gap (s)
        Z_FLOWER_MIN            – optional: lower bound on z (m)
        Z_FLOWER_MAX            – optional: upper bound on z (m)
        HIVE_XYZ                – optional: (x,y,z) of the hive (m).
                                  When given together with HIVE_EXCLUSION_M,
                                  visits inside the hive zone are dropped
                                  (these are hive entries/exits, not flowers).
        HIVE_EXCLUSION_M        – optional: hive exclusion radius (m)

    Returns
    -------
    DataFrame with one row per detected flower visit:
        date, system_id, visit_start, visit_end, dwell_s,
        x, y, z,                    ← midpoint of P1 and P2
        start_to_start_s,           ← time between Track A's start and Track B's start
        dist_m, uid_a, uid_b,
        condition                   ← ON / OFF / BASELINE
    """
    SPATIAL_TOL = float(thresholds["SPATIAL_TOLERANCE"])
    MIN_DWELL   = float(thresholds["MIN_DWELL"])
    MAX_DWELL   = float(thresholds["MAX_DWELL"])
    Z_MIN       = thresholds.get("Z_FLOWER_MIN")
    Z_MAX       = thresholds.get("Z_FLOWER_MAX")

    sub = (tracks[tracks["system_id"] == system_id]
           .dropna(subset=["x_last", "y_last", "z_last",
                           "x_first", "y_first", "z_first",
                           "wall_start", "wall_end"])
           .sort_values("wall_start")
           .reset_index(drop=True))
    if len(sub) < 2:
        return _empty_visits_frame()

    visits = []
    a_x = sub["x_last"].to_numpy();  a_y = sub["y_last"].to_numpy();  a_z = sub["z_last"].to_numpy()
    b_x = sub["x_first"].to_numpy(); b_y = sub["y_first"].to_numpy(); b_z = sub["z_first"].to_numpy()
    a_end   = sub["wall_end"].to_numpy()
    b_start = sub["wall_start"].to_numpy()
    a_start = sub["wall_start"].to_numpy()
    uids    = sub["detection_uid"].to_numpy()

    for i in range(len(sub) - 1):
        # zero-duration / corrupt rows
        if sub.iloc[i]["duration_s"] <= 0 or sub.iloc[i + 1]["duration_s"] <= 0:
            continue

        dx = b_x[i + 1] - a_x[i]
        dy = b_y[i + 1] - a_y[i]
        dz = b_z[i + 1] - a_z[i]
        dist = float(np.sqrt(dx * dx + dy * dy + dz * dz))
        if not np.isfinite(dist) or dist > SPATIAL_TOL:
            continue

        # gap between A.end and B.start, in seconds (may be negative
        # for overlap-style visits, see module docstring)
        dwell_s = (b_start[i + 1] - a_end[i]) / np.timedelta64(1, "s")
        if not (MIN_DWELL <= dwell_s <= MAX_DWELL):
            continue

        # canopy z window (apply to *both* end-points)
        if Z_MIN is not None and Z_MAX is not None:
            if not (Z_MIN <= a_z[i]    <= Z_MAX):  continue
            if not (Z_MIN <= b_z[i + 1] <= Z_MAX): continue

        # location & wall times
        mx, my, mz = (a_x[i] + b_x[i + 1]) / 2, (a_y[i] + b_y[i + 1]) / 2, (a_z[i] + b_z[i + 1]) / 2
        visit_start = pd.Timestamp(a_end[i])
        visit_end   = pd.Timestamp(b_start[i + 1])
        start_to_start_s = float((b_start[i + 1] - a_start[i]) / np.timedelta64(1, "s"))

        ts_a_start = pd.Timestamp(a_start[i])
        if ts_a_start.tzinfo is not None:
            local_date = ts_a_start.tz_convert("Europe/Amsterdam").date()
        else:
            local_date = ts_a_start.date()

        visits.append({
            "date":             local_date,
            "system_id":        int(system_id),
            "visit_start":      visit_start,
            "visit_end":        visit_end,
            "dwell_s":          float(dwell_s),
            "x":                float(mx),
            "y":                float(my),
            "z":                float(mz),
            "start_to_start_s": start_to_start_s,
            "dist_m":           dist,
            "uid_a":            int(uids[i]),
            "uid_b":            int(uids[i + 1]),
            "condition":        condition_for(ts_a_start),
        })

    if not visits:
        return _empty_visits_frame()
    return pd.DataFrame(visits)


def _empty_visits_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "date", "system_id", "visit_start", "visit_end", "dwell_s",
        "x", "y", "z", "start_to_start_s",
        "dist_m", "uid_a", "uid_b", "condition",
    ])


# --------------------------------------------------------------------------- #
# Convenience: calibration helper for the reference event                     #
# --------------------------------------------------------------------------- #
def calibrate_from_ground_truth(ft: pd.DataFrame,
                                det: pd.DataFrame,
                                uid_a: int = 1002164,
                                uid_b: int = 1002167) -> dict:
    """
    Compute the spatial tolerance from a manually confirmed visit.

    Returns a dict suitable for ``thresholds`` in :func:`compute`.
    """
    sumr = build_track_summary(ft, det)
    A = sumr[sumr["detection_uid"] == uid_a].iloc[0]
    B = sumr[sumr["detection_uid"] == uid_b].iloc[0]
    dx, dy, dz = B["x_first"] - A["x_last"], B["y_first"] - A["y_last"], B["z_first"] - A["z_last"]
    dist = float(np.sqrt(dx * dx + dy * dy + dz * dz))
    return dict(
        SPATIAL_TOLERANCE = max(dist * 1.5, 0.30),
        # NB: relaxed from the spec's +1.0 s because the calibration pair has
        # ~-1.6 s gap (overlap due to PATS keeping the lock through landing).
        MIN_DWELL         = -3.0,
        MAX_DWELL         = 15.0,
        Z_FLOWER_MIN      = -1.5,
        Z_FLOWER_MAX      = -0.3,
        _ref_distance_m   = dist,
    )
