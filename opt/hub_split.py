"""Faz D: 'outbound' hub birlestirme - hub_merge.py'nin ayna gorunumu.

hub_merge.py, AYNI HEDEFE giden FARKLI cikisli dusuk-dolu sevkiyatlari
ortak bir hub'da birlestirip TEK bir son bacakla devam ettiriyordu (N
sevkiyat -> N kendi leg1 + 1 ortak leg2).

Bu modul ise AYNI CIKISTAN FARKLI HEDEFLERE giden dusuk-dolu sevkiyatlari
ortak bir hub'a kadar TEK (birlesik, daha dolu) bir aracla tasiyip, hub'dan
itibaren her biri kendi hedefine kendi araciyla devam ediyor (N sevkiyat ->
1 ortak leg1 + N kendi leg2). Ayni ekonomik mantik, ters yonde.
"""
import sys
from itertools import combinations
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config_opt as cfg
from costs import handling_duration_minutes, spot_cost, delay_hours_ceiled, sla_penalty
from hub_merge import _cheapest_single_vehicle, _fill_ratio

LOW_FILL_THRESHOLD = 0.90
MAX_DETOUR_RATIO = 2.5
MAX_GROUP_SIZE = 25
GROUP_SIZES = (2, 3, 4)   # kucukten buyuge (hub_merge'de dogrulanan sira)


