"""Araç maliyeti formülü ve SLA cezası hesabı."""
from __future__ import annotations

import math
from datetime import datetime

import pandas as pd

from . import config, utils


def tek_arac_maliyeti(
    desi: float,
    route_row: pd.Series,
    arac_tipi: str,
    kiralik_mi: bool,
    arac_tablosu: pd.DataFrame,
) -> tuple[float, int, int, int]:
    """Tek bir aracın (bu rotada, bu desi ile) maliyetini ve süre bileşenlerini döner.

    Döner: (toplam_maliyet_tl, yol_dk, cikis_ellecleme_dk, varis_ellecleme_dk)
    """
    satir = arac_tablosu.loc[arac_tipi]
    saatlik = satir["kiralik_saatlik_tl"] if kiralik_mi else satir["spot_saatlik_tl"]
    km_tl = satir["kiralik_km_tl"] if kiralik_mi else satir["spot_km_tl"]

    yol_dk = utils.saat_yukari_yuvarla_dk(route_row[f"sure_saat_{arac_tipi}"])
    cikis_dk = utils.ellecleme_dakika(desi)
    varis_dk = utils.ellecleme_dakika(desi)

    kullanim_saat = utils.dk_to_saat(yol_dk + cikis_dk + varis_dk)
    maliyet = saatlik * kullanim_saat + route_row["mesafe_km"] * km_tl
    return maliyet, yol_dk, cikis_dk, varis_dk


def sla_cezasi(
    varis_ellecleme_tamamlanma: datetime,
    sla_bitis_hedef: datetime,
    tasinan_desi: float,
) -> float:
    """Geciken Desi x ceil(gecikme_saat) x 0.4 TL (gecikme yoksa 0)."""
    fark_saat = (varis_ellecleme_tamamlanma - sla_bitis_hedef).total_seconds() / 3600.0
    if fark_saat <= 0:
        return 0.0
    gecikme_saat = math.ceil(fark_saat)
    return tasinan_desi * gecikme_saat * config.SLA_CEZA_TL_PER_DESI_PER_SAAT
