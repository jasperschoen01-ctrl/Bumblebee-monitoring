# Bumblebee 5G Foraging Study — Project Handoff Summary

_Last updated: 2026-05-21. Paste this into a new chat to bring it up to speed._

## 1. What this project is

Determine whether **5G transmitter exposure affects bumblebee foraging behaviour** in a
greenhouse, using **PATS-C stereo-camera 3D flight-tracking** data. Two camera systems run
in parallel:

- **system 900** — far from the 5G antenna (low/no exposure)
- **system 939** — next to the antenna (sensor 4 sits next to PATS-939, row 213). **939 is the
  CLOSE/high-exposure system.**

Exposure runs on a **3-day ON / 3-day OFF cycle** anchored at `CYCLE_ANCHOR = 2026-04-23`.
Dates before the anchor are **BASELINE** (treatment-free, 4/13–4/22). Audio data was scrapped;
this is flight-only.

Repo root: `Bumblebee-monitoring/`. All working notebooks: `src/flight_analysis/pats/`.
Raw data: `data/flight_data/<date>_system_<sys>/` containing `detections.csv`,
`flight_tracks.csv`, and (baseline folders only) `hive_return_results.csv`.

## 2. Coordinate system & key constants

- `x` = horizontal across, `y` = **vertical (larger y = higher up)**, `z` = depth. All metres.
- Hive positions `HIVE_XYZ`: `900: (-0.040, -0.665, -1.195)`, `939: (-0.086, -0.828, -1.045)`.
- Hive **entrance** ≈ `y = -0.665`. Flowers sit **below** the entrance (more negative y);
  there are essentially **no flowers above the entrance** — the basis for the ceiling filter.
- There is a real flower patch at `x ≈ 1.1 m` (confirmed: 279 visits across all dates, ±11 cm
  spread). Two foraging zones: near hive (x≈0) and the patch (x≈1.1).
- Velocity reference values from literature: 15 m/s = biological max ground speed (Goulson 2010);
  0.5 m/s = minimum take-off / landing speed (Combes & Daniel 2003).

## 3. Pipeline order (which notebook produces what)

```
[1] flower_visit_pipeline.ipynb   -> data/multi_day/flower_visit_summary.csv (+ flower_visits.csv, _rejected.csv)
[2] multi_day_pipeline.ipynb      -> daily_summary.csv, per_track_indicators.csv, indicators_daily.csv  (THE model input)
[3] validation.ipynb              -> sensor/integrity QC (Methods sentences)
[3] indicator_validation.ipynb    -> baseline-only QC of the 6 indicators -> baseline_ranges.csv
[4] exposure_analysis.ipynb       -> figures / exploratory plots
[4] 5g_foraging_effect_model.ipynb-> FINAL pre-registered verdict (5-check rule, composite FII, mixed-effects)
[5] statistical_methods.ipynb     -> reading guide for [4] (not data-dependent)
```

`multi_day_pipeline.ipynb` reads directly from `data/flight_data/` (NOT a stale cache — that was
fixed). It also joins dBm levels and data-transfer files from `../../../data`. dBm sensor→system
map: `{6: 900, 4: 939}`; dBm file starts 4/23 so BASELINE rows have NaN dBm.

## 4. The six indicators (final set)

1. `neg_exit_count` — foraging volume (sign-aligned = −n_v3 hive exits)
2. `neg_re_ratio` — detection asymmetry (−re_ratio_v3)
3. `path_tortuosity` — navigation efficiency (daily median of arc/end-to-end per track)
4. `ifi_cv` — foraging-rhythm regularity (CV of inter-flight intervals)
5. `mean_handling_time_s` — Heinrich 1979 efficiency metric (from flower-visit pipeline)
6. `n_distinct_flowers` — flower-constancy proxy, Klein 2003 (from flower-visit pipeline)

Dropped earlier: `median_ifi_s` (redundant with neg_exit_count) and `vertical_deviation`
(weak biological meaning). **NOTE:** the swap of these into `5g_foraging_effect_model.ipynb`'s
`INDICATORS` list is still PENDING (data is ready in `indicators_daily.csv`). Cell 19 of
flower_visit_pipeline shows the exact edit. Current model verdict on data: **SUGGESTIVE 3/5**.

## 5. Most recent work (this session) — flower_visit_pipeline restructure

