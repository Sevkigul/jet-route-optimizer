"""Ozellik uretimi - panel dengeleme ve tahmin özellikleri."""
import numpy as np
import pandas as pd

# Veri kalitesi ve tahmin ufku sinirlari
EGITIM_SON = pd.Timestamp("2026-05-09")        # guvenilir son egitim gunu
BOZUK_GUN = pd.Timestamp("2026-05-10")         # eksik/kismi gun - kullanilmaz
TAHMIN_BASLANGIC = pd.Timestamp("2026-05-11")
TAHMIN_SON = pd.Timestamp("2026-05-17")

# Sizinti (data leakage) onlemek icin tum gecikmeler >= 7 gun:
# 11-17 Mayis tahmin edilirken t-1..t-6 verisi elde olmayacagi icin
# en yakin gecikme 7 gun secildi (direct forecasting yaklasimi).
LAG_GUNLER = [7, 14, 21]
ROLLING_PENCERE = [7, 28]


def build_panel(data):
    """Dengeli panel kurar: her güzergah x her gün tek satir.

    - 1 Oca - 9 May : gerçek veri; teslimat olmayan gün-güzergah = 0 (zero-fill)
    - 10 May        : bozuk/eksik gün, desi = NaN (egitime girmez)
    - 11-17 May     : tahmin hedefi, desi = NaN
    Tarih ekseni kesintisiz tutulur; boylece gecikme hesabi takvimle hizali kalir.
    """
    desi = data["desi"][["origin", "destination", "date", "desi"]].copy()

    rotalar = desi[["origin", "destination"]].drop_duplicates()
    tarihler = pd.date_range(desi["date"].min(), TAHMIN_SON, freq="D")
    izgara = rotalar.merge(pd.DataFrame({"date": tarihler}), how="cross")

    panel = izgara.merge(desi, on=["origin", "destination", "date"], how="left")

    # Zero-filling: gecmis gunlerdeki eksik = 0 desi (o gun teslimat yok)
    gecmis = panel["date"] <= EGITIM_SON
    panel.loc[gecmis, "desi"] = panel.loc[gecmis, "desi"].fillna(0.0)

    # Bozuk gun (10 May): tum satirlar NaN -> egitime alinmaz
    panel.loc[panel["date"] == BOZUK_GUN, "desi"] = np.nan

    return panel.sort_values(["origin", "destination", "date"]).reset_index(drop=True)


def add_features(panel):
    """Panele takvim, gecikme (lag) ve hareketli ortalama ozellikleri ekler."""
    df = panel.sort_values(["origin", "destination", "date"]).copy()

    # --- Takvim ozellikleri ---
    df["dayofweek"] = df["date"].dt.dayofweek
    df["is_weekend"] = (df["dayofweek"] >= 5).astype(int)
    df["month"] = df["date"].dt.month
    df["day"] = df["date"].dt.day

    # --- Gecikme ozellikleri (hepsi >= 7 gun, sizinti yok) ---
    grup = df.groupby(["origin", "destination"])["desi"]
    for g in LAG_GUNLER:
        df[f"lag_{g}"] = grup.shift(g)

    # --- Hareketli ortalama / std (7 gun kaydirilmis seri uzerinden) ---
    df["_shifted"] = grup.shift(7)
    grup_s = df.groupby(["origin", "destination"])["_shifted"]
    for p in ROLLING_PENCERE:
        df[f"roll_mean_{p}"] = grup_s.transform(
            lambda s: s.rolling(p, min_periods=1).mean()
        )
    df["roll_std_7"] = grup_s.transform(
        lambda s: s.rolling(7, min_periods=1).std()
    )
    df = df.drop(columns="_shifted")

    return df.reset_index(drop=True)


if __name__ == "__main__":
    from data_loader import load_data

    data = load_data()
    panel = build_panel(data)
    print("Panel boyutu      :", panel.shape)
    print("Tarih araligi     :", panel["date"].min().date(), "->",
          panel["date"].max().date())
    print("Guzergah sayisi   :",
          panel[["origin", "destination"]].drop_duplicates().shape[0])

    feat = add_features(panel)
    print("\nÖzellikli panel   :", feat.shape)
    print("Sütunlar          :", list(feat.columns))

    egitim = feat[feat["desi"].notna()]
    tahmin = feat[(feat["date"] >= TAHMIN_BASLANGIC)
                  & (feat["date"] <= TAHMIN_SON)]
    print(f"\nEgitim satiri     : {len(egitim)}")
    print(f"Tahmin satiri     : {len(tahmin)}  ({tahmin['date'].nunique()} gun)")