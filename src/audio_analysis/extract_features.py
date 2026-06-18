"""
extract_features.py
-------------------
Turn Audacity-labeled audio into a feature table you can train a classifier on.

Workflow
~~~~~~~~
1. Label your audio in Audacity:
   - Tracks > Add New > Label Track (Cmd+Shift+N)
   - Play with spacebar; Cmd+B drops a label at the playhead
   - Type a label name (e.g. flight, pollination, silence, voice, noise)
   - Drag the label's right handle to the end of the event so labels have duration
2. Export: File > Export > Export Labels  --> save as  <audio_stem>.labels.txt
   in  data/audio_data/labels/   (e.g. Bee3.labels.txt for Bee3.WAV)
3. Run this script:
       python extract_features.py
4. Output lands in  data/audio_data/features/features.parquet  (+ features.csv)

Each labeled region becomes one or more feature rows (WINDOW_SEC windows
with HOP_SEC hop). The resulting table has one column per feature plus
meta columns: file, t_start, t_end, label.

Features extracted per window
-----------------------------
- Band energies in 5 bands (<200, 200-600, 600-1500, 1500-3000, 3000-5000 Hz)
- Spectral centroid, bandwidth, roll-off, flatness
- Zero-crossing rate
- 13 MFCCs (mean across the window)
- Fundamental frequency estimate (librosa.yin)
- Top-5 peak frequencies + amplitudes in 100-2500 Hz

Add or drop features in extract_window_features() if you want.
"""

import os
import glob
import warnings

import numpy as np
import pandas as pd
import librosa
from scipy.signal import find_peaks

warnings.filterwarnings("ignore", category=UserWarning)

# --- Settings ---------------------------------------------------------------
# Paths are relative to this script's location so it "just works" when run
# from anywhere.
HERE        = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR   = os.path.normpath(os.path.join(HERE, "..", "..", "data", "audio_data", "original"))
LABEL_DIR   = os.path.normpath(os.path.join(HERE, "..", "..", "data", "audio_data", "labels"))
OUT_DIR     = os.path.normpath(os.path.join(HERE, "..", "..", "data", "audio_data", "features"))

WINDOW_SEC  = 0.10   # length of one feature window
HOP_SEC     = 0.05   # step between successive windows (50% overlap)
SR_TARGET   = 22050  # downsample everything to this rate (fast FFT, consistent features)
N_MFCC      = 13

os.makedirs(OUT_DIR, exist_ok=True)


# --- Helpers ----------------------------------------------------------------
def read_audacity_labels(path):
    """Parse an Audacity label-export file -> list[(start_s, end_s, label)]."""
    rows = []
    with open(path) as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            try:
                start = float(parts[0])
                end   = float(parts[1])
            except ValueError:
                continue
            label = parts[2].strip()
            if not label:
                continue
            rows.append((start, end, label))
    return rows


def find_audio_for(stem):
    """Find the audio file that matches a given label stem (e.g. 'Bee3')."""
    for ext in (".WAV", ".wav", ".mp3", ".flac", ".ogg"):
        p = os.path.join(AUDIO_DIR, stem + ext)
        if os.path.exists(p):
            return p
    return None


