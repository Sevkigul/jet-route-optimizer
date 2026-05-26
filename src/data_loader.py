"""Veri yukleme modulu - 4 Excel dosyasini okur ve standart isimlerle dondurur."""
from pathlib import Path
import pandas as pd

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# data/raw/ icindeki gercek dosya adlari
FILES = {
    "desi":      "Desi_talep.xlsx",
    "koordinat": "Koordinatlar.xlsx",
    "kiralik":   "Kiralik_Araclar.xlsx",
    "arac":      "Arac_Kapasite_Maliyet.xlsx",
}

# Turkce sutun adlari -> kod dostu standart adlar
RENAME = {
    "desi": {
        "Çıkış Transfer Merkezi": "origin",
        "Varış Transfer Merkezi": "destination",
        "Tarih": "date",
        "Toplam Desi": "desi",
    },
    "koordinat": {
        "Transfer Merkezi": "center",
        "Enlem": "lat",
        "Boylam": "lon",
    },
    "kiralik": {
        "Çıkış Transfer Merkezi": "origin",
        "Varış Transfer Merkezi": "destination",
        "Araç sayısı": "vehicle_count",
        "Araç Türü": "vehicle_type",
    },
    "arac": {
        "Araç Adı": "vehicle_type",
        "Kapasite (desi)": "capacity",
        "Kiralık Araç Günlük Kira (TL)": "rental_daily_cost",
        "Kiralık Araç Kilometre Başına Maliyet (TL)": "rental_km_cost",
        "Spot Araç Sabit Günlük Maliyet (TL)": "spot_daily_cost",
        "Spot Kilometre Başına Maliyet (TL)": "spot_km_cost",
    },
}


def _clean_text(df):
    """Metin sütunlarindaki bas/son boşluklari temizler."""
    for col in df.columns:
        if df[col].dtype == "object" or str(df[col].dtype).startswith("str"):
            df[col] = df[col].astype(str).str.strip()
    return df


def load_data(raw_dir=RAW_DIR):
    """4 Excel dosyasini yukler, standart sütun adlariyla dict döndürür."""
    raw_dir = Path(raw_dir)
    data = {}
    for key, fname in FILES.items():
        path = raw_dir / fname
        if not path.exists():
            raise FileNotFoundError(f"Dosya bulunamadi: {path}")
        df = pd.read_excel(path).rename(columns=RENAME[key])
        data[key] = _clean_text(df)

    # Tarih sutununu datetime'a cevir
    data["desi"]["date"] = pd.to_datetime(data["desi"]["date"])
    return data


def check_coverage(data):
    """Desi ve Kiralik merkezlerinin Koordinatlar'da var olup olmadiğini kontrol eder."""
    centers = set(data["koordinat"]["center"])
    desi_centers = set(data["desi"]["origin"]) | set(data["desi"]["destination"])
    kiralik_centers = set(data["kiralik"]["origin"]) | set(data["kiralik"]["destination"])
    missing_desi = sorted(desi_centers - centers)
    missing_kiralik = sorted(kiralik_centers - centers)

    if missing_desi:
        print(f"UYARI - Desi'de olup Koordinatlar'da OLMAYAN: {missing_desi}")
    if missing_kiralik:
        print(f"UYARI - Kiralik'ta olup Koordinatlar'da OLMAYAN: {missing_kiralik}")
    if not missing_desi and not missing_kiralik:
        print("Tum merkezlerin koordinati mevcut.")
    return missing_desi, missing_kiralik


if __name__ == "__main__":
    data = load_data()
    for key, df in data.items():
        print(f"{key}: {df.shape}  sutunlar={list(df.columns)}")
    print()
    check_coverage(data)