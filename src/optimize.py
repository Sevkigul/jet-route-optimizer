"""Araç optimizasyonu — tahmin edilen talebi en düşük maliyetle araçlara dağıtır.

Her gün her güzergah için: önce sabit kiralik filo kapasitesi doldurulur,
kalan yük tamsayili programlama (MIP) ile en ucuz spot araç kombinasyonuna atanir.
"""
import os
import glob
import warnings

import pandas as pd
from pulp import LpProblem, LpMinimize, LpVariable, lpSum, value, PULP_CBC_CMD

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR  = os.path.join(BASE_DIR, "data", "raw")
PROC_DIR = os.path.join(BASE_DIR, "data", "processed")
OUT_DIR  = os.path.join(BASE_DIR, "outputs")
os.makedirs(OUT_DIR, exist_ok=True)

TAHMIN_PATH = os.path.join(OUT_DIR,  "talep_tahmini.xlsx")
MESAFE_PATH = os.path.join(PROC_DIR, "mesafe_matrisi.csv")
OUTPUT_PATH = os.path.join(OUT_DIR,  "arac_plani.xlsx")

# İki yönlü kiralik: A->B araci B->A'da da kullanilabiliyorsa True yapin.
BIDIRECTIONAL_KIRALIK = False


def find_file(directory, keyword):
    """Adinda keyword geçen ilk .xlsx dosyasini bulur."""
    for f in glob.glob(os.path.join(directory, "*.xlsx")):
        if keyword.lower() in os.path.basename(f).lower():
            return f
    raise FileNotFoundError(f"'{keyword}' içeren .xlsx bulunamadi: {directory}")


def load_all():
    """Tahmin, mesafe, kiralik filo ve araç maliyet verilerini yükler."""
    print("Veriler yükleniyor...")
    tahmin = pd.read_excel(TAHMIN_PATH).iloc[:, :4]
    tahmin.columns = ["origin", "destination", "date", "demand"]
    tahmin["date"] = pd.to_datetime(tahmin["date"])
    tahmin["demand"] = tahmin["demand"].clip(lower=0)

    mesafe = pd.read_csv(MESAFE_PATH, index_col=0)

    kiral = pd.read_excel(find_file(RAW_DIR, "kiral")).iloc[:, :4]
    kiral.columns = ["origin", "destination", "count", "vehicle_type"]

    # 6 sütun: ad, kapasite, kiralik günlük, kiralik km, spot günlük, spot km.
    # Spot atama icin spot sütunlari (5-6) kullanilir.
    kap = pd.read_excel(find_file(RAW_DIR, "kapasite")).iloc[:, :6]
    kap.columns = ["vehicle_type", "capacity", "rental_daily", "rental_km",
                   "spot_daily", "spot_km"]

    print(f"  tahmin {tahmin.shape} | mesafe {mesafe.shape} | "
          f"kiralik {len(kiral)} satir | araç {len(kap)} tip")
    return tahmin, mesafe, kiral, kap


def build_rental_capacity(kiral, capacity):
    """Güzergah bazinda kiralik kapasite (desi)."""
    cap = {}
    for _, r in kiral.iterrows():
        key = (r["origin"], r["destination"])
        cap[key] = cap.get(key, 0.0) + capacity.get(r["vehicle_type"], 0) * int(r["count"])

    if BIDIRECTIONAL_KIRALIK:
        birlesik = {}
        for (o, d), c in cap.items():
            toplam = c + cap.get((d, o), 0.0)
            birlesik[(o, d)] = toplam
            birlesik[(d, o)] = toplam
        return birlesik
    return cap


def solve_spot(remaining, dist, vts, capacity, spot_daily, spot_km):
    """Kalan yükü en ucuz spot araç kombinasyonuna atar (tamsayili programlama).

    Araç sayilari tam sayi olmak zorunda; MIP global en ucuz çözümü garantiler.
    Spot maliyet = arac_sayisi * (spot_gunluk + spot_km * mesafe).
    """
    if remaining <= 0 or dist <= 0:
        return {v: 0 for v in vts}

    unit = {v: spot_daily[v] + spot_km[v] * dist for v in vts}

    prob = LpProblem("spot", LpMinimize)
    x = {v: LpVariable(f"x_{v}", lowBound=0, cat="Integer") for v in vts}
    prob += lpSum(unit[v] * x[v] for v in vts)                      # amaç: maliyet
    prob += lpSum(capacity[v] * x[v] for v in vts) >= remaining     # kisit: kapasite
    prob.solve(PULP_CBC_CMD(msg=0))

    return {v: int(value(x[v]) or 0) for v in vts}


