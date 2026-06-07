"""Tüm pipeline'i tek komutta çalıştirir: mesafe matrisi -> tahmin -> optimizasyon."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from data_loader import load_data
from utils import get_coordinates, build_distance_matrix
from forecast import run_forecast
from optimize import load_all, run_optimization, save_output


def main():
    print("\n[1/3] Mesafe matrisi üretiliyor...")
    data = load_data()
    coords = get_coordinates(data)
    build_distance_matrix(coords)

    print("\n[2/3] Talep tahmini yapiliyor...")
    run_forecast()

    print("\n[3/3] Araç optimizasyonu yapiliyor...")
    tahmin, mesafe, kiral, kap = load_all()
    df, spot_total, rental_total = run_optimization(tahmin, mesafe, kiral, kap)
    save_output(df, spot_total, rental_total)

    print("\n" + "=" * 60)
    print("  Pipeline tamamlandi.")
    print(f"  Toplam maliyet: {spot_total + rental_total:,.2f} TL")
    print("  Çiktilar: outputs/talep_tahmini.xlsx, outputs/arac_plani.xlsx")
    print("=" * 60)


if __name__ == "__main__":
    main()