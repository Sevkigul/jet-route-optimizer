"""Araç optimizasyonu — tahmin edilen talebi en düşük maliyetle araçlara dağıtır.

Her gün her güzergah için: sabit kiralik filo zorunlu olarak yola çikar
(dolulugundan bagimsiz), kalan yük tamsayili programlama (MIP) ile en ucuz
spot araç kombinasyonuna atanir. Spot araçlar için minimum %10 doluluk
kisiti uygulanir; kiralik araçlar muaftir. Araç dönüşleri kapsam dişidir.

Ugrama (multi-stop): yol üstündeki bir merkeze ugrayarak o hattin yükünü
ayni araçta taşima. Yük araçta kalir, aktarma yapilmaz (konsolidasyon
degildir). Temel çözümün üzerine iyileştirme katmani olarak uygulanir ve
UGRAMA_AKTIF ile kapatilabilir.
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

# Spot araçlar için minimum doluluk orani (kiralik araçlar muaf)
MIN_DOLULUK = 0.10
# Ugrama iyileştirmesi: yol üstü sapma toleransi (1.15 = en fazla %15 sapma)
UGRAMA_AKTIF = True
SAPMA_ESIGI  = 1.15


def find_file(directory, keyword):
    """Adinda keyword geçen ilk .xlsx dosyasini bulur."""
    for f in glob.glob(os.path.join(directory, "*.xlsx")):
        if keyword.lower() in os.path.basename(f).lower():
            return f
    raise FileNotFoundError(f"'{keyword}' içeren .xlsx bulunamadi: {directory}")


def load_all():
    """Tahmin, mesafe, kiralik filo ve araç maliyet verilerini yükler."""
    print("Veriler yükleniyor...")
    # Tahmin formati: Tarih | Çıkış TM | Varış TM | Tahmin Edilen Desi
    tahmin = pd.read_excel(TAHMIN_PATH).iloc[:, :4]
    tahmin.columns = ["date", "origin", "destination", "demand"]
    tahmin["date"] = pd.to_datetime(tahmin["date"])
    tahmin["demand"] = tahmin["demand"].clip(lower=0)

    mesafe = pd.read_csv(MESAFE_PATH, index_col=0)

    kiral = pd.read_excel(find_file(RAW_DIR, "kiral")).iloc[:, :4]
    kiral.columns = ["origin", "destination", "count", "vehicle_type"]

    # 6 sütun: ad, kapasite, kiralik günlük, kiralik km, spot günlük, spot km.
    kap = pd.read_excel(find_file(RAW_DIR, "kapasite")).iloc[:, :6]
    kap.columns = ["vehicle_type", "capacity", "rental_daily", "rental_km",
                   "spot_daily", "spot_km"]

    print(f"  tahmin {tahmin.shape} | mesafe {mesafe.shape} | "
          f"kiralik {len(kiral)} satir | araç {len(kap)} tip")
    return tahmin, mesafe, kiral, kap


def build_rental_capacity(kiral, capacity):
    """Güzergah bazinda kiralik kapasite (desi). Tek yönlü: A->B araci
    yalnizca A->B hattina sayilir (dönüş kapsam dişi)."""
    cap = {}
    for _, r in kiral.iterrows():
        key = (r["origin"], r["destination"])
        cap[key] = cap.get(key, 0.0) + capacity.get(r["vehicle_type"], 0) * int(r["count"])
    return cap


def solve_spot(remaining, dist, vts, capacity, spot_daily, spot_km):
    """Kalan yükü en ucuz spot kombinasyonuna atar (tamsayili programlama).

    Kisitlar: toplam kapasite yükü karşilamali ve kullanilan her spot araç
    kapasitesinin en az MIN_DOLULUK orani kadar dolu olmali. Kalan yük en
    küçük aracin minimum doluluk eşiginin altindaysa spot araç çikarilmaz.
    Döndürür: (araç sayilari, araç başina atanan desi) sözlükleri.
    """
    bos = ({v: 0 for v in vts}, {v: 0.0 for v in vts})
    if remaining <= 0 or dist <= 0:
        return bos

    esik = MIN_DOLULUK * min(capacity[v] for v in vts)
    if remaining < esik:
        return bos  # hiçbir araç %10 dolulugu saglayamaz -> spot çikmaz

    unit = {v: spot_daily[v] + spot_km[v] * dist for v in vts}

    prob = LpProblem("spot", LpMinimize)
    x = {v: LpVariable(f"x_{v}", lowBound=0, cat="Integer") for v in vts}
    y = {v: LpVariable(f"y_{v}", lowBound=0) for v in vts}  # tipe atanan desi

    prob += lpSum(unit[v] * x[v] for v in vts)                       # amaç
    prob += lpSum(y[v] for v in vts) == remaining                    # tüm yük
    for v in vts:
        prob += y[v] <= capacity[v] * x[v]                           # kapasite
        prob += y[v] >= MIN_DOLULUK * capacity[v] * x[v]             # min doluluk
    prob.solve(PULP_CBC_CMD(msg=0))

    counts = {v: int(value(x[v]) or 0) for v in vts}
    loads  = {}
    for v in vts:  # tip içinde eşit dagitim -> her araç ayni dolulukta
        loads[v] = (float(value(y[v]) or 0.0) / counts[v]) if counts[v] else 0.0
    return counts, loads


def ugrama_iyilestirme(df, mesafe, capacity, spot_km):
    """Yol üstü hatlarin spot yükünü, ayni gün baska bir spot araca ugrama
    ile bindirir; bindirilen hattin spot araçlari iptal edilir.

    Kurallar: yük araçta kalir (aktarma yok), araç başina en fazla bir
    ugrama, sapma (d1+d2) <= SAPMA_ESIGI * dogrudan mesafe, kapasite
    aşilmaz. Yalnizca net kazanç saglayan birleşmeler uygulanir.
    """
    def dist(a, b):
        try:
            return float(mesafe.loc[a, b])
        except KeyError:
            return None

    df = df.reset_index(drop=True)
    df["Uğrama"] = ""
    spot_mask = df["Araç Tipi"].str.startswith("Spot")
    silinecek, kazanc_toplam, sayac = set(), 0.0, 0

    for tarih in df["Tarih"].unique():
        g = df[(df["Tarih"] == tarih) & spot_mask & (~df.index.isin(silinecek))]
        if g.empty:
            continue
        hatlar = (g.groupby(["Çıkış TM", "Varış TM"])
                    .agg(yuk=("Atanan Desi", "sum"), maliyet=("Maliyet", "sum"))
                    .reset_index())

        # adaylar: (kazanç, bindirilecek hat, taşiyici araç satiri, ugrama TM, sapma)
        adaylar = []
        for _, l1 in hatlar.iterrows():
            A1, B1 = l1["Çıkış TM"], l1["Varış TM"]
            for _, l2 in hatlar.iterrows():
                A2, B2 = l2["Çıkış TM"], l2["Varış TM"]
                if (A1, B1) == (A2, B2):
                    continue
                if A1 == A2:   ugrak = B1   # yükü ugrakta birak
                elif B1 == B2: ugrak = A1   # ugraktan yük al
                else:          continue
                dAB, d1, d2 = dist(A2, B2), dist(A2, ugrak), dist(ugrak, B2)
                if not all([dAB, d1, d2]) or d1 + d2 > SAPMA_ESIGI * dAB:
                    continue
                sapma = d1 + d2 - dAB
                tasiyici = g[(g["Çıkış TM"] == A2) & (g["Varış TM"] == B2)]
                for idx, arac in tasiyici.iterrows():
                    tip = arac["Araç Tipi"].replace("Spot ", "")
                    bos = capacity[tip] - arac["Atanan Desi"]
                    if bos < l1["yuk"]:
                        continue
                    net = l1["maliyet"] - sapma * spot_km[tip]
                    if net > 0:
                        adaylar.append((net, (A1, B1), idx, ugrak, sapma, l1["yuk"]))
                    break  # hat başina en dar yeterli araci degil ilkini dene

        kullanilan_hat, ugrayan_arac = set(), set()
        for net, hat, idx, ugrak, sapma, yuk in sorted(adaylar, reverse=True):
            if hat in kullanilan_hat or idx in ugrayan_arac or idx in silinecek:
                continue
            arac = df.loc[idx]
            tip = arac["Araç Tipi"].replace("Spot ", "")
            if capacity[tip] - arac["Atanan Desi"] < yuk:
                continue
            # bindirilen hattin spot araçlarini iptal et
            iptal = df[(df["Tarih"] == tarih) & spot_mask &
                       (df["Çıkış TM"] == hat[0]) & (df["Varış TM"] == hat[1])]
            silinecek.update(iptal.index)
            # taşiyici araci güncelle: yük, rota maliyeti, ugrama notu
            df.at[idx, "Atanan Desi"] = round(arac["Atanan Desi"] + yuk, 1)
            df.at[idx, "Maliyet"] = round(arac["Maliyet"] + sapma * spot_km[tip], 2)
            df.at[idx, "Uğrama"] = f"{ugrak} ({hat[0]}→{hat[1]} yükü, {yuk:.0f} desi)"
            kullanilan_hat.add(hat); ugrayan_arac.add(idx)
            kazanc_toplam += net; sayac += 1

    df = df.drop(index=silinecek).reset_index(drop=True)
    if sayac:
        print(f"  Ugrama: {sayac} birleşme, ~{kazanc_toplam:,.0f} TL tasarruf")
    return df


def run_optimization(tahmin, mesafe, kiral, kap):
    """Günlük plani kurar: kiralik araçlar zorunlu, kalan yük spot'a.

    Çikti her satirda tek araç atamasi içerir:
    Tarih | Araç Tipi | Çıkış TM | Varış TM | Atanan Desi | Maliyet | Uğrama
    """
    print("Optimizasyon basliyor...")

    vts        = kap["vehicle_type"].tolist()
    capacity   = dict(zip(kap["vehicle_type"], kap["capacity"]))
    spot_daily = dict(zip(kap["vehicle_type"], kap["spot_daily"]))
    spot_km    = dict(zip(kap["vehicle_type"], kap["spot_km"]))
    rent_daily = dict(zip(kap["vehicle_type"], kap["rental_daily"]))
    rent_km    = dict(zip(kap["vehicle_type"], kap["rental_km"]))

    rental_cap = build_rental_capacity(kiral, capacity)

    rows = []
    rental_total = 0.0
    dates = sorted(tahmin["date"].unique())

    for date in dates:
        gun = tahmin[tahmin["date"] == date]
        talep_map = {(r["origin"], r["destination"]): r["demand"]
                     for _, r in gun.iterrows()}

        # --- Kiralik araçlar: zorunlu, doluluk kisiti yok, her gün yola çikar
        for _, k in kiral.iterrows():
            o, d, n, vt = k["origin"], k["destination"], int(k["count"]), k["vehicle_type"]
            dist = mesafe.loc[o, d] if (o in mesafe.index and d in mesafe.columns) else 0.0
            dem = talep_map.get((o, d), 0.0)
            kira_kap = rental_cap.get((o, d), 0.0)
            atanan_toplam = min(dem, kira_kap)
            # ayni hattaki kiralik araçlara oransal dagitim (eşit doluluk)
            pay_orani = atanan_toplam / kira_kap if kira_kap > 0 else 0.0
            maliyet = rent_daily.get(vt, 0) + rent_km.get(vt, 0) * dist
            for _ in range(n):
                rows.append({
                    "Tarih": date.strftime("%Y-%m-%d"),
                    "Araç Tipi": f"Kiralık {vt}",
                    "Çıkış TM": o,
                    "Varış TM": d,
                    "Atanan Desi": round(capacity.get(vt, 0) * pay_orani, 1),
                    "Maliyet": round(maliyet, 2),
                })
                rental_total += maliyet

        # --- Spot araçlar: kiraliktan artan yük, min %10 doluluk kisiti
        for _, r in gun.iterrows():
            o, d, dem = r["origin"], r["destination"], r["demand"]
            kalan = max(0.0, dem - rental_cap.get((o, d), 0.0))
            dist = mesafe.loc[o, d] if (o in mesafe.index and d in mesafe.columns) else 0.0

            counts, loads = solve_spot(kalan, dist, vts, capacity,
                                       spot_daily, spot_km)
            for vt in vts:
                if counts[vt] == 0:
                    continue
                maliyet = spot_daily[vt] + spot_km[vt] * dist
                for _ in range(counts[vt]):
                    rows.append({
                        "Tarih": date.strftime("%Y-%m-%d"),
                        "Araç Tipi": f"Spot {vt}",
                        "Çıkış TM": o,
                        "Varış TM": d,
                        "Atanan Desi": round(loads[vt], 1),
                        "Maliyet": round(maliyet, 2),
                    })
        print(f"  {date.strftime('%Y-%m-%d')} tamamlandi")

    df = pd.DataFrame(rows)
    if UGRAMA_AKTIF:
        df = ugrama_iyilestirme(df, mesafe, capacity, spot_km)
    else:
        df["Uğrama"] = ""

    spot_total = df.loc[df["Araç Tipi"].str.startswith("Spot"), "Maliyet"].sum()
    return df, spot_total, rental_total


def save_output(df, spot_total, rental_total):
    """Araç planini ve maliyet özetini Excel'e yazar."""
    grand = spot_total + rental_total
    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Araç Planı", index=False)

        gunluk = (df.groupby("Tarih")["Maliyet"].sum().reset_index()
                    .rename(columns={"Maliyet": "Günlük Toplam Maliyet (TL)"}))
        gunluk.loc[len(gunluk)] = ["Spot Toplam", round(spot_total, 2)]
        gunluk.loc[len(gunluk)] = ["Kiralık Toplam", round(rental_total, 2)]
        gunluk.loc[len(gunluk)] = ["GENEL TOPLAM", round(grand, 2)]
        gunluk.to_excel(w, sheet_name="Maliyet Özeti", index=False)

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
    print(f"  TOPLAM MALİYET  : {spot_total + rental_total:,.2f} TL")
    print("=" * 60)


if __name__ == "__main__":
    main()