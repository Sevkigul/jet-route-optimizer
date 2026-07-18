"""Takvim + lag/rolling ozellik uretimi (Faz 2).

(origin, destination, slot) bazinda gruplanir - 09:00 ve 17:00 ayri
gunluk seriler olarak ele alinir. Bu, eski asamadaki (origin, destination)
gruplamasindan tek farkli nokta.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config


def add_features(panel, distance):
    df = panel.sort_values(["origin", "destination", "slot", "date"]).copy()

    # --- Statik guzergah ozellikleri (mesafe, SLA gunu) ---
    df = df.merge(distance, on=["origin", "destination"], how="left")

    # --- Takvim ozellikleri ---
    df["dayofweek"] = df["date"].dt.dayofweek
    df["is_weekend"] = (df["dayofweek"] >= 5).astype(int)
    df["month"] = df["date"].dt.month
    df["day"] = df["date"].dt.day
    df["is_holiday"] = df["date"].isin(config.OFFICIAL_HOLIDAYS).astype(int)
    df["is_evening_slot"] = df["slot"]  # 0/1, zaten binary

    # --- Lag ozellikleri (MIN_LAG_DAYS tabanli, config'ten turetilmis) ---
    grup = df.groupby(["origin", "destination", "slot"])["desi"]
    for g in config.LAG_DAYS:
        df[f"lag_{g}"] = grup.shift(g)

    # --- Hareketli ortalama / std (MIN_LAG_DAYS kaydirilmis seri uzerinden) ---
    df["_shifted"] = grup.shift(config.MIN_LAG_DAYS)
    grup_s = df.groupby(["origin", "destination", "slot"])["_shifted"]
    for p in config.ROLLING_WINDOWS:
        df[f"roll_mean_{p}"] = grup_s.transform(lambda s: s.rolling(p, min_periods=1).mean())
    df["roll_std_7"] = grup_s.transform(lambda s: s.rolling(7, min_periods=1).std())
    df = df.drop(columns="_shifted")

    return df.reset_index(drop=True)


LAG_COLS = [f"lag_{g}" for g in config.LAG_DAYS]
ROLL_COLS = [f"roll_mean_{p}" for p in config.ROLLING_WINDOWS] + ["roll_std_7"]
CATEGORICAL = ["origin", "destination"]
NUMERIC = (["dayofweek", "is_weekend", "month", "day", "is_holiday", "is_evening_slot",
            "mesafe_km", "sla_gun"] + LAG_COLS + ROLL_COLS)
FEATURES = CATEGORICAL + NUMERIC


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def validate_features(feat):
    hedef = feat[(feat["date"] >= config.TARGET_START) & (feat["date"] <= config.TARGET_END)]
    for col in LAG_COLS:
        n_nan = hedef[col].isna().sum()
        _assert(n_nan == 0, f"hedef penceredeki {col} icin {n_nan} NaN var (leakage riski/eksik gecmis)")
    print(f"[features] hedef pencerede lag kolonlarinda NaN yok ({len(LAG_COLS)} kolon kontrol edildi)")

    tatil_gunleri = feat.loc[feat["is_holiday"] == 1, "date"].unique()
    print(f"[features] is_holiday=1 olan gun sayisi: {len(tatil_gunleri)} (beklenen {len(config.OFFICIAL_HOLIDAYS)})")
    _assert(len(tatil_gunleri) == len(config.OFFICIAL_HOLIDAYS), "tatil bayragi beklenen gun sayisiyla eslesmiyor")

    egitim = feat[feat["desi"].notna()]
    baslangic_disi_nan = egitim[LAG_COLS[0]].isna().sum()
    print(f"[features] egitim setinde en kisa lag ({LAG_COLS[0]}) icin NaN sayisi (grup basi, beklenen): {baslangic_disi_nan}")


if __name__ == "__main__":
    from data_loader import load_data
    from panel import build_panel

    data = load_data()
    panel = build_panel(data)
    feat = add_features(panel, data["distance"])
    print("Ozellikli panel:", feat.shape)
    print("Kolonlar:", list(feat.columns))
    validate_features(feat)
