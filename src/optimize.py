"""
optimize_v2.py — Faz 3: Araç Optimizasyonu (DÜZELTILMIŞ & HIZLANDIRILMIŞ)
========================================
Düzeltilen kritik hatalar:
  1. Çift sayım hatası: Konsolidasyon kararları önce toplanır, sonra tek seferde çözülür.
  2. Gereksiz MIP kaldırıldı: fast_exact_spot_cost zaten globally optimal çözüm verir
     (4 araç tipi için brute-force = garantili optimal, LP gerekmiyor).
  3. İki yönlü kiralık kapasite: A→B ve B→A kiralık araçlar birlikte değerlendiriliyor.
  4. Merkeziyet bazlı hub seçimi: hacim × (1 / ortalama_mesafe) skoru kullanılıyor.
  5. Eager aggregation: Hub kararları commit edilip yük matrisi güncellendikten sonra çözüm.
"""

import warnings
warnings.filterwarnings("ignore")

import os
import glob
import math
import pandas as pd
import numpy as np
from itertools import product as iproduct

BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR   = os.path.join(BASE_DIR, "data", "raw")
PROC_DIR  = os.path.join(BASE_DIR, "data", "processed")
OUT_DIR   = os.path.join(BASE_DIR, "outputs")
os.makedirs(OUT_DIR, exist_ok=True)

TAHMIN_PATH = os.path.join(OUT_DIR,  "talep_tahmini.xlsx")
MESAFE_PATH = os.path.join(PROC_DIR, "mesafe_matrisi.csv")
OUTPUT_PATH = os.path.join(OUT_DIR,  "arac_plani.xlsx")


# ─────────────────────────────────────────────
# YARDIMCI
# ─────────────────────────────────────────────
def find_file(directory: str, keyword: str) -> str:
    for fpath in glob.glob(os.path.join(directory, "*.xlsx")):
        if keyword.lower() in os.path.basename(fpath).lower():
            return fpath
    raise FileNotFoundError(f"'{keyword}' içeren .xlsx bulunamadı → {directory}")


# ─────────────────────────────────────────────
# VERİ YÜKLE
# ─────────────────────────────────────────────
def load_all():
    print("📂 Veriler yükleniyor...")

    df_tahmin = pd.read_excel(TAHMIN_PATH).iloc[:, :4]
    df_tahmin.columns = ['origin', 'destination', 'date', 'forecast_desi']
    df_tahmin["date"] = pd.to_datetime(df_tahmin["date"])
    df_tahmin["forecast_desi"] = df_tahmin["forecast_desi"].clip(lower=0)
    print(f"  ✓ talep_tahmini: {len(df_tahmin)} satır, {df_tahmin['date'].nunique()} gün")

    df_mesafe = pd.read_csv(MESAFE_PATH, index_col=0)
    print(f"  ✓ mesafe_matrisi: {df_mesafe.shape[0]}×{df_mesafe.shape[1]}")

    kiral_path = find_file(RAW_DIR, "kiral")
    df_kiral = pd.read_excel(kiral_path).iloc[:, :4]
    df_kiral.columns = ['origin', 'destination', 'count', 'vehicle_type']
    print(f"  ✓ kiralık_araçlar: {len(df_kiral)} satır")

    kap_path = find_file(RAW_DIR, "kapasite")
    df_kap = pd.read_excel(kap_path).iloc[:, :4]
    df_kap.columns = ['vehicle_type', 'capacity_desi', 'daily_fixed_cost', 'cost_per_km']
    print(f"  ✓ araç_kapasite_maliyet: {len(df_kap)} satır")

    return df_tahmin, df_mesafe, df_kiral, df_kap


# ─────────────────────────────────────────────
# DÜZELTME 3: İKİ YÖNLÜ KİRALIK KAPASİTE
# ─────────────────────────────────────────────
def build_rental_capacity(df_kiral: pd.DataFrame, df_kap: pd.DataFrame) -> dict:
    """
    Kiralık araçlar iki yönlü çalışır: A→B giden araç B→A dönüşte de kullanılabilir.
    Bu nedenle kapasite haritasını frozenset(orig, dest) bazında tutuyoruz.
    Her güzergah çifti için toplam kapasite = (A→B + B→A) araç kapasitelerinin toplamı.
    
    NOT: Eğer iş kuralı gereği iki yön bağımsızsa, bidirectional=False ile çağırın.
    """
    cap_dict = dict(zip(df_kap["vehicle_type"], df_kap["capacity_desi"]))
    if "Tır" in cap_dict:
        cap_dict["Tır"] = 22400

    # Yönlü kapasite (ham)
    directed_cap: dict[tuple, float] = {}
    for _, row in df_kiral.iterrows():
        key = (row["origin"], row["destination"])
        cap = cap_dict.get(row["vehicle_type"], 0) * int(row["count"])
        directed_cap[key] = directed_cap.get(key, 0) + cap

    # İki yönü birleştir — aynı araçlar her iki yönde kullanılabilir
    # (tek yönlüyse sadece directed_cap'i döndürün)
    bidirectional_cap: dict[tuple, float] = {}
    seen = set()
    for (o, d), cap in directed_cap.items():
        pair = frozenset([o, d])
        if pair not in seen:
            reverse_cap = directed_cap.get((d, o), 0)
            total = cap + reverse_cap
            bidirectional_cap[(o, d)] = total
            bidirectional_cap[(d, o)] = total
            seen.add(pair)

    return bidirectional_cap


