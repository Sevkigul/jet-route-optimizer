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
(takim arkadasi) aynen bu yontem denenmis ve dogrulanmis (13,7M TL sonuc,
%75 ort. doluluk) - biz kendi mimarimize (ledger, chain_id, portions) uyarlıyoruz.

Basitlestirme (mert-guncellemeler'den alinmis, dogrulanmis bir karar):
milk-run rotalari Tir KULLANMAZ (sadece Kamyon/Hafif Kamyon/Kamyonet) -
boylece tir kapasitesi/durak-basi-tuketim karmasikligindan kacinilir.
"""
import sys
from itertools import combinations
from pathlib import Path
from collections import defaultdict, Counter

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config_opt as cfg
from costs import handling_duration_minutes, spot_cost, delay_hours_ceiled, sla_penalty
from hub_merge import _fill_ratio

LOW_FILL_THRESHOLD = 0.90
MAX_STOPS = 12
REF_VEHICLE_TYPE = "Kamyon"
MILKRUN_VEHICLE_TYPES = [vt for vt in cfg.VEHICLE_TYPES if vt != cfg.TRUCK_CAP_VEHICLE_TYPE]


def _direct_cost(o, d, vt, distance, vehicle_specs):
    if (o, d) not in distance:
        return float("inf")
    km = distance[(o, d)]["mesafe_km"]
    hours = distance[(o, d)]["duration"][vt]
    return spot_cost(vt, km, hours, vehicle_specs)


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
            s = _direct_cost(origin, b, REF_VEHICLE_TYPE, distance, vehicle_specs) - \
                _direct_cost(a, b, REF_VEHICLE_TYPE, distance, vehicle_specs)
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


def _cheapest_route_vehicle(origin, stops, total_desi, distance, vehicle_specs):
    best_vt, best_cost = None, float("inf")
    for vt in MILKRUN_VEHICLE_TYPES:
        if total_desi > vehicle_specs[vt]["capacity"]:
            continue
        cost, prev, ok = 0.0, origin, True
        for d in stops:
            if (prev, d) not in distance:
                ok = False
                break
            cost += _direct_cost(prev, d, vt, distance, vehicle_specs)
            prev = d
        if ok and cost < best_cost:
            best_cost, best_vt = cost, vt
    return best_vt, best_cost


def _try_build_route(origin, stops, dest_members, distance, vehicle_specs, ledger, demand_lookup):
    total_desi = sum(m["carried_desi"] for d in stops for m in dest_members[d])
    original_cost = sum(m["cost"] for d in stops for m in dest_members[d])

    vt, route_cost = _cheapest_route_vehicle(origin, stops, total_desi, distance, vehicle_specs)
    if vt is None or route_cost >= original_cost:
        return None

    all_members = [m for d in stops for m in dest_members[d]]
    depart_after = max(m["depart_at"] for m in all_members)
    outbound_handling = handling_duration_minutes(total_desi)
    depart = depart_after + pd.Timedelta(minutes=outbound_handling)

    origin_today = depart_after.normalize()
    if not ledger.try_consume_handling(origin, origin_today, total_desi):
        return None

    consumed = [(origin, origin_today, total_desi)]
    legs = []
    t_cursor, prev = depart, origin
    ok = True
    sla_delta = 0.0

    for d in stops:
        hours = distance[(prev, d)]["duration"][vt]
        km = distance[(prev, d)]["mesafe_km"]
        arrive = t_cursor + pd.Timedelta(hours=hours)
        dropped = sum(m["carried_desi"] for m in dest_members[d])
        drop_today = arrive.normalize()

        if not ledger.try_consume_handling(d, drop_today, dropped):
            ok = False
            break
        consumed.append((d, drop_today, dropped))

        hm = handling_duration_minutes(dropped)
        done = arrive + pd.Timedelta(minutes=hm)

        drop_portions = [p for m in dest_members[d] for p in m["portions"]]
        legs.append({
            "vehicle_internal_id": None,   # asagida ortak grup id ile doldurulacak
            "vehicle_class": "Spot", "vehicle_type": vt,
            "origin": prev, "destination": d,
            "depart_at": t_cursor, "arrive_at": arrive, "travel_hours": hours,
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
        for hub, day, desi in consumed:
            ledger.release_handling(hub, day, desi)
        return None

    net_savings = (original_cost - route_cost) - sla_delta
    if net_savings <= 0:
        for hub, day, desi in consumed:
            ledger.release_handling(hub, day, desi)
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