def _try_group_split(members, origin, distance, vehicle_specs, ledger, demand_lookup, hubs):
    """members: ayni cikistan (origin), ayni gun, FARKLI hedeflere giden 2+
    dusuk-dolu Spot dispatch'i. Ortak bir hub_m'ye kadar TEK (birlesik) arac
    ile gidilir, hub_m'den itibaren her uye kendi hedefine kendi araciyla
    devam eder."""
    total_desi = sum(m["carried_desi"] for m in members)
    original_cost = sum(m["cost"] for m in members)
    shared_depart1 = max(m["depart_at"] for m in members)

    best = None
    for hub_m in hubs:
        if hub_m == origin:
            continue
        if (origin, hub_m) not in distance:
            continue
        if any((hub_m, m["destination"]) not in distance for m in members):
            continue

        km1 = distance[(origin, hub_m)]["mesafe_km"]
        travel_hours_by_type1 = distance[(origin, hub_m)]["duration"]
        today = shared_depart1.normalize()
        truck_left_origin = ledger.truck_remaining(origin, today)
        leg1_choice = _cheapest_single_vehicle(total_desi, km1, travel_hours_by_type1, vehicle_specs, truck_left_origin)
        if leg1_choice is None:
            continue
        vt1, cost1 = leg1_choice
        hours1 = travel_hours_by_type1[vt1]
        shared_arrive1 = shared_depart1 + pd.Timedelta(hours=hours1)
        unload_minutes = handling_duration_minutes(total_desi)

        ok_detour = True
        legs2 = []
        for m in members:
            direct_km = distance[(origin, m["destination"])]["mesafe_km"]
            km2 = distance[(hub_m, m["destination"])]["mesafe_km"]
            if km2 > MAX_DETOUR_RATIO * direct_km:
                ok_detour = False
                break
            travel_hours_by_type2 = distance[(hub_m, m["destination"])]["duration"]
            depart2 = shared_arrive1 + pd.Timedelta(minutes=unload_minutes + handling_duration_minutes(m["carried_desi"]))
            dest_today = depart2.normalize()
            truck_left_dest = ledger.truck_remaining(m["destination"], dest_today)
            truck_left_hub = ledger.truck_remaining(hub_m, dest_today)
            choice2 = _cheapest_single_vehicle(m["carried_desi"], km2, travel_hours_by_type2,
                                                vehicle_specs, min(truck_left_dest, truck_left_hub))
            if choice2 is None:
                ok_detour = False
                break
            vt2, cost2 = choice2
            hours2 = travel_hours_by_type2[vt2]
            arrive2 = depart2 + pd.Timedelta(hours=hours2)
            inbound_final = handling_duration_minutes(m["carried_desi"])
            legs2.append({
                "member": m, "vt": vt2, "hours2": hours2, "cost2": cost2,
                "depart2": depart2, "arrive2": arrive2,
                "delivered_at": arrive2 + pd.Timedelta(minutes=inbound_final),
                "reload_minutes": handling_duration_minutes(m["carried_desi"]),
            })
        if not ok_detour:
            continue

        alt_total_cost = cost1 + sum(x["cost2"] for x in legs2)
        if alt_total_cost >= original_cost:
            continue

        sla_delta = 0.0
        for x in legs2:
            m = x["member"]
            for talep_id, desi, _chain_id in m["portions"]:
                info = demand_lookup[talep_id]
                old_delay = delay_hours_ceiled(m["delivered_at"], info["deadline"])
                new_delay = delay_hours_ceiled(x["delivered_at"], info["deadline"])
                sla_delta += sla_penalty(desi, new_delay) - sla_penalty(desi, old_delay)

        net_savings = (original_cost - alt_total_cost) - sla_delta
        if net_savings <= 0:
            continue

        m_date = shared_arrive1.normalize()
        handling_needed = total_desi + sum(m["carried_desi"] for m in members)  # 1 indirme + N yukleme
        ok = ledger.try_consume_handling(hub_m, m_date, handling_needed)

        leg1_vid = "SPLIT1-" + "-".join(m["vehicle_internal_id"] for m in members)
        truck_ops = []
        truck_ok = True
        if ok and vt1 == cfg.TRUCK_CAP_VEHICLE_TYPE:
            truck_ok = ledger.try_consume_truck(origin, today, leg1_vid)
            if truck_ok:
                truck_ok = ledger.try_consume_truck(hub_m, m_date, leg1_vid)
                if truck_ok:
                    truck_ops.append((origin, today, leg1_vid))
                    truck_ops.append((hub_m, m_date, leg1_vid))
                else:
                    ledger.release_truck(origin, today, leg1_vid)

        leg2_truck_ok = True
        if ok and truck_ok:
            for x in legs2:
                if x["vt"] == cfg.TRUCK_CAP_VEHICLE_TYPE:
                    vid = f"{x['member']['vehicle_internal_id']}-SPLIT2"
                    dest_today = x["arrive2"].normalize()
                    if ledger.try_consume_truck(hub_m, dest_today, vid) and ledger.try_consume_truck(x["member"]["destination"], dest_today, vid):
                        truck_ops.append((hub_m, dest_today, vid))
                        truck_ops.append((x["member"]["destination"], dest_today, vid))
                    else:
                        ledger.release_truck(hub_m, dest_today, vid)
                        ledger.release_truck(x["member"]["destination"], dest_today, vid)
                        leg2_truck_ok = False
                        break

        if not ok or not truck_ok or not leg2_truck_ok:
            if ok:
                ledger.release_handling(hub_m, m_date, handling_needed)
            for hub, date, vid in truck_ops:
                ledger.release_truck(hub, date, vid)
            continue

        candidate = {
            "net_savings": net_savings,
            "hub_m": hub_m,
            "m_date": m_date,
            "handling_needed": handling_needed,
            "truck_ops": truck_ops,
            "legs": _build_legs(members, origin, hub_m, vt1, cost1, shared_depart1, shared_arrive1,
                                 hours1, total_desi, unload_minutes, legs2),
        }
        if best is None or candidate["net_savings"] > best["net_savings"]:
            if best is not None:
                _rollback(best, ledger)
            best = candidate
        else:
            _rollback(candidate, ledger)

    return best


