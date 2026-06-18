"""
train_classifier.py
-------------------
Train a random-forest classifier on features.parquet produced by
extract_features.py, and report how well it separates the labels.

Why random forest first?
- Works well with the ~30 tabular features we're producing
- No feature scaling needed
- Gives you feature importance, so you can see which features carry the signal
- Trains in seconds on a laptop; a CNN is overkill until this plateaus

What this script prints
-----------------------
- Label counts in train and test
- Overall accuracy + macro-F1
- Per-class precision / recall / F1
- Confusion matrix
- Top 15 most important features

What it saves
-------------
- model.joblib         : the trained pipeline (features -> label)
- metrics.json         : numeric metrics for comparison across runs
- confusion_matrix.png : visual confusion matrix
- feature_importance.png : bar chart of top features
"""

import os
import json
from collections import Counter

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import joblib

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    accuracy_score,
    f1_score,
)


# --- Settings ---------------------------------------------------------------
HERE         = os.path.dirname(os.path.abspath(__file__))
FEATURES_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "data", "audio_data", "features"))
MODELS_DIR   = os.path.normpath(os.path.join(HERE, "..", "..", "models", "bumblebee_rf"))
RESULTS_DIR  = os.path.normpath(os.path.join(HERE, "..", "..", "results", "classifier"))

TEST_FRACTION  = 0.25
RANDOM_SEED    = 42
MIN_PER_CLASS  = 10        # drop classes with fewer labeled windows than this

os.makedirs(MODELS_DIR,  exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)


# --- Load features ----------------------------------------------------------
parquet_path = os.path.join(FEATURES_DIR, "features.parquet")
csv_path     = os.path.join(FEATURES_DIR, "features.csv")

if os.path.exists(parquet_path):
    df = pd.read_parquet(parquet_path)
elif os.path.exists(csv_path):
    df = pd.read_csv(csv_path)
else:
    raise FileNotFoundError(f"No features file in {FEATURES_DIR}. Run extract_features.py first.")

print(f"Loaded {len(df)} rows from {FEATURES_DIR}")
print("Raw label counts:\n", df["label"].value_counts().to_string(), "\n")

# Drop classes with too few examples to be useful
counts = df["label"].value_counts()
rare   = counts[counts < MIN_PER_CLASS].index.tolist()
if rare:
    print(f"Dropping rare classes (<{MIN_PER_CLASS} windows): {rare}")
    df = df[~df["label"].isin(rare)].reset_index(drop=True)

meta_cols    = ["file", "t_start", "t_end", "label"]
feature_cols = [c for c in df.columns if c not in meta_cols]

X = df[feature_cols].fillna(0.0).values
y = df["label"].values


# --- Group-aware split: no leakage of neighbouring windows -----------------
# We split by label region (consecutive rows from the same file) so that
# overlapping windows don't appear in both train and test.
#
# Simple heuristic: give every (file, label, contiguous_region) a group id
# and stratify-split by those groups.
groups = []
last_key = None
group_id = -1
for _, row in df.iterrows():
    key = (row["file"], row["label"])
    if key != last_key:
        group_id += 1
        last_key = key
    groups.append(group_id)
df["_group"] = groups

# Use a simple 75/25 split at the group level
unique_groups = df["_group"].unique()
rng = np.random.default_rng(RANDOM_SEED)
rng.shuffle(unique_groups)
n_test_groups = max(1, int(len(unique_groups) * TEST_FRACTION))
test_groups   = set(unique_groups[:n_test_groups])

is_test = df["_group"].isin(test_groups).values
X_train, X_test = X[~is_test], X[is_test]
y_train, y_test = y[~is_test], y[is_test]

print(f"Train: {len(X_train)} windows, Test: {len(X_test)} windows")
print("Train label counts:", dict(Counter(y_train)))
print("Test label counts: ", dict(Counter(y_test)))


# --- Train ------------------------------------------------------------------
clf = RandomForestClassifier(
    n_estimators=300,
    min_samples_leaf=2,
    class_weight="balanced",
    random_state=RANDOM_SEED,
    n_jobs=-1,
)
clf.fit(X_train, y_train)


# --- Evaluate ---------------------------------------------------------------
if len(X_test) == 0:
    print("No test data available (likely only one labeled region). "
          "Skipping evaluation but still saving the model.")
    y_pred = []
    report = {}
    acc = f1m = float("nan")
else:
    y_pred = clf.predict(X_test)
    acc    = accuracy_score(y_test, y_pred)
    f1m    = f1_score(y_test, y_pred, average="macro")
    report = classification_report(y_test, y_pred, output_dict=True, zero_division=0)

    print(f"\nAccuracy   : {acc:.3f}")
    print(f"Macro F1   : {f1m:.3f}")
    print("\nPer-class report:")
    print(classification_report(y_test, y_pred, zero_division=0))

    # Confusion matrix
    classes = sorted(set(np.concatenate([y_test, y_pred])))
    cm = confusion_matrix(y_test, y_pred, labels=classes)
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(classes))); ax.set_xticklabels(classes, rotation=45, ha="right")
    ax.set_yticks(range(len(classes))); ax.set_yticklabels(classes)
    ax.set_xlabel("Predicted"); ax.set_ylabel("True")
    ax.set_title("Confusion matrix (test set)")
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            ax.text(j, i, cm[i, j], ha="center", va="center",
                    color="white" if cm[i, j] > cm.max()/2 else "black", fontsize=9)
    fig.colorbar(im)
    plt.tight_layout()
    cm_path = os.path.join(RESULTS_DIR, "confusion_matrix.png")
    fig.savefig(cm_path, dpi=140)
    print(f"\nSaved: {cm_path}")

# Feature importance
imp = pd.Series(clf.feature_importances_, index=feature_cols).sort_values(ascending=False)
print("\nTop 15 features:")
print(imp.head(15).to_string())

fig, ax = plt.subplots(figsize=(8, 6))
imp.head(20)[::-1].plot.barh(ax=ax, color="steelblue")
ax.set_title("Top 20 feature importances")
ax.set_xlabel("Importance")
plt.tight_layout()
fi_path = os.path.join(RESULTS_DIR, "feature_importance.png")
fig.savefig(fi_path, dpi=140)
print(f"Saved: {fi_path}")


# --- Save -------------------------------------------------------------------
model_path = os.path.join(MODELS_DIR, "model.joblib")
joblib.dump({"model": clf, "feature_cols": feature_cols}, model_path)
print(f"Saved: {model_path}")

metrics_path = os.path.join(RESULTS_DIR, "metrics.json")
with open(metrics_path, "w") as f:
    json.dump(
        {
            "accuracy": acc,
            "macro_f1": f1m,
            "n_train": int(len(X_train)),
            "n_test":  int(len(X_test)),
            "classes": sorted(set(y.tolist())),
            "classification_report": report,
        },
        f,
        indent=2,
    )
print(f"Saved: {metrics_path}")
