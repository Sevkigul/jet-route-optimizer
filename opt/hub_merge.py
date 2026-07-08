"""Faz C: gercek hub birlestirme - FARKLI kokenli, AYNI hedefe giden dusuk-dolu
spot sevkiyatlarini ortak bir ara merkezde birlestirip TEK (daha dolu) araca
yukler.

consolidation.py'daki mevcut Faz B, TEK BIR sevkiyat icin daha ucuz bir
alternatif rota arar (kendi basina). Bu modul ise FARKLI rotalardaki 2 veya 3
ayri sevkiyati (ayni gun, ayni hedef) ortak bir hub'da birlestirerek, her
birinin kendi giris bacagini (origin->hub) ayri araclarla yapip, hub'dan
hedefe TEK bir (daha dolu, dolayisiyla ucuz) arac ile devam etmelerini dener.

Performans notu: leg2'nin TEK bir arac ile cozulup cozulemeyecegini kontrol
etmek icin PuLP/CBC (solve_spot) yerine dogrudan 4 arac tipini karsilastiran
hafif bir fonksiyon (_cheapest_single_vehicle) kullanilir - CBC her cagrida
bir alt-surec (subprocess) baslattigindan (~15ms/cagri), binlerce aday x 18
hub aramasinda bu fark saatler mertebesinde performans kazanci saglar.
"""
import sys
from itertools import combinations
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config_opt as cfg
from costs import handling_duration_minutes, spot_cost, delay_hours_ceiled, sla_penalty

# Degerler parametre taramasiyla bulundu (toplam maliyeti minimize eden nokta;
# daha agresif degerlerde SLA cezasi artisi kazanci geciyor - bkz. proje notlari).
LOW_FILL_THRESHOLD = 0.90
MAX_DETOUR_RATIO = 2.5   # leg1 (origin->hub_m) dogrudan (origin->destination) mesafesini bu katindan fazla asmasin
MAX_GROUP_SIZE = 25      # ikili/uclu/dortlu arama kombinatoryal patlamasin diye, tek (destination,gun) grubunda ust sinir
# Kucukten buyuge (2,3,4) - buyukten kucuge degil! Once buyuk gruplari
# aramak, kucuk-ama-kesin-iyi eslesmeleri "yiyip" daha sonra daha degerli
# olabilecek kucuk gruplarin bulunmasini engelliyor (denendi: buyukten kucuge
# 18.13M, kucukten buyuge 17.97M TL - deneysel olarak dogrulandi).
GROUP_SIZES = (2, 3, 4)


def _fill_ratio(d, vehicle_specs):
    return d["carried_desi"] / vehicle_specs[d["vehicle_type"]]["capacity"]


def _cheapest_single_vehicle(desi, distance_km, travel_hours_by_type, vehicle_specs, truck_left):
    """CBC/MIP cagirmadan, 4 arac tipi arasinda TEK aracla tasinabilecek en
    ucuz secenegi bulur. Kapasiteyi asan veya (Tir icin) tir kapasitesi
    olmayan tipler elenir. Dondurur: (vehicle_type, cost) veya None."""
    best = None
    for vt in cfg.VEHICLE_TYPES:
        cap = vehicle_specs[vt]["capacity"]
        if desi > cap:
            continue
        if vt == cfg.TRUCK_CAP_VEHICLE_TYPE and truck_left < 1:
            continue
        cost = spot_cost(vt, distance_km, travel_hours_by_type[vt], vehicle_specs)
        if best is None or cost < best[1]:
            best = (vt, cost)
    return best