def _build_legs(members, origin, hub_m, vt1, cost1, depart1, arrive1, hours1,
                 total_desi, unload_minutes, legs2):
    combined_id = "SPLIT-" + "-".join(m["vehicle_internal_id"] for m in members) + "-M1"
    outbound1 = handling_duration_minutes(total_desi)
    legs = [{
        "vehicle_internal_id": combined_id,
        "vehicle_class": "Spot", "vehicle_type": vt1,
        "origin": origin, "destination": hub_m,
        "depart_at": depart1, "arrive_at": arrive1, "travel_hours": hours1,
        "outbound_handling_min": outbound1,
        "inbound_handling_min": unload_minutes,
        "delivered_at": arrive1 + pd.Timedelta(minutes=unload_minutes),
        "cost": cost1, "carried_desi": total_desi,
        "portions": [p for m in members for p in m["portions"]],
    }]
    for x in legs2:
        m = x["member"]
        legs.append({
            "vehicle_internal_id": f"{m['vehicle_internal_id']}-M2",
            "vehicle_class": "Spot", "vehicle_type": x["vt"],
            "origin": hub_m, "destination": m["destination"],
            "depart_at": x["depart2"], "arrive_at": x["arrive2"], "travel_hours": x["hours2"],
            "outbound_handling_min": x["reload_minutes"],
            "inbound_handling_min": handling_duration_minutes(m["carried_desi"]),
            "delivered_at": x["delivered_at"],
            "cost": x["cost2"], "carried_desi": m["carried_desi"], "portions": m["portions"],
        })
    return legs


def _rollback(candidate, ledger):
    ledger.release_handling(candidate["hub_m"], candidate["m_date"], candidate["handling_needed"])
    for hub, date, vid in candidate["truck_ops"]:
        ledger.release_truck(hub, date, vid)


def run_hub_split(dispatches, distance, vehicle_specs, ledger, demand_lookup, hubs):
    if not cfg.CONSOLIDATION_ENABLED:
        return dispatches, 0.0, 0

    spot = [d for d in dispatches if d["vehicle_class"] == "Spot"]
    others = [d for d in dispatches if d["vehicle_class"] != "Spot"]

    from collections import defaultdict, Counter
    groups = defaultdict(list)
    for d in spot:
        if _fill_ratio(d, vehicle_specs) < LOW_FILL_THRESHOLD:
            groups[(d["origin"], d["depart_at"].normalize())].append(d)
        else:
            others.append(d)

    used = set()
    total_savings = 0.0
    n_applied_by_size = Counter()
    n_skipped_oversized = 0
    result = list(others)

    for key, all_members in groups.items():
        origin = key[0]
        search_members = all_members
        if len(all_members) > MAX_GROUP_SIZE:
            search_members = sorted(all_members, key=lambda d: _fill_ratio(d, vehicle_specs))[:MAX_GROUP_SIZE]
            n_skipped_oversized += 1

        for group_size in GROUP_SIZES:
            available = [d for d in search_members if d["vehicle_internal_id"] not in used]
            for combo in combinations(available, group_size):
                if any(m["vehicle_internal_id"] in used for m in combo):
                    continue
                best = _try_group_split(list(combo), origin, distance, vehicle_specs,
                                         ledger, demand_lookup, hubs)
                if best:
                    result.extend(best["legs"])
                    for m in combo:
                        used.add(m["vehicle_internal_id"])
                    total_savings += best["net_savings"]
                    n_applied_by_size[group_size] += 1

        for d in all_members:
            if d["vehicle_internal_id"] not in used:
                result.append(d)

    if n_skipped_oversized:
        print(f"[hub_split] UYARI: {n_skipped_oversized} grup MAX_GROUP_SIZE={MAX_GROUP_SIZE} sinirini asti")

    n_applied = sum(n_applied_by_size.values())
    breakdown = ", ".join(f"{n_applied_by_size[s]} {s}'li" for s in GROUP_SIZES)
    print(f"[hub_split] {breakdown} birleştirme uygulandı")

    return result, total_savings, n_applied