# ─────────────────────────────────────────────
# DÜZELTME 4: MERKEZİYET BAZLI HUB SEÇİMİ
# ─────────────────────────────────────────────
def select_hubs(df_tahmin: pd.DataFrame, df_mesafe: pd.DataFrame, n_hubs: int = 2) -> list:
    """
    Hub skoru = toplam_hacim / ortalama_mesafe
    Sadece hacim yüksek değil, coğrafi olarak da merkezi olan şehirler seçilir.
    """
    cities = set(df_tahmin["origin"].unique()) | set(df_tahmin["destination"].unique())
    cities = [c for c in cities if c in df_mesafe.index]

    vol_origin = df_tahmin.groupby("origin")["forecast_desi"].sum()
    vol_dest   = df_tahmin.groupby("destination")["forecast_desi"].sum()
    total_vol  = vol_origin.add(vol_dest, fill_value=0)

    scores = {}
    for city in cities:
        vol = total_vol.get(city, 0)
        other_cities = [c for c in cities if c != city and c in df_mesafe.columns]
        if not other_cities:
            scores[city] = 0
            continue
        avg_dist = df_mesafe.loc[city, other_cities].replace(0, np.nan).mean()
        if pd.isna(avg_dist) or avg_dist == 0:
            scores[city] = 0
            continue
        scores[city] = vol / avg_dist  # yüksek hacim + düşük mesafe = iyi hub

    top_hubs = sorted(scores, key=scores.get, reverse=True)[:n_hubs]
    return top_hubs


# ─────────────────────────────────────────────
# DÜZELTME 2: SADECE BRUTE-FORCE (MIP YOK)
# ─────────────────────────────────────────────
def exact_spot_assignment(demand: float, distance_km: float,
                          vehicle_types: list, capacities: dict,
                          daily_fixed: dict, cost_per_km: dict) -> tuple[dict, float]:
    """
    4 araç tipi için garantili optimal çözüm (brute-force enumeration).
    MIP'e gerek yok — arama uzayı yönetilebilir.
    Döndürür: (araç_sayıları_dict, toplam_maliyet)
    """
    if demand <= 0 or distance_km <= 0:
        return {vt: 0 for vt in vehicle_types}, 0.0

    vt = sorted(vehicle_types, key=lambda v: capacities[v], reverse=True)
    unit_cost = {v: daily_fixed[v] + cost_per_km[v] * distance_km for v in vt}

    best_cost = float('inf')
    best_counts = {v: 0 for v in vt}

    max_v0 = math.ceil(demand / capacities[vt[0]])
    for c0 in range(max_v0 + 1):
        rem1 = demand - c0 * capacities[vt[0]]
        cost0 = c0 * unit_cost[vt[0]]
        if cost0 >= best_cost:
            break  # monoton artış — budge et

        if rem1 <= 0:
            if cost0 < best_cost:
                best_cost = cost0
                best_counts = {vt[0]: c0, vt[1]: 0, vt[2]: 0, vt[3]: 0}
            continue

        max_v1 = math.ceil(rem1 / capacities[vt[1]])
        for c1 in range(max_v1 + 1):
            rem2 = rem1 - c1 * capacities[vt[1]]
            cost01 = cost0 + c1 * unit_cost[vt[1]]
            if cost01 >= best_cost:
                break

            if rem2 <= 0:
                if cost01 < best_cost:
                    best_cost = cost01
                    best_counts = {vt[0]: c0, vt[1]: c1, vt[2]: 0, vt[3]: 0}
                continue

            max_v2 = math.ceil(rem2 / capacities[vt[2]])
            for c2 in range(max_v2 + 1):
                rem3 = rem2 - c2 * capacities[vt[2]]
                cost012 = cost01 + c2 * unit_cost[vt[2]]
                if cost012 >= best_cost:
                    break

                if rem3 <= 0:
                    if cost012 < best_cost:
                        best_cost = cost012
                        best_counts = {vt[0]: c0, vt[1]: c1, vt[2]: c2, vt[3]: 0}
                    continue

                c3 = math.ceil(rem3 / capacities[vt[3]])
                cost = cost012 + c3 * unit_cost[vt[3]]
                if cost < best_cost:
                    best_cost = cost
                    best_counts = {vt[0]: c0, vt[1]: c1, vt[2]: c2, vt[3]: c3}

    return best_counts, best_cost


