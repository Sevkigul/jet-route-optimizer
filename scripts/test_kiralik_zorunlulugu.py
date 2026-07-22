"""Kiralik zorunlulugu regresyon testi.

Sartname: "Talep yetersiz olsa bile kiralik araclari cikarmak zorundasinizdir."
Bu kural, talebin dustugu senaryolarda sessizce ihlal edilebilir; testler talebi
kasitli budayip her kiralik hattin her gun yine de sefere ciktigini dogrular.

Senaryolar:
  T1  normal tahmin                      -> 12 hat x 7 gun tam
  T2  bir kiralik cikis merkezinin (Yalova) tum talebi silinir
  T3  bir gunun (1 Temmuz) tum talebi silinir
  T4  tum talep silinir (en uc durum: plan sadece bos kiralik seferlerden olusur)

Calistirma:  python scripts/test_kiralik_zorunlulugu.py
Cikis kodu 0 ise tum testler gecti.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, data_loader as dl, demand_forecast as fc, optimizer  # noqa: E402

BEKLENEN_GUN = (config.TAHMIN_BITIS.date() - config.TAHMIN_BASLANGIC.date()).days + 1


def kiralik_kontrol(plan: pd.DataFrame, kiralik_df: pd.DataFrame) -> list[str]:
    """Her (hat, arac turu) icin her gun dogru sayida kiralik sefer var mi?"""
    hatalar = []
    kiralik = plan[plan["Araç Tipi"] == "Kiralık"]
    for _, hat in kiralik_df.iterrows():
        hp = kiralik[
            (kiralik["Çıkış Transfer Merkezi"] == hat["cikis"])
            & (kiralik["Varış Transfer Merkezi"] == hat["varis"])
            & (kiralik["Araç türü"] == hat["arac_tipi"])
        ]
        gun_basi = hp.drop_duplicates("Araç ID").groupby("Çıkış Tarihi").size()
        ad = f"{hat['cikis']}->{hat['varis']} ({hat['arac_tipi']})"
        if len(gun_basi) != BEKLENEN_GUN:
            hatalar.append(f"{ad}: {len(gun_basi)}/{BEKLENEN_GUN} gun sefer var")
        elif (gun_basi != hat["arac_sayisi"]).any():
            hatalar.append(f"{ad}: gun basina arac sayisi hatali {gun_basi.to_dict()}")
    return hatalar


def calistir(ad: str, forecast: pd.DataFrame, veri: dict) -> bool:
    plan = optimizer.plan_olustur(forecast, veri)
    hatalar = kiralik_kontrol(plan, veri["kiralik_araclar"])
    kiralik_sefer = plan[plan["Araç Tipi"] == "Kiralık"]["Araç ID"].nunique()
    durum = "GECTI" if not hatalar else "KALDI"
    print(f"[{durum}] {ad}")
    print(f"         tahmin {len(forecast):>5} satir / {forecast['tahmin_desi'].sum():>10,.0f} desi"
          f" -> {plan['Araç ID'].nunique():>4} sefer ({kiralik_sefer} kiralik)")
    for h in hatalar:
        print(f"         ! {h}")
    return not hatalar


def main() -> None:
    veri = dl.load_all()
    forecast = fc.forecast_all(veri["gecmis_talep"])

    sonuclar = []
    sonuclar.append(calistir("T1 normal tahmin", forecast, veri))

    f2 = forecast[forecast["cikis"] != "Yalova"].reset_index(drop=True)
    sonuclar.append(calistir("T2 Yalova cikisli tum talep silindi", f2, veri))

    hedef_gun = pd.Timestamp("2026-07-01")
    f3 = forecast[forecast["tarih"] != hedef_gun].reset_index(drop=True)
    sonuclar.append(calistir("T3 1 Temmuz talebi silindi", f3, veri))

    f4 = forecast.iloc[0:0]
    sonuclar.append(calistir("T4 tum talep silindi", f4, veri))

    print(f"\n{sum(sonuclar)}/{len(sonuclar)} test gecti")
    sys.exit(0 if all(sonuclar) else 1)


if __name__ == "__main__":
    main()
