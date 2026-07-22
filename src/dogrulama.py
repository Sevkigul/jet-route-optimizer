"""outputs/Tasima-plani.xlsx için bağımsız kural-uyum ve özet-metrik doğrulayıcısı.

Yalnızca ÇIKTI dosyalarını (Tasima-plani.xlsx) ve girdi verisini (data_loader.
load_all çıktısı) kullanır — optimizer'ın iç durumuna bakmaz, şartname
kurallarını sıfırdan uygular. `main.py` bunu pipeline'ın son adımı olarak
çağırır.
"""
from __future__ import annotations

import re
from datetime import datetime

import pandas as pd

from . import config, output_writer as ow, utils

KAPASITE = {"Tır": 22400, "Kamyon": 12000, "Hafif Kamyon": 7200, "Kamyonet": 5600}


def _kok_id(talep_id: str) -> str:
    return re.sub(r"-\d+$", "", talep_id) if talep_id else talep_id


def _leg_dt(tarih_str: str, saat) -> datetime:
    """'GG.AA.YYYY' + datetime.time -> datetime."""
    gg, aa, yy = str(tarih_str).split(".")
    return datetime(int(yy), int(aa), int(gg), saat.hour, saat.minute)


def verify(plan: pd.DataFrame, forecast: pd.DataFrame, veri: dict) -> tuple[str, int]:
    """(rapor_metni, hata_sayisi) döner. hata_sayisi==0 ise tüm kontroller PASS."""
    mesafe_idx = veri["mesafe_matrisi"].set_index(["cikis", "varis"])
    plan = plan.copy()
    plan["Talep ID"] = plan["Talep ID"].fillna("").astype(str)

    satirlar: list[str] = []
    hata_sayisi = 0

    def yaz(s: str = "") -> None:
        satirlar.append(s)

    yaz("=== A1: Kolon şeması ===")
    if list(plan.columns) == ow.TASIMA_PLANI_KOLONLARI:
        yaz("OK")
    else:
        yaz("HATA: kolonlar eşleşmiyor")
        hata_sayisi += 1

    yaz("\n=== A2: Araç ID format/benzersizlik ===")
    arac_satirlar = plan.drop_duplicates("Araç ID")
    format_hatali = arac_satirlar[~arac_satirlar["Araç ID"].str.match(r"^V\d{4}$")]
    yaz(f"format hatalı Araç ID sayısı: {len(format_hatali)}")
    if len(format_hatali) > 0:
        hata_sayisi += 1

    yaz("\n=== A3: Desi mutabakatı (forecast vs son-hop teslim) ===")
    plan_bos_olmayan = plan[plan["Talep ID"] != ""].copy()
    plan_bos_olmayan["kok"] = plan_bos_olmayan["Talep ID"].apply(_kok_id)
    forecast_idx = forecast.set_index("talep_id")
    fark_sayisi, toplam_fark = 0, 0.0
    for kok, grup in plan_bos_olmayan.groupby("kok"):
        if kok not in forecast_idx.index:
            continue
        orijinal_varis = forecast_idx.loc[kok, "varis"]
        orijinal_desi = forecast_idx.loc[kok, "tahmin_desi"]
        son_hop = grup[grup["Varış Transfer Merkezi"] == orijinal_varis]
        teslim_edilen = son_hop["Taşınan Desi"].sum()
        if abs(teslim_edilen - orijinal_desi) > 0.5:
            fark_sayisi += 1
            toplam_fark += abs(teslim_edilen - orijinal_desi)
    yaz(f"Uyuşmayan kök talep sayısı: {fark_sayisi} (toplam fark: {toplam_fark:.1f} desi)")
    if fark_sayisi > 0:
        hata_sayisi += 1

    yaz("\n=== A4: Elleçleme kapasite aşımı ===")
    ellecleme_kullanim: dict[tuple[str, object], float] = {}
    for _, row in plan.iterrows():
        for tm, saat_str, tarih_str, dk in [
            (row["Çıkış Transfer Merkezi"], row["Çıkış Saati"], row["Çıkış Tarihi"], row["Çıkış Elleçleme süresi"]),
            (row["Varış Transfer Merkezi"], row["Varış Saati"], row["Varış Tarihi"], row["Varış elleçleme süresi"]),
        ]:
            if dk <= 0:
                continue
            gg, aa, yy = tarih_str.split(".")
            baslangic = datetime(int(yy), int(aa), int(gg), saat_str.hour, saat_str.minute)
            for gun, gun_dk in utils.gun_gun_dagit(baslangic, int(dk)):
                key = (tm, gun)
                ellecleme_kullanim[key] = ellecleme_kullanim.get(key, 0.0) + gun_dk / dk * row["Taşınan Desi"]

    asim_sayisi = 0
    for (tm, gun), kullanim in ellecleme_kullanim.items():
        limit = veri["ellecleme_kapasite"].get(tm, 0.0)
        if kullanim > limit + 1.0:
            asim_sayisi += 1
            yaz(f"  AŞIM: {tm} {gun} kullanım={kullanim:.0f} limit={limit:.0f}")
    yaz(f"Elleçleme aşımı olan (TM,gün) sayısı: {asim_sayisi}")
    if asim_sayisi > 0:
        hata_sayisi += 1

    yaz("\n=== A5: Tır kapasite aşımı ===")
    # Kural (Q&A): ayni arac ayni TM'de ayni gun yeniden kullanilirsa 1 tir
    # sayilir -> arac basina (TM, gun) ziyaretleri tekillestirilir (zincirli
    # spot Tir seferleri boylece dogru sayilir).
    tir_satirlari = plan[plan["Araç türü"] == "Tır"]
    tir_kullanim: dict[tuple[str, object], int] = {}
    for arac_id, grup in tir_satirlari.groupby("Araç ID"):
        ziyaretler = set()
        for _, r in grup.iterrows():
            gg, aa, yy = r["Çıkış Tarihi"].split(".")
            ziyaretler.add((r["Çıkış Transfer Merkezi"], datetime(int(yy), int(aa), int(gg)).date()))
            gg, aa, yy = r["Varış Tarihi"].split(".")
            ziyaretler.add((r["Varış Transfer Merkezi"], datetime(int(yy), int(aa), int(gg)).date()))
        for tm, gun in ziyaretler:
            tir_kullanim[(tm, gun)] = tir_kullanim.get((tm, gun), 0) + 1

    tir_asim = 0
    for (tm, gun), adet in tir_kullanim.items():
        limit = veri["tir_kapasite"].get(tm, 0)
        if adet > limit:
            tir_asim += 1
            yaz(f"  AŞIM: {tm} {gun} adet={adet} limit={limit}")
    yaz(f"Tır kapasite aşımı olan (TM,gün) sayısı: {tir_asim}")
    if tir_asim > 0:
        hata_sayisi += 1

    yaz("\n=== A6: Kiralık kuralları ===")
    kiralik_df = veri["kiralik_araclar"]
    kiralik_plan = plan[plan["Araç Tipi"] == "Kiralık"]
    beklenen_gun_sayisi = (config.TAHMIN_BITIS.date() - config.TAHMIN_BASLANGIC.date()).days + 1
    kiralik_hata = 0
    for _, hat in kiralik_df.iterrows():
        hat_planlari = kiralik_plan[
            (kiralik_plan["Çıkış Transfer Merkezi"] == hat["cikis"])
            & (kiralik_plan["Varış Transfer Merkezi"] == hat["varis"])
            & (kiralik_plan["Araç türü"] == hat["arac_tipi"])
        ]
        gun_basi_leg = hat_planlari.drop_duplicates("Araç ID").groupby("Çıkış Tarihi").size()
        eksik_gun = beklenen_gun_sayisi - len(gun_basi_leg)
        yanlis_sayida_gun = (gun_basi_leg != hat["arac_sayisi"]).sum()
        if eksik_gun > 0 or yanlis_sayida_gun > 0:
            kiralik_hata += 1
            yaz(f"  SORUN: {hat['cikis']}->{hat['varis']} ({hat['arac_tipi']}): "
                f"eksik_gun={eksik_gun}, yanlis_sayida_gun={yanlis_sayida_gun}")
    yaz(f"Sorunlu kiralık hat sayısı: {kiralik_hata}")
    if kiralik_hata > 0:
        hata_sayisi += 1

    yaz("\n=== A7: Toplam maliyet mutabakatı (bağımsız yeniden hesap) ===")
    arac_tablosu = veri["arac_kapasite_maliyet"]
    tutarsiz_arac = 0
    for arac_id, grup in plan.groupby("Araç ID"):
        leg_ozet = grup.groupby(["Çıkış Transfer Merkezi", "Varış Transfer Merkezi"], sort=False).agg(
            varis_ell=("Varış elleçleme süresi", "max"),
            cikis_ell=("Çıkış Elleçleme süresi", "max"),
            cikis_tarih=("Çıkış Tarihi", "first"),
            cikis_saat=("Çıkış Saati", "first"),
            varis_tarih=("Varış Tarihi", "first"),
            varis_saat=("Varış Saati", "first"),
            yol_dk=("Yolculuk süresi", "max"),
        )
        arac_tipi = grup.iloc[0]["Araç türü"]
        kiralik_mi = grup.iloc[0]["Araç Tipi"] == "Kiralık"
        satir = arac_tablosu.loc[arac_tipi]
        rh = satir["kiralik_saatlik_tl"] if kiralik_mi else satir["spot_saatlik_tl"]
        rk = satir["kiralik_km_tl"] if kiralik_mi else satir["spot_km_tl"]
        leg_maliyet = 0.0
        for (cikis_tm, varis_tm) in leg_ozet.index:
            route_row = mesafe_idx.loc[(cikis_tm, varis_tm)]
            yol_dk = utils.saat_yukari_yuvarla_dk(route_row[f"sure_saat_{arac_tipi}"])
            leg_maliyet += rh * (yol_dk / 60.0) + rk * route_row["mesafe_km"]
        dep_dk = leg_ozet["cikis_ell"].sum()
        unload_dk_toplam = leg_ozet["varis_ell"].sum()
        handle_maliyet = rh * (dep_dk + unload_dk_toplam) / 60.0
        # Bekleme: kayitli varis (ellecleme baslangici), cikis + yol'dan
        # sonraysa arac o kadar dakika indirilmeyi beklemistir; kullanim
        # suresine dahildir ve ucretlenir (sartname Q&A). Zaman damgalarindan
        # bagimsizca geri cikarilir.
        bekleme_dk = 0.0
        for leg in leg_ozet.itertuples():
            cik = _leg_dt(leg.cikis_tarih, leg.cikis_saat)
            var = _leg_dt(leg.varis_tarih, leg.varis_saat)
            bekleme_dk += max(0.0, (var - cik).total_seconds() / 60.0 - float(leg.yol_dk))
        beklenen_toplam = leg_maliyet + handle_maliyet + rh * bekleme_dk / 60.0
        gercek_toplam = grup["Toplam maliyet"].sum() - grup["SLA cezası"].sum()
        if abs(gercek_toplam - beklenen_toplam) > max(1.0, 0.01 * beklenen_toplam):
            tutarsiz_arac += 1
            if tutarsiz_arac <= 10:
                yaz(f"  UYUŞMUYOR: {arac_id} beklenen={beklenen_toplam:.2f} gerçek={gercek_toplam:.2f}")
    yaz(f"Maliyeti uyuşmayan Araç ID sayısı: {tutarsiz_arac} / {plan['Araç ID'].nunique()}")
    if tutarsiz_arac > 0:
        hata_sayisi += 1

    yaz("\n" + "=" * 60)
    yaz(f"TOPLAM HATA: {hata_sayisi} (0 ise tüm kontroller PASS)")
    yaz("=" * 60)

    yaz("\n=== B: Özet metrikler ===")
    # doluluk = arac basina en yuklu bacagin kapasiteye orani (zincirli Tir
    # seferlerinde iki seferin yuku toplanmaz, tepe yuk esas alinir)
    leg_yuk = plan[plan["Talep ID"] != ""].groupby(
        ["Araç ID", "Çıkış Transfer Merkezi", "Varış Transfer Merkezi"]
    )["Taşınan Desi"].sum()
    tip_per_leg = arac_satirlar.set_index("Araç ID")["Araç türü"]
    doluluk = leg_yuk.groupby("Araç ID").max() / tip_per_leg.map(KAPASITE)
    durak_sayisi = plan.groupby("Araç ID")["Varış Transfer Merkezi"].nunique()
    milk_run_sayisi = int((durak_sayisi > 1).sum())
    toplam_maliyet = plan["Toplam maliyet"].sum() - plan["SLA cezası"].sum()
    sla_cezasi = plan["SLA cezası"].sum()

    yaz(f"Araç maliyeti     : {toplam_maliyet:>14,.2f} TL")
    yaz(f"SLA cezası        : {sla_cezasi:>14,.2f} TL")
    yaz(f"Genel toplam      : {toplam_maliyet + sla_cezasi:>14,.2f} TL")
    yaz(f"Toplam sefer      : {plan['Araç ID'].nunique()}")
    yaz(f"Milk-run (>1 durak): {milk_run_sayisi}")
    yaz(f"Medyan doluluk    : %{100*doluluk.median():.1f}")
    yaz(f"Ortalama doluluk  : %{100*doluluk.mean():.1f}")
    yaz(f"Doluluk <%20 oranı: %{100*(doluluk<0.2).mean():.1f}")

    return "\n".join(satirlar), hata_sayisi
