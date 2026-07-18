"""Talep ID uretimi ve TALEP TAHMINI.xlsx sablonuna birebir uygun cikti yazimi (Faz 4).

Format yanlisligi yarismada dogrudan eleme sebebi oldugundan, yazmadan
once gercek sablon dosyasiyla kolon/format karsilastirmasi yapilir.
"""
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config

REQUIRED_COLUMNS = [
    "Talep ID",
    "Tarih",
    "Talep Tamamlama Saati",
    "Çıkış Transfer Merkezi",
    "Varış Transfer Merkezi",
    "Tahmin Edilen Desi",
]


def build_output(feat_with_pred):
    """Tahmin panelinden [TARGET_START, TARGET_END] satirlarini alip sablon formatinda dataframe uretir."""
    hedef = feat_with_pred[
        (feat_with_pred["date"] >= config.TARGET_START) & (feat_with_pred["date"] <= config.TARGET_END)
    ].copy()

    hedef["slot_display"] = hedef["slot"].map(config.CODE_TO_DISPLAY)
    hedef["tarih_display"] = hedef["date"].dt.strftime(config.OUTPUT_DATE_FORMAT)
    hedef["desi_display"] = hedef["tahmin"].round(config.DESI_ROUND_DECIMALS)

    hedef = hedef.sort_values(["date", "origin", "destination", "slot_display"]).reset_index(drop=True)
    hedef["talep_id"] = [f"D{i:05d}" for i in range(1, len(hedef) + 1)]

    out = pd.DataFrame({
        "Talep ID": hedef["talep_id"],
        "Tarih": hedef["tarih_display"],
        "Talep Tamamlama Saati": hedef["slot_display"],
        "Çıkış Transfer Merkezi": hedef["origin"],
        "Varış Transfer Merkezi": hedef["destination"],
        "Tahmin Edilen Desi": hedef["desi_display"],
    })
    return out


def guard_against_template(out_df):
    """Yazmadan once gercek sablon dosyasiyla sema karsilastirmasi - sessizce gecilmez, hata firlatir."""
    template = pd.read_excel(config.TEMPLATE_FILE)

    if list(out_df.columns) != list(template.columns):
        raise AssertionError(
            f"Kolon uyumsuzlugu!\n  beklenen: {list(template.columns)}\n  uretilen: {list(out_df.columns)}"
        )

    ids = out_df["Talep ID"]
    if ids.nunique() != len(ids):
        raise AssertionError("Talep ID degerleri unique degil")
    kotu_id = ids[~ids.str.match(r"^D\d{5}$")]
    if len(kotu_id) > 0:
        raise AssertionError(f"Talep ID formatina uymayan degerler: {kotu_id.tolist()[:5]}")

    izin_verilen_saatler = {v["display"] for v in config.SLOT_MAP.values()}
    kotu_saat = set(out_df["Talep Tamamlama Saati"].unique()) - izin_verilen_saatler
    if kotu_saat:
        raise AssertionError(f"Beklenmeyen 'Talep Tamamlama Saati' degerleri: {kotu_saat}")

    print(f"[output] sema kontrolu OK: {len(out_df)} satir, kolonlar sablona birebir uyuyor")


def save_output(out_df):
    guard_against_template(out_df)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_df.to_excel(config.OUTPUT_FILE, index=False)
    print(f"[output] kaydedildi: {config.OUTPUT_FILE}")
    print(f"[output] {len(out_df)} satir | {out_df['Tarih'].nunique()} gun | "
          f"{out_df[['Çıkış Transfer Merkezi','Varış Transfer Merkezi']].drop_duplicates().shape[0]} guzergah")
    print(f"[output] toplam tahmin edilen desi: {out_df['Tahmin Edilen Desi'].sum():,.1f}")
    return out_df
