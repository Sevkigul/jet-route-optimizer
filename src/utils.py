"""Yardimci fonksiyonlar - eksik koordinat tamamlama ve mesafe matrisi üretimi."""
from pathlib import Path
import numpy as np
import pandas as pd

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

# Koordinatlar.xlsx'te eksik olan merkezler.
EKSIK_KOORDINAT = {
    "Kocaeli": (40.7655, 29.9408),  
}

def get_coordinates(data):
    """Koordinat tablosunu döner; eksik merkezleri EKSIK_KOORDINAT'tan tamamlar."""
    coords = data["koordinat"].copy()
    mevcut = set(coords["center"])
    eklenecek = [
        {"center": m, "lat": lat, "lon": lon}
        for m, (lat, lon) in EKSIK_KOORDINAT.items()
        if m not in mevcut
    ]
    if eklenecek:
        coords = pd.concat([coords, pd.DataFrame(eklenecek)], ignore_index=True)
        print(f"Koordinata elle eklendi: {[e['center'] for e in eklenecek]}")
    return coords

def haversine(lat1, lon1, lat2, lon2):

    R = 6371.0  # Dunya yaricapi (km)
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return R * 2 * np.arcsin(np.sqrt(a))

def build_distance_matrix(coords, save=True):
    """Tum merkez çiftleri arasi mesafe matrisi (km) uretir."""
    centers = coords["center"].tolist()
    lat = coords.set_index("center")["lat"]
    lon = coords.set_index("center")["lon"]
    mat = pd.DataFrame(index=centers, columns=centers, dtype=float)
    for i in centers:
        for j in centers:
            mat.loc[i, j] = 0.0 if i == j else haversine(
                lat[i], lon[i], lat[j], lon[j]
            )
    if save:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        out = PROCESSED_DIR / "mesafe_matrisi.csv"
        mat.to_csv(out, encoding="utf-8-sig")
        print(f"Mesafe matrisi kaydedildi: {out}")
    return mat


if __name__ == "__main__":
    from data_loader import load_data

    data = load_data()
    coords = get_coordinates(data)
    mat = build_distance_matrix(coords)
    print(f"\nMesafe matrisi boyutu: {mat.shape}")
    print("Ornek (ilk 5x5, km):")
    print(mat.iloc[:5, :5].round(1))