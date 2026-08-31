"""
EVALUATION - STEP 2: Measure model accuracy from your labeled test set.

Reads your manually labeled CSV and computes:
  - Overall accuracy
  - Precision / Recall / F1 score
  - Accuracy broken down by match_type and college
  - Confusion matrix
  - Threshold analysis (what cutoff gives best accuracy?)
  - Worst failures for inspection

INSTRUCTIONS:
    1. Complete labeling in evaluation/labeled_pairs.csv first
    2. Run: python evaluation/07_evaluate_model.py
    3. Review evaluation/results/evaluation_report.txt
"""

import os
import sqlite3
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")   # no display needed
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

LABELED_CSV = "Evaluation/sample_results.csv"
RESULTS_DIR = "Evaluation/results"


# ── METRICS ──────────────────────────────────────────────────────────────────

def precision_recall_f1(y_true, y_pred):
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) > 0 else 0)
    return precision, recall, f1


# ── THRESHOLD ANALYSIS ────────────────────────────────────────────────────────

def threshold_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each possible score threshold, treat matches above it as positive.
    Find the threshold that maximizes F1.
    """
    thresholds = np.arange(0.55, 1.0, 0.01)
    rows = []
    for t in thresholds:
        y_pred = (df["similarity_score"] >= t).astype(int).tolist()
        y_true = df["your_label"].tolist()
        acc = sum(1 for a, b in zip(y_true, y_pred) if a == b) / len(y_true)
        p, r, f1 = precision_recall_f1(y_true, y_pred)
        rows.append({"threshold": round(t, 2), "accuracy": acc,
                     "precision": p, "recall": r, "f1": f1})
    return pd.DataFrame(rows)


# ── PLOTS ─────────────────────────────────────────────────────────────────────

def plot_confusion_matrix(y_true, y_pred, out_path):
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 0)
    matrix = [[tn, fp], [fn, tp]]
    labels = [["True Neg", "False Pos"], ["False Neg", "True Pos"]]

    fig, ax = plt.subplots(figsize=(5, 4))
    colors = [["#d4f0d4", "#f4d4d4"], ["#f4d4d4", "#d4f0d4"]]
    for i in range(2):
        for j in range(2):
            ax.add_patch(plt.Rectangle((j, 1-i), 1, 1,
                         color=colors[i][j], ec="white", lw=2))
            ax.text(j+0.5, 1.5-i, f"{labels[i][j]}\n{matrix[i][j]}",
                    ha="center", va="center", fontsize=13, fontweight="bold")

    ax.set_xlim(0, 2); ax.set_ylim(0, 2)
    ax.set_xticks([0.5, 1.5]); ax.set_xticklabels(["Predicted 0", "Predicted 1"])
    ax.set_yticks([0.5, 1.5]); ax.set_yticklabels(["Actual 1", "Actual 0"])
    ax.set_title("Confusion Matrix", fontsize=14, fontweight="bold", pad=12)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()
    print(f"  Saved -> {out_path}")


def plot_threshold_curve(thresh_df: pd.DataFrame, out_path: str):
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(thresh_df["threshold"], thresh_df["precision"],
            label="Precision", color="#2563eb", lw=2)
    ax.plot(thresh_df["threshold"], thresh_df["recall"],
            label="Recall",    color="#16a34a", lw=2)
    ax.plot(thresh_df["threshold"], thresh_df["f1"],
            label="F1",        color="#d97706", lw=2.5, ls="--")

    best_t = thresh_df.loc[thresh_df["f1"].idxmax(), "threshold"]
    ax.axvline(best_t, color="gray", ls=":", lw=1.5,
               label=f"Best threshold = {best_t:.2f}")

    ax.set_xlabel("Similarity score threshold")
    ax.set_ylabel("Score")
    ax.set_title("Precision / Recall / F1 vs Threshold", fontweight="bold")
    ax.legend(loc="lower left")
    ax.set_ylim(0, 1.05)
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()
    print(f"  Saved -> {out_path}")


def plot_score_distribution(df: pd.DataFrame, out_path: str):
    correct   = df[df["your_label"] == 1]["similarity_score"]
    incorrect = df[df["your_label"] == 0]["similarity_score"]
    fig, ax = plt.subplots(figsize=(8, 4))
    bins = np.arange(0.55, 1.01, 0.02)
    ax.hist(correct,   bins=bins, alpha=0.6, color="#16a34a", label="Correct (1)")
    ax.hist(incorrect, bins=bins, alpha=0.6, color="#e24b4a", label="Incorrect (0)")
    ax.set_xlabel("Similarity score")
    ax.set_ylabel("Count")
    ax.set_title("Score Distribution: Correct vs Incorrect Matches", fontweight="bold")
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()
    print(f"  Saved -> {out_path}")


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)

    # Load labeled data
    df = pd.read_csv(LABELED_CSV)

    # Keep only rows with a clear label (1 or 0), skip ?
    df = df[df["your_label"].isin([1, 0, "1", "0"])]
    df["your_label"] = df["your_label"].astype(int)

    if len(df) < 20:
        print(f"Only {len(df)} labeled rows found. Label at least 20 before evaluating.")
        return

    print(f"Evaluating on {len(df)} labeled pairs ...\n")

    y_true = df["your_label"].tolist()

    # Use match_type as the default "prediction"
    # exact/strong = predicted positive, partial/weak = predicted negative
    df["predicted"] = df["match_type"].isin(["exact", "strong"]).astype(int)
    y_pred = df["predicted"].tolist()

    # ── Overall metrics ───────────────────────────────────────────────────────
    accuracy = sum(1 for a, b in zip(y_true, y_pred) if a == b) / len(y_true)
    precision, recall, f1 = precision_recall_f1(y_true, y_pred)

    print(f"{'='*50}")
    print(f"  OVERALL RESULTS")
    print(f"{'='*50}")
    print(f"  Labeled pairs   : {len(df)}")
    print(f"  Correct (1)     : {sum(y_true)}")
    print(f"  Incorrect (0)   : {len(y_true) - sum(y_true)}")
    print(f"\n  Accuracy        : {accuracy*100:.1f}%")
    print(f"  Precision       : {precision*100:.1f}%")
    print(f"  Recall          : {recall*100:.1f}%")
    print(f"  F1 Score        : {f1*100:.1f}%")

    # ── By match type ─────────────────────────────────────────────────────────
    print(f"\n  Accuracy by match type:")
    for mt in ["exact", "strong", "partial"]:
        sub = df[df["match_type"] == mt]
        if len(sub) == 0: continue
        acc = (sub["your_label"] == sub["predicted"]).mean()
        correct = sub["your_label"].sum()
        print(f"    {mt:<10} {acc*100:5.1f}%  ({correct}/{len(sub)} correct)")

    # ── By college ───────────────────────────────────────────────────────────
    print(f"\n  Accuracy by college:")
    for college in df["cc_college"].unique():
        sub = df[df["cc_college"] == college]
        if len(sub) < 2: continue
        acc = (sub["your_label"] == sub["predicted"]).mean()
        print(f"    {college[:45]:<45} {acc*100:5.1f}%  (n={len(sub)})")

    # ── Threshold analysis ────────────────────────────────────────────────────
    thresh_df = threshold_analysis(df)
    best_row  = thresh_df.loc[thresh_df["f1"].idxmax()]
    print(f"\n  Best threshold  : {best_row['threshold']:.2f}")
    print(f"  At threshold {best_row['threshold']:.2f} → Precision {best_row['precision']*100:.1f}%  "
          f"Recall {best_row['recall']*100:.1f}%  F1 {best_row['f1']*100:.1f}%")

    # ── Worst failures ────────────────────────────────────────────────────────
    print(f"\n  Worst false positives (labeled 0, model said strong/exact):")
    fp_df = df[(df["your_label"] == 0) & (df["predicted"] == 1)]
    fp_df = fp_df.nlargest(5, "similarity_score")
    for _, r in fp_df.iterrows():
        print(f"    [{r['similarity_score']:.3f}] {r['cc_code']:<12} '{str(r['cc_title'])[:30]}'")
        print(f"           -> {r['brd_code']:<10} '{str(r['brd_title'])[:30]}'")

    # ── Plots ─────────────────────────────────────────────────────────────────
    print(f"\n  Generating plots ...")
    plot_confusion_matrix(
        y_true, y_pred,
        os.path.join(RESULTS_DIR, "confusion_matrix.png")
    )
    plot_threshold_curve(
        thresh_df,
        os.path.join(RESULTS_DIR, "threshold_curve.png")
    )
    plot_score_distribution(
        df,
        os.path.join(RESULTS_DIR, "score_distribution.png")
    )

    # ── Save report ───────────────────────────────────────────────────────────
    report_path = os.path.join(RESULTS_DIR, "evaluation_report.txt")
    with open(report_path, "w") as f:
        f.write(f"Course Transfer Matcher — Evaluation Report\n")
        f.write(f"{'='*50}\n")
        f.write(f"Labeled pairs : {len(df)}\n")
        f.write(f"Accuracy      : {accuracy*100:.1f}%\n")
        f.write(f"Precision     : {precision*100:.1f}%\n")
        f.write(f"Recall        : {recall*100:.1f}%\n")
        f.write(f"F1 Score      : {f1*100:.1f}%\n")
        f.write(f"Best threshold: {best_row['threshold']:.2f}\n")
    print(f"  Report -> {report_path}")

    print(f"\n  Done. Check evaluation/results/ for all outputs.")
    print(f"\n  Interpretation guide:")
    print(f"    Accuracy > 85%  → model is ready for the web app")
    print(f"    Accuracy 70-85% → consider tuning the threshold")
    print(f"    Accuracy < 70%  → revisit department mapping or model")


if __name__ == "__main__":
    main()
