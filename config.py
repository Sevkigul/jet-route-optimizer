"""Talep tahmini icin tum ayarlanabilir degerler tek yerde.

Hedef tarih araligi yarisma organizasyonundan henuz teyit edilmedi.
Teyit gelince sadece TARGET_START / TARGET_END degistirilecek, baska
kod degisikligi gerekmeyecek.
"""
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR / "outputs"

DEMAND_FILE = ROOT_DIR / "teknofest26_gelismis.xlsx"
DISTANCE_FILE = ROOT_DIR / "sehirler_arasi_lojistik.xlsx"
TEMPLATE_FILE = ROOT_DIR / "TALEP TAHMİNİ.xlsx"
OUTPUT_FILE = OUTPUT_DIR / "talep_tahmini_gelismis.xlsx"

# --- Tarih araligi -----------------------------------------------------
LAST_HISTORY_DATE = pd.Timestamp("2026-06-28")   # verideki son gun

# PLACEHOLDER - organizasyon teyit edince guncelle:
TARGET_START = pd.Timestamp("2026-06-29")
TARGET_END = pd.Timestamp("2026-07-05")

# --- Saat dilimleri ------------------------------------------------------
# Ham veri "9:00"/"17:00" iken cikti formati "09:00:00"/"17:00:00" bekliyor.
# Tek mapping'ten turetilir, string round-trip'e guvenilmez.
SLOT_MAP = {
    "9:00": {"code": 0, "display": "09:00:00"},
    "17:00": {"code": 1, "display": "17:00:00"},
}
SLOT_CODES = [v["code"] for v in SLOT_MAP.values()]
CODE_TO_DISPLAY = {v["code"]: v["display"] for v in SLOT_MAP.values()}

# --- Ufuk / lag ayarlari --------------------------------------------------
# Siginti (leakage) onlemi: en kisa lag, tahmin ufkunun uzunlugundan kisa olamaz.
FORECAST_HORIZON_DAYS = (TARGET_END - TARGET_START).days + 1
MIN_LAG_DAYS = max(7, FORECAST_HORIZON_DAYS)
LAG_DAYS = sorted({MIN_LAG_DAYS, MIN_LAG_DAYS + 7, MIN_LAG_DAYS + 14})
ROLLING_WINDOWS = [7, 28]

# --- Resmi tatiller (2026, Ocak - Temmuz) --------------------------------
# Kurban Bayrami (27-30 Mayis) web'den dogrulandi - eski asamadaki referans
# repo bunu icermiyordu cunku onlarin verisi 17 Mayis'ta bitiyordu.
OFFICIAL_HOLIDAYS = pd.to_datetime([
    "2026-01-01",                              # Yilbasi
    "2026-03-20", "2026-03-21", "2026-03-22",  # Ramazan Bayrami
    "2026-04-23",                              # 23 Nisan
    "2026-05-01",                              # 1 Mayis
    "2026-05-19",                              # 19 Mayis
    "2026-05-27", "2026-05-28", "2026-05-29", "2026-05-30",  # Kurban Bayrami
])

# --- Model ayarlari --------------------------------------------------------
SLOT_MODE = "shared"   # "shared" (tek model + slot ozelligi) | "separate" (slot basina ayri model)

LGB_PARAMS = {
    "objective": "tweedie",
    "tweedie_variance_power": 1.25,
    "num_leaves": 15,
    "learning_rate": 0.03,
    "n_estimators": 300,
    "min_child_samples": 40,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "random_state": 42,
    "verbose": -1,
}

CAT_PARAMS = {
    "loss_function": "Tweedie:variance_power=1.25",
    "depth": 5,
    "learning_rate": 0.03,
    "iterations": 300,
    "random_seed": 42,
    "verbose": 0,
    "allow_writing_files": False,
}

VALIDATION_FOLDS = 6   # 179 gunluk veriyle eski asamadaki 3'ten daha fazla pencere mumkun

# --- Cikti bicimlendirme ---------------------------------------------------
OUTPUT_DATE_FORMAT = "%d.%m.%Y"
DESI_ROUND_DECIMALS = 1
