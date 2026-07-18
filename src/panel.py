"""Guzergah x gun x slot izgarasi ve lifecycle-farkinda sifir doldurma (Faz 2)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from data_loader import load_data


def build_panel(data):
    """Dengeli panel kurar: gozlemlenen her guzergah x her gun x her slot icin tek satir.

    - Gecmis (LAST_HISTORY_DATE'e kadar) ve guzergahin kendi ilk gozlem
      tarihinden sonraki bosluklar: 0 desi (o gun-slot'ta teslimat yok).
    - Guzergahin ilk gozlem tarihinden ONCEKI gunler: panelde hic yer almaz
      (henuz var olmayan bir guzergaha yapay sifir talep uydurmamak icin).
    - Hedef pencere (TARGET_START..TARGET_END): desi = NaN (tahmin edilecek).
    """
    demand = data["demand"]

    routes = demand[["origin", "destination"]].drop_duplicates()
    route_first_date = (
        demand.groupby(["origin", "destination"])["date"].min()
        .rename("route_first_date")
        .reset_index()
    )

    dates = pd.date_range(demand["date"].min(), config.TARGET_END, freq="D")
    slots = pd.DataFrame({"slot": config.SLOT_CODES})

    grid = routes.merge(pd.DataFrame({"date": dates}), how="cross").merge(slots, how="cross")
    grid = grid.merge(route_first_date, on=["origin", "destination"], how="left")

    # Guzergahin var olmadigi donemdeki satirlari at
    grid = grid[grid["date"] >= grid["route_first_date"]].drop(columns="route_first_date")

    panel = grid.merge(demand, on=["origin", "destination", "date", "slot"], how="left")

    gecmis = panel["date"] <= config.LAST_HISTORY_DATE
    panel.loc[gecmis, "desi"] = panel.loc[gecmis, "desi"].fillna(0.0)

    return panel.sort_values(["origin", "destination", "slot", "date"]).reset_index(drop=True)


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def validate_panel(panel, data):
    demand = data["demand"]
    n_routes = demand[["origin", "destination"]].drop_duplicates().shape[0]
    _assert(n_routes == 289, f"guzergah sayisi beklenenden farkli: {n_routes}")

    gecmis = panel[panel["date"] <= config.LAST_HISTORY_DATE]
    fill_ratio = 1 - (gecmis["desi"] == 0).sum() / len(gecmis)
    print(f"[panel] gecmis satir sayisi: {len(gecmis)}, gozlemlenmis oran: {fill_ratio:.3f} (beklenen ~0.638)")

    hedef = panel[(panel["date"] >= config.TARGET_START) & (panel["date"] <= config.TARGET_END)]
    _assert(hedef["desi"].isna().all(), "hedef penceredeki bazi satirlarda desi NaN degil")
    print(f"[panel] hedef pencere satir sayisi: {len(hedef)} "
          f"({hedef['date'].nunique()} gun x {n_routes} guzergah x {len(config.SLOT_CODES)} slot)")

    # Gec baslayan bir guzergahi spot-check et: ilk gozlem tarihinden onceki satir olmamali
    route_first = demand.groupby(["origin", "destination"])["date"].min()
    late_routes = route_first[route_first > pd.Timestamp("2026-01-05")]
    print(f"[panel] gec baslayan guzergah sayisi: {len(late_routes)} (beklenen 34)")
    for (o, d), first_date in late_routes.head(3).items():
        sub = panel[(panel["origin"] == o) & (panel["destination"] == d)]
        _assert(sub["date"].min() == first_date,
                f"{o}->{d} icin panel {sub['date'].min()} ile basliyor, beklenen {first_date}")
    print("[panel] gec baslayan guzergah spot-check OK")


if __name__ == "__main__":
    data = load_data()
    panel = build_panel(data)
    print("Panel boyutu:", panel.shape)
    print("Tarih araligi:", panel["date"].min().date(), "->", panel["date"].max().date())
    validate_panel(panel, data)