def _try_group_merge(members, destination, distance, vehicle_specs, ledger, demand_lookup, hubs):
    """members: 2 veya daha fazla dusuk-dolu Spot dispatch'i (ayni gun, ayni hedef).
    Her biri kendi origin->hub_m bacagini kendi araciyla yapar, hub_m'den
    hedefe TEK bir arac ile (birlesik yukle) devam edilir."""
    total_desi = sum(m["carried_desi"] for m in members)
    original_cost = sum(m["cost"] for m in members)

    best = None
    for hub_m in hubs:
        if hub_m == destination:
            continue
        if any((m["origin"], hub_m) not in distance for m in members):
            continue
        if (hub_m, destination) not in distance:
            continue

        ok_detour = True
        legs1 = []
        for m in members:
            direct_km = distance[(m["origin"], destination)]["mesafe_km"]
            km1 = distance[(m["origin"], hub_m)]["mesafe_km"]
            if km1 > MAX_DETOUR_RATIO * direct_km:
                ok_detour = False
                break
            travel_hours_by_type1 = distance[(m["origin"], hub_m)]["duration"]
            # leg1 icin arac tipi SABIT tutulmuyor - orijinal mesafeden cok daha
            # kisa olabilecek bu yeni bacak icin en ucuz tipi yeniden ararız (orn.
            # Kamyon vs Hafif Kamyon siralamasi mesafeye gore degisebilir). Tir
            # kapasitesi kontrolu icin, eger orijinal arac zaten Tir ise o slotu
            # zaten kullandigindan +1 telafi ile degerlendirilir (asil tuketim
            # degisikligi sadece adayi KABUL edersek asagida uygulanir).
            origin_today = m["depart_at"].normalize()
            truck_left_origin = ledger.truck_remaining(m["origin"], origin_today)
            effective_truck_left = truck_left_origin + (1 if m["vehicle_type"] == cfg.TRUCK_CAP_VEHICLE_TYPE else 0)
            choice1 = _cheapest_single_vehicle(m["carried_desi"], km1, travel_hours_by_type1,
                                                vehicle_specs, effective_truck_left)
            if choice1 is None:
                ok_detour = False
                break
            vt, cost1 = choice1
            hours1 = travel_hours_by_type1[vt]
            arrive1 = m["depart_at"] + pd.Timedelta(hours=hours1)
            legs1.append({"member": m, "vt": vt, "hours1": hours1, "cost1": cost1, "arrive1": arrive1,
                          "origin_today": origin_today})
        if not ok_detour:
            continue

        latest_arrival = max(x["arrive1"] for x in legs1)
        unload_minutes = sum(handling_duration_minutes(x["member"]["carried_desi"]) for x in legs1)
        reload_minutes = handling_duration_minutes(total_desi)
        depart2 = latest_arrival + pd.Timedelta(minutes=unload_minutes + reload_minutes)

        km2 = distance[(hub_m, destination)]["mesafe_km"]
        travel_hours_by_type = distance[(hub_m, destination)]["duration"]
        truck_left_m = ledger.truck_remaining(hub_m, depart2.normalize())
        truck_left_dest = ledger.truck_remaining(destination, depart2.normalize())
        truck_left = min(truck_left_m, truck_left_dest)

        leg2_choice = _cheapest_single_vehicle(total_desi, km2, travel_hours_by_type, vehicle_specs, truck_left)
        if leg2_choice is None:
            continue
        vt_leg2, cost2 = leg2_choice

        alt_total_cost = sum(x["cost1"] for x in legs1) + cost2
        if alt_total_cost >= original_cost:
            continue

        arrive2 = depart2 + pd.Timedelta(hours=travel_hours_by_type[vt_leg2])
        inbound_final = handling_duration_minutes(total_desi)
        new_delivered_at = arrive2 + pd.Timedelta(minutes=inbound_final)

        sla_delta = 0.0
        for m in members:
            for talep_id, desi, _chain_id in m["portions"]:
                info = demand_lookup[talep_id]
                old_delay = delay_hours_ceiled(m["delivered_at"], info["deadline"])
                new_delay = delay_hours_ceiled(new_delivered_at, info["deadline"])
                sla_delta += sla_penalty(desi, new_delay) - sla_penalty(desi, old_delay)

        net_savings = (original_cost - alt_total_cost) - sla_delta
        if net_savings <= 0:
            continue

        m_date = latest_arrival.normalize()
        handling_needed = sum(m["carried_desi"] for m in members) + total_desi  # N indirme + 1 yukleme
        ok = ledger.try_consume_handling(hub_m, m_date, handling_needed)

        truck_vid = "MERGE-" + "-".join(m["vehicle_internal_id"] for m in members)
        truck_ok = True
        if ok and vt_leg2 == cfg.TRUCK_CAP_VEHICLE_TYPE:
            truck_ok = (ledger.try_consume_truck(hub_m, m_date, truck_vid)
                        and ledger.try_consume_truck(destination, arrive2.normalize(), truck_vid))

        if not ok or not truck_ok:
            if ok:
                ledger.release_handling(hub_m, m_date, handling_needed)
                if vt_leg2 == cfg.TRUCK_CAP_VEHICLE_TYPE:
                    ledger.release_truck(hub_m, m_date, truck_vid)
                    ledger.release_truck(destination, arrive2.normalize(), truck_vid)
            continue

        # leg1'de arac tipi degistiyse, cikis hub'inin tir kapasitesini buna
        # gore ayarla (eski tip Tir idiyse serbest birak, yeni tip Tir ise
        # yeni bir slot dene - basarisiz olursa tum adayi geri al).
        origin_truck_applied = []
        origin_truck_ok = True
        for x in legs1:
            m = x["member"]
            old_vt = m["vehicle_type"]
            if old_vt == x["vt"]:
                continue
            if old_vt == cfg.TRUCK_CAP_VEHICLE_TYPE:
                ledger.release_truck(m["origin"], x["origin_today"], m["vehicle_internal_id"])
                origin_truck_applied.append(("release", m["origin"], x["origin_today"], m["vehicle_internal_id"]))
            if x["vt"] == cfg.TRUCK_CAP_VEHICLE_TYPE:
                new_vid = f"{m['vehicle_internal_id']}-M1"
                if not ledger.try_consume_truck(m["origin"], x["origin_today"], new_vid):
                    origin_truck_ok = False
                    break
                origin_truck_applied.append(("consume", m["origin"], x["origin_today"], new_vid))

        if not origin_truck_ok:
            # bu adayi tamamen geri al: origin degisiklikleri + hub_m/dest tuketimi
            for action, hub, date, vid in origin_truck_applied:
                if action == "consume":
                    ledger.release_truck(hub, date, vid)
                else:
                    ledger.try_consume_truck(hub, date, vid)  # eski tuketimi geri koy
            ledger.release_handling(hub_m, m_date, handling_needed)
            if vt_leg2 == cfg.TRUCK_CAP_VEHICLE_TYPE:
                ledger.release_truck(hub_m, m_date, truck_vid)
                ledger.release_truck(destination, arrive2.normalize(), truck_vid)
            continue

        candidate = {
            "net_savings": net_savings,
            "hub_m": hub_m,
            "destination": destination,
            "m_date": m_date,
            "dest_date": arrive2.normalize(),
            "handling_needed": handling_needed,
            "vt_leg2": vt_leg2,
            "truck_vid": truck_vid,
            "origin_truck_applied": origin_truck_applied,
            "legs": _build_legs(legs1, hub_m, destination, vt_leg2, cost2, depart2, arrive2,
                                 total_desi, unload_minutes, reload_minutes, new_delivered_at,
                                 travel_hours_by_type[vt_leg2]),
        }
        if best is None or candidate["net_savings"] > best["net_savings"]:
            if best is not None:
                _rollback(best, ledger)
            best = candidate
        else:
            _rollback(candidate, ledger)

    return best


