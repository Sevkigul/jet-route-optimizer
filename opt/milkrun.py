"""Faz F: milk-run (cok duraklı ugrama, YENIDEN ELLECLEME YOK).

PDF acikca izin veriyor: "Kullanacağınız araçlara birçok farklı transfer
merkezine gidecek yükleri yükleyebilirsiniz." Yani TEK bir arac, ayni
cikistan farkli hedeflere sirayla ugrayip yuk birakabilir - hub_merge/
hub_split'teki gibi bir ARA MERKEZDE INDIR+YENIDEN YUKLE (2x ellecleme)
degil, her durakta sadece O DURAGA DUSEN yuk elleclenir (1x). Bu yuzden
milk-run, ayni ekonomik firsati (dusuk-dolu, ayni cikisli sevkiyatlari
birlestirmek) hub_split'ten daha ucuza yakalar.

Yontem: Clarke-Wright tasarruf algoritmasi (acik rota - arac donmez).
Referans: jet-route-optimizer reposunun mert-guncellemeler branch'inde
(takim arkadasi) aynen bu yontem denenmis ve dogrulanmis - biz kendi
mimarimize (ledger, chain_id, portions) uyarlıyoruz.

Basitlestirme (mert-guncellemeler'den alinmis, dogrulanmis bir karar):
milk-run rotalari Tir KULLANMAZ (sadece Kamyon/Hafif Kamyon/Kamyonet) -
boylece tir kapasitesi/durak-basi-tuketim karmasikligindan kacinilir.

Maliyet: organizasyon netlestirmesine gore "Kullanim Suresi" = TUM rota
boyunca gecen cikis elleclemesi (bir kez, tam yuk icin) + her bacagin
yolculuk suresi (toplam) + her duraktaki (sadece O DURAGA DUSEN yuk icin)
elleçleme suresi (toplam). Sureler dakikaya yuvarlanir, elleçleme
kapasitesi gece yarisini asarsa oranli bolunur. Clarke-Wright'in kendi
tasarruf SIRALAMASI (_direct_cost) ise sadece yolculuk+km uzerinden kalir -
zira elleçleme toplam miktari hangi rota secilirse secilsin ayni kaliyor,
sadece YOLCULUK maliyeti rotalar arasinda ayirt edici oluyor.
"""
import sys
from itertools import combinations
from pathlib import Path
from collections import defaultdict, Counter

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config_opt as cfg
from costs import (handling_duration_minutes, spot_cost, delay_hours_ceiled, sla_penalty,
                    travel_minutes, usage_hours)
from hub_merge import _fill_ratio

LOW_FILL_THRESHOLD = 0.90
MAX_STOPS = 12
REF_VEHICLE_TYPE = "Kamyon"
MILKRUN_VEHICLE_TYPES = [vt for vt in cfg.VEHICLE_TYPES if vt != cfg.TRUCK_CAP_VEHICLE_TYPE]


def _direct_travel_cost(o, d, vt, distance, vehicle_specs):
    """Sadece yolculuk+km uzerinden kaba maliyet - YALNIZCA Clarke-Wright'in
    tasarruf SIRALAMASI icin kullanilir (bkz. modul docstring'i)."""
    if (o, d) not in distance:
        return float("inf")
    km = distance[(o, d)]["mesafe_km"]
    travel_min = travel_minutes(distance[(o, d)]["duration"][vt])
    s = vehicle_specs[vt]
    return s["spot_hourly"] * (travel_min / 60.0) + s["spot_km"] * km


