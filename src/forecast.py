"""Talep tahmini - LightGBM modeli, çoklu katman doğrulama ve 11-17 Mayıs tahmini."""
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

from data_loader import load_data
from features import build_panel, add_features, TAHMIN_BASLANGIC, TAHMIN_SON

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs"

# Modelin kullanacaği özellikler
KATEGORIK = ["origin", "destination"]
SAYISAL = ["dayofweek", "is_weekend", "month", "day",
           "lag_7", "lag_14", "lag_21",
           "roll_mean_7", "roll_mean_28", "roll_std_7"]
OZELLIKLER = KATEGORIK + SAYISAL

# Parametreler, çoklu zaman-katmani doğrulamasinin ORTALAMA WMAPE'sine göre
# seçildi (tek pencereye uyum yerine, genellemesi sağlam config).
LGB_PARAMS = {
    "objective": "regression",
    "metric": "rmse",
    "num_leaves": 15,
    "learning_rate": 0.03,
    "n_estimators": 300,
    "min_child_samples": 40,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "random_state": 42,
    "verbose": -1,
}

DOGRULAMA_KATMAN = 3   # son kaç adet 7-günlük pencerede test edilecek


def _hazirla(df):
    """Kategorik sütunlari 'category' tipine çevirir (LightGBM otomatik tanir)."""
    df = df.copy()
    for c in KATEGORIK:
        df[c] = df[c].astype("category")
    return df


def egit(train_df):
    """Eğitim verisiyle LightGBM modelini log1p dönüşümlü hedefle eğitir."""
    X = _hazirla(train_df)[OZELLIKLER]
    y = np.log1p(train_df["desi"].values)
    model = lgb.LGBMRegressor(**LGB_PARAMS)
    model.fit(X, y)
    return model


def tahmin_et(model, df):
    """Tahmin yapar, log1p dönüşümünü geri alir, negatif değerleri kirpar."""
    X = _hazirla(df)[OZELLIKLER]
    pred = np.expm1(model.predict(X))
    return np.clip(pred, 0, None)


def metrikler(gercek, tahmin):
    """MAE, RMSE ve WMAPE (ağirlikli yüzde hata) hesaplar."""
    gercek = np.asarray(gercek, dtype=float)
    tahmin = np.asarray(tahmin, dtype=float)
    mae = np.mean(np.abs(gercek - tahmin))
    rmse = np.sqrt(np.mean((gercek - tahmin) ** 2))
    wmape = 100 * np.sum(np.abs(gercek - tahmin)) / np.sum(gercek)
    return {"MAE": mae, "RMSE": rmse, "WMAPE_%": wmape}


def dogrula(egitim, katman=DOGRULAMA_KATMAN):
    """Çoklu zaman-katmani doğrulama: son N adet 7-günlük pencerede test eder. """
    son = egitim["date"].max()
    skorlar = []
    for i in range(katman):
        val_son = son - pd.Timedelta(days=7 * i)
        val_bas = val_son - pd.Timedelta(days=6)
        tr = egitim[egitim["date"] < val_bas]
        val = egitim[(egitim["date"] >= val_bas) & (egitim["date"] <= val_son)]

        model = egit(tr)
        skor = metrikler(val["desi"].values, tahmin_et(model, val))
        skorlar.append(skor)
        print(f"  Katman {i + 1} ({val_bas.date()} - {val_son.date()}): "
              f"WMAPE {skor['WMAPE_%']:6.2f} | MAE {skor['MAE']:>9,.0f}")

    ort = np.mean([s["WMAPE_%"] for s in skorlar])
    print(f"  --> Ortalama WMAPE: {ort:.2f}")
    return skorlar


def run_forecast():
    data = load_data()
    panel = add_features(build_panel(data))

    egitim = panel[panel["desi"].notna()].copy()
    tahmin_seti = panel[(panel["date"] >= TAHMIN_BASLANGIC)
                        & (panel["date"] <= TAHMIN_SON)].copy()

    # --- 1) Çoklu katman doğrulama ---
    print("=== Çoklu katman doğrulama (time-based) ===")
    dogrula(egitim)

    # --- 2) Final model: tüm eğitim verisi -> 11-17 Mayıs tahmini ---
    final_model = egit(egitim)
    tahmin_seti["tahmin"] = tahmin_et(final_model, tahmin_seti)

    print("\n=== Özellik önemi (final model) ===")
    onem = pd.Series(final_model.feature_importances_, index=OZELLIKLER)
    for ad, deger in onem.sort_values(ascending=False).items():
        print(f"  {ad:14s}: {deger}")

    # --- 3) Çıktı dosyası: talep_tahmini.xlsx ---
    cikti = tahmin_seti[["origin", "destination", "date", "tahmin"]].copy()
    cikti.columns = ["Çıkış Transfer Merkezi", "Varış Transfer Merkezi",
                     "Tarih", "Tahmin Edilen Desi"]
    cikti["Tahmin Edilen Desi"] = cikti["Tahmin Edilen Desi"].round(2)
    cikti = cikti.sort_values(["Tarih", "Çıkış Transfer Merkezi",
                               "Varış Transfer Merkezi"]).reset_index(drop=True)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    yol = OUTPUT_DIR / "talep_tahmini.xlsx"
    cikti.to_excel(yol, index=False)

    print("\n=== Çıktı ===")
    print(f"  Kaydedildi: {yol}")
    print(f"  {len(cikti)} satır | {cikti['Tarih'].nunique()} gün | "
          f"{cikti[['Çıkış Transfer Merkezi','Varış Transfer Merkezi']].drop_duplicates().shape[0]} güzergah")
    print(f"  11-17 Mayıs toplam tahmin edilen desi: "
          f"{cikti['Tahmin Edilen Desi'].sum():,.0f}")

    return cikti


if __name__ == "__main__":
    run_forecast()