"""Milk-run (çok duraklı uğrama) rota üretimi — Clarke-Wright tasarruf algoritması.

Aynı çıkış merkezinden aynı gün havuzunda farklı hedeflere giden ince yükler,
tek bir spot aracın sırayla uğradığı bir rotada birleştirilir:
o -> d1 -> d2 -> ... -> dk (açık rota, araç geri dönmez).

Şartname: "araçlara birçok farklı transfer merkezine gidecek yükleri
yükleyebilirsiniz" (spot serbest, kiralık uğrayamaz). Uğramada araçta kalan
yük YENİDEN ELLEÇLENMEZ — bu yüzden hub konsolidasyonunun aksine ekstra
elleçleme kapasitesi tüketmez; sadece indirilen yük elleçlenir (bkz. optimizer.py).

Tasarruf (açık rota): s(a,b) = maliyet(o,b) - maliyet(a,b) — b'yi doğrudan
o'dan göndermek yerine a'nın peşine eklemenin kazancı.
"""
from __future__ import annotations

import pandas as pd

from . import vehicle_costing as vc

REFERANS_TIP = "Kamyon"


def _maliyet_tahmini(a: str, b: str, mesafe_idx: pd.DataFrame, arac_tablosu, kapasite: float) -> float:
    """Sıralama amaçlı kaba maliyet tahmini (tam yük varsayımıyla, referans tip)."""
    route_row = mesafe_idx.loc[(a, b)]
    maliyet, *_ = vc.tek_arac_maliyeti(kapasite, route_row, REFERANS_TIP, False, arac_tablosu)
    return maliyet


def clarke_wright(
    o: str,
    dest_load: dict[str, float],
    mesafe_idx: pd.DataFrame,
    arac_tablosu,
    kapasite: float,
    max_durak: int,
    uygun_mu=None,
) -> list[list[str]]:
    """dest_load: {varis: desi}. Kapasite/durak sınırını aşmayan açık milk-run
    rotaları döndürür: [[d1,d2,...], ...] (her rota ziyaret sırasına göre).

    uygun_mu: opsiyonel geri çağırım; sıralı durak listesi alır, rota tüm
    duraklara SLA içinde teslim edebiliyorsa True döner. Verilirse hem
    birleştirme hem 2-opt adımları yalnız uygun rotaları kabul eder — böylece
    km tasarrufu hiçbir durağı geciktiremez (ceza kaldıraç değildir ilkesi)."""
    dests = [d for d, desi in dest_load.items() if desi > 1e-9]
    if not dests:
        return []
    if len(dests) == 1:
        return [dests]

    def ec(a, b):
        return _maliyet_tahmini(a, b, mesafe_idx, arac_tablosu, kapasite)

    routes = {d: [d] for d in dests}
    load = {d: dest_load[d] for d in dests}
    rid = {d: d for d in dests}
    head = {d: d for d in dests}
    tail = {d: d for d in dests}

    savings = []
    for a in dests:
        for b in dests:
            if a == b:
                continue
            s = ec(o, b) - ec(a, b)
            if s > 0:
                savings.append((s, a, b))
    savings.sort(reverse=True)

    for s, a, b in savings:
        ra, rb = rid[a], rid[b]
        if ra == rb:
            continue
        if tail[ra] != a or head[rb] != b:
            continue
        if load[ra] + load[rb] > kapasite + 1e-6:
            continue
        if len(routes[ra]) + len(routes[rb]) > max_durak:
            continue
        if uygun_mu is not None and not uygun_mu(routes[ra] + routes[rb]):
            continue
        routes[ra].extend(routes[rb])
        load[ra] += load[rb]
        tail[ra] = tail[rb]
        for d in routes[rb]:
            rid[d] = ra
        del routes[rb]
        del load[rb]

    return [_two_opt(o, r, mesafe_idx, uygun_mu) for r in routes.values()]