def clarke_wright(origin, dest_desi, distance, vehicle_specs, cap, max_stops=MAX_STOPS):
    """dest_desi: {destination: toplam_desi}. Acik milk-run rotalari dondurur:
    [[d1,d2,...], ...] - her biri ziyaret sirali hedef listesi."""
    dests = [d for d, L in dest_desi.items() if L > 1e-9]
    if len(dests) <= 1:
        return [dests] if dests else []

    load = dict(dest_desi)
    routes = {d: [d] for d in dests}
    rid = {d: d for d in dests}
    head = {d: d for d in dests}
    tail = {d: d for d in dests}

    savings = []
    for a in dests:
        for b in dests:
            if a == b or (origin, b) not in distance or (a, b) not in distance:
                continue
            s = _direct_travel_cost(origin, b, REF_VEHICLE_TYPE, distance, vehicle_specs) - \
                _direct_travel_cost(a, b, REF_VEHICLE_TYPE, distance, vehicle_specs)
            if s > 0:
                savings.append((s, a, b))
    savings.sort(reverse=True)

    for s, a, b in savings:
        ra, rb = rid[a], rid[b]
        if ra == rb or tail[ra] != a or head[rb] != b:
            continue
        if load[ra] + load[rb] > cap + 1e-6:
            continue
        if len(routes[ra]) + len(routes[rb]) > max_stops:
            continue
        routes[ra].extend(routes[rb])
        load[ra] += load[rb]
        tail[ra] = tail[rb]
        for d in routes[rb]:
            rid[d] = ra
        del routes[rb]
        del load[rb]

    return list(routes.values())


def _route_cost_for_type(origin, stops, dest_members, vt, distance, vehicle_specs):
    """Butun rota icin (tek arac tipiyle) TAM maliyet: Kullanim Suresi =
    cikis elleçleme (bir kez, tam yuk) + tum bacaklarin yolculuk sureleri
    toplami + her duraktaki (sadece o duraga dusen yuk icin) elleçleme
    sureleri toplami. Dondurur: (maliyet, toplam_km) veya (None, None)."""
    total_desi = sum(m["carried_desi"] for d in stops for m in dest_members[d])
    if total_desi > vehicle_specs[vt]["capacity"]:
        return None, None

    total_km = 0.0
    total_travel_min = 0
    prev = origin
    for d in stops:
        if (prev, d) not in distance:
            return None, None
        total_km += distance[(prev, d)]["mesafe_km"]
        total_travel_min += travel_minutes(distance[(prev, d)]["duration"][vt])
        prev = d

    outbound_handling = handling_duration_minutes(total_desi)
    total_drop_handling = sum(
        handling_duration_minutes(sum(m["carried_desi"] for m in dest_members[d]))
        for d in stops
    )
    usage_hrs = usage_hours(outbound_handling, total_travel_min, total_drop_handling)
    cost = spot_cost(vt, total_km, usage_hrs, vehicle_specs)
    return cost, total_km


def _cheapest_route_vehicle(origin, stops, dest_members, distance, vehicle_specs):
    best_vt, best_cost = None, float("inf")
    for vt in MILKRUN_VEHICLE_TYPES:
        cost, _ = _route_cost_for_type(origin, stops, dest_members, vt, distance, vehicle_specs)
        if cost is not None and cost < best_cost:
            best_cost, best_vt = cost, vt
    return best_vt, best_cost