def _build_legs(legs1, hub_m, destination, vt_leg2, cost2, depart2, arrive2,
                 total_desi, unload_minutes, reload_minutes, new_delivered_at, travel_hours2):
    legs = []
    all_portions = []
    for x in legs1:
        m = x["member"]
        inbound = handling_duration_minutes(m["carried_desi"])
        legs.append({
            "vehicle_internal_id": f"{m['vehicle_internal_id']}-M1",
            "vehicle_class": "Spot", "vehicle_type": x["vt"],
            "origin": m["origin"], "destination": hub_m,
            "depart_at": m["depart_at"], "arrive_at": x["arrive1"], "travel_hours": x["hours1"],
            "outbound_handling_min": m["outbound_handling_min"],
            "inbound_handling_min": inbound,
            "delivered_at": x["arrive1"] + pd.Timedelta(minutes=inbound),
            "cost": x["cost1"], "carried_desi": m["carried_desi"], "portions": m["portions"],
        })
        all_portions.extend(m["portions"])

    combined_id = "+".join(x["member"]["vehicle_internal_id"] for x in legs1) + "-M2"
    legs.append({
        "vehicle_internal_id": combined_id,
        # leg2, tum uyelerin portion'larini tasir; her portion kendi chain_id'sini
        # (talep_id, desi, chain_id) icinde tasidigi icin output_opt burada dogru
        # sekilde her uyenin kendi zincirini ayirt edebilir (yanlislikla split sayilmaz).
        "vehicle_class": "Spot", "vehicle_type": vt_leg2,
        "origin": hub_m, "destination": destination,
        "depart_at": depart2, "arrive_at": arrive2, "travel_hours": travel_hours2,
        "outbound_handling_min": unload_minutes + reload_minutes,
        "inbound_handling_min": handling_duration_minutes(total_desi),
        "delivered_at": new_delivered_at,
        "cost": cost2, "carried_desi": total_desi, "portions": all_portions,
    })
    return legs


