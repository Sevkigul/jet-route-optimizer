"""Talep tahmini - LightGBM modeli, çoklu katman doğrulama ve 11-17 Mayıs tahmini."""
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb
from catboost import CatBoostRegressor

from data_loader import load_data
from features import build_panel, add_features, TAHMIN_BASLANGIC, TAHMIN_SON

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs"

# Modelin kullanacaği özellikler
KATEGORIK = ["origin", "destination"]
SAYISAL = ["dayofweek", "is_weekend", "month", "day", "is_holiday",
           "lag_7", "lag_14", "lag_21",
           "roll_mean_7", "roll_mean_28", "roll_std_7"]
OZELLIKLER = KATEGORIK + SAYISAL

# Parametreler, çoklu zaman-katmani doğrulamasinin ORTALAMA WMAPE'sine göre
# seçildi (tek pencereye uyum yerine, genellemesi sağlam config).
# Tweedie: sifir-yogun, saga carpik talep dagilimi icin (log1p'den daha isabetli,
# coklu katman dogrulamada ortalama WMAPE ~47 -> ~27)
LGB_PARAMS = {
    "objective": "tweedie",
    "tweedie_variance_power": 1.25,
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


CAT_PARAMS = {
    "loss_function": "Tweedie:variance_power=1.25",
    "depth": 5,
    "learning_rate": 0.03,
    "iterations": 300,
    "random_seed": 42,
    "verbose": 0,
    "allow_writing_files": False,  # catboost_info/ log klasoru olusturulmasin
}


def egit(train_df):
    """LightGBM + CatBoost ikilisini (tweedie hedef) eğitir.

    İki bagimsiz kütüphanenin ortalamasi hata çeşitliligini azaltir;
    çoklu katman dogrulamada tekil modellerden daha iyi sonuç verir.
    """
    y = train_df["desi"].values
    m_lgb = lgb.LGBMRegressor(**LGB_PARAMS)
    m_lgb.fit(_hazirla(train_df)[OZELLIKLER], y)
    m_cat = CatBoostRegressor(**CAT_PARAMS, cat_features=KATEGORIK)
    m_cat.fit(train_df[OZELLIKLER], y)
    return m_lgb, m_cat


def tahmin_et(model, df):
    """İki modelin ortalama tahminini üretir, negatif değerleri kirpar."""
    m_lgb, m_cat = model
    p = (m_lgb.predict(_hazirla(df)[OZELLIKLER]) +
         m_cat.predict(df[OZELLIKLER])) / 2
    return np.clip(p, 0, None)


def metrikler(gercek, tahmin):
    """MAE, RMSE ve WMAPE (ağirlikli yüzde hata) hesaplar."""
    gercek = np.asarray(gercek, dtype=float)
    tahmin = np.asarray(tahmin, dtype=float)
    mae = np.mean(np.abs(gercek - tahmin))
    rmse = np.sqrt(np.mean((gercek - tahmin) ** 2))
    wmape = 100 * np.sum(np.abs(gercek - tahmin)) / np.sum(gercek)
    return {"MAE": mae, "RMSE": rmse, "WMAPE_%": wmape}


def dogrula(egitim, katman=DOGRULAMA_KATMAN):
    """Çoklu zaman-katmani doğrulama: son N adet 7-günlük pencerede test eder.

    Tek pencere yaniltici olabilir; ortalama WMAPE gerçekçi başari ölçüsüdür.
    """
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
    onem = pd.Series(final_model[0].feature_importances_, index=OZELLIKLER)
    for ad, deger in onem.sort_values(ascending=False).items():
        print(f"  {ad:14s}: {deger}")

    # --- 3) Çıktı dosyası: talep_tahmini.xlsx ---
    # Resmi teslim formati: Tarih | Çıkış TM | Varış TM | Tahmin Edilen Desi
    cikti = tahmin_seti[["date", "origin", "destination", "tahmin"]].copy()
    cikti.columns = ["Tarih", "Çıkış TM", "Varış TM", "Tahmin Edilen Desi"]
    cikti["Tarih"] = cikti["Tarih"].dt.strftime("%Y-%m-%d") 
    cikti["Tahmin Edilen Desi"] = cikti["Tahmin Edilen Desi"].round(2)
    cikti = cikti.sort_values(["Tarih", "Çıkış TM",
                               "Varış TM"]).reset_index(drop=True)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    yol = OUTPUT_DIR / "talep_tahmini.xlsx"
    cikti.to_excel(yol, index=False)

    print("\n=== Çıktı ===")
    print(f"  Kaydedildi: {yol}")
    print(f"  {len(cikti)} satır | {cikti['Tarih'].nunique()} gün | "
          f"{cikti[['Çıkış TM','Varış TM']].drop_duplicates().shape[0]} güzergah")
    print(f"  11-17 Mayıs toplam tahmin edilen desi: "
          f"{cikti['Tahmin Edilen Desi'].sum():,.0f}")

    return cikti


if __name__ == "__main__":
    run_forecast()