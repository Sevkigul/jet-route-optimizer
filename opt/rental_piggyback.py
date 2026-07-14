"""Faz E: kiralik araclarin BOS kapasitesini kullanma.

Kiralik araclar rota degistiremez (sadece kendi sabit rotasinda calisir),
bu yuzden bos kapasitelerine baska bir rotanin yukunu dogrudan bindiremeyiz.
AMA: eger baska bir talebin CIKISI, bir kiralik aracin CIKISIYLA ayniysa, o
talep kiraligin HEDEFINE kadar BEDAVA (zaten odenmis kapasiteye) "biner",
oradan kendi gercek hedefine ayri bir arac ile devam eder. Bu, resmi
soru-cevapta acikca izin verilen bir konsolidasyon deseni (Q&A #2).

Basitlestirme: eklenen yukun kiraligin kendi elleçleme suresine (dolayisiyla
depart/arrive saatlerine) etkisi ihmal edilir (tipik olarak birkac desi
icin birkac dakika farkeder) - sadece elleçleme KAPASITESI tuketimi dogru
sekilde (ve gece yarisini asarsa oranli) guncellenir. Kiraligin kendi
maliyeti ZATEN sabit oldugundan (yuke bagli degil), bu adayin tek maliyeti
candidate'in leg2'sidir.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config_opt as cfg
from costs import handling_duration_minutes, delay_hours_ceiled, sla_penalty, travel_minutes
from hub_merge import _cheapest_single_vehicle, _fill_ratio

LOW_FILL_THRESHOLD = 0.90


def _try_piggyback(rental_d, candidate, distance, vehicle_specs, ledger, demand_lookup):
    cap = vehicle_specs[rental_d["vehicle_type"]]["capacity"]
    spare = cap - rental_d["carried_desi"]
    if candidate["carried_desi"] > spare + 1e-6:
        return None

    hub_m = rental_d["destination"]
    final_dest = candidate["destination"]
    if hub_m == final_dest or (hub_m, final_dest) not in distance:
        return None

    km2 = distance[(hub_m, final_dest)]["mesafe_km"]
    travel_hours_by_type2 = distance[(hub_m, final_dest)]["duration"]

    # kiraligin kendi varis elleclemesi tamamlandiktan sonra, candidate'in
    # yuku ayrica yuklenir (rentalin kendi elleclemesi zaten hesaba katildi,
    # bu ekstra "yukleme" adimi sadece candidate'in kendi desisi icindir)
    reload_minutes = handling_duration_minutes(candidate["carried_desi"])
    depart2 = rental_d["delivered_at"] + pd.Timedelta(minutes=reload_minutes)

    today2 = depart2.normalize()
    truck_left_hub = ledger.truck_remaining(hub_m, today2)
    truck_left_final = ledger.truck_remaining(final_dest, today2)
    choice2 = _cheapest_single_vehicle(candidate["carried_desi"], km2, travel_hours_by_type2,
                                        vehicle_specs, min(truck_left_hub, truck_left_final))
    if choice2 is None:
        return None
    vt2, cost2 = choice2

    # kiraligin kendi maliyeti sabit (yuke bagli degil) - net karsilastirma
    # sadece candidate'in eski maliyeti ile yeni leg2 maliyeti arasinda
    if cost2 >= candidate["cost"]:
        return None

    min2 = travel_minutes(travel_hours_by_type2[vt2])
    arrive2 = depart2 + pd.Timedelta(minutes=min2)
    inbound_final = handling_duration_minutes(candidate["carried_desi"])
    new_delivered_at = arrive2 + pd.Timedelta(minutes=inbound_final)

    sla_delta = 0.0
    for talep_id, desi, _chain_id in candidate["portions"]:
        info = demand_lookup[talep_id]
        old_delay = delay_hours_ceiled(candidate["delivered_at"], info["deadline"])
        new_delay = delay_hours_ceiled(new_delivered_at, info["deadline"])
        sla_delta += sla_penalty(desi, new_delay) - sla_penalty(desi, old_delay)

    net_savings = (candidate["cost"] - cost2) - sla_delta
    if net_savings <= 0:
        return None

    # --- ledger: once candidate'in ESKI (artik gecersiz) tuketimini iade et ---
    # (candidate'in orijinal cikis elleclemesi aggregate/gun bazinda tutuldugundan
    # tam sureyle-orantili tersine cevrilemez - en iyi yaklasiklikla duz gun bazinda iade edilir)
    old_origin_today = candidate["depart_at"].normalize()
    old_dest_today = candidate["arrive_at"].normalize()
    ledger.release_handling(candidate["origin"], old_origin_today, candidate["carried_desi"])
    ledger.release_handling(candidate["destination"], old_dest_today, candidate["carried_desi"])
    if candidate["vehicle_type"] == cfg.TRUCK_CAP_VEHICLE_TYPE:
        ledger.release_truck(candidate["origin"], old_origin_today, candidate["vehicle_internal_id"])
        ledger.release_truck(candidate["destination"], old_dest_today, candidate["vehicle_internal_id"])

    # --- sonra kiraligin EKSTRA elleçleme tuketimini ekle (rentalin KENDI
    # elleçleme penceresi icinde, gece yarisini asarsa oranli bolunur) ---
    origin_start = rental_d["depart_at"] - pd.Timedelta(minutes=rental_d["outbound_handling_min"])
    hub_start = rental_d["arrive_at"]
    ok_origin = ledger.try_consume_handling_timed(
        rental_d["origin"], origin_start, rental_d["outbound_handling_min"], candidate["carried_desi"])
    ok_hub = ledger.try_consume_handling_timed(
        hub_m, hub_start, rental_d["inbound_handling_min"], candidate["carried_desi"]) if ok_origin else False
    # candidate'in YENI (leg2) varis elleclemesi, gercek varis noktasinda (final_dest)
    ok_final = ledger.try_consume_handling_timed(
        final_dest, arrive2, inbound_final, candidate["carried_desi"]) if ok_hub else False
    ok = ok_origin and ok_hub and ok_final

    truck_vid = f"PIGGY-{rental_d['vehicle_internal_id']}-{candidate['vehicle_internal_id']}"
    truck_ok = True
    if ok and vt2 == cfg.TRUCK_CAP_VEHICLE_TYPE:
        truck_ok = ledger.try_consume_truck(hub_m, today2, truck_vid) and ledger.try_consume_truck(final_dest, arrive2.normalize(), truck_vid)

    if not ok or not truck_ok:
        if truck_ok is False and vt2 == cfg.TRUCK_CAP_VEHICLE_TYPE:
            ledger.release_truck(hub_m, today2, truck_vid)
            ledger.release_truck(final_dest, arrive2.normalize(), truck_vid)
        if ok_origin:
            ledger.release_handling_timed(rental_d["origin"], origin_start, rental_d["outbound_handling_min"], candidate["carried_desi"])
        if ok_hub:
            ledger.release_handling_timed(hub_m, hub_start, rental_d["inbound_handling_min"], candidate["carried_desi"])
        if ok_final:
            ledger.release_handling_timed(final_dest, arrive2, inbound_final, candidate["carried_desi"])
        # candidate'in eski tuketimini geri koy (bu aday reddedildi)
        ledger.force_consume_handling(candidate["origin"], old_origin_today, candidate["carried_desi"])
        ledger.force_consume_handling(candidate["destination"], old_dest_today, candidate["carried_desi"])
        if candidate["vehicle_type"] == cfg.TRUCK_CAP_VEHICLE_TYPE:
            ledger.try_consume_truck(candidate["origin"], old_origin_today, candidate["vehicle_internal_id"])
            ledger.try_consume_truck(candidate["destination"], old_dest_today, candidate["vehicle_internal_id"])
        return None

    leg2 = {
        "vehicle_internal_id": f"{candidate['vehicle_internal_id']}-PIGGY",
        "vehicle_class": "Spot", "vehicle_type": vt2,
        "origin": hub_m, "destination": final_dest,
        "depart_at": depart2, "arrive_at": arrive2, "travel_hours": min2 / 60.0,
        "outbound_handling_min": reload_minutes,
        "inbound_handling_min": inbound_final,
        "delivered_at": new_delivered_at,
        "cost": cost2, "carried_desi": candidate["carried_desi"], "portions": candidate["portions"],
    }

    return {
        "net_savings": net_savings,
        "rental_extra_desi": candidate["carried_desi"],
        "leg2": leg2,
    }


def run_rental_piggyback(dispatches, distance, vehicle_specs, ledger, demand_lookup):
    if not cfg.CONSOLIDATION_ENABLED:
        return dispatches, 0.0, 0

    rentals = [d for d in dispatches if d["vehicle_class"] == "Kiralık"]
    spot = [d for d in dispatches if d["vehicle_class"] == "Spot"]
    others = [d for d in dispatches if d["vehicle_class"] not in ("Kiralık", "Spot")]

    # rental capacity durumunu ayrica takip et (bir rental birden fazla candidate alabilir)
    remaining_spare = {
        r["vehicle_internal_id"]: vehicle_specs[r["vehicle_type"]]["capacity"] - r["carried_desi"]
        for r in rentals
    }
    rentals_by_origin = {}
    for r in rentals:
        rentals_by_origin.setdefault((r["origin"], r["depart_at"].normalize()), []).append(r)

    used = set()
    total_savings = 0.0
    n_applied = 0
    extra_legs = []

    for d in spot:
        if _fill_ratio(d, vehicle_specs) >= LOW_FILL_THRESHOLD:
            continue
        key = (d["origin"], d["depart_at"].normalize())
        candidates_rentals = rentals_by_origin.get(key, [])
        best = None
        best_rental = None
        for r in candidates_rentals:
            if remaining_spare[r["vehicle_internal_id"]] < d["carried_desi"] - 1e-6:
                continue
            result = _try_piggyback(r, d, distance, vehicle_specs, ledger, demand_lookup)
            if result and (best is None or result["net_savings"] > best["net_savings"]):
                best = result
                best_rental = r
        if best:
            remaining_spare[best_rental["vehicle_internal_id"]] -= best["rental_extra_desi"]
            best_rental["carried_desi"] += best["rental_extra_desi"]
            best_rental["portions"] = best_rental["portions"] + d["portions"]
            extra_legs.append(best["leg2"])
            used.add(d["vehicle_internal_id"])
            total_savings += best["net_savings"]
            n_applied += 1

    result = others + rentals + [d for d in spot if d["vehicle_internal_id"] not in used] + extra_legs
    print(f"[rental_piggyback] {n_applied} yuk kiralik boş kapasitesine bindirildi")
    return result, total_savings, n_applied
