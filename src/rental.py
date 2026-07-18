"""Kiralik arac zorunlu gunluk sevkiyati.

Politika: kiralik filo, gunun 09:00 VE 17:00 dalgalarini birlikte (17:00
elleclemesi tamamlandiktan sonra tek seferde) tasir - "gec cikis" politikasi.

Gerekce: leftover_gec = max(0, toplam_talep - kapasite) her zaman
leftover_erken = max(0, talep_0900 - kapasite) + talep_1700'den kucuk esittir
(kanit: erken politikada 17:00 talebinin tamami spot'a kaliyor, gec politikada
ise sadece kapasiteyi asan kisim kaliyor). Yani gec politika spot ihtiyacini
hic bir zaman erken politikadan fazla yapmiyor, cogu zaman daha az yapiyor.
Tek bedeli: 09:00 yukunun ~8 saat gec cikmasi - bu gecikme genel SLA hesabina
(costs.sla_penalty) zaten dahil edildigi icin gozden kacmiyor, sadece cok
sıkı SLA'larda maliyetli olabilir (bu veri setinde SLA min 24 saat).
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config_opt as cfg
from costs import handling_duration_minutes, rental_cost, travel_minutes, usage_hours
from portions import consume_fifo


def build_rental_fleet(rentals):
    """route -> [vehicle_type, vehicle_type, ...] (her fiziksel arac icin bir eleman)."""
    fleet = {}
    for r in rentals:
        key = (r["origin"], r["destination"])
        fleet.setdefault(key, []).extend([r["vehicle_type"]] * int(r["count"]))
    return fleet


def dispatch_rentals_for_day(route, date, fleet_types, vehicle_specs, distance, portion_pool, ledger):
    """Bir rota + bir gun icin zorunlu kiralik arac sevkiyatini kurar.

    portion_pool: bu rotanin (henuz tuketilmemis) portion listesi - in-place guncellenir.
    Dondurur: dispatch kayitlari listesi (her fiziksel kiralik arac icin bir kayit).
    """
    if not fleet_types:
        return []

    origin, destination = route
    dist_info = distance[route]
    distance_km = dist_info["mesafe_km"]

    total_capacity = sum(vehicle_specs[vt]["capacity"] for vt in fleet_types)

    day_1700 = date + pd.Timedelta(hours=17)

    carried, _ = consume_fifo(portion_pool, total_capacity, cutoff_time=day_1700)
    carried_total = sum(desi for _, desi in carried)

    dispatches = []
    for idx, vt in enumerate(fleet_types):
        cap = vehicle_specs[vt]["capacity"]
        # ayni rotadaki kiralik araclara esit dolulukta pay dagit (eski repo mantigi)
        share = cap * (carried_total / total_capacity) if total_capacity > 0 else 0.0

        # elleçleme suresi HER ARACIN KENDI yukune gore hesaplanir (PDF: "bir arac
        # icerisinde tasinan tum gonderiler icin elleçleme ayni anda baslar/biter" -
        # yani sure o aracin kendi desisine bagli, diger araclarin yukune degil).
        # Tum sureler dakikaya yuvarlanir (organizasyon netlestirmesi).
        outbound_handling = handling_duration_minutes(share)
        depart_at = day_1700 + pd.Timedelta(minutes=outbound_handling)

        travel_hrs = dist_info["duration"][vt]
        travel_min = travel_minutes(travel_hrs)
        arrive_at = depart_at + pd.Timedelta(minutes=travel_min)
        inbound_handling = handling_duration_minutes(share)

        # Kullanim Suresi = cikis elleçleme + yolculuk + varis elleçleme (organizasyon netlestirmesi)
        usage_hrs = usage_hours(outbound_handling, travel_min, inbound_handling)
        cost = rental_cost(vt, distance_km, usage_hrs, vehicle_specs)

        # elleçleme kapasitesini kosulsuz tuket (zorunlu sevkiyat), gece yarisini
        # asarsa sureyle orantili olarak ilgili gunlere bolunur.
        ledger.force_consume_handling_timed(origin, day_1700, outbound_handling, share)
        ledger.force_consume_handling_timed(destination, arrive_at, inbound_handling, share)

        vehicle_id = f"R-{origin}-{destination}-{date.date()}-{idx}"

        # Tir kapasitesi: kiralik araclar da tuketir (organizasyon dogruladi),
        # zorunlu oldugundan kosulsuz (force) tuketilir.
        if vt == cfg.TRUCK_CAP_VEHICLE_TYPE:
            ledger.force_consume_truck(origin, depart_at.normalize(), vehicle_id)
            ledger.force_consume_truck(destination, arrive_at.normalize(), vehicle_id)

        dispatches.append({
            "vehicle_internal_id": vehicle_id,
            "vehicle_class": "Kiralık",
            "vehicle_type": vt,
            "origin": origin,
            "destination": destination,
            "depart_at": depart_at,
            "arrive_at": arrive_at,
            "travel_hours": travel_min / 60.0,
            "outbound_handling_min": outbound_handling,
            "inbound_handling_min": inbound_handling,
            "delivered_at": arrive_at + pd.Timedelta(minutes=inbound_handling),
            "cost": cost,
            "carried_desi": share,
            "portions": [],  # asagida route bazinda tum arac gruplarina paylastirilir
        })

    # tasinan portion'lari araclara (yaklasik) esit oranla dagit - talep ID izlenebilirligi icin
    _distribute_portions_to_vehicles(dispatches, carried)

    return dispatches


def _distribute_portions_to_vehicles(dispatches, carried):
    """carried=[(talep_id,desi),...] listesini dispatches'e (araclara) FIFO dagitir,
    her aracin carried_desi kapasitesini asmayacak sekilde.

    Her portion'a, tasiyan aracin kendi vehicle_internal_id'si "chain_id" olarak
    eklenir - bu, portion'un konsolidasyon/hub-merge sirasinda kac bacak
    degistirirse degistirsin hangi ORIJINAL sevkiyattan geldigini izler
    (output_opt.py bunu gercek bolunme ile cok-bacakli-tek-teslimat'i
    ayirt etmek icin kullanir).
    """
    remaining_capacity = {i: d["carried_desi"] for i, d in enumerate(dispatches)}
    v_idx = 0
    for talep_id, desi in carried:
        left = desi
        while left > 1e-9 and v_idx < len(dispatches):
            cap_left = remaining_capacity[v_idx]
            take = min(cap_left, left)
            if take > 1e-9:
                chain_id = dispatches[v_idx]["vehicle_internal_id"]
                dispatches[v_idx]["portions"].append((talep_id, take, chain_id))
                remaining_capacity[v_idx] -= take
                left -= take
            if remaining_capacity[v_idx] <= 1e-9:
                v_idx += 1
