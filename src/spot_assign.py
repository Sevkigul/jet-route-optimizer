"""Spot arac MIP - verilen desi miktarini en ucuza arac tipi/sayisina dagitir.

Maliyet artik SADECE yolculuga bagli degil - "Kullanim Suresi" (cikis
elleçleme + yolculuk + varis elleçleme) uzerinden hesaplaniyor (organizasyon
netlestirmesi). Elleçleme suresi TASINAN DESIYE bagli oldugundan, her aracin
maliyeti artik SABIT (rota/tip bazli) + DEGISKEN (o aracin kendi yukune
bagli, elleçleme uzerinden) bilesenlerinin toplami - ikisi de DESI'de
DOGRUSAL oldugundan (yuvarlama etkisi ihmal edilebilir buyuklukte), MIP
yine dogrusal kalir:

    arac_basina_maliyet(y) = FIXED[tip] + VARIABLE[tip] * y
    FIXED[tip]    = saatlik_ucret[tip] * (yol_dakika[tip]/60) + km_ucret[tip]*mesafe
    VARIABLE[tip] = saatlik_ucret[tip] * (2*HANDLING_MIN_PER_DESI/60)   (cikis+varis elleçleme)

Eski asamadaki solve_spot'un genisletilmesi: %10 min-doluluk kisiti YOK
(bu asamanin PDF'inde boyle bir kural yok), spot Tir sayisi ise
truck_remaining (hem cikis hem varis hub'inin tir kapasitesi) ile sinirli.
"""
import sys
from pathlib import Path

from pulp import LpProblem, LpMinimize, LpVariable, lpSum, value, PULP_CBC_CMD

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config_opt as cfg
from costs import travel_minutes


def _fixed_variable_cost(vt, distance_km, travel_hours_by_type, vehicle_specs, pricing="spot"):
    s = vehicle_specs[vt]
    hourly = s[f"{pricing}_hourly"]
    km_rate = s[f"{pricing}_km"]
    travel_min = travel_minutes(travel_hours_by_type[vt])
    fixed = hourly * (travel_min / 60.0) + km_rate * distance_km
    variable_per_desi = hourly * (2 * cfg.HANDLING_MIN_PER_DESI / 60.0)
    return fixed, variable_per_desi


def solve_spot(remaining_desi, distance_km, travel_hours_by_type, vehicle_specs,
               truck_remaining_origin, truck_remaining_dest, vehicle_types=None):
    """Verilen desiyi en ucuz spot arac kombinasyonuna atar.

    Dondurur: (counts: {tip: adet}, loads: {tip: arac_basina_desi})
    """
    vehicle_types = vehicle_types or cfg.VEHICLE_TYPES
    empty = ({vt: 0 for vt in vehicle_types}, {vt: 0.0 for vt in vehicle_types})
    if remaining_desi <= 1e-9:
        return empty

    fixed_cost = {}
    var_cost = {}
    for vt in vehicle_types:
        fixed_cost[vt], var_cost[vt] = _fixed_variable_cost(
            vt, distance_km, travel_hours_by_type, vehicle_specs, pricing="spot"
        )

    prob = LpProblem("spot_assign", LpMinimize)
    x = {vt: LpVariable(f"x_{vt}", lowBound=0, cat="Integer") for vt in vehicle_types}
    y = {vt: LpVariable(f"y_{vt}", lowBound=0) for vt in vehicle_types}

    prob += lpSum(fixed_cost[vt] * x[vt] + var_cost[vt] * y[vt] for vt in vehicle_types)
    prob += lpSum(y[vt] for vt in vehicle_types) == remaining_desi
    for vt in vehicle_types:
        cap = vehicle_specs[vt]["capacity"]
        prob += y[vt] <= cap * x[vt]
        if vt == cfg.TRUCK_CAP_VEHICLE_TYPE:
            max_trucks = min(truck_remaining_origin, truck_remaining_dest)
            prob += x[vt] <= max(0, max_trucks)

    prob.solve(PULP_CBC_CMD(msg=0))

    counts = {vt: int(value(x[vt]) or 0) for vt in vehicle_types}
    loads = {}
    for vt in vehicle_types:
        loads[vt] = (float(value(y[vt]) or 0.0) / counts[vt]) if counts[vt] else 0.0
    return counts, loads


if __name__ == "__main__":
    specs = {
        "Tır": {"capacity": 22400, "spot_hourly": 487.5, "spot_km": 25},
        "Kamyon": {"capacity": 12000, "spot_hourly": 318.25, "spot_km": 21},
        "Hafif Kamyon": {"capacity": 7200, "spot_hourly": 364.583333, "spot_km": 20},
        "Kamyonet": {"capacity": 5600, "spot_hourly": 197.916667, "spot_km": 18},
    }
    travel = {"Tır": 0.92, "Kamyon": 0.86, "Hafif Kamyon": 0.8, "Kamyonet": 0.75}
    counts, loads = solve_spot(10000, 60, travel, specs, truck_remaining_origin=5, truck_remaining_dest=5)
    print("counts:", counts)
    print("loads:", loads)

    print("\nTruck kapasitesi 0 iken (Tır kullanilamaz):")
    counts2, loads2 = solve_spot(10000, 60, travel, specs, truck_remaining_origin=0, truck_remaining_dest=5)
    print("counts:", counts2)
    print("loads:", loads2)
