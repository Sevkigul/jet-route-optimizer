"""Elleçleme suresi, kiralik/spot maliyet ve SLA cezasi - saf fonksiyonlar."""
import math
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config_opt as cfg


def handling_duration_minutes(batch_desi):
    """Bir aracin tum yukunun elleclenme suresi (dakika). Cikis ve varis icin
    ayri ayri cagrilir, her ikisi de aracin TOPLAM yukune gore hesaplanir."""
    return cfg.HANDLING_MIN_PER_DESI * batch_desi


def rental_cost(vehicle_type, distance_km, travel_hours, specs):
    s = specs[vehicle_type]
    return s["rental_hourly"] * travel_hours + s["rental_km"] * distance_km


def spot_cost(vehicle_type, distance_km, travel_hours, specs):
    s = specs[vehicle_type]
    return s["spot_hourly"] * travel_hours + s["spot_km"] * distance_km


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

    created = pd.Timestamp("2026-07-07 15:00:00")
    # PDF ornegi: 5000 desi, 15:00 varis, elleçleme 50 dk -> 15:50 elleçlenmis
    print("elleçleme suresi (5000 desi):", handling_duration_minutes(5000), "dk (beklenen 50.0)")

    deadline = sla_deadline(pd.Timestamp("2026-07-07 09:00:00"), 1)
    print("deadline (1 gun):", deadline, "(beklenen 2026-07-08 09:00:00)")

    delivered = pd.Timestamp("2026-07-08 10:00:00")
    dh = delay_hours_ceiled(delivered, deadline)
    print("gecikme saati:", dh, "(beklenen 1)")
    print("SLA cezasi (6000 desi, 1 saat):", sla_penalty(6000, dh), "TL (beklenen 2400.0)")

    delivered2 = pd.Timestamp("2026-07-08 11:21:00")
    dh2 = delay_hours_ceiled(delivered2, deadline)
    print("gecikme saati (2s21dk gecikme):", dh2, "(beklenen 3, PDF ornegi 2s20dk->3 ile tutarli)")
