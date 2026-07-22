"""Teknofest Gelişmiş Çözüm Aşaması — uçtan uca pipeline.

Talep tahmini -> saat bazlı milk-run taşıma planı -> bağımsız doğrulama.
Tek komutla çalışır:

    python main.py

Çıktılar `outputs/` altına yazılır: Talep-tahmini.xlsx, Tasima-plani.xlsx.
Mimari detayları için README.md'ye bakınız.
"""
from __future__ import annotations

import argparse
import time

import pandas as pd

from src import config, data_loader as dl, demand_forecast as fc, dogrulama, optimizer, output_writer as ow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-forecast", action="store_true",
        help="outputs/forecast.pkl zaten varsa talep tahminini yeniden üretme, doğrudan optimizasyona geç.",
    )
    args = parser.parse_args()

    t0 = time.time()
    veri = dl.load_all()

    if args.skip_forecast and (config.OUTPUT_DIR / "forecast.pkl").exists():
        forecast = pd.read_pickle(config.OUTPUT_DIR / "forecast.pkl")
        print(f"[1/3] Talep tahmini atlandı, mevcut forecast.pkl kullanılıyor ({len(forecast)} satır).")
    else:
        print("[1/3] Talep tahmini üretiliyor...")
        forecast = fc.forecast_all(veri["gecmis_talep"])
        forecast.to_pickle(config.OUTPUT_DIR / "forecast.pkl")
        ow.write_talep_tahmini(forecast, config.FILE_OUT_TALEP_TAHMIN)
        print(f"      {len(forecast)} satır -> {config.FILE_OUT_TALEP_TAHMIN}")

    print("[2/3] Taşıma planı optimize ediliyor (milk-run + kiralık + biriktirme)...")
    plan = optimizer.plan_olustur(forecast, veri)
    ow.write_tasima_plani(plan, config.FILE_OUT_TASIMA_PLANI)
    print(f"      {len(plan)} satır, {plan['Araç ID'].nunique()} sefer -> {config.FILE_OUT_TASIMA_PLANI}")

    print("[3/3] Bağımsız doğrulama çalıştırılıyor...\n")
    rapor, hata_sayisi = dogrulama.verify(plan, forecast, veri)
    print(rapor)

    print(f"\nToplam süre: {time.time()-t0:.1f} sn")
    if hata_sayisi:
        raise SystemExit(f"Doğrulama {hata_sayisi} hata buldu, yukarıya bakınız.")


if __name__ == "__main__":
    main()
