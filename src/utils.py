"""Zaman ve yuvarlama hesaplamaları için merkezi, saf fonksiyonlar.

optimizer.py ve vehicle_costing.py bu fonksiyonları kullanır; kendi
yuvarlama/gün-bölme mantıklarını tekrar yazmazlar.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from . import config


def ellecleme_dakika(desi: float) -> int:
    """desi * 0.01 dk/desi, dakikaya YUKARI yuvarlanmış."""
    if desi <= 0:
        return 0
    return math.ceil(desi * config.ELLECLEME_DK_PER_DESI)


def saat_yukari_yuvarla_dk(saat: float) -> int:
    """Ondalık saat (yol süresi) -> yukarı yuvarlanmış tam dakika."""
    return math.ceil(saat * 60)


def dk_to_saat(dakika: float) -> float:
    return dakika / 60.0


def zaman_ekle(baslangic: datetime, dakika: float) -> datetime:
    return baslangic + timedelta(minutes=dakika)


def gun_gun_dagit(baslangic: datetime, sure_dk: int) -> list[tuple[date, int]]:
    """(baslangic, baslangic+sure_dk) aralığını takvim günlerine böler.

    Örn: 23:30 başlayıp 100dk süren işlem -> [(gün1, 30), (gün2, 70)].
    sure_dk=0 ise boş liste döner (elleçlenecek bir şey yok demektir).
    """
    parcalar: list[tuple[date, int]] = []
    kalan = sure_dk
    an = baslangic
    while kalan > 0:
        gun_sonu = datetime.combine(an.date() + timedelta(days=1), datetime.min.time())
        bu_gun_dk = min(kalan, int((gun_sonu - an).total_seconds() // 60))
        if bu_gun_dk <= 0:
            # an tam gece yarısındaysa bir sonraki tam güne geç
            an = gun_sonu
            gun_sonu = datetime.combine(an.date() + timedelta(days=1), datetime.min.time())
            bu_gun_dk = min(kalan, int((gun_sonu - an).total_seconds() // 60))
        parcalar.append((an.date(), bu_gun_dk))
        kalan -= bu_gun_dk
        an = gun_sonu
    return parcalar


def saat_str_to_time(saat_str: str) -> tuple[int, int]:
    hh, mm = saat_str.split(":")
    return int(hh), int(mm)