def yerel_arama(
    o: str,
    routes: list[list[str]],
    dest_load: dict[str, float],
    mesafe_idx: pd.DataFrame,
    kapasite: float,
    max_durak: int,
    uygun_mu,
    rota_maliyet,
) -> list[list[str]]:
    """Clarke-Wright çözümünü yerel arama ile cilalar.

    İki hamle türü, iyileşme kalmayana dek döner:
      1. Rota birleştirme: iki rota tek araçta daha ucuzsa birleştirilir
         (her iki sıra denenir, kazanan 2-opt ile kısaltılır).
      2. Durak taşıma (relocate): bir durak başka rotanın en iyi pozisyonuna
         taşınınca toplam maliyet düşüyorsa taşınır.
    Her aday önce uygun_mu (SLA + kapasite-erteleme zamanlaması) süzgecinden
    geçer — cila hiçbir durağı geciktiremez. Maliyet ölçüsü rota_maliyet
    geri çağırımıdır (o rotayı taşıyabilecek en ucuz spot tipin maliyeti).
    """
    routes = [list(r) for r in routes]
    if len(routes) <= 1:
        return routes

    degisti, tur = True, 0
    while degisti and tur < 30:
        degisti, tur = False, tur + 1
        # 1) rota birlestirme
        for i in range(len(routes)):
            for j in range(i + 1, len(routes)):
                r1, r2 = routes[i], routes[j]
                if len(r1) + len(r2) > max_durak:
                    continue
                if sum(dest_load[d] for d in r1 + r2) > kapasite + 1e-6:
                    continue
                adaylar = [c for c in (r1 + r2, r2 + r1) if uygun_mu(c)]
                if not adaylar:
                    continue
                aday = min((_two_opt(o, c, mesafe_idx, uygun_mu) for c in adaylar), key=rota_maliyet)
                if rota_maliyet(aday) < rota_maliyet(r1) + rota_maliyet(r2) - 1e-6:
                    routes[i] = aday
                    routes.pop(j)
                    degisti = True
                    break
            if degisti:
                break
        if degisti:
            continue
        # 2) durak tasima (relocate)
        for i in range(len(routes)):
            if len(routes[i]) == 1:
                continue
            for d in list(routes[i]):
                kaynak_yeni = [x for x in routes[i] if x != d]
                if not uygun_mu(kaynak_yeni):
                    continue
                for j in range(len(routes)):
                    if i == j or len(routes[j]) + 1 > max_durak:
                        continue
                    if sum(dest_load[x] for x in routes[j]) + dest_load[d] > kapasite + 1e-6:
                        continue
                    en_iyi = None
                    for poz in range(len(routes[j]) + 1):
                        aday = routes[j][:poz] + [d] + routes[j][poz:]
                        if uygun_mu(aday):
                            m = rota_maliyet(aday)
                            if en_iyi is None or m < en_iyi[0]:
                                en_iyi = (m, aday)
                    if en_iyi is None:
                        continue
                    eski = rota_maliyet(routes[i]) + rota_maliyet(routes[j])
                    if rota_maliyet(kaynak_yeni) + en_iyi[0] < eski - 1e-6:
                        routes[i] = kaynak_yeni
                        routes[j] = en_iyi[1]
                        degisti = True
                        break
                if degisti:
                    break
            if degisti:
                break

    return routes


def _two_opt(o: str, stops: list[str], mesafe_idx: pd.DataFrame, uygun_mu=None) -> list[str]:
    """Açık rota (o'dan başlar, dönmez) durak sırasını 2-opt ile km açısından
    kısaltır. uygun_mu verilirse yalnız SLA-uyumlu sıralar kabul edilir
    (başlangıç sırası uygun olduğundan uygunluk korunur)."""
    if len(stops) <= 2:
        return stops
    seq = list(stops)

    def rota_km(sq):
        prev, toplam = o, 0.0
        for s in sq:
            toplam += mesafe_idx.loc[(prev, s), "mesafe_km"]
            prev = s
        return toplam

    best = rota_km(seq)
    improved = True
    while improved:
        improved = False
        for i in range(len(seq) - 1):
            for j in range(i + 1, len(seq)):
                aday = seq[:i] + seq[i:j + 1][::-1] + seq[j + 1:]
                c = rota_km(aday)
                if c < best - 1e-6 and (uygun_mu is None or uygun_mu(aday)):
                    seq, best, improved = aday, c, True
    return seq