def extract_window_features(y_win, sr):
    """Compute a dict of features for one audio window."""
    if len(y_win) < 32:
        return None

    feats = {}

    # FFT (once, used several ways below)
    N        = len(y_win)
    fft_vals = np.fft.rfft(y_win)
    freqs    = np.fft.rfftfreq(N, d=1.0 / sr)
    amp      = np.abs(fft_vals) / N

    # Band energies (squared amplitude summed in each band)
    for lo, hi in [(0, 200), (200, 600), (600, 1500), (1500, 3000), (3000, 5000)]:
        mask = (freqs >= lo) & (freqs < hi)
        feats[f"energy_{lo}_{hi}"] = float(np.sum(amp[mask] ** 2))

    # Spectral shape (librosa handles windowing internally)
    feats["centroid"]  = float(librosa.feature.spectral_centroid(y=y_win, sr=sr).mean())
    feats["bandwidth"] = float(librosa.feature.spectral_bandwidth(y=y_win, sr=sr).mean())
    feats["rolloff"]   = float(librosa.feature.spectral_rolloff(y=y_win, sr=sr).mean())
    feats["flatness"]  = float(librosa.feature.spectral_flatness(y=y_win).mean())
    feats["zcr"]       = float(librosa.feature.zero_crossing_rate(y_win).mean())

    # MFCCs (mean across the window)
    mfcc = librosa.feature.mfcc(y=y_win, sr=sr, n_mfcc=N_MFCC).mean(axis=1)
    for i, v in enumerate(mfcc):
        feats[f"mfcc_{i}"] = float(v)

    # Fundamental frequency estimate (flight-buzz range)
    try:
        f0 = librosa.yin(y_win, fmin=100, fmax=500, sr=sr)
        feats["f0_mean"]   = float(np.nanmean(f0))
        feats["f0_std"]    = float(np.nanstd(f0))
    except Exception:
        feats["f0_mean"] = np.nan
        feats["f0_std"]  = np.nan

    # Top-5 peaks in 100-2500 Hz
    band_mask = (freqs >= 100) & (freqs <= 2500)
    band_amp  = np.where(band_mask, amp, 0.0)
    bin_hz    = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0
    distance  = max(1, int(20.0 / bin_hz))     # require peaks >=20 Hz apart
    peak_max  = band_amp.max() if band_amp.size else 0.0
    prominence = peak_max * 0.05 if peak_max > 0 else 1e-12
    peaks_idx, _ = find_peaks(band_amp, prominence=prominence, distance=distance)
    if peaks_idx.size:
        order = np.argsort(amp[peaks_idx])[::-1][:5]
        peaks_idx = peaks_idx[order]
    pf = freqs[peaks_idx] if peaks_idx.size else np.array([])
    pa = amp[peaks_idx]   if peaks_idx.size else np.array([])
    for i in range(5):
        feats[f"peak_{i+1}_hz"]  = float(pf[i]) if i < len(pf) else 0.0
        feats[f"peak_{i+1}_amp"] = float(pa[i]) if i < len(pa) else 0.0

    return feats


def process_file(audio_path, labels):
    """Extract a feature row per window inside every labeled region."""
    y, sr = librosa.load(audio_path, sr=SR_TARGET, mono=True)
    win_samples = int(WINDOW_SEC * sr)
    hop_samples = int(HOP_SEC * sr)

    rows = []
    for (start_s, end_s, label) in labels:
        i0 = max(0, int(start_s * sr))
        i1 = min(len(y), int(end_s * sr))
        if i1 - i0 < win_samples:
            # Label too short for a single window -> use one center window anyway
            center = (i0 + i1) // 2
            i0 = max(0, center - win_samples // 2)
            i1 = i0 + win_samples
            if i1 > len(y):
                continue
        for s in range(i0, i1 - win_samples + 1, hop_samples):
            seg  = y[s : s + win_samples]
            feat = extract_window_features(seg, sr)
            if feat is None:
                continue
            feat["file"]    = os.path.basename(audio_path)
            feat["t_start"] = s / sr
            feat["t_end"]   = (s + win_samples) / sr
            feat["label"]   = label
            rows.append(feat)
    return rows


# --- Main -------------------------------------------------------------------
def main():
    label_files = sorted(glob.glob(os.path.join(LABEL_DIR, "*.labels.txt")))
    if not label_files:
        print(f"No label files found in {LABEL_DIR}")
        print("Export from Audacity: File > Export > Export Labels")
        print("Save as <audio_stem>.labels.txt  (e.g. Bee3.labels.txt for Bee3.WAV)")
        return

    all_rows = []
    for lp in label_files:
        stem = os.path.basename(lp).replace(".labels.txt", "")
        audio_path = find_audio_for(stem)
        if audio_path is None:
            print(f"[skip] no audio file in {AUDIO_DIR} matching stem '{stem}'")
            continue
        labels = read_audacity_labels(lp)
        print(f"[ok]   {stem:30s}  {len(labels):3d} labels")
        all_rows.extend(process_file(audio_path, labels))

    if not all_rows:
        print("No feature rows produced.")
        return

    df = pd.DataFrame(all_rows)
    meta = ["file", "t_start", "t_end", "label"]
    feat = [c for c in df.columns if c not in meta]
    df = df[meta + feat]

    csv_out     = os.path.join(OUT_DIR, "features.csv")
    parquet_out = os.path.join(OUT_DIR, "features.parquet")
    df.to_csv(csv_out, index=False)
    try:
        df.to_parquet(parquet_out, index=False)
        print(f"\nWrote {len(df)} rows -> {parquet_out}")
    except Exception as e:
        print(f"(Parquet save failed: {e}; CSV is still available.)")
    print(f"Wrote {len(df)} rows -> {csv_out}")

    print("\nLabel counts:")
    print(df["label"].value_counts().to_string())


if __name__ == "__main__":
    main()
