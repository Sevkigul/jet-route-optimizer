"""Rota/arac optimizasyonu icin tum ayarlanabilir degerler tek yerde.

RENTAL_EXEMPT_FROM_TRUCK_CAP: ARTIK KAPALI (False). Organizasyon Soru-Cevap
oturumunda acikca dogruladi: "Kiralik araclar da tir kapasitesini tuketir."
Onceki celiski (Balikesir=0, Tekirdag=1 ama zorunlu kiralik Tir talebi bunu
astigi icin) organizasyon tarafindan tir_kapasiteleri v2 dosyasiyla
COZULDU (Balikesir 0->1, Tekirdag 1->2 - tam da zorunlu kiralik yukune
denk geliyor). Artik muafiyete gerek yok.
"""
from pathlib import Path

import pandas as pd

import config as demand_config

ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR / "outputs"

HANDLING_CAPACITY_FILE = ROOT_DIR / "Ellecleme-kapasite.xlsx"
TRUCK_CAPACITY_FILE = ROOT_DIR / "tir_kapasiteleri v2.xlsx"
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

# --- Tir kapasitesi (bkz. yukaridaki docstring) ----------------------------
RENTAL_EXEMPT_FROM_TRUCK_CAP = False
TRUCK_CAP_VEHICLE_TYPE = "Tır"   # kapasite sadece bu arac tipini sinirlar (organizasyon dogruladi)

# --- Kiralik arac varsayilan cikis saati (talep sifir olsa bile) -----------
RENTAL_DEFAULT_HOUR = 9

# --- Dusuk hacimli rotalarda "biriktir mi cikar mi" karari ------------------
# Min-doluluk KURALI yok ama maliyet acisindan hala anlamli: sabit arac
# maliyeti yuke bagli olmadigindan, dusuk talepli rotalarda her gun kucuk
# bir arac cikarmak yerine birkac gunluk talebi biriktirip tek seferde
# gondermek cok daha ucuz olabilir. Bu iki parametre bu karari yonetir:
#   - batch/en_kucuk_arac_kapasitesi < MIN_FILL_THRESHOLD VE
#   - havuzdaki en erken deadline'a en az SLA_SAFETY_HOURS saat varsa
#   -> bugun cikma, biriktirmeye devam et (kapasite/SLA riski yoksa).
# Guvenlik payi formulu: required_lead_hours = rotanin en hizli arac tipi
# suresi + SLA_SAFETY_HOURS (bkz. scheduler.py). Bu, uzun rotalarda (orn.
# Istanbul->Sanliurfa, 1213km, ~15-18 saat yolculuk) duz bir sabit saatin
# yetersiz kalacagini hesaba katar - rota ne kadar uzunsa o kadar erken
# zorunlu dispatch tetiklenir. SLA_SAFETY_HOURS negatif olabilir (kalan
# payin negatif olmasi, "en hizli aracin bile tam zamaninda yetisemeyecegi"
# anlamina gelir - SLA cezasi bu veri setinde (0.4 TL/desi/saat) arac
# maliyetine kiyasla ucuz oldugundan, bu bilincli bir maliyet tercihi).
# Degerler parametre taramasiyla (fill_threshold x safety_hours grid search)
# bulundu - toplam maliyeti (arac + SLA cezasi) minimize eden nokta. Milkrun'daki
# eksik-release bugu (bkz. milkrun.py) duzeltildikten sonra milk-run gercekten
# calismaya basladigi icin optimum nokta kaydi: dusuk-doluluk esigini daha da
# dusurmek (0.50 -> 0.18) artik daha iyi sonuc veriyor, cunku eskiden "beklet,
# doluluk artsin" tek konsolidasyon yoluyken artik ayni gun farkli hedeflere
# giden ince yukler milk-run ile (SLA riski almadan) birlestirilebiliyor - bu
# yuzden gun-bazli bekletmeye daha az ihtiyac var (eski deger 0.65/-4, sonra
# 0.50/-2 idi).
MIN_FILL_THRESHOLD = 0.18
SLA_SAFETY_HOURS = -2

# Rota bazinda SLA suresine (1 veya 2 gun) gore FARKLI guvenlik payi denendi
# (parametre taramasi) - hicbir kombinasyon global SLA_SAFETY_HOURS'tan daha
# iyi cikmadi (mevcut deger zaten oyle bir yerel optimumda ki rota bazinda
# ayirt etmek net kazanc saglamiyor). Bu yuzden ayni degeri kullanmaya devam
# ediyoruz - SLA_SAFETY_HOURS_BY_GUN karmasikligi kaldirildi.

# --- Konsolidasyon ----------------------------------------------------------
CONSOLIDATION_MAX_HOPS = 1   # sadece 1-hop (A->B->C) trans-shipment aranir
CONSOLIDATION_ENABLED = True

# --- Arac tipleri (sabit sira, Araç_Kapasite_Maliyet_Saat.xlsx ile ayni) ---
VEHICLE_TYPES = ["Tır", "Kamyon", "Hafif Kamyon", "Kamyonet"]

# --- Cikti bicimlendirme ---------------------------------------------------
OUTPUT_DATE_FORMAT = "%d.%m.%Y"
OUTPUT_TIME_FORMAT = "%H:%M"   # organizasyon netlestirmesi: "SS:DD yeterli" (saniyeye gerek yok)
COST_ROUND_DECIMALS = 2
DESI_ROUND_DECIMALS = 1
