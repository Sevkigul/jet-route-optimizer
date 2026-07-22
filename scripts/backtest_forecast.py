"""Talep tahmini backtest'i — haric tutulan gun setleri ve halflife secimi.

MVP dersi: tek pencere dogrulama yaniltici olabilir; bu yuzden 3 ayri temiz
7-gunluk pencerede test edilir ve ORTALAMA WMAPE esas alinir.

Her pencere icin egitim = pencere baslangicindan onceki tum gunler; recency
agirligi referansi da pencere baslangicinin bir onceki gunudur (uretim
kosullarinin birebir taklidi). Gercek degerler, pencere icindeki
(hat x slot x gun) izgarasi uzerinde zero-fill ile olusturulur (kayit yok =
0 desi; uretimdeki varsayimla ayni).

Calistirma:  python scripts/backtest_forecast.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import data_loader as dl, demand_forecast as fc  # noqa: E402

# 3 dogrulama penceresi: verinin sonundaki temiz haftalar (28 Haziran kismi
# oldugu icin son pencere 21-27 Haziran'da biter; Kurban haftasi pencere
# DISINDA kalir, sadece egitim tarafinda etkisi olculur).
PENCERELER = [
    (pd.Timestamp("2026-06-21"), pd.Timestamp("2026-06-27")),
    (pd.Timestamp("2026-06-14"), pd.Timestamp("2026-06-20")),
    (pd.Timestamp("2026-06-07"), pd.Timestamp("2026-06-13")),
]

# Karsilastirilan haric-gun setleri. Kayit bagimsiz kalsin diye uretim
# sabitleri (fc.HARIC_GUNLER) yerine acik tarihlerle yazildi.
# Not: 28 Haziran tum pencerelerin egitim araliginin DISINDA kaldigi icin
# v0 ile v1 backtest'te ayni cikar; 28 Haziran'in gerekcesi backtest degil,
# veri kalitesidir (47 satir, normal gun ~370 - MVP'deki 10 Mayis'in aynisi).
_KISMI = [pd.Timestamp("2026-06-28")]
_KURBAN = list(pd.date_range("2026-05-27", "2026-06-01"))  # bayram + telafi patlamasi
_ARIFE = [pd.Timestamp("2026-05-26")]  # yarim gun: 193K desi, normal Sali ~1.1M
SETLER = {
    "v0 hicbiri (eski davranis)": [],
    "v1 sadece 28 Haziran": _KISMI,
    "v2 v1 + Kurban 27May-1Haz": _KISMI + _KURBAN,
    "v3 v2 + 26 Mayis (arife)": _KISMI + _KURBAN + _ARIFE,
}

HALFLIFELER = [2.0, 3.0, 4.0, 6.0]


def wmape(gercek: np.ndarray, tahmin: np.ndarray) -> float:
    return 100 * np.abs(gercek - tahmin).sum() / gercek.sum()


def pencere_wmape(daily: pd.DataFrame, bas: pd.Timestamp, son: pd.Timestamp,
                  haric: list, halflife: float) -> float:
    egitim = daily[(daily["tarih"] < bas) & (~daily["tarih"].isin(haric))]
    hedefler = list(pd.date_range(bas, son, freq="D"))
    pred = fc._weighted_weekday_forecast(
        egitim, hedefler, halflife, referans=bas - pd.Timedelta(days=1)
    )
    gercek = daily[(daily["tarih"] >= bas) & (daily["tarih"] <= son)]
    m = gercek.merge(
        pred, on=["cikis", "varis", "talep_tamamlanma_saati", "tarih"], how="left"
    )
    m["tahmin"] = m["tahmin"].fillna(0.0).clip(lower=0)
    return wmape(m["toplam_desi"].values, m["tahmin"].values)


def main() -> None:
    talep = dl.load_gecmis_talep()
    daily = fc._full_calendar_daily(talep)

    print("=== 1) Haric-gun seti karsilastirmasi (halflife=3.0) ===")
    print(f"{'set':<32}" + "".join(f"  P{i+1} ({b.strftime('%d.%m')}-{s.strftime('%d.%m')})" for i, (b, s) in enumerate(PENCERELER)) + "   ORT")
    for ad, haric in SETLER.items():
        skorlar = [pencere_wmape(daily, b, s, haric, 3.0) for b, s in PENCERELER]
        print(f"{ad:<32}" + "".join(f"  {sk:>16.2f}" for sk in skorlar) + f"  {np.mean(skorlar):6.2f}")

    print("\n=== 2) Halflife taramasi (kazanan haric-gun setiyle) ===")
    # 1. tablodan en dusuk ortalamali set ile calisir
    ort = {ad: np.mean([pencere_wmape(daily, b, s, h, 3.0) for b, s in PENCERELER])
           for ad, h in SETLER.items()}
    kazanan_ad = min(ort, key=ort.get)
    kazanan = SETLER[kazanan_ad]
    print(f"(set: {kazanan_ad})")
    for hl in HALFLIFELER:
        skorlar = [pencere_wmape(daily, b, s, kazanan, hl) for b, s in PENCERELER]
        print(f"halflife={hl:<4}" + "".join(f"  P{i+1}={sk:6.2f}" for i, sk in enumerate(skorlar)) + f"   ORT={np.mean(skorlar):6.2f}")


if __name__ == "__main__":
    main()