def _try_build_route(origin, stops, dest_members, distance, vehicle_specs, ledger, demand_lookup):
    total_desi = sum(m["carried_desi"] for d in stops for m in dest_members[d])
    original_cost = sum(m["cost"] for d in stops for m in dest_members[d])

    vt, route_cost = _cheapest_route_vehicle(origin, stops, dest_members, distance, vehicle_specs)
    if vt is None or route_cost >= original_cost:
        return None

    all_members = [m for d in stops for m in dest_members[d]]
    depart_after = max(m["depart_at"] for m in all_members)
    outbound_handling = handling_duration_minutes(total_desi)
    depart = depart_after + pd.Timedelta(minutes=outbound_handling)

    ok_origin = ledger.try_consume_handling_timed(origin, depart_after, outbound_handling, total_desi)
    if not ok_origin:
        return None

    consumed = [(origin, depart_after, outbound_handling, total_desi)]
    legs = []
    t_cursor, prev = depart, origin
    ok = True
    sla_delta = 0.0

    for d in stops:
        travel_min = travel_minutes(distance[(prev, d)]["duration"][vt])
        km = distance[(prev, d)]["mesafe_km"]
        arrive = t_cursor + pd.Timedelta(minutes=travel_min)
        dropped = sum(m["carried_desi"] for m in dest_members[d])
        hm = handling_duration_minutes(dropped)

        if not ledger.try_consume_handling_timed(d, arrive, hm, dropped):
            ok = False
            break
        consumed.append((d, arrive, hm, dropped))

        done = arrive + pd.Timedelta(minutes=hm)

        drop_portions = [p for m in dest_members[d] for p in m["portions"]]
        legs.append({
            "vehicle_internal_id": None,   # asagida ortak grup id ile doldurulacak
            "vehicle_class": "Spot", "vehicle_type": vt,
            "origin": prev, "destination": d,
            "depart_at": t_cursor, "arrive_at": arrive, "travel_hours": travel_min / 60.0,
            "outbound_handling_min": outbound_handling if prev == origin else 0.0,
            "inbound_handling_min": hm,
            "delivered_at": done,
            "cost": 0.0,  # toplam rota maliyeti tek bacakta gosterilecek (asagida)
            "carried_desi": dropped, "portions": drop_portions,
            "_km": km,
        })

        for m in dest_members[d]:
            for talep_id, desi, _cid in m["portions"]:
                info = demand_lookup[talep_id]
                old_delay = delay_hours_ceiled(m["delivered_at"], info["deadline"])
                new_delay = delay_hours_ceiled(done, info["deadline"])
                sla_delta += sla_penalty(desi, new_delay) - sla_penalty(desi, old_delay)

        t_cursor, prev = done, d

    if not ok:
        for hub, start, dur, desi in consumed:
            ledger.release_handling_timed(hub, start, dur, desi)
        return None

    net_savings = (original_cost - route_cost) - sla_delta
    if net_savings <= 0:
        for hub, start, dur, desi in consumed:
            ledger.release_handling_timed(hub, start, dur, desi)
        return None

    # maliyeti ilk bacakta goster, digerlerinde 0 (cifte sayimi onlemek icin - output_opt'taki
    # "arac basina bir kez" mantigiyla tutarli, ama burada TEK arac oldugu icin biz kendimiz
    # dogrudan ilk bacaga yaziyoruz)
    legs[0]["cost"] = route_cost

    group_id = "MILK-" + "-".join(m["vehicle_internal_id"] for m in all_members)
    for leg in legs:
        leg["vehicle_internal_id"] = group_id
        leg["vehicle_group_id"] = group_id

    return {"net_savings": net_savings, "legs": legs, "members": all_members}


def run_milkrun(dispatches, distance, vehicle_specs, ledger, demand_lookup):
    if not cfg.CONSOLIDATION_ENABLED:
        return dispatches, 0.0, 0

    spot = [d for d in dispatches if d["vehicle_class"] == "Spot"]
    others = [d for d in dispatches if d["vehicle_class"] != "Spot"]

    groups = defaultdict(lambda: defaultdict(list))
    non_candidates = []
    for d in spot:
        if _fill_ratio(d, vehicle_specs) < LOW_FILL_THRESHOLD and d["vehicle_type"] != cfg.TRUCK_CAP_VEHICLE_TYPE:
            groups[(d["origin"], d["depart_at"].normalize())][d["destination"]].append(d)
        else:
            non_candidates.append(d)

    result = list(others) + non_candidates
    total_savings = 0.0
    n_applied = 0
    n_routes_with_stops = Counter()

    for (origin, day), dest_members in groups.items():
        dest_desi = {d: sum(m["carried_desi"] for m in members) for d, members in dest_members.items()}
        cap = max(vehicle_specs[vt]["capacity"] for vt in MILKRUN_VEHICLE_TYPES)
        routes = clarke_wright(origin, dest_desi, distance, vehicle_specs, cap)

        for stops in routes:
            if len(stops) < 2:
                for m in dest_members[stops[0]] if stops else []:
                    result.append(m)
                continue
            built = _try_build_route(origin, stops, dest_members, distance, vehicle_specs, ledger, demand_lookup)
            if built:
                result.extend(built["legs"])
                total_savings += built["net_savings"]
                n_applied += 1
                n_routes_with_stops[len(stops)] += 1
            else:
                for d in stops:
                    result.extend(dest_members[d])

    print(f"[milkrun] {n_applied} milk-run rotası oluşturuldu "
          f"(durak dağılımı: {dict(n_routes_with_stops)})")
    return result, total_savings, n_applied