def fast_cost_only(demand: float, distance_km: float,
                   vehicle_types: list, capacities: dict,
                   daily_fixed: dict, cost_per_km: dict) -> float:
    """Sadece maliyet döndürür (konsolidasyon kararları için hız optimizasyonu)."""
    _, cost = exact_spot_assignment(demand, distance_km, vehicle_types, capacities, daily_fixed, cost_per_km)
    return cost


# ─────────────────────────────────────────────
# DÜZELTME 1 & 5: DOĞRU KONSOLİDASYON
# ─────────────────────────────────────────────
def consolidate_loads(S: dict, HUBS: list, df_mesafe: pd.DataFrame,
                      vehicle_types: list, capacities: dict,
                      daily_fixed: dict, cost_per_km: dict) -> tuple[dict, dict]:
    """
    Düzeltilmiş konsolidasyon:
    - Önce TÜM aktarma kararları değerlendirilir (greedy, savings bazlı).
    - Kararlar commit edilmeden önce marjinal maliyet *güncel S* üzerinden hesaplanır.
    - Bir rota aktarılınca S güncellenir → sonraki kararlar güncel yükleri görür.
    - Bu şekilde çift sayım ortadan kalkar.
    """
    transfer_status = {route: "Direkt" for route in S.keys()}

    # Sadece hub olmayan, yükü > 0 olan rotalar → konsolidasyon adayı
    candidates = [
        (o, d) for (o, d) in list(S.keys())
        if S[(o, d)] > 0 and o not in HUBS and d not in HUBS
    ]

    # Savings skoruna göre sırala (büyük yük → önce değerlendir)
    candidates.sort(key=lambda r: S[r], reverse=True)

    for orig, dest in candidates:
        load = S.get((orig, dest), 0)
        if load <= 0:
            continue

        # Mesafe kontrolü
        if orig not in df_mesafe.index or dest not in df_mesafe.columns:
            continue
        dist_direct = df_mesafe.loc[orig, dest]
        if dist_direct <= 0:
            continue

        cost_direct = fast_cost_only(load, dist_direct, vehicle_types, capacities, daily_fixed, cost_per_km)

        best_hub = None
        best_savings = 0.0  # sadece tasarruf varsa aktarırız

        for hub in HUBS:
            if hub == orig or hub == dest:
                continue
            if hub not in df_mesafe.columns or hub not in df_mesafe.index:
                continue

            dist_o_h = df_mesafe.loc[orig, hub]
            dist_h_d = df_mesafe.loc[hub, dest]
            if dist_o_h <= 0 or dist_h_d <= 0:
                continue

            # Marjinal maliyet: mevcut S üzerinden (güncel yük dikkate alınıyor)
            cur_oh = S.get((orig, hub), 0.0)
            cur_hd = S.get((hub, dest), 0.0)

            cost_oh_before = fast_cost_only(cur_oh,        dist_o_h, vehicle_types, capacities, daily_fixed, cost_per_km)
            cost_oh_after  = fast_cost_only(cur_oh + load, dist_o_h, vehicle_types, capacities, daily_fixed, cost_per_km)
            marginal_oh = cost_oh_after - cost_oh_before

            cost_hd_before = fast_cost_only(cur_hd,        dist_h_d, vehicle_types, capacities, daily_fixed, cost_per_km)
            cost_hd_after  = fast_cost_only(cur_hd + load, dist_h_d, vehicle_types, capacities, daily_fixed, cost_per_km)
            marginal_hd = cost_hd_after - cost_hd_before

            total_hub_cost = marginal_oh + marginal_hd
            savings = cost_direct - total_hub_cost

            if savings > best_savings:
                best_savings = savings
                best_hub = hub

        # Tasarruf varsa yükü aktar — S'yi hemen güncelle (sonraki kararlar görsün)
        if best_hub is not None:
            S[(orig, best_hub)]  = S.get((orig, best_hub), 0.0) + load
            S[(best_hub, dest)]  = S.get((best_hub, dest), 0.0) + load
            S[(orig, dest)]      = 0.0
            transfer_status[(orig, dest)] = f"{best_hub} üzerinden aktarıldı"

    return S, transfer_status


