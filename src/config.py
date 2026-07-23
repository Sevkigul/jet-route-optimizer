"""Yarışma kuralları ve dosya yollarından türetilen sabitler."""
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

# --- Ham veri dosyaları ---
FILE_TALEP_GECMIS = DATA_DIR / "teknofest26_gelismis.xlsx"
FILE_MESAFE_MATRISI = DATA_DIR / "sehirler_arasi_lojistik.xlsx"
FILE_ARAC_KAPASITE_MALIYET = DATA_DIR / "Araç_Kapasite_Maliyet_Saat.xlsx"
FILE_KIRALIK_ARACLAR = DATA_DIR / "Kiralık_Araclar.xlsx"
FILE_ELLECLEME_KAPASITE = DATA_DIR / "Ellecleme-kapasite.xlsx"
FILE_TIR_KAPASITE = DATA_DIR / "tir_kapasiteleri v2.xlsx"  # SkillCamp Q&A sonrası güncellenen versiyon

# --- Şablon dosyaları (format referansı) ---
FILE_TALEP_TAHMIN_SABLON = DATA_DIR / "TALEP TAHMİNİ.xlsx"
FILE_TASIMA_PLANI_SABLON = DATA_DIR / "TAŞIMA PLANI.xlsx"

# --- Çıktı dosyaları ---
FILE_OUT_TALEP_TAHMIN = OUTPUT_DIR / "Talep-tahmini.xlsx"
FILE_OUT_TASIMA_PLANI = OUTPUT_DIR / "Tasima-plani.xlsx"

# --- Talep tahmini ufku (Q&A'da netleştirildi) ---
TAHMIN_BASLANGIC = datetime(2026, 6, 29, 9, 0)
TAHMIN_BITIS = datetime(2026, 7, 5, 17, 0)
TALEP_SAATLERI = ["09:00", "17:00"]

# --- Elleçleme kuralları ---
ELLECLEME_DK_PER_DESI = 0.01  # hem çıkış hem varış elleçlemesi için aynı

# --- SLA cezası ---
SLA_CEZA_TL_PER_DESI_PER_SAAT = 0.4

# --- Araç tipleri ---
ARAC_TIPLERI = ["Tır", "Kamyon", "Hafif Kamyon", "Kamyonet"]
TIR_KAPASITE_KISITLI_TIP = "Tır"  # sadece bu tip için TM tır kapasitesi geçerli

# --- v3: Milk-run (çok duraklı uğrama) + zamansal biriktirme ---
# (v2'nin hub-and-spoke sabitleri kaldırıldı - referans çözüm karşılaştırması
# sonrası çift elleçleme sorunu nedeniyle milk-run mimarisiyle değiştirildi)
# (max_stops, thin_esik) ikilisi SLA-uyumlu rotalama eklendikten sonra yeniden
# tarandı: 16/16000, SLA cezası SIFIR iken en düşük maliyeti veriyor
# (13.97M vs 12/22400'ün 14.01M'i; daha uzun rota + biraz daha az havuzlama).
MILKRUN_MAX_STOPS = 16  # tek milk-run rotasındaki azami durak sayısı
MILKRUN_THIN_ESIK_DESI = 16000.0  # bunun altındaki hat-gün talebi "ince" sayılır (çok-günlü havuzlama adayı)
MULTIDAY_POOL = True  # kapatılırsa maliyet +83K TL ve ceza doğar (deneyle ölçüldü)
MULTIDAY_SLA_GUVENLIK_SAAT = 6  # deneyle doğrulandı: 4/2/0 saat hem cezayı hem maliyeti artırıyor
