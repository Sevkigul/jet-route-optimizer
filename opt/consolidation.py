"""Faz B: temel plan (Faz A) uzerine, sonradan eklenen, acilip kapatilabilir
trans-shipment (konsolidasyon) arama katmani.

Kapsam ve bilincli sinirlamalar:
- Sadece SPOT sevkiyatlari aday olarak degerlendirilir (kiralik araclar
  sabit rotali/zorunlu oldugu icin bu arama disinda tutulur - onlarin
  yukunu ayrica baska bir araca aktarma da PDF'e gore mumkun ama bu
  farkli bir desen, bu surumde kapsam disi).
- Sadece 1-hop (A->M->D) aranir (config_opt.CONSOLIDATION_MAX_HOPS=1).
- Ara bacaklarda AYNI arac tipi kullanilir (orijinal sevkiyatin tipi) -
  tip degistirmeyi de arayan bir versiyon daha fazla kazanc bulabilir
  ama truck-kapasite muhasebesini ciddi karmasiklastirir.
- Sadece dusuk dolulukta (< LOW_FILL_THRESHOLD) sevkiyatlar aranir.
- Her aday transactional islenir: once orijinal sevkiyatin ledger
  tuketimi iade edilir (release), yeni 2-bacakli alternatif icin kapasite
  denenir (try_consume); herhangi biri basarisiz olursa VEYA net kazanc
  pozitif degilse tum degisiklikler geri alinir (rollback) ve orijinal
  sevkiyat degismeden birakilir.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config_opt as cfg
from costs import handling_duration_minutes, spot_cost, delay_hours_ceiled, sla_penalty

LOW_FILL_THRESHOLD = 0.5
MAX_DETOUR_RATIO = 2.0   # dist(o,m)+dist(m,d) <= bu oran * dist(o,d) olmali


def _portion_sla_delta(portions, demand_lookup, old_delivered_at, new_delivered_at):
    delta = 0.0
    for talep_id, desi, _chain_id in portions:
        info = demand_lookup[talep_id]
        old_delay = delay_hours_ceiled(old_delivered_at, info["deadline"])
        new_delay = delay_hours_ceiled(new_delivered_at, info["deadline"])
        delta += sla_penalty(desi, new_delay) - sla_penalty(desi, old_delay)
    return delta


def _try_candidate(d, hub_m, distance, vehicle_specs, ledger, demand_lookup):
    origin, destination = d["origin"], d["destination"]
    vt = d["vehicle_type"]
    carried = d["carried_desi"]

    if (origin, hub_m) not in distance or (hub_m, destination) not in distance:
        return None

    direct_km = distance[(origin, destination)]["mesafe_km"]
    km1 = distance[(origin, hub_m)]["mesafe_km"]
    km2 = distance[(hub_m, destination)]["mesafe_km"]
    if km1 + km2 > MAX_DETOUR_RATIO * direct_km:
        return None

    hours1 = distance[(origin, hub_m)]["duration"][vt]
    hours2 = distance[(hub_m, destination)]["duration"][vt]

    cost1 = spot_cost(vt, km1, hours1, vehicle_specs)
    cost2 = spot_cost(vt, km2, hours2, vehicle_specs)
    alt_cost = cost1 + cost2
    if alt_cost >= d["cost"]:
        return None

    # zamanlama
    depart1 = d["depart_at"]
    arrive1 = depart1 + pd.Timedelta(hours=hours1)
    handling_m = handling_duration_minutes(carried)   # indirme
    handling_m2 = handling_duration_minutes(carried)  # yeniden yukleme
    depart2 = arrive1 + pd.Timedelta(minutes=handling_m + handling_m2)
    arrive2 = depart2 + pd.Timedelta(hours=hours2)
    inbound_final = handling_duration_minutes(carried)
    new_delivered_at = arrive2 + pd.Timedelta(minutes=inbound_final)

    sla_delta = _portion_sla_delta(d["portions"], demand_lookup, d["delivered_at"], new_delivered_at)
    net_savings = (d["cost"] - alt_cost) - sla_delta
    if net_savings <= 0:
        return None

    # --- transactional ledger denemesi (sadece GERCEKTEN tuketilen kadar iade edilir) ---
    m_date = arrive1.normalize()
    handling_ok1 = ledger.try_consume_handling(hub_m, m_date, carried)
    handling_ok2 = ledger.try_consume_handling(hub_m, m_date, carried) if handling_ok1 else False
    ok = handling_ok1 and handling_ok2

    truck_ids_consumed = []
    if ok and vt == cfg.TRUCK_CAP_VEHICLE_TYPE:
        leg1_vid = f"CONSOL-{d['vehicle_internal_id']}-leg1-arr"
        leg2_vid = f"CONSOL-{d['vehicle_internal_id']}-leg2-dep"
        ok1 = ledger.try_consume_truck(hub_m, m_date, leg1_vid)
        ok2 = ledger.try_consume_truck(hub_m, m_date, leg2_vid) if ok1 else False
        if ok1:
            truck_ids_consumed.append((hub_m, m_date, leg1_vid))
        if ok2:
            truck_ids_consumed.append((hub_m, m_date, leg2_vid))
        ok = ok1 and ok2

    if not ok:
        # geri al: sadece bu asamaya kadar GERCEKTEN tuketilmis olan kadar iade et
        consumed_handling = (1 if handling_ok1 else 0) + (1 if handling_ok2 else 0)
        if consumed_handling:
            ledger.release_handling(hub_m, m_date, consumed_handling * carried)
        for hub, date, vid in truck_ids_consumed:
            ledger.release_truck(hub, date, vid)
        return None

    leg1 = {
        "vehicle_internal_id": f"{d['vehicle_internal_id']}-C1",
        "vehicle_class": "Spot",
        "vehicle_type": vt,
        "origin": origin,
        "destination": hub_m,
        "depart_at": depart1,
        "arrive_at": arrive1,
        "travel_hours": hours1,
        "outbound_handling_min": d["outbound_handling_min"],
        "inbound_handling_min": handling_m,
        "delivered_at": arrive1 + pd.Timedelta(minutes=handling_m),
        "cost": cost1,
        "carried_desi": carried,
        "portions": d["portions"],
    }
    leg2 = {
        "vehicle_internal_id": f"{d['vehicle_internal_id']}-C2",
        "vehicle_class": "Spot",
        "vehicle_type": vt,
        "origin": hub_m,
        "destination": destination,
        "depart_at": depart2,
        "arrive_at": arrive2,
        "travel_hours": hours2,
        "outbound_handling_min": handling_m2,
        "inbound_handling_min": inbound_final,
        "delivered_at": new_delivered_at,
        "cost": cost2,
        "carried_desi": carried,
        "portions": d["portions"],
    }
    return {"net_savings": net_savings, "legs": [leg1, leg2]}


def run_consolidation(dispatches, distance, vehicle_specs, ledger, demand_lookup, hubs):
    if not cfg.CONSOLIDATION_ENABLED:
        return dispatches, 0.0, 0

    result = []
    total_savings = 0.0
    n_applied = 0

    for d in dispatches:
        if d["vehicle_class"] != "Spot" or d.get("vehicle_group_id"):
            # milk-run bacaklari (vehicle_group_id'si var) tekrar aday sayilmaz
            result.append(d)
            continue

        capacity = vehicle_specs[d["vehicle_type"]]["capacity"]
        fill_ratio = d["carried_desi"] / capacity if capacity else 1.0
        if fill_ratio >= LOW_FILL_THRESHOLD:
            result.append(d)
            continue

        best = None
        for hub_m in hubs:
            if hub_m in (d["origin"], d["destination"]):
                continue
            # once orijinal tuketimi iade et (deneme icin), basarisiz olursa geri koyacagiz
            candidate = _try_candidate(d, hub_m, distance, vehicle_specs, ledger, demand_lookup)
            if candidate and (best is None or candidate["net_savings"] > best["net_savings"]):
                if best is not None:
                    # onceki adayi geri al, bu adayi tut
                    _rollback_candidate(best, ledger)
                best = candidate
            elif candidate:
                _rollback_candidate(candidate, ledger)

        if best is not None:
            # kabul: orijinal sevkiyatin o/d tuketimi degismiyor (zaten aciklandigi gibi),
            # sadece hub_m'deki yeni tuketim kalici hale geliyor (zaten try_consume ile yapildi)
            result.extend(best["legs"])
            total_savings += best["net_savings"]
            n_applied += 1
        else:
            result.append(d)

    return result, total_savings, n_applied


def _rollback_candidate(candidate, ledger):
    leg1, leg2 = candidate["legs"]
    hub_m = leg1["destination"]
    m_date = leg1["arrive_at"].normalize()
    carried = leg1["carried_desi"]
    ledger.release_handling(hub_m, m_date, 2 * carried)
    if leg1["vehicle_type"] == cfg.TRUCK_CAP_VEHICLE_TYPE:
        ledger.release_truck(hub_m, m_date, f"CONSOL-{leg1['vehicle_internal_id'].rsplit('-C1',1)[0]}-leg1-arr")
        ledger.release_truck(hub_m, m_date, f"CONSOL-{leg1['vehicle_internal_id'].rsplit('-C1',1)[0]}-leg2-dep")
