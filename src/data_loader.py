"""Ham excel dosyalarını temiz pandas DataFrame'lere / lookup yapılarına dönüştürür.

Tüm fonksiyonlar saf okuma yapar, iş kuralı (SLA, elleçleme vs.) içermez.
"""
from __future__ import annotations

import pandas as pd

from . import config


def load_gecmis_talep() -> pd.DataFrame:
    """Geçmiş talep verisi: tarih, cikis, varis, talep_id, toplam_desi, saat (09:00/17:00 str)."""
    df = pd.read_excel(config.FILE_TALEP_GECMIS)
    df["talep_tamamlanma_saati"] = df["talep_tamamlanma_saati"].apply(_normalize_saat_str)
    df["tarih"] = pd.to_datetime(df["tarih"]).dt.normalize()
    return df


def load_mesafe_matrisi() -> pd.DataFrame:
    """Çıkış-varış ikilisi başına mesafe, araç tipine göre seyir süresi (saat) ve SLA (gün)."""
    df = pd.read_excel(config.FILE_MESAFE_MATRISI)
    df = df.rename(
        columns={
            "cikis": "cikis",
            "varis": "varis",
            "mesafe_km": "mesafe_km",
            "Tir_Suresi_Saat": "sure_saat_Tır",
            "Kamyon_Suresi_Saat": "sure_saat_Kamyon",
            "Hafif_Kamyon_Suresi_Saat": "sure_saat_Hafif Kamyon",
            "Kamyonet_Suresi_Saat": "sure_saat_Kamyonet",
            "hedef_teslim_gun": "sla_gun",
        }
    )
    return df


def load_arac_kapasite_maliyet() -> pd.DataFrame:
    """Araç tipi başına desi kapasitesi, kiralık/spot saatlik kira ve km maliyeti."""
    df = pd.read_excel(config.FILE_ARAC_KAPASITE_MALIYET)
    df = df.rename(
        columns={
            "Araç Adı": "arac_tipi",
            "Kapasite (desi)": "kapasite_desi",
            "Kiralık Araç Saatlik Kira (TL)": "kiralik_saatlik_tl",
            "Kiralık Araç Kilometre Başına Maliyet (TL)": "kiralik_km_tl",
            "Spot Araç Saatlik Kira (TL)": "spot_saatlik_tl",
            "Spot Kilometre Başına Maliyet (TL)": "spot_km_tl",
        }
    )
    return df.set_index("arac_tipi")


def load_kiralik_araclar() -> pd.DataFrame:
    """Her gün zorunlu çıkması gereken kiralık araç hatları."""
    df = pd.read_excel(config.FILE_KIRALIK_ARACLAR)
    df = df.rename(
        columns={
            "Çıkış Transfer Merkezi": "cikis",
            "Varış Transfer Merkezi": "varis",
            "Araç sayısı": "arac_sayisi",
            "Araç Türü": "arac_tipi",
        }
    )
    return df


def load_ellecleme_kapasite() -> dict[str, float]:
    """TM adı -> günlük elleçleme kapasitesi (desi)."""
    df = pd.read_excel(config.FILE_ELLECLEME_KAPASITE)
    return dict(zip(df["transfer_merkezi"], df["ellecleme_kapasite"]))


def load_tir_kapasite() -> dict[str, int]:
    """TM adı -> günlük tır işlem kapasitesi (adet). Güncellenmiş v2 dosyası kullanılıyor."""
    df = pd.read_excel(config.FILE_TIR_KAPASITE)
    return dict(zip(df["transfer_merkezi"], df["tir_kapasitesi"]))


def _normalize_saat_str(value) -> str:
    """'9:00' / datetime.time(9,0) gibi değerleri 'HH:MM' formatına çevirir."""
    if hasattr(value, "hour"):
        return f"{value.hour:02d}:{value.minute:02d}"
    hh, mm = str(value).split(":")
    return f"{int(hh):02d}:{int(mm):02d}"


def load_all() -> dict:
    """Tüm veri setlerini tek seferde yükler."""
    return {
        "gecmis_talep": load_gecmis_talep(),
        "mesafe_matrisi": load_mesafe_matrisi(),
        "arac_kapasite_maliyet": load_arac_kapasite_maliyet(),
        "kiralik_araclar": load_kiralik_araclar(),
        "ellecleme_kapasite": load_ellecleme_kapasite(),
        "tir_kapasite": load_tir_kapasite(),
    }
