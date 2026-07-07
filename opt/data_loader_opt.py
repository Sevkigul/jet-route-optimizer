"""Optimizasyon icin veri yukleme, standardizasyon ve capraz dogrulama."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config_opt as cfg


def load_handling_capacity():
    df = pd.read_excel(cfg.HANDLING_CAPACITY_FILE)
    return dict(zip(df["transfer_merkezi"], df["ellecleme_kapasite"]))


def load_truck_capacity():
    df = pd.read_excel(cfg.TRUCK_CAPACITY_FILE)
    return dict(zip(df["transfer_merkezi"], df["tir_kapasitesi"]))


def load_rentals():
    df = pd.read_excel(cfg.RENTAL_FILE)
    df = df.rename(columns={
        "Çıkış Transfer Merkezi": "origin",
        "Varış Transfer Merkezi": "destination",
        "Araç sayısı": "count",
        "Araç Türü": "vehicle_type",
    })
    return df[["origin", "destination", "count", "vehicle_type"]].to_dict("records")


def load_vehicle_specs():
    df = pd.read_excel(cfg.VEHICLE_SPEC_FILE)
    df = df.rename(columns={
        "Araç Adı": "vehicle_type",
        "Kapasite (desi)": "capacity",
        "Kiralık Araç Saatlik Kira (TL)": "rental_hourly",
        "Kiralık Araç Kilometre Başına Maliyet (TL)": "rental_km",
        "Spot Araç Saatlik Kira (TL)": "spot_hourly",
        "Spot Kilometre Başına Maliyet (TL)": "spot_km",
    })
    specs = {}
    for _, r in df.iterrows():
        specs[r["vehicle_type"]] = {
            "capacity": r["capacity"],
            "rental_hourly": r["rental_hourly"],
            "rental_km": r["rental_km"],
            "spot_hourly": r["spot_hourly"],
            "spot_km": r["spot_km"],
        }
    return specs


DURATION_COL_BY_TYPE = {
    "Tır": "Tir_Suresi_Saat",
    "Kamyon": "Kamyon_Suresi_Saat",
    "Hafif Kamyon": "Hafif_Kamyon_Suresi_Saat",
    "Kamyonet": "Kamyonet_Suresi_Saat",
}


def load_distance():
    df = pd.read_excel(cfg.DISTANCE_FILE)
    dist = {}
    for _, r in df.iterrows():
        key = (r["cikis"], r["varis"])
        dist[key] = {
            "mesafe_km": r["mesafe_km"],
            "sla_gun": r["hedef_teslim_gun"],
            "duration": {vt: r[col] for vt, col in DURATION_COL_BY_TYPE.items()},
        }
    return dist


def load_forecast():
    df = pd.read_excel(cfg.FORECAST_FILE)
    df = df.rename(columns={
        "Talep ID": "talep_id",
        "Tarih": "tarih_str",
        "Talep Tamamlama Saati": "saat_str",
        "Çıkış Transfer Merkezi": "origin",
        "Varış Transfer Merkezi": "destination",
        "Tahmin Edilen Desi": "desi",
    })
    df["created_at"] = pd.to_datetime(
        df["tarih_str"] + " " + df["saat_str"], format="%d.%m.%Y %H:%M:%S"
    )
    return df[["talep_id", "created_at", "origin", "destination", "desi"]]


def load_data():
    return {
        "handling_capacity": load_handling_capacity(),
        "truck_capacity": load_truck_capacity(),
        "rentals": load_rentals(),
        "vehicle_specs": load_vehicle_specs(),
        "distance": load_distance(),
        "forecast": load_forecast(),
    }


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def validate_all(data):
    hubs_handling = set(data["handling_capacity"])
    hubs_truck = set(data["truck_capacity"])
    hubs_dist_o = {o for (o, d) in data["distance"]}
    hubs_dist_d = {d for (o, d) in data["distance"]}

    _assert(hubs_handling == hubs_truck,
            f"elleçleme ve tır kapasitesi hub setleri farklı: {hubs_handling ^ hubs_truck}")
    _assert(hubs_handling == hubs_dist_o,
            f"elleçleme hub seti ile mesafe matrisi çıkış seti farklı: {hubs_handling ^ hubs_dist_o}")
    _assert(hubs_dist_d.issubset(hubs_handling),
            f"mesafe matrisi varış setinde tanımsız hub var: {hubs_dist_d - hubs_handling}")

    for r in data["rentals"]:
        key = (r["origin"], r["destination"])
        _assert(key in data["distance"], f"kiralık rota mesafe matrisinde yok: {key}")

    forecast_pairs = set(zip(data["forecast"]["origin"], data["forecast"]["destination"]))
    missing = forecast_pairs - set(data["distance"])
    _assert(not missing, f"talep çiftleri mesafe matrisinde eksik: {missing}")

    n_routes = len(forecast_pairs)
    print(f"[data_loader_opt] doğrulama OK: {len(hubs_handling)} hub, "
          f"{len(data['distance'])} mesafe çifti, {len(data['rentals'])} kiralık rota, "
          f"{len(data['vehicle_specs'])} araç tipi, {n_routes} talep güzergahı, "
          f"{len(data['forecast'])} talep satırı")


if __name__ == "__main__":
    data = load_data()
    validate_all(data)
    print("\nörnek elleçleme kapasitesi:", list(data["handling_capacity"].items())[:3])
    print("örnek tır kapasitesi:", list(data["truck_capacity"].items())[:3])
    print("örnek araç spec:", data["vehicle_specs"]["Tır"])
    print("örnek mesafe:", data["distance"][("İstanbul", "Yalova")])