def rental_weekly_cost(kiral, rental_daily, rental_km, mesafe, gun):
    """Sabit kiralik filonun dönem boyunca toplam maliyeti."""
    toplam = 0.0
    for _, r in kiral.iterrows():
        o, d, n, vt = r["origin"], r["destination"], int(r["count"]), r["vehicle_type"]
        dist = mesafe.loc[o, d] if (o in mesafe.index and d in mesafe.columns) else 0.0
        toplam += n * gun * (rental_daily.get(vt, 0) + rental_km.get(vt, 0) * dist)
    return toplam


def run_optimization(tahmin, mesafe, kiral, kap):
    print("Optimizasyon basliyor...")

    vts        = kap["vehicle_type"].tolist()
    capacity   = dict(zip(kap["vehicle_type"], kap["capacity"]))
    spot_daily = dict(zip(kap["vehicle_type"], kap["spot_daily"]))
    spot_km    = dict(zip(kap["vehicle_type"], kap["spot_km"]))
    rent_daily = dict(zip(kap["vehicle_type"], kap["rental_daily"]))
    rent_km    = dict(zip(kap["vehicle_type"], kap["rental_km"]))

    rental_cap = build_rental_capacity(kiral, capacity)

    rows = []
    spot_total = 0.0
    dates = sorted(tahmin["date"].unique())

    for date in dates:
        gun = tahmin[tahmin["date"] == date]
        for _, r in gun.iterrows():
            o, d, dem = r["origin"], r["destination"], r["demand"]
            kiralik = rental_cap.get((o, d), 0.0)
            kalan = max(0.0, dem - kiralik)
            dist = mesafe.loc[o, d] if (o in mesafe.index and d in mesafe.columns) else 0.0

            counts = solve_spot(kalan, dist, vts, capacity, spot_daily, spot_km)
            cost = sum((spot_daily[v] + spot_km[v] * dist) * c
                       for v, c in counts.items())
            spot_total += cost

            row = {
                "Tarih": date.strftime("%Y-%m-%d"),
                "Çıkış Merkezi": o,
                "Varış Merkezi": d,
                "Tahmin Edilen Desi": round(dem, 1),
                "Kiralık Kapasite": round(kiralik, 1),
                "Kalan Yük (Spot)": round(kalan, 1),
                "Mesafe (km)": round(dist, 1),
                "Spot Maliyet (TL)": round(cost, 2),
            }
            for v in vts:
                row[f"Spot_{v}"] = counts.get(v, 0)
            rows.append(row)
        print(f"  {date.strftime('%Y-%m-%d')} tamamlandi")

    rental_total = rental_weekly_cost(kiral, rent_daily, rent_km, mesafe, len(dates))
    return pd.DataFrame(rows), spot_total, rental_total


def save_output(df, spot_total, rental_total):
    """Araç planini ve maliyet özetini Excel'e yazar."""
    grand = spot_total + rental_total
    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Araç Planı", index=False)
        ozet = (df.groupby("Tarih")["Spot Maliyet (TL)"].sum().reset_index()
                  .rename(columns={"Spot Maliyet (TL)": "Günlük Spot Maliyet (TL)"}))
        ozet.loc[len(ozet)] = ["Spot Toplam", round(spot_total, 2)]
        ozet.loc[len(ozet)] = ["Kiralık Toplam (sabit)", round(rental_total, 2)]
        ozet.loc[len(ozet)] = ["GENEL TOPLAM", round(grand, 2)]
        ozet.to_excel(w, sheet_name="Maliyet Özeti", index=False)
    print(f"\nAraç plani kaydedildi: {OUTPUT_PATH}")


def main():
    print("=" * 60)
    print("  HepsiJET — Araç Optimizasyonu")
    print("=" * 60)

    tahmin, mesafe, kiral, kap = load_all()
    df, spot_total, rental_total = run_optimization(tahmin, mesafe, kiral, kap)
    save_output(df, spot_total, rental_total)

    print("\n" + "=" * 60)
    print(f"  Spot maliyet    : {spot_total:,.2f} TL")
    print(f"  Kiralık maliyet : {rental_total:,.2f} TL")
    print(f"  GENEL TOPLAM    : {spot_total + rental_total:,.2f} TL")
    print("=" * 60)


if __name__ == "__main__":
    main()