`flower_visit_pipeline.ipynb` was restructured and **verified to run correctly on a single day**
(full 46-folder sweep only times out in the sandbox's 45 s shell limit — it runs fine in Jupyter).
Backup saved as `flower_visit_pipeline.backup_before_wallclock_hivebox_ceiling.ipynb`. Five changes:

1. **Wall-clock linker (the big fix).** `flight_tracks.csv`'s `elapsed` column **restarts at ~0
   every ~50-min recording chunk**. The old cross-track linker compared raw `elapsed` between two
   tracks, so it paired tracks from different chunks (~74% spurious on 4/22 sys900). Fix: each
   track is anchored to its wall-clock start from `detections.csv` (`datetime` field, format
   `%Y%m%d_%H%M%S`); any frame's wall time = `wall_start + (elapsed − first_elapsed)`. Linking now
   compares wall-clock. Verified: cross-track durations now all fall inside the 1–30 s window.
2. **Hive = square BOX** (was a 0.15 m sphere). The hive is a physical box; a bee landing on a flat
   face/corner used to escape the sphere and get logged as a visit. Box half-extents
   `HIVE_HALF_X/Y/Z` default to a 30 cm cube (superset of the old sphere). **These defaults should
   be confirmed against the real hive dimensions.**
3. **Post = full height.** The 15×15 cm mounting post (xz cross-section centred on hive_x, hive_z)
   now spans the entire frame top-to-bottom (no y restriction).
4. **Ceiling filter** at `Y_CEILING_M = -0.5`: visits with `y > -0.5` are rejected as
   `above_ceiling` (no flowers above the entrance). Data context: ~20% of old kept visits were
   above −0.5, ~37.5% above −0.665.
5. **New §5b sanity section** — set `VIZ_DATE`/`VIZ_SYS`, prints each kept visit's **uids +
   wall-clock start/end** and plots the raw track trajectories (xy + xz) with hive box / post /
   ceiling overlaid, plus a `UIDs to download` list for pulling videos.

`flower_visits.csv` now carries wall-clock columns `t_start_dt`, `t_end_dt` (ISO) plus
`t_start_s`/`t_end_s` (seconds since midnight).

**Is the v3 / multi_day pipeline affected by the elapsed bug? NO.** It timestamps each track from
`detections.csv` `datetime` (wall-clock, one value per uid) and only uses `elapsed` to order frames
*within* a single track. Its `ifi_cv` already uses wall-clock `ts.diff()`. Only the flower-visit
cross-track linker was broken.

## 6. Project rules / conventions (persistent)

- User is on **macOS** — give Mac-related answers when relevant.
- **Box plots shown side-by-side must share the y-axis** (use `sharey="row"`). Otherwise alignment
  doesn't matter.
- Before big changes to a notebook, **create a backup** named
  `"<name>".backup_before_"<what changed>".ipynb`.
- All pipeline notebooks carry a `<!-- STATUS_BLOCK_v1 -->` header (WORKS / PENDING / flow diagram).

## 7. Pending tasks

- [ ] Run the restructured `flower_visit_pipeline.ipynb` fully in Jupyter and review §5b output;
      confirm/adjust `HIVE_HALF_X/Y/Z` against real hive dimensions.
- [ ] Swap indicators in `5g_foraging_effect_model.ipynb`: drop `median_ifi_s` + `vertical_deviation`,
      add `mean_handling_time_s` + `n_distinct_flowers` (data ready in `indicators_daily.csv`; edit shown
      in flower_visit_pipeline cell 19).
- [ ] Add `mean_dbm` as a continuous covariate in the mixed-effects model; re-run the 5-check rule.
- [ ] Build a working `components.v3_pipeline_restructure.ipynb` (user's draft) — symmetrical
      exit/return v3 logic + spike-healing (interpolate frames >7 m/s rather than delete) + wall-clock
      timeline. The v3 IFI is already wall-clock-safe.
- [ ] Wire indoor greenhouse T / RH / light when those sensors become available.
- [ ] Feed updated flower-visit indicators into `exposure_analysis.ipynb`, then finalise the model.

## 8. Sandbox / environment notes

- `scipy` and `statsmodels` are NOT installed in the sandbox. For testing, a `/tmp/scipy_stub`
  with namedtuple-based `mannwhitneyu` / `spearmanr` / `false_discovery_control` was used. The
  notebooks themselves expect scipy/statsmodels in the user's real Jupyter env.
- Shell calls have a hard **45 s timeout** — chunk long sweeps; the full flower-visit sweep over
  46 folders exceeds it (O(n²) clustering + cross-track), so validate on single folders.
- `pandas` 2.3, `numpy` 2.2, `matplotlib` 3.10 are available.
- Generator scripts (that build/patch the notebooks) live in the session `outputs/` dir; the most
  recent is `restructure_flower_visit_pipeline.py`.
