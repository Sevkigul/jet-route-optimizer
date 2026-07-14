"""Elleçleme suresi, kiralik/spot maliyet ve SLA cezasi - saf fonksiyonlar.

Organizasyon Soru-Cevap netlestirmeleri (2026.07):
- Tum sureler (yolculuk VE elleçleme) dakikaya cevrilip YUKARI yuvarlanir.
  Ornek: 0.92 saat = 55.2 dk -> 56 dk.
- "Kullanim Suresi" (maliyet formulundeki), sadece yolculuk suresi DEGIL;
  cikis elleçlemesi + yolculuk + varis elleçlemesi + bekleme sürelerinin
  TOPLAMIdir. Resmi ornek: 10000 desi, 5 saatlik yol ->
  100 dk (cikis elleç.) + 300 dk (yol) + 100 dk (varis elleç.) = 500 dk.
"""
import math
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config_opt as cfg


def round_up_minutes(minutes):
    """Herhangi bir sureyi (dakika) en yakin BUYUK tam sayiya yuvarlar."""
    return math.ceil(minutes - 1e-9)


def travel_minutes(travel_hours):
    """Yolculuk suresini (saat) dakikaya cevirip yukari yuvarlar."""
    return round_up_minutes(travel_hours * 60.0)


def handling_duration_minutes(batch_desi):
    """Bir aracin tum yukunun elleclenme suresi (dakika), yukari yuvarlanmis
    tam sayi. Cikis ve varis icin ayri ayri cagrilir, her ikisi de aracin
    TOPLAM yukune gore hesaplanir."""
    return round_up_minutes(cfg.HANDLING_MIN_PER_DESI * batch_desi)


def usage_hours(outbound_handling_min, travel_min, inbound_handling_min, waiting_min=0.0):
    """Toplam 'Kullanim Suresi' (saat) = cikis elleçleme + yolculuk + varis
    elleçleme + bekleme (hepsi dakika olarak toplanir, sonra saate cevrilir).
    Tum bilesenler zaten yukari-yuvarlanmis tam dakika olarak verilmelidir."""
    return (outbound_handling_min + travel_min + inbound_handling_min + waiting_min) / 60.0


def rental_cost(vehicle_type, distance_km, usage_hrs, specs):
    s = specs[vehicle_type]
    return s["rental_hourly"] * usage_hrs + s["rental_km"] * distance_km


def spot_cost(vehicle_type, distance_km, usage_hrs, specs):
    s = specs[vehicle_type]
    return s["spot_hourly"] * usage_hrs + s["spot_km"] * distance_km


def sla_deadline(created_at, sla_gun):
    """SLA suresi hedef_teslim_gun gun cinsinden verilir, tam saat olarak (gun*24h) uygulanir."""
    return created_at + timedelta(hours=24 * sla_gun)


def delay_hours_ceiled(delivered_at, deadline):
    """Gecikme suresini saat bazinda, yukari yuvarlayarak hesaplar. Gecikme yoksa 0."""
    delta_hours = (delivered_at - deadline).total_seconds() / 3600.0
    if delta_hours <= 0:
        return 0
    return math.ceil(delta_hours - 1e-9)


def sla_penalty(desi, delay_hours):
    if delay_hours <= 0:
        return 0.0
    return desi * delay_hours * cfg.SLA_TL_PER_DESI_HOUR


if __name__ == "__main__":
    import pandas as pd

    # Resmi ornek (Soru-Cevap): 10000 desi, 5 saatlik yol, bekleme yok
    out_h = handling_duration_minutes(10000)
    trav_m = travel_minutes(5.0)
    in_h = handling_duration_minutes(10000)
    print(f"cikis elleçleme: {out_h} dk (beklenen 100)")
    print(f"yol suresi: {trav_m} dk (beklenen 300)")
    print(f"varis elleçleme: {in_h} dk (beklenen 100)")
    u = usage_hours(out_h, trav_m, in_h)
    print(f"kullanim suresi: {u*60:.0f} dk (beklenen 500)")

    # Resmi ornek 2: 1 saatlik bekleme eklenince
    u2 = usage_hours(out_h, trav_m, in_h, waiting_min=60)
    print(f"kullanim suresi (1s bekleme ile): {u2*60:.0f} dk (beklenen 560)")

    # Yuvarlama ornegi: 0.92 saat = 55.2 dk -> 56 dk
    print(f"\n0.92 saat -> {travel_minutes(0.92)} dk (beklenen 56)")

    deadline = sla_deadline(pd.Timestamp("2026-07-07 09:00:00"), 1)
    print("\ndeadline (1 gun):", deadline, "(beklenen 2026-07-08 09:00:00)")

    delivered = pd.Timestamp("2026-07-08 10:00:00")
    dh = delay_hours_ceiled(delivered, deadline)
    print("gecikme saati:", dh, "(beklenen 1)")
    print("SLA cezasi (6000 desi, 1 saat):", sla_penalty(6000, dh), "TL (beklenen 2400.0)")

    delivered2 = pd.Timestamp("2026-07-08 11:21:00")
    dh2 = delay_hours_ceiled(delivered2, deadline)
    print("gecikme saati (2s21dk gecikme):", dh2, "(beklenen 3, PDF ornegi 2s20dk->3 ile tutarli)")
