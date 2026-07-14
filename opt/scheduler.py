"""Ana orkestrasyon: gun -> rota -> (kiralik once, sonra spot) -> kapasite yetmezse
otomatik olarak ertesi gune tasan portion havuzu (ayri bir kuyruk yapisi gerekmez -
tuketilmeyen portion zaten havuzda kalir ve ertesi gun tekrar "available" olur).

Elleçleme kapasitesi asilirsa: PDF'e gore yukler bekletilip ertesi gun gonderilir -
bu sert bir kisit, karar gerektirmez.

Ayrica (kural olmasa da) maliyet-guduml bir "biriktir mi cikar mi" karari var:
dusuk hacimli rotalarda sabit arac maliyeti (saatlik ucret) yuke bagli
olmadigindan, her gun kucuk bir arac cikarmak yerine birkac gunluk talebi
biriktirip daha dolu tek bir arac gondermek cok daha ucuz olabilir. Bu karar
SLA riski yoksa (havuzdaki en erken deadline'a hala guvenli bir pay varsa)
uygulanir - bkz. config_opt.MIN_FILL_THRESHOLD / SLA_SAFETY_HOURS.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config_opt as cfg
from data_loader_opt import load_data, validate_all
from state import CapacityLedger
from portions import build_portion_pools, consume_fifo, available_desi, earliest_deadline
from rental import build_rental_fleet, dispatch_rentals_for_day
from spot_assign import solve_spot
from costs import handling_duration_minutes, spot_cost, travel_minutes, usage_hours


def _dispatch_spot_for_wave(route, date, day_1700, pool, distance, vehicle_specs, ledger,
                             is_last_day=False, min_vehicle_capacity=None):
    """Bir rota icin bugunku (kiralik sonrasi kalan) talebi spot araclara atar.

    Elleçleme kapasitesi (cikis hub'i) sert bir tavan olarak once uygulanir;
    tir kapasitesi ise MIP icinde kisit olarak islenir (varis hub'i icin
    ayni-gun-varis varsayimiyla tahmini uygulanir, gercek varis tarihi
    farkli cikarsa bu bilinen bir basitlestirmedir).

    Dusuk doluluk + SLA riski yoksa (ve son gun degilse) bugun hic cikilmaz,
    talep havuzda kalip yarinki (daha dolu olabilecek) dalgada tekrar denenir.
    """
    origin, destination = route
    today = date.normalize()

    available_now = available_desi(pool, cutoff_time=day_1700)
    if available_now <= 1e-9:
        return []

    if not is_last_day and min_vehicle_capacity:
        fill_if_now = available_now / min_vehicle_capacity
        if fill_if_now < cfg.MIN_FILL_THRESHOLD:
            deadline = earliest_deadline(pool)
            slack_hours = (deadline - day_1700).total_seconds() / 3600.0 if deadline else 1e9
            # guvenlik payi, rotanin KENDI yolculuk suresini de hesaba katmali - orn.
            # Istanbul->Sanliurfa gibi 15-18 saatlik uzun rotalarda duz "6 saat" payi
            # yetersiz kalir (arac zaten yolda o kadar saat geciriyor); en hizli arac
            # tipinin sure tahmini + sabit guvenlik payi kullanilir. (Rota SLA suresine
            # gore ayirt edilen bir versiyon denendi, kazanc saglamadigi icin geri alindi.)
            fastest_travel_hours = min(distance[route]["duration"].values())
            required_lead_hours = fastest_travel_hours + cfg.SLA_SAFETY_HOURS
            if slack_hours > required_lead_hours:
                return []  # dusuk doluluk + SLA riski yok -> biriktirmeye devam et

    handling_left_origin = max(0.0, ledger.handling_remaining(origin, today))
    batch = min(available_now, handling_left_origin)
    if batch <= 1e-9:
        return []  # bugun hic yer yok, portion havuzda kalir, yarin tekrar denenir

    dist_info = distance[route]
    travel_hours_by_type = dist_info["duration"]
    distance_km = dist_info["mesafe_km"]

    truck_left_origin = ledger.truck_remaining(origin, today)
    truck_left_dest = ledger.truck_remaining(destination, today)  # ayni-gun-varis varsayimi

    counts, loads = solve_spot(batch, distance_km, travel_hours_by_type, vehicle_specs,
                                truck_left_origin, truck_left_dest)

    actual_batch = sum(counts[vt] * loads[vt] for vt in counts)
    if actual_batch <= 1e-9:
        return []

    # cikis elleçlemesi: tum gunun sevk edilen yuku icin (aggregate), gece
    # yarisini asarsa sureyle orantili bolunur.
    aggregate_outbound_handling = handling_duration_minutes(actual_batch)
    ledger.try_consume_handling_timed(origin, day_1700, aggregate_outbound_handling, actual_batch)
    consumed, _ = consume_fifo(pool, actual_batch, cutoff_time=day_1700)

    dispatches = []
    v_no = 0
    remaining_consumed = list(consumed)
    for vt, count in counts.items():
        if count == 0:
            continue
        per_vehicle_load = loads[vt]
        travel_hrs = travel_hours_by_type[vt]
        travel_min = travel_minutes(travel_hrs)

        for _ in range(count):
            outbound_handling = handling_duration_minutes(per_vehicle_load)
            depart_at = day_1700 + pd.Timedelta(minutes=outbound_handling)
            arrive_at = depart_at + pd.Timedelta(minutes=travel_min)
            inbound_handling = handling_duration_minutes(per_vehicle_load)
            arrival_day = arrive_at.normalize()

            # Kullanim Suresi = cikis elleçleme + yolculuk + varis elleçleme
            usage_hrs = usage_hours(outbound_handling, travel_min, inbound_handling)
            cost = spot_cost(vt, distance_km, usage_hrs, vehicle_specs)

            if vt == cfg.TRUCK_CAP_VEHICLE_TYPE:
                ledger.try_consume_truck(origin, today, f"S-{route}-{date.date()}-{v_no}")
                ledger.try_consume_truck(destination, arrival_day, f"S-{route}-{date.date()}-{v_no}")
            ledger.try_consume_handling_timed(destination, arrive_at, inbound_handling, per_vehicle_load)

            vehicle_portions, remaining_consumed = _take_portions(remaining_consumed, per_vehicle_load)

            vehicle_id = f"S-{origin}-{destination}-{date.date()}-{v_no}"
            vehicle_portions = [(tid, desi, vehicle_id) for tid, desi in vehicle_portions]
            dispatches.append({
                "vehicle_internal_id": vehicle_id,
                "vehicle_class": "Spot",
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
                "carried_desi": per_vehicle_load,
                "portions": vehicle_portions,
            })
            v_no += 1

    return dispatches


def _take_portions(consumed_list, amount):
    """consumed_list=[(talep_id,desi),...] listesinden 'amount' kadarini one alir."""
    taken = []
    left = amount
    remaining = list(consumed_list)
    while left > 1e-9 and remaining:
        talep_id, desi = remaining[0]
        take = min(desi, left)
        taken.append((talep_id, take))
        left -= take
        if desi - take <= 1e-9:
            remaining.pop(0)
        else:
            remaining[0] = (talep_id, desi - take)
    return taken, remaining


def run_schedule(data):
    ledger = CapacityLedger(data["handling_capacity"], data["truck_capacity"])
    fleet = build_rental_fleet(data["rentals"])
    portion_pools = build_portion_pools(data["forecast"], data["distance"])

    all_routes = set(portion_pools) | set(fleet)
    dates = pd.date_range(cfg.TARGET_START, cfg.TARGET_END, freq="D")
    min_vehicle_capacity = min(v["capacity"] for v in data["vehicle_specs"].values())

    all_dispatches = []
    for date in dates:
        day_1700 = date + pd.Timedelta(hours=17)
        is_last_day = date.normalize() == cfg.TARGET_END.normalize()
        for route in sorted(all_routes):
            pool = portion_pools.get(route, [])
            fleet_types = fleet.get(route, [])

            if fleet_types:
                rentals_today = dispatch_rentals_for_day(
                    route, date, fleet_types, data["vehicle_specs"], data["distance"], pool, ledger
                )
                all_dispatches.extend(rentals_today)

            spot_today = _dispatch_spot_for_wave(
                route, date, day_1700, pool, data["distance"], data["vehicle_specs"], ledger,
                is_last_day=is_last_day, min_vehicle_capacity=min_vehicle_capacity
            )
            all_dispatches.extend(spot_today)

    leftover = {route: portion_pools[route] for route in portion_pools if portion_pools[route]}
    total_leftover_desi = sum(sum(p["desi_remaining"] for p in pl) for pl in leftover.values())

    print(f"[scheduler] {len(all_dispatches)} araç dispatch edildi")
    if total_leftover_desi > 1e-6:
        print(f"[scheduler] UYARI: hafta sonunda hâlâ kuyrukta {total_leftover_desi:,.1f} desi var "
              f"({len(leftover)} güzergahta) - kapasite yetersizliğinden dolayı hedef pencere içinde "
              f"gönderilemedi")

    return all_dispatches, leftover, ledger


if __name__ == "__main__":
    data = load_data()
    validate_all(data)
    dispatches, leftover, ledger = run_schedule(data)

    total_cost = sum(d["cost"] for d in dispatches)
    rental_cost_total = sum(d["cost"] for d in dispatches if d["vehicle_class"] == "Kiralık")
    spot_cost_total = sum(d["cost"] for d in dispatches if d["vehicle_class"] == "Spot")
    print(f"\nToplam araç maliyeti: {total_cost:,.2f} TL "
          f"(kiralık {rental_cost_total:,.2f} + spot {spot_cost_total:,.2f})")