# ─────────────────────────────────────────────
# ANA OPTİMİZASYON DÖNGÜSÜ
# ─────────────────────────────────────────────
def run_optimization(df_tahmin, df_mesafe, df_kiral, df_kap):
    print("\n🔧 Optimizasyon (v2 — Düzeltilmiş) başlıyor...")

    # Hub seçimi: merkeziyet skoru
    HUBS = select_hubs(df_tahmin, df_mesafe, n_hubs=2)
    print(f"🌍 Merkeziyet bazlı hub'lar: {HUBS}")

    vehicle_types = df_kap["vehicle_type"].tolist()
    capacities    = dict(zip(df_kap["vehicle_type"], df_kap["capacity_desi"]))
    if "Tır" in capacities:
        capacities["Tır"] = 22400
    daily_fixed   = dict(zip(df_kap["vehicle_type"], df_kap["daily_fixed_cost"]))
    cost_per_km   = dict(zip(df_kap["vehicle_type"], df_kap["cost_per_km"]))

    # İki yönlü kiralık kapasite haritası
    rental_cap_map = build_rental_capacity(df_kiral, df_kap)

    results = []
    total_spot_cost = 0.0
    dates = sorted(df_tahmin["date"].unique())

    for date in dates:
        df_day = df_tahmin[df_tahmin["date"] == date].copy()

        # Kiralık kapasiteyi düş
        S = {}
        original_demands = {}
        original_rentals = {}
        for _, row in df_day.iterrows():
            orig, dest, demand = row["origin"], row["destination"], row["forecast_desi"]
            rental = rental_cap_map.get((orig, dest), 0.0)
            S[(orig, dest)] = max(0.0, demand - rental)
            original_demands[(orig, dest)] = demand
            original_rentals[(orig, dest)] = rental

        # Konsolidasyon (düzeltilmiş)
        S, transfer_status = consolidate_loads(
            S, HUBS, df_mesafe, vehicle_types, capacities, daily_fixed, cost_per_km
        )

        # Nihai atama — brute-force optimal (MIP yok)
        for (orig, dest), remaining in S.items():
            if orig not in df_mesafe.index or dest not in df_mesafe.columns:
                dist = 0.0
            else:
                dist = df_mesafe.loc[orig, dest]

            spot_counts, spot_cost = exact_spot_assignment(
                remaining, dist, vehicle_types, capacities, daily_fixed, cost_per_km
            )
            total_spot_cost += spot_cost

            result_row = {
                "Tarih":              date.strftime("%Y-%m-%d"),
                "Çıkış Merkezi":      orig,
                "Varış Merkezi":      dest,
                "Tahmin Edilen Desi": round(original_demands.get((orig, dest), 0), 1),
                "Kiralık Kapasite":   round(original_rentals.get((orig, dest), 0), 1),
                "Kalan Yük (Spot)":   round(remaining, 1),
                "Mesafe (km)":        round(dist, 1),
                "Spot Maliyet (TL)":  round(spot_cost, 2),
                "Aktarma Durumu":     transfer_status.get((orig, dest), "Direkt"),
            }
            for vt in vehicle_types:
                result_row[f"Spot_{vt}"] = spot_counts.get(vt, 0)
            results.append(result_row)

        print(f"  ✓ {date.strftime('%Y-%m-%d')} tamamlandı")

    return pd.DataFrame(results), total_spot_cost


# ─────────────────────────────────────────────
# EXCEL'E YAZ
# ─────────────────────────────────────────────
def save_output(df_result: pd.DataFrame, total_cost: float):
    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as writer:
        df_result.to_excel(writer, sheet_name="Araç Planı", index=False)
        df_summary = (
            df_result.groupby("Tarih")["Spot Maliyet (TL)"]
            .sum()
            .reset_index()
            .rename(columns={"Spot Maliyet (TL)": "Günlük Spot Maliyet (TL)"})
        )
        df_summary.loc[len(df_summary)] = ["TOPLAM", round(total_cost, 2)]
        df_summary.to_excel(writer, sheet_name="Günlük Özet", index=False)

    print(f"\n💾 Araç planı kaydedildi → {OUTPUT_PATH}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  HepsiJET — Faz 3: Araç Optimizasyonu (v2 — Düzeltilmiş)")
    print("=" * 60)

    df_tahmin, df_mesafe, df_kiral, df_kap = load_all()
    df_result, total_cost = run_optimization(df_tahmin, df_mesafe, df_kiral, df_kap)
    save_output(df_result, total_cost)

    print("\n" + "=" * 60)
    print(f"  📊 TOPLAM SPOT MALİYET: {total_cost:,.2f} TL")
    print("  ↑  Bu değeri README.md'e kopyalayın.")
    print("=" * 60)


if __name__ == "__main__":
    main()