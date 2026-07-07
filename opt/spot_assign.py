"""Spot arac MIP - verilen desi miktarini en ucuza arac tipi/sayisina dagitir.

Eski asamadaki solve_spot'un genisletilmesi: %10 min-doluluk kisiti YOK
(bu asamanin PDF'inde boyle bir kural yok), spot Tir sayisi ise
truck_remaining (hem cikis hem varis hub'inin tir kapasitesi) ile sinirli.
"""
import sys
from pathlib import Path

from pulp import LpProblem, LpMinimize, LpVariable, lpSum, value, PULP_CBC_CMD

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config_opt as cfg
from costs import spot_cost


def solve_spot(remaining_desi, distance_km, travel_hours_by_type, vehicle_specs,
               truck_remaining_origin, truck_remaining_dest, vehicle_types=None):
    """Verilen desiyi en ucuz spot arac kombinasyonuna atar.

    Dondurur: (counts: {tip: adet}, loads: {tip: arac_basina_desi})
    """
    vehicle_types = vehicle_types or cfg.VEHICLE_TYPES
    empty = ({vt: 0 for vt in vehicle_types}, {vt: 0.0 for vt in vehicle_types})
    if remaining_desi <= 1e-9:
        return empty

    unit_cost = {
        vt: spot_cost(vt, distance_km, travel_hours_by_type[vt], vehicle_specs)
        for vt in vehicle_types
    }

    prob = LpProblem("spot_assign", LpMinimize)
    x = {vt: LpVariable(f"x_{vt}", lowBound=0, cat="Integer") for vt in vehicle_types}
    y = {vt: LpVariable(f"y_{vt}", lowBound=0) for vt in vehicle_types}

    prob += lpSum(unit_cost[vt] * x[vt] for vt in vehicle_types)
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
