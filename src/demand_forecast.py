"""Talep tahmini: iki bağımsız yöntemin harmanı (ensemble).

Bileşen 1 — ağırlıklı hafta-günü ortalaması (`_weighted_weekday_forecast`)
    Her (cikis, varis, saat) hattı için geçmiş veri 2026-01-01 - 2026-06-28
    takvimine göre sıfır-doldurulur (kayıt yok = 0 desi). Her hedef tarih için,
    aynı haftanın gününe (Pzt..Paz) denk gelen geçmiş gözlemlerin ağırlıklı
    ortalaması alınır; ağırlık veri sonuna uzaklığa göre üstel azalır:
        weight = 0.5 ** (hafta_farki / HALFLIFE_WEEKS)
    Kısmi gün ve bayram anomalisi eğitim dışıdır (HARIC_GUNLER). Bu bileşen
    haftalık mevsimsel yapıyı güçlü yakalar.

Bileşen 2 — sağlam mevsimsel lag topluluğu (`_lag_ensemble_forecast`)
    Her (hat, saat, gün) için son 4 haftanın aynı-gün değeri (lag 7/14/21/28);
    tahmin = 0.5*(min atılmış ortalama) + 0.5*(medyan). Min atma bayram/kampanya
    çukurunu, medyan ani sıçramayı otomatik yumuşatır. Ufuk <= 7 gün olduğundan
    tüm lag'ler gözlemli veridir (sızıntı yok). Bu bileşen güncel seviyeyi
    (trend) daha iyi yakalar.

Nihai tahmin = BLEND_WEIGHT * bileşen1 + (1 - BLEND_WEIGHT) * bileşen2.
İki yöntem farklı hatalar yaptığından harman ikisinden de düşük WMAPE verir
(3 pencereli backtest: ağırlıklı %21.37, lag-topluluğu %23.27, harman (0.85)
%21.09). Hiç veri görülmemiş hat için tahmin üretilmez.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config

HALFLIFE_WEEKS_DEFAULT = 3.0  # 3 pencereli backtest ile doğrulandı (notebook §3)
VERI_BASLANGIC = pd.Timestamp("2026-01-01")
VERI_BITIS = pd.Timestamp("2026-06-28")

# --- Harman (ensemble) ---
LAG_ENSEMBLE_LAGS = (7, 14, 21, 28)  # son 4 haftanin ayni gunu
BLEND_WEIGHT = 0.85  # nihai = 0.85*agirlikli + 0.15*lag-toplulugu. Bu, WMAPE
# kazancini korurken (saf %21.37 -> harman %21.09) optimizasyonda SLA cezasini
# SIFIR tutan en yuksek harman agirligidir (0.80'den itibaren ceza doguyor).

# --- Veri kalitesi: egitime alinmayan gunler ---
# Zero-fill takvimi bu gunleri sahte "0 talep" gozlemine cevirdigi icin sadece
# satir filtrelemek yetmez; gunler takvim izgarasindan tamamen cikarilir.
# Secim 3 pencereli backtest ile dogrulandi (notebook §3).
KISMI_GUNLER = [pd.Timestamp("2026-06-28")]  # kismi gun: 47 satir (normal gun ~370)
TATIL_ANOMALI_GUNLER = list(pd.date_range("2026-05-26", "2026-06-01"))
# 26 Mayis arife (yarim gun, 193K desi) + 27-31 Mayis Kurban Bayrami (talep
# ~sifir) + 1 Haziran telafi patlamasi (2.64M desi, normal gunun ~3 kati).
# Hedef haftada (29 Haz - 5 Tem) tatil yok; bu gunler haftalik ortalamada
# sistematik yanlilik yaratiyor. Backtest (3 pencere ort. WMAPE):
# haric yok %23.11 -> bu setle %21.37.
HARIC_GUNLER = KISMI_GUNLER + TATIL_ANOMALI_GUNLER


def _full_calendar_daily(talep: pd.DataFrame) -> pd.DataFrame:
    """(cikis, varis, saat, tarih) tam takvim ızgarası; olmayan günler 0 desi."""
    tum_gunler = pd.date_range(VERI_BASLANGIC, VERI_BITIS, freq="D")
    hatlar = talep[["cikis", "varis", "talep_tamamlanma_saati"]].drop_duplicates()

    grid = hatlar.merge(pd.DataFrame({"tarih": tum_gunler}), how="cross")
    gercek = (
        talep.groupby(["cikis", "varis", "talep_tamamlanma_saati", "tarih"])["toplam_desi"]
        .sum()
        .reset_index()
    )
    merged = grid.merge(
        gercek, on=["cikis", "varis", "talep_tamamlanma_saati", "tarih"], how="left"
    )
    merged["toplam_desi"] = merged["toplam_desi"].fillna(0.0)
    return merged


def _weighted_weekday_forecast(
    daily: pd.DataFrame,
    hedef_tarihler: list[pd.Timestamp],
    halflife_weeks: float,
    referans: pd.Timestamp = VERI_BITIS,
) -> pd.DataFrame:
    """referans: recency agirliginin sifir noktasi (uretimde veri sonu;
    backtest'te egitim penceresinin sonu — aksi halde backtest gercekci olmaz)."""
    daily = daily.copy()
    daily["dow"] = daily["tarih"].dt.dayofweek
    daily["hafta_farki"] = (referans - daily["tarih"]).dt.days / 7.0
    daily["weight"] = 0.5 ** (daily["hafta_farki"] / halflife_weeks)
    daily["w_val"] = daily["weight"] * daily["toplam_desi"]

    agg = (
        daily.groupby(["cikis", "varis", "talep_tamamlanma_saati", "dow"])
        .agg(w_val_sum=("w_val", "sum"), w_sum=("weight", "sum"))
        .reset_index()
    )
    agg["tahmin"] = agg["w_val_sum"] / agg["w_sum"]

    hedef_df = pd.DataFrame({"tarih": hedef_tarihler})
    hedef_df["dow"] = hedef_df["tarih"].dt.dayofweek

    out = agg.merge(hedef_df, on="dow", how="inner")
    return out[["cikis", "varis", "talep_tamamlanma_saati", "tarih", "tahmin"]]


def _lag_ensemble_forecast(
    talep: pd.DataFrame, hedef_tarihler: list[pd.Timestamp]
) -> pd.DataFrame:
    """Sağlam mevsimsel lag topluluğu (harmanın 2. bileşeni).

    Izgara ufka kadar uzatılır (lag'lerin takvimle hizalanması için); geçmiş
    günler sıfır-doldurulur. Her hedef hücre için lag 7/14/21/28 alınıp
    0.5*(min atılmış ortalama) + 0.5*(medyan) hesaplanır.
    """
    son = max(hedef_tarihler)
    tum_gunler = pd.date_range(VERI_BASLANGIC, son, freq="D")
    hatlar = talep[["cikis", "varis", "talep_tamamlanma_saati"]].drop_duplicates()
    grid = hatlar.merge(pd.DataFrame({"tarih": tum_gunler}), how="cross")
    gercek = (
        talep.groupby(["cikis", "varis", "talep_tamamlanma_saati", "tarih"])["toplam_desi"]
        .sum().reset_index()
    )
    df = grid.merge(gercek, on=["cikis", "varis", "talep_tamamlanma_saati", "tarih"], how="left")
    gecmis = df["tarih"] <= VERI_BITIS
    df.loc[gecmis, "toplam_desi"] = df.loc[gecmis, "toplam_desi"].fillna(0.0)
    df = df.sort_values(["cikis", "varis", "talep_tamamlanma_saati", "tarih"]).reset_index(drop=True)

    g = df.groupby(["cikis", "varis", "talep_tamamlanma_saati"])["toplam_desi"]
    lag_kolon = [f"lag{l}" for l in LAG_ENSEMBLE_LAGS]
    for l, k in zip(LAG_ENSEMBLE_LAGS, lag_kolon):
        df[k] = g.shift(l)

    hedef = df[df["tarih"].isin(hedef_tarihler) & df["lag28"].notna()].copy()
    L = hedef[lag_kolon].to_numpy()
    dropmin = (L.sum(axis=1) - L.min(axis=1)) / (L.shape[1] - 1)
    medyan = np.median(L, axis=1)
    hedef["tahmin"] = np.clip(0.5 * dropmin + 0.5 * medyan, 0, None)
    return hedef[["cikis", "varis", "talep_tamamlanma_saati", "tarih", "tahmin"]]


def forecast_all(
    talep: pd.DataFrame,
    baslangic: pd.Timestamp = config.TAHMIN_BASLANGIC,
    bitis: pd.Timestamp = config.TAHMIN_BITIS,
    halflife_weeks: float = HALFLIFE_WEEKS_DEFAULT,
    haric_gunler: list | None = None,
) -> pd.DataFrame:
    """Her (cikis, varis, saat) x hedef tarih için tahmin üretir.

    haric_gunler: egitim disi birakilacak tarihler (None -> HARIC_GUNLER).
    Dönen DataFrame kolonları: talep_id, tarih, talep_tamamlanma_saati,
    cikis, varis, tahmin_desi
    """
    if haric_gunler is None:
        haric_gunler = HARIC_GUNLER
    baslangic = pd.Timestamp(baslangic).normalize()
    bitis = pd.Timestamp(bitis).normalize()
    hedef_tarihler = list(pd.date_range(baslangic, bitis, freq="D"))

    daily = _full_calendar_daily(talep)
    daily = daily[~daily["tarih"].isin(haric_gunler)]
    agirlikli = _weighted_weekday_forecast(daily, hedef_tarihler, halflife_weeks)
    lag_ens = _lag_ensemble_forecast(talep, hedef_tarihler)

    forecast = agirlikli.merge(
        lag_ens.rename(columns={"tahmin": "tahmin_lag"}),
        on=["cikis", "varis", "talep_tamamlanma_saati", "tarih"], how="left",
    )
    # lag toplulugu uretmediyse (hatta 28 gunden az gecmis) agirlikli tahmine dus
    forecast["tahmin_lag"] = forecast["tahmin_lag"].fillna(forecast["tahmin"])
    forecast["tahmin"] = (
        BLEND_WEIGHT * forecast["tahmin"] + (1 - BLEND_WEIGHT) * forecast["tahmin_lag"]
    )

    forecast["tahmin"] = forecast["tahmin"].round().clip(lower=0).astype(int)
    forecast = forecast.sort_values(
        ["tarih", "talep_tamamlanma_saati", "cikis", "varis"]
    ).reset_index(drop=True)
    forecast.insert(0, "talep_id", [f"D{i+1:05d}" for i in range(len(forecast))])
    forecast = forecast.rename(columns={"tahmin": "tahmin_desi"})
    return forecast[["talep_id", "tarih", "talep_tamamlanma_saati", "cikis", "varis", "tahmin_desi"]]
