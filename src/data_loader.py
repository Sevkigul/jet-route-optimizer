"""Ham verilerin yuklenmesi ve standardizasyonu (Faz 1)."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config


def _slot_code(raw):
    return config.SLOT_MAP[raw]["code"]


def load_demand():
    """teknofest26_gelismis.xlsx dosyasini yukler ve kolonlari standardize eder."""
    df = pd.read_excel(config.DEMAND_FILE)
    df = df.rename(columns={
        "tarih": "date",
        "cikis": "origin",
        "varis": "destination",
        "toplam_desi": "desi",
        "talep_tamamlanma_saati": "slot_raw",
    })
    df["date"] = pd.to_datetime(df["date"])
    df["slot"] = df["slot_raw"].map(_slot_code)

    if df["slot"].isna().any():
        bilinmeyen = df.loc[df["slot"].isna(), "slot_raw"].unique()
        raise ValueError(f"Beklenmeyen slot degeri(leri): {bilinmeyen}")

    return df[["date", "origin", "destination", "slot", "desi"]]


def load_distance():
    """sehirler_arasi_lojistik.xlsx dosyasini yukler (statik guzergah ozellikleri)."""
    df = pd.read_excel(config.DISTANCE_FILE)
    df = df.rename(columns={
        "cikis": "origin",
        "varis": "destination",
        "mesafe_km": "mesafe_km",
        "hedef_teslim_gun": "sla_gun",
    })
    keep = ["origin", "destination", "mesafe_km", "sla_gun"]
    return df[keep].drop_duplicates(subset=["origin", "destination"])


def load_data():
    demand = load_demand()
    distance = load_distance()
    return {"demand": demand, "distance": distance}


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def validate_demand(df):
    """Yukleme sonrasi bilinen ground-truth sayilarla dogrulama."""
    _assert(len(df) == 66024, f"satir sayisi beklenenden farkli: {len(df)}")
    _assert(df["origin"].nunique() == 18, f"cikis TM sayisi beklenenden farkli: {df['origin'].nunique()}")
    _assert(df["destination"].nunique() == 17, f"varis TM sayisi beklenenden farkli: {df['destination'].nunique()}")
    _assert(df["date"].nunique() == 179, f"gun sayisi beklenenden farkli: {df['date'].nunique()}")
    n_routes = df[["origin", "destination"]].drop_duplicates().shape[0]
    _assert(n_routes == 289, f"guzergah sayisi beklenenden farkli: {n_routes}")
    dupes = df.duplicated(subset=["date", "origin", "destination", "slot"]).sum()
    _assert(dupes == 0, f"duplicate (date,route,slot) satiri var: {dupes}")
    print(f"[data_loader] dogrulama OK: {len(df)} satir, {n_routes} guzergah, "
          f"{df['date'].nunique()} gun, slot degerleri {sorted(df['slot'].unique())}")


if __name__ == "__main__":
    data = load_data()
    validate_demand(data["demand"])
    print("\ndistance ornegi:")
    print(data["distance"].head())
