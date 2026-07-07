"""Rota/arac optimizasyonu icin tum ayarlanabilir degerler tek yerde.

RENTAL_EXEMPT_FROM_TRUCK_CAP: veri setinde bir celiski var - bazi hublarda
tir_kapasitesi=0 ama oraya zorunlu kiralik Tir rotasi tanimli (orn.
Istanbul->Balikesir). Kullaniciyla netlestik: kiralik araclar tir kapasitesi
kisitindan muaf sayilir, sadece spot Tir araclari bu limite tabi olur.
Organizasyondan netlik gelirse tek satir degisir.
"""
from pathlib import Path

import pandas as pd

import config as demand_config

ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR / "outputs"

HANDLING_CAPACITY_FILE = ROOT_DIR / "Ellecleme-kapasite.xlsx"
TRUCK_CAPACITY_FILE = ROOT_DIR / "tir_kapasiteleri.xlsx"
RENTAL_FILE = ROOT_DIR / "Kiralık_Araclar.xlsx"
VEHICLE_SPEC_FILE = ROOT_DIR / "Araç_Kapasite_Maliyet_Saat.xlsx"
DISTANCE_FILE = ROOT_DIR / "sehirler_arasi_lojistik.xlsx"
FORECAST_FILE = demand_config.OUTPUT_FILE   # outputs/talep_tahmini_gelismis.xlsx

TASIMA_TEMPLATE_FILE = ROOT_DIR / "TAŞIMA PLANI.xlsx"
TASIMA_OUTPUT_FILE = OUTPUT_DIR / "tasima_plani_gelismis.xlsx"

# --- Planlama araligi (talep tahmini ile ayni hedef pencere) -------------
TARGET_START = demand_config.TARGET_START
TARGET_END = demand_config.TARGET_END

# --- Elleçleme -------------------------------------------------------------
HANDLING_MIN_PER_DESI = 0.01   # dakika / desi

# --- SLA cezasi -------------------------------------------------------------
SLA_TL_PER_DESI_HOUR = 0.4

# --- Tir kapasitesi varsayimi (bkz. yukaridaki docstring) ------------------
RENTAL_EXEMPT_FROM_TRUCK_CAP = True
TRUCK_CAP_VEHICLE_TYPE = "Tır"   # kapasite sadece bu arac tipini sinirlar

# --- Kiralik arac varsayilan cikis saati (talep sifir olsa bile) -----------
RENTAL_DEFAULT_HOUR = 9

# --- Konsolidasyon ----------------------------------------------------------
CONSOLIDATION_MAX_HOPS = 1   # sadece 1-hop (A->B->C) trans-shipment aranir
CONSOLIDATION_ENABLED = True

# --- Arac tipleri (sabit sira, Araç_Kapasite_Maliyet_Saat.xlsx ile ayni) ---
VEHICLE_TYPES = ["Tır", "Kamyon", "Hafif Kamyon", "Kamyonet"]

# --- Cikti bicimlendirme ---------------------------------------------------
OUTPUT_DATE_FORMAT = "%d.%m.%Y"
OUTPUT_TIME_FORMAT = "%H:%M:%S"
COST_ROUND_DECIMALS = 2
DESI_ROUND_DECIMALS = 1
