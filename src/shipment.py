"""Milk-run modeli için basitleştirilmiş talep (Item) ve araç (Vehicle) veri yapıları.

v2'nin çok-bacaklı hub geçişi (lineage/hop-index) kaldırıldı: milk-run'da bir
talep en fazla TEK bir aracın rotasındaki TEK bir durağa düşer (o durakta
teslim edilir). Bir talep yalnızca KAPASİTE nedeniyle birden fazla araca
bölünürse (paralel bölünme) Talep ID sonek alır.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Item:
    idx: int
    talep_id: str
    cikis: str
    varis: str
    desi: float
    hazir: datetime
    sla_bitis_hedef: datetime
    pool_gun: date | None = None
    dispatch_hazir: datetime | None = None
    drops: list = field(default_factory=list)  # sim sırasında [(Vehicle, desi, durak_sira)],
    # id_finalize sonrası [(Vehicle, desi, durak_sira, nihai_talep_id)]


@dataclass
class Vehicle:
    vid: str = ""
    kiralik_mi: bool = False
    arac_tipi: str = ""
    route: list[str] = field(default_factory=list)  # [cikis, durak1, durak2, ...]
    cargo: list = field(default_factory=list)  # [(item_idx, desi, durak_sira)] durak_sira: 1..k
    leg_dep: list[datetime] = field(default_factory=list)
    leg_arr: list[datetime] = field(default_factory=list)
    leg_km: list[float] = field(default_factory=list)
    leg_travel_dk: list[int] = field(default_factory=list)
    leg_wait_dk: list[int] = field(default_factory=list)  # durakta indirilmeyi bekleme (kapasite ertelemesi)
    stop_unload_dk: list[int] = field(default_factory=list)
    stop_done: list[datetime] = field(default_factory=list)
    leg_cost: list[float] = field(default_factory=list)
    dep_handle_dk: int = 0
    handle_cost: float = 0.0
    wait_cost: float = 0.0
    cost: float = 0.0
    # spot Tir zincirleme: bu sefer, ayni fiziksel aracin onceki seferinin
    # devamiysa (ayni TM'de ayni gun yeniden yukleme) onceki Vehicle'a isaret
    # eder; Arac ID atamasinda ayni ID'yi devralir
    zincir_onceki: "Vehicle | None" = None


def id_finalize(items: list[Item]) -> None:
    """Her item için drops listesinin boyutuna göre nihai Talep ID'leri üretir.
    item.drops [(Vehicle, desi, durak_sira)] -> [(Vehicle, desi, durak_sira, nihai_id)]."""
    for item in items:
        gecerli = [d for d in item.drops if d[1] > 1e-9]
        if len(gecerli) <= 1:
            item.drops = [(v, desi, si, item.talep_id) for v, desi, si in gecerli]
        else:
            item.drops = [
                (v, desi, si, f"{item.talep_id}-{n}")
                for n, (v, desi, si) in enumerate(gecerli, start=1)
            ]
