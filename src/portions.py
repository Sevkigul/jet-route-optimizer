"""Talep parcalarinin (portion) yonetimi: olusturma, FIFO tuketim, deadline hesabi."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config_opt as cfg
from costs import sla_deadline


def build_portion_pools(forecast_df, distance):
    """Her (origin, destination) icin created_at sirali portion listesi dondurur.

    Her portion: {talep_id, origin, destination, desi_remaining, created_at, deadline}
    """
    pools = {}
    for _, r in forecast_df.sort_values("created_at").iterrows():
        route = (r["origin"], r["destination"])
        sla_gun = distance[route]["sla_gun"]
        portion = {
            "talep_id": r["talep_id"],
            "origin": r["origin"],
            "destination": r["destination"],
            "desi_remaining": float(r["desi"]),
            "created_at": r["created_at"],
            "deadline": sla_deadline(r["created_at"], sla_gun),
        }
        pools.setdefault(route, []).append(portion)
    for route in pools:
        pools[route].sort(key=lambda p: p["created_at"])
    return pools


def consume_fifo(portion_list, desi_needed, cutoff_time=None):
    """portion_list'ten (created_at sirali, en eski once) desi_needed kadar tuketir.

    cutoff_time verilirse, sadece created_at <= cutoff_time olan portionlar
    tuketilebilir (henuz olusmamis/gelecek talep erken tuketilemez). Liste
    created_at'e gore sirali oldugundan, cutoff'u asan ilk portionda durulur.

    portion_list yerinde (in-place) guncellenir - tuketilen portionlarin
    desi_remaining'i azalir, tukenenler listeden cikarilir.
    Dondurur: consumed = [(talep_id, desi_alinan), ...], eksik_kalan_desi (talep karsilanamadiysa > 0)
    """
    consumed = []
    remaining_need = desi_needed
    i = 0
    while remaining_need > 1e-9 and i < len(portion_list):
        p = portion_list[i]
        if cutoff_time is not None and p["created_at"] > cutoff_time:
            break
        take = min(p["desi_remaining"], remaining_need)
        if take > 1e-9:
            consumed.append((p["talep_id"], take))
            p["desi_remaining"] -= take
            remaining_need -= take
        if p["desi_remaining"] <= 1e-9:
            portion_list.pop(i)
        else:
            i += 1
    return consumed, remaining_need


def available_desi(portion_list, cutoff_time=None):
    if cutoff_time is None:
        return total_desi(portion_list)
    return sum(p["desi_remaining"] for p in portion_list if p["created_at"] <= cutoff_time)


def earliest_deadline(portion_list):
    if not portion_list:
        return None
    return min(p["deadline"] for p in portion_list)


def total_desi(portion_list):
    return sum(p["desi_remaining"] for p in portion_list)