def _rollback(candidate, ledger):
    ledger.release_handling(candidate["hub_m"], candidate["m_date"], candidate["handling_needed"])
    if candidate["vt_leg2"] == cfg.TRUCK_CAP_VEHICLE_TYPE:
        ledger.release_truck(candidate["hub_m"], candidate["m_date"], candidate["truck_vid"])
        ledger.release_truck(candidate["destination"], candidate["dest_date"], candidate["truck_vid"])
    for action, hub, date, vid in candidate.get("origin_truck_applied", []):
        if action == "consume":
            ledger.release_truck(hub, date, vid)
        else:
            ledger.try_consume_truck(hub, date, vid)  # eski tuketimi geri koy


def run_hub_merge(dispatches, distance, vehicle_specs, ledger, demand_lookup, hubs):
    if not cfg.CONSOLIDATION_ENABLED:
        return dispatches, 0.0, 0

    spot = [d for d in dispatches if d["vehicle_class"] == "Spot"]
    others = [d for d in dispatches if d["vehicle_class"] != "Spot"]

    from collections import defaultdict
    groups = defaultdict(list)
    for d in spot:
        if _fill_ratio(d, vehicle_specs) < LOW_FILL_THRESHOLD:
            groups[(d["destination"], d["depart_at"].normalize())].append(d)
        else:
            others.append(d)

    used = set()
    total_savings = 0.0
    from collections import Counter
    n_applied_by_size = Counter()
    n_skipped_oversized = 0
    result = list(others)

    for key, all_members in groups.items():
        destination = key[0]
        search_members = all_members
        if len(all_members) > MAX_GROUP_SIZE:
            # kombinatoryal patlama olmasin diye, en dusuk dolulukluler
            # (en cok kazanc potansiyeli olanlar) once denenir, geri kalani
            # bu turda merge aranmadan oldugu gibi sonuca eklenir (asagida).
            search_members = sorted(all_members, key=lambda d: _fill_ratio(d, vehicle_specs))[:MAX_GROUP_SIZE]
            n_skipped_oversized += 1

        for group_size in GROUP_SIZES:
            available = [d for d in search_members if d["vehicle_internal_id"] not in used]
            for combo in combinations(available, group_size):
                if any(m["vehicle_internal_id"] in used for m in combo):
                    continue
                best = _try_group_merge(list(combo), destination, distance, vehicle_specs,
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
        print(f"[hub_merge] UYARI: {n_skipped_oversized} grup MAX_GROUP_SIZE={MAX_GROUP_SIZE} sinirini asti, "
              f"her grupta sadece en dusuk doluluklu {MAX_GROUP_SIZE} sevkiyat arasinda arama yapildi "
              f"(geri kalanlar mergesiz oldugu gibi birakildi)")

    n_applied = sum(n_applied_by_size.values())
    breakdown = ", ".join(f"{n_applied_by_size[s]} {s}'li" for s in GROUP_SIZES)
    print(f"[hub_merge] {breakdown} birleştirme uygulandı")

    return result, total_savings, n_applied
