"""Talep tahmini: (çıkış TM, varış TM, talep tamamlanma saati) bazında,
haftanın gününe göre üstel-azalan ağırlıklı (recency-weighted) ortalama.

Yöntem
------
Her (cikis, varis, saat) hattı için geçmiş veri, 2026-01-01 - 2026-06-28
takvimine göre sıfır-doldurularak günlük seriye çevrilir (bir günde talep
oluşmamışsa o gün 0 kabul edilir - gerçekleşmiş işlem verisi olduğu için).

Tahmin yapılacak her hedef tarih için, o tarihin haftanın günü (Pazartesi..
Pazar) ile eşleşen tüm geçmiş gözlemlerin ağırlıklı ortalaması alınır.
Ağırlık, veri sonu tarihine (2026-06-28) olan uzaklığa göre üstel azalır:

    weight = 0.5 ** (hafta_farki / HALFLIFE_WEEKS)

Bu; yoğun hatlarda yakın geçmişe (trend/mevsimsellik) daha çok ağırlık
verirken, seyrek hatlarda da elde ne varsa (eski gözlemler dahil, düşük
ağırlıkla) kullanarak sağlam bir tahmin üretir. Hiç veri görülmemiş
(cikis, varis) hattı için tahmin üretilmez.

Veri kalitesi: kismi gun (28 Haziran) ile Kurban Bayrami anomalisi
(27 Mayis - 1 Haziran) egitim disidir (HARIC_GUNLER, gerekce asagida ve
scripts/backtest_forecast.py cikti tablosunda).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config

HALFLIFE_WEEKS_DEFAULT = 3.0  # backtest ile doğrulandı: scripts/backtest_forecast.py
VERI_BASLANGIC = pd.Timestamp("2026-01-01")
VERI_BITIS = pd.Timestamp("2026-06-28")

# --- Veri kalitesi: egitime alinmayan gunler ---
# MVP'deki "10 Mayis bozuk gun disari" kararinin devami (bkz. main branch
# src/features.py BOZUK_GUN). Zero-fill takvimi bu gunleri sahte "0 talep"
# gozlemine cevirdigi icin sadece satir filtrelemek yetmez; gunler takvim
# izgarasindan tamamen cikarilir. Secim keyfi degil, 3 pencereli backtest ile
# dogrulandi: scripts/backtest_forecast.py
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
    forecast = _weighted_weekday_forecast(daily, hedef_tarihler, halflife_weeks)

    forecast["tahmin"] = forecast["tahmin"].round().clip(lower=0).astype(int)
    forecast = forecast.sort_values(
        ["tarih", "talep_tamamlanma_saati", "cikis", "varis"]
    ).reset_index(drop=True)
    forecast.insert(0, "talep_id", [f"D{i+1:05d}" for i in range(len(forecast))])
    forecast = forecast.rename(columns={"tahmin": "tahmin_desi"})
    return forecast[["talep_id", "tarih", "talep_tamamlanma_saati", "cikis", "varis", "tahmin_desi"]]
