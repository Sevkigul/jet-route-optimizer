"""Coklu pencereli zaman-bazli backtest + metrikler (Faz 3).

Tek pencere yaniltici olabilir (eski asamada da bu tespit edilmisti), bu
yuzden coklu "as-of" kesim tarihinde test edilir; ufuk-gunu, slot ve
sifir/sifir-olmayan kirilimlari raporlanir.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config


def metrics(actual, pred):
    actual = np.asarray(actual, dtype=float)
    pred = np.asarray(pred, dtype=float)
    mae = float(np.mean(np.abs(actual - pred)))
    rmse = float(np.sqrt(np.mean((actual - pred) ** 2)))
    denom = np.sum(actual)
    wmape = float(100 * np.sum(np.abs(actual - pred)) / denom) if denom > 0 else float("nan")
    bias = float(np.mean(pred - actual) / np.mean(actual)) if np.mean(actual) != 0 else float("nan")
    return {"MAE": mae, "RMSE": rmse, "WMAPE_%": wmape, "bias": bias, "n": len(actual)}


def naive_baseline_predict(df):
    """Trivial baseline: en kisa lag kolonunu oldugu gibi tahmin olarak kullan."""
    lag_col = f"lag_{config.MIN_LAG_DAYS}"
    return df[lag_col].fillna(0.0).values


def backtest(feat, train_fn, predict_fn, n_folds=None, label=""):
    """Rolling-origin backtest: son N adet horizon-gunluk pencerede test eder."""
    n_folds = n_folds or config.VALIDATION_FOLDS
    horizon = config.FORECAST_HORIZON_DAYS

    egitim = feat[feat["desi"].notna()].copy()
    son = egitim["date"].max()

    fold_scores = []
    for i in range(n_folds):
        val_son = son - pd.Timedelta(days=horizon * i)
        val_bas = val_son - pd.Timedelta(days=horizon - 1)
        tr = egitim[egitim["date"] < val_bas]
        val = egitim[(egitim["date"] >= val_bas) & (egitim["date"] <= val_son)]

        if len(val) == 0 or len(tr) == 0:
            continue

        model = train_fn(tr)
        pred = predict_fn(model, val)

        overall = metrics(val["desi"].values, pred)
        fold_scores.append(overall)

        print(f"  [{label}] Katman {i + 1} ({val_bas.date()} - {val_son.date()}): "
              f"WMAPE {overall['WMAPE_%']:6.2f} | MAE {overall['MAE']:>9,.1f} | bias {overall['bias']:+.3f}")

        _slot_breakdown(val, pred)
        _zero_nonzero_breakdown(val, pred)
        _horizon_breakdown(val, pred, val_bas)

    if not fold_scores:
        raise RuntimeError("Hicbir backtest katmani calisamadi (veri yetersiz)")

    avg_wmape = np.nanmean([s["WMAPE_%"] for s in fold_scores])
    avg_bias = np.nanmean([s["bias"] for s in fold_scores])
    print(f"  [{label}] --> Ortalama WMAPE: {avg_wmape:.2f} | Ortalama bias: {avg_bias:+.3f}\n")
    return fold_scores, avg_wmape


def _slot_breakdown(val, pred):
    val = val.copy()
    val["_pred"] = pred
    for slot_code, info in {v["code"]: v for v in config.SLOT_MAP.values()}.items():
        sub = val[val["slot"] == slot_code]
        if len(sub) == 0:
            continue
        m = metrics(sub["desi"].values, sub["_pred"].values)
        print(f"      slot={info['display']}: WMAPE {m['WMAPE_%']:6.2f} | bias {m['bias']:+.3f} | n={m['n']}")


def _zero_nonzero_breakdown(val, pred):
    val = val.copy()
    val["_pred"] = pred
    zero = val[val["desi"] == 0]
    nonzero = val[val["desi"] > 0]
    if len(zero) > 0:
        m0 = metrics(zero["desi"].values, zero["_pred"].values)
        print(f"      sifir-talep satirlari: MAE {m0['MAE']:.2f} | n={m0['n']}")
    if len(nonzero) > 0:
        m1 = metrics(nonzero["desi"].values, nonzero["_pred"].values)
        print(f"      sifir-disi satirlar:  WMAPE {m1['WMAPE_%']:6.2f} | bias {m1['bias']:+.3f} | n={m1['n']}")


def _horizon_breakdown(val, pred, val_bas):
    val = val.copy()
    val["_pred"] = pred
    val["_horizon_day"] = (val["date"] - val_bas).dt.days + 1
    for day, sub in val.groupby("_horizon_day"):
        m = metrics(sub["desi"].values, sub["_pred"].values)
        print(f"      gun+{day}: WMAPE {m['WMAPE_%']:6.2f} | n={m['n']}")


if __name__ == "__main__":
    from data_loader import load_data
    from panel import build_panel
    from features import add_features

    data = load_data()
    feat = add_features(build_panel(data), data["distance"])

    print("=== Baseline saglik kontrolu (lag'i oldugu gibi tahmin olarak kullan) ===")
    backtest(feat, train_fn=lambda tr: None, predict_fn=lambda model, df: naive_baseline_predict(df),
             n_folds=3, label="naive")
