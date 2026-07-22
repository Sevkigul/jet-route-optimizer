"""v3 — Milk-run (çok duraklı uğrama) tabanlı kural-tabanlı greedy motor.

Kapsam: her (çıkış merkezi × gün) için 09:00+17:00 talebi TEK havuzda birleşir.
Sıra: (1) kiralık zorunlu tek-duraklı seferler, (2) tam dolu büyük araçlar
(Tır, sonra Kamyon) tek-duraklı direkt "peel", (3) kalan ince yükler
Clarke-Wright (milkrun.py) ile çok-duraklı rotalara birleşir. Uğramada araçta
kalan yük yeniden elleçlenmez — sadece o durakta inen yük elleçlenir.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

import pandas as pd

from . import config, milkrun, utils, vehicle_costing as vc
from .capacity_ledger import EllecklemeLedger, TirLedger
from .output_writer import TASIMA_PLANI_KOLONLARI
from .shipment import Item, Vehicle, id_finalize

NON_TIR_TIPLER = [t for t in config.ARAC_TIPLERI if t != config.TIR_KAPASITE_KISITLI_TIP]


@dataclass
class _OptimizerState:
    mesafe_idx: pd.DataFrame
    arac_tablosu: pd.DataFrame
    ellecleme_ledger: EllecklemeLedger
    tir_ledger: TirLedger
    kiralik_lanes: dict
    kiralik_slot_zamani: dict
    departed: set = field(default_factory=set)  # (cikis,varis,arac_tipi,slot_i,gun)
    vehicles: list = field(default_factory=list)
    # (tm, gun) -> [(musait_olma_ani, Vehicle)]: o TM'ye o gun varmis ve ayni
    # gun yeniden kullanilabilecek spot Tir'lar (Q&A: ayni arac ayni TM'de
    # hareket etmeden bosaltilip yeniden yuklenirse 1 tir kapasitesi sayilir)
    tir_havuzu: dict = field(default_factory=dict)


def _sla_bitis_hedef(hazir: datetime, sla_gun: float) -> datetime:
    return hazir + timedelta(hours=float(sla_gun) * 24)


def _build_items(forecast: pd.DataFrame, mesafe_idx: pd.DataFrame) -> list[Item]:
    items = []
    for row in forecast.itertuples(index=False):
        if row.tahmin_desi <= 0:
            continue
        hh, mm = utils.saat_str_to_time(row.talep_tamamlanma_saati)
        hazir = datetime.combine(row.tarih.date(), time(hh, mm))
        sla_gun = mesafe_idx.loc[(row.cikis, row.varis), "sla_gun"]
        items.append(Item(
            idx=len(items), talep_id=row.talep_id, cikis=row.cikis, varis=row.varis,
            desi=float(row.tahmin_desi), hazir=hazir,
            sla_bitis_hedef=_sla_bitis_hedef(hazir, sla_gun),
        ))
    return items


def _cok_gunlu_havuzlama(items: list[Item], mesafe_idx: pd.DataFrame) -> None:
    """2 günlük SLA'sı olan, ince hat-günleri güvenlik payıyla ertesi güne
    biriktirir (daha dolu milk-run'a binsinler). Yalnız ARDIŞIK gün çiftleri
    (çift indeksli gün -> ertesi gün) denenir."""
    for it in items:
        it.pool_gun = it.hazir.date()
        it.dispatch_hazir = it.hazir
    if not config.MULTIDAY_POOL:
        return

    gunler = sorted({it.hazir.date() for it in items})
    idx = {g: i for i, g in enumerate(gunler)}
    laneday: dict = defaultdict(float)
    for it in items:
        laneday[(it.cikis, it.varis, it.hazir.date())] += it.desi

    for it in items:
        di = idx[it.hazir.date()]
        sla_gun = mesafe_idx.loc[(it.cikis, it.varis), "sla_gun"]
        if (sla_gun != 2 or di % 2 != 0 or di + 1 >= len(gunler)
                or laneday[(it.cikis, it.varis, it.hazir.date())] >= config.MILKRUN_THIN_ESIK_DESI):
            continue
        hold_gun = gunler[di + 1]
        worst_dep = datetime.combine(hold_gun, time(max(int(s.split(":")[0]) for s in config.TALEP_SAATLERI), 0))
        tmin = utils.saat_yukari_yuvarla_dk(mesafe_idx.loc[(it.cikis, it.varis), "sure_saat_Kamyon"])
        hm = utils.ellecleme_dakika(it.desi)
        tamamlanma_tahmini = worst_dep + timedelta(minutes=hm + tmin + hm)
        pay = timedelta(hours=config.MULTIDAY_SLA_GUVENLIK_SAAT)
        if tamamlanma_tahmini <= it.sla_bitis_hedef - pay:
            it.pool_gun = hold_gun
            it.dispatch_hazir = datetime.combine(hold_gun, time()) + timedelta(
                hours=it.hazir.hour, minutes=it.hazir.minute
            )


def _kiralik_slot_zamanlari(kiralik_lanes: dict, tir_kapasite: dict, items: list[Item], gunler: list) -> dict:
    """(cikis,varis,gun) -> kiralığın çıkış anı. Tır kapasitesi dar olan varış
    merkezlerine giden kiralık tırlar erken (ilk dalga) çıkar (gece yarısı
    aşımı / ardışık gün yığılması riskini azaltmak için); diğerleri o
    hat için en yoğun dalgada çıkar."""
    tir_arr: dict = defaultdict(int)
    for (o, d), vl in kiralik_lanes.items():
        for arac_tipi, cnt in vl:
            if arac_tipi == config.TIR_KAPASITE_KISITLI_TIP:
                tir_arr[d] += cnt
    tight = {d for d, n in tir_arr.items() if tir_kapasite.get(d, 0) <= n}

    dload: dict = defaultdict(lambda: defaultdict(float))
    for it in items:
        dload[(it.cikis, it.varis)][it.hazir.hour] += it.desi

    saatler = sorted(int(s.split(":")[0]) for s in config.TALEP_SAATLERI)
    erken = saatler[0]
    sonuc = {}
    for (o, d) in kiralik_lanes:
        for gun in gunler:
            if d in tight:
                sonuc[(o, d, gun)] = datetime.combine(gun, time(erken, 0))
            else:
                en_yogun_saat = max(saatler, key=lambda h: dload[(o, d)].get(h, 0.0))
                sonuc[(o, d, gun)] = datetime.combine(gun, time(en_yogun_saat, 0))
    return sonuc


def _draw(dq: deque, miktar: float, items: list[Item], max_ready: datetime | None = None) -> list[tuple[int, float]]:
    alinan = []
    kalan = miktar
    while kalan > 1e-9 and dq:
        idx, rem = dq[0]
        if max_ready is not None and items[idx].dispatch_hazir > max_ready:
            break
        al = min(rem, kalan)
        alinan.append((idx, al))
        kalan -= al
        rem -= al
        if rem <= 1e-9:
            dq.popleft()
        else:
            dq[0][1] = rem
    return alinan


def _kiralik_tir_rezervasyonu(st: _OptimizerState, tm: str, gun) -> int:
    sayac = 0
    for (o, d), vl in st.kiralik_lanes.items():
        for arac_tipi, cnt in vl:
            if arac_tipi != config.TIR_KAPASITE_KISITLI_TIP or (o != tm and d != tm):
                continue
            for i in range(cnt):
                if (o, d, arac_tipi, i, gun) not in st.departed:
                    sayac += 1
    return sayac


def _tir_musait(st: _OptimizerState, tm: str, gun) -> int:
    """TM'nin o gunku tir kapasitesinden, henuz cikmamis kiralik Tir
    rezervasyonlari dusulmus kalan slot sayisi."""
    return int(st.tir_ledger.mevcut_kapasite(tm, gun)) - _kiralik_tir_rezervasyonu(st, tm, gun)


def _peek_depart_ve_varis(
    st: _OptimizerState, items: list[Item], dq: deque, miktar: float, cikis: str, varis: str,
) -> tuple[datetime, datetime]:
    """Kuyrugu bozmadan, ilk `miktar` desilik yukun en gec hazir anini ve
    Tir ile tahmini varis anini hesaplar (_arac_olustur ile ayni zaman
    modeli; ellecleme-erteleme haric)."""
    kalan, depart = miktar, None
    for idx, rem in dq:
        h = items[idx].dispatch_hazir
        depart = h if depart is None or h > depart else depart
        kalan -= min(rem, kalan)
        if kalan <= 1e-9:
            break
    tip = config.TIR_KAPASITE_KISITLI_TIP
    yol_dk = utils.saat_yukari_yuvarla_dk(st.mesafe_idx.loc[(cikis, varis), f"sure_saat_{tip}"])
    varis_ani = depart + timedelta(minutes=utils.ellecleme_dakika(miktar) + yol_dk)
    return depart, varis_ani


def _tir_kaynak_bul(st: _OptimizerState, cikis: str, varis: str, depart: datetime, varis_ani: datetime):
    """Spot Tir seferi icin kapasite kaynagi arar.

    Varis TM'sinde (tahmini varis GUNUNDE - gece asan seferde ertesi gun)
    kapasite sarttir. Cikis tarafinda once ayni gun o TM'ye varmis musait
    spot Tir zinciri denenir (ayni arac ayni TM'de 1 sayilir, ekstra slot
    tuketmez); yoksa cikis ledger kapasitesi kullanilir.
    Donen: Vehicle (zincirlenecek onceki sefer) | "ledger" | None (kaynak yok).
    """
    if _tir_musait(st, varis, varis_ani.date()) < 1:
        return None
    havuz = st.tir_havuzu.get((cikis, depart.date()), [])
    for i, (musait_an, arac) in enumerate(havuz):
        if musait_an <= depart:
            havuz.pop(i)
            return arac
    if _tir_musait(st, cikis, depart.date()) >= 1:
        return "ledger"
    return None


def _ellecleme_uygula_admisyonlu(
    ledger: EllecklemeLedger, tm: str, istenen_baslangic: datetime, toplam_desi: float
) -> tuple[int, datetime, datetime]:
    """İstenen anda kapasite yetersizse bir sonraki günün başına ertelenerek
    elleçleme uygulanır (aşımı yapısal olarak engeller). Döner:
    (dk, tamamlanma_ani, fiili_baslangic) — fiili_baslangic, kapasite nedeniyle
    ertelendiyse istenen_baslangic'ten farklı olabilir; çağıran taraf bunu
    'Varış Saati/Tarihi' olarak kaydetmeli (aksi halde çıktıdaki zaman damgası
    ile ledger'a yazılan gün uyuşmaz)."""
    if toplam_desi <= 1e-9:
        return 0, istenen_baslangic, istenen_baslangic
    baslangic = _ellecleme_fiili_baslangic(ledger, tm, istenen_baslangic, toplam_desi)
    dk = utils.ellecleme_dakika(toplam_desi)
    for gun, gun_dk in utils.gun_gun_dagit(baslangic, dk):
        desi_payi = gun_dk / dk * toplam_desi
        ledger.tuket(tm, gun, desi_payi)
    return dk, utils.zaman_ekle(baslangic, dk), baslangic


def _ellecleme_fiili_baslangic(
    ledger: EllecklemeLedger, tm: str, istenen_baslangic: datetime, toplam_desi: float
) -> datetime:
    """Kapasite yetersizse ertelenmis fiili baslangici dondurur — TUKETMEDEN.
    Hem gercek uygulama (_ellecleme_uygula_admisyonlu) hem kuru-kosum
    zamanlama tahminleri (_rota_teslim_tahmini, rota_uygun) ayni fonksiyonu
    kullanir; boylece tahmin ile gerceklesme birebir ayni modeldir."""
    if toplam_desi <= 1e-9:
        return istenen_baslangic
    baslangic = istenen_baslangic
    for _ in range(400):
        dk = utils.ellecleme_dakika(toplam_desi)
        bugun_gun, bugun_dk = utils.gun_gun_dagit(baslangic, dk)[0]
        bugun_desi_payi = bugun_dk / dk * toplam_desi
        if bugun_desi_payi <= ledger.mevcut_kapasite(tm, bugun_gun) + 1e-6:
            break
        baslangic = datetime.combine(baslangic.date() + timedelta(days=1), datetime.min.time())
    return baslangic


def _en_ucuz_tek_tip_ve_maliyet(
    stops: list[str], toplam_yuk: float, cikis: str, mesafe_idx: pd.DataFrame, arac_tablosu
) -> tuple[str, float]:
    """Rotayi tek basina tasiyabilecek en ucuz spot (Tir disi) tipi ve
    maliyetini doner; hicbir tip sigmiyorsa (en buyuk tip, inf)."""
    en_iyi, en_iyi_maliyet = None, None
    for tip in NON_TIR_TIPLER:
        if arac_tablosu.loc[tip, "kapasite_desi"] + 1e-6 < toplam_yuk:
            continue
        prev, maliyet = cikis, 0.0
        satir = arac_tablosu.loc[tip]
        for s in stops:
            route_row = mesafe_idx.loc[(prev, s)]
            yol_dk = utils.saat_yukari_yuvarla_dk(route_row[f"sure_saat_{tip}"])
            maliyet += satir["spot_saatlik_tl"] * (yol_dk / 60.0) + satir["spot_km_tl"] * route_row["mesafe_km"]
            prev = s
        # elleçleme maliyeti (yükleme tam yük + her duraktaki inişler toplamı ~ tam yük)
        maliyet += satir["spot_saatlik_tl"] * (2 * utils.ellecleme_dakika(toplam_yuk) / 60.0)
        if en_iyi_maliyet is None or maliyet < en_iyi_maliyet:
            en_iyi_maliyet, en_iyi = maliyet, tip
    if en_iyi is None:
        return max(NON_TIR_TIPLER, key=lambda t: arac_tablosu.loc[t, "kapasite_desi"]), float("inf")
    return en_iyi, en_iyi_maliyet


def _en_ucuz_tek_tip(stops: list[str], toplam_yuk: float, cikis: str, mesafe_idx: pd.DataFrame, arac_tablosu) -> str:
    return _en_ucuz_tek_tip_ve_maliyet(stops, toplam_yuk, cikis, mesafe_idx, arac_tablosu)[0]


def _arac_olustur(
    st: _OptimizerState, items: list[Item], cikis: str, stops: list[str],
    cargo_by_dest: dict, arac_tipi: str, kiralik_mi: bool, default_dep: datetime,
    zincir_onceki: Vehicle | None = None,
) -> Vehicle:
    route = [cikis] + list(stops)
    cargo = []
    for si, d in enumerate(stops, start=1):
        for idx, desi in cargo_by_dest.get(d, []):
            if desi > 1e-9:
                cargo.append((idx, desi, si))
    yuk_toplam = sum(c[1] for c in cargo)

    v = Vehicle(kiralik_mi=kiralik_mi, arac_tipi=arac_tipi, route=route, cargo=cargo)

    readys = [items[idx].dispatch_hazir for idx, _, _ in cargo]
    depart_after = max(readys) if readys else default_dep

    yuk_dk, yuk_tamamlanma, yuk_fiili_baslangic = _ellecleme_uygula_admisyonlu(
        st.ellecleme_ledger, cikis, depart_after, yuk_toplam
    )
    v.dep_handle_dk = yuk_dk

    drop_desi = {si: 0.0 for si in range(1, len(route))}
    for idx, desi, si in cargo:
        drop_desi[si] += desi

    prev, t = cikis, yuk_tamamlanma
    for i, d in enumerate(stops, start=1):
        route_row = st.mesafe_idx.loc[(prev, d)]
        tmin = utils.saat_yukari_yuvarla_dk(route_row[f"sure_saat_{arac_tipi}"])
        arr = utils.zaman_ekle(t, tmin)
        dd = drop_desi[i]
        um_dk, tamamlanma, fiili_varis = _ellecleme_uygula_admisyonlu(st.ellecleme_ledger, d, arr, dd)
        v.leg_dep.append(t)
        v.leg_arr.append(fiili_varis)  # kapasite nedeniyle ertelendiyse gerçek (geç) varış
        # Erteleme varsa arac fiziksel varis (arr) ile ellecleme baslangici
        # arasinda YUKLU bekler; sartname Q&A geregi bu bekleme kullanim
        # suresine dahildir ve ucretlenir. (Yukleme ONCESI erteleme ucretsiz:
        # yuk aracsiz bekler, arac gec cagrilir.)
        v.leg_wait_dk.append(int(round((fiili_varis - arr).total_seconds() / 60)))
        v.stop_done.append(tamamlanma)
        v.leg_km.append(route_row["mesafe_km"])
        v.leg_travel_dk.append(tmin)
        v.stop_unload_dk.append(um_dk)
        t = tamamlanma
        prev = d

    if arac_tipi == config.TIR_KAPASITE_KISITLI_TIP:
        if zincir_onceki is not None:
            # ayni fiziksel arac: cikis TM'deki slot onceki varista sayildi
            v.zincir_onceki = zincir_onceki
        else:
            st.tir_ledger.tuket(cikis, yuk_fiili_baslangic.date(), 1)
        st.tir_ledger.tuket(stops[-1], v.leg_arr[-1].date(), 1)
        if not kiralik_mi:
            # bosaltma bitince ayni TM'den ayni gun yeniden kullanilabilir
            st.tir_havuzu.setdefault((stops[-1], v.leg_arr[-1].date()), []).append(
                (v.stop_done[-1], v)
            )

    satir = st.arac_tablosu.loc[arac_tipi]
    rh = satir["kiralik_saatlik_tl"] if kiralik_mi else satir["spot_saatlik_tl"]
    rk = satir["kiralik_km_tl"] if kiralik_mi else satir["spot_km_tl"]
    v.leg_cost = [rh * (tmin / 60.0) + rk * km for tmin, km in zip(v.leg_travel_dk, v.leg_km)]
    handle_dk_toplam = v.dep_handle_dk + sum(v.stop_unload_dk)
    v.handle_cost = rh * handle_dk_toplam / 60.0
    v.wait_cost = rh * sum(v.leg_wait_dk) / 60.0
    v.cost = sum(v.leg_cost) + v.handle_cost + v.wait_cost

    for idx, desi, si in cargo:
        items[idx].drops.append((v, desi, si))
    st.vehicles.append(v)
    return v


def _rota_teslim_tahmini(
    st: _OptimizerState, items: list[Item], cikis: str, stops: list[str],
    cargo_by_dest: dict, arac_tipi: str,
) -> dict:
    """Rotanin durak bazinda teslim (ellecleme tamamlanma) zamanlarini
    kapasite-ertelemesiz varsayimla onceden hesaplar (kuru kosum;
    _arac_olustur ile ayni zaman modeli). Donen: {durak: tamamlanma}."""
    cargo = [(idx, desi) for d in stops for idx, desi in cargo_by_dest.get(d, [])]
    if not cargo:
        return {}
    yuk = sum(desi for _, desi in cargo)
    t = max(items[idx].dispatch_hazir for idx, _ in cargo)
    # ellecleme kapasitesi doluysa yukleme/indirme ertelenir; kuru-kosum da
    # ayni admisyon modelini kullanir (tuketmeden) — tahmin/gerceklesme farki
    # kalmaz, SLA denetimi erteleme kaynakli gecikmeleri de gorur
    t = _ellecleme_fiili_baslangic(st.ellecleme_ledger, cikis, t, yuk)
    t += timedelta(minutes=utils.ellecleme_dakika(yuk))
    tamamlanma, prev = {}, cikis
    for d in stops:
        row = st.mesafe_idx.loc[(prev, d)]
        t += timedelta(minutes=utils.saat_yukari_yuvarla_dk(row[f"sure_saat_{arac_tipi}"]))
        dd = sum(desi for _, desi in cargo_by_dest.get(d, []))
        t = _ellecleme_fiili_baslangic(st.ellecleme_ledger, d, t, dd)
        t += timedelta(minutes=utils.ellecleme_dakika(dd))
        tamamlanma[d] = t
        prev = d
    return tamamlanma


def _durak_deadline(items: list[Item], cargo_by_dest: dict, d: str) -> datetime:
    parcalar = cargo_by_dest.get(d, [])
    if not parcalar:
        return datetime.max
    return min(items[idx].sla_bitis_hedef for idx, _ in parcalar)


def _direkt_zamaninda_tip(st: _OptimizerState, items: list[Item], cikis: str, d: str, cargo_by_dest: dict) -> str | None:
    """Duragi tek basina, zamaninda teslim edebilecek en ucuz (Tir disi) spot
    tipi doner; hicbir tip yetisemiyorsa None."""
    adaylar = []
    for tip in NON_TIR_TIPLER:
        yuk = sum(desi for _, desi in cargo_by_dest.get(d, []))
        if st.arac_tablosu.loc[tip, "kapasite_desi"] + 1e-6 < yuk:
            continue
        tamam = _rota_teslim_tahmini(st, items, cikis, [d], {d: cargo_by_dest[d]}, tip)
        if tamam and tamam[d] <= _durak_deadline(items, cargo_by_dest, d):
            row = st.mesafe_idx.loc[(cikis, d)]
            maliyet, *_ = vc.tek_arac_maliyeti(yuk, row, tip, False, st.arac_tablosu)
            adaylar.append((maliyet, tip))
    return min(adaylar)[1] if adaylar else None


def _rota_ceza_tahmini(st, items, cikis, stops, cargo_by_dest, arac_tipi) -> float:
    tamam = _rota_teslim_tahmini(st, items, cikis, stops, cargo_by_dest, arac_tipi)
    toplam = 0.0
    for d in stops:
        for idx, desi in cargo_by_dest.get(d, []):
            toplam += vc.sla_cezasi(tamam[d], items[idx].sla_bitis_hedef, desi)
    return toplam


def _sla_onarim(
    st: _OptimizerState, items: list[Item], cikis: str, stops: list[str], cargo_by_dest: dict,
) -> list[tuple[list[str], str]]:
    """SLA onarim gecidi: km-odakli Clarke-Wright rotasi teslim tarihlerine
    karsi kuru-kosumla denetlenir. Gec kalan durak varsa (1) duraklar teslim
    tarihine gore siralanip yeniden denenir, (2) hala gec kalan ve DIREKT
    sevkiyatla zamaninda yetisebilen duraklar rotadan cikarilip kendi aracina
    verilir. Ceza ancak fiziksel olarak kacinilmazsa kalir (cozum ilkesi:
    ceza maliyet dusurme kaldiraci degildir); o durumda da toplam cezayi
    kucuk tutan durak sirasi secilir. Donen: [(stops, arac_tipi), ...]."""
    sonuc: list[tuple[list[str], str]] = []
    kalan = list(stops)
    while kalan:
        yuk = sum(desi for d in kalan for _, desi in cargo_by_dest.get(d, []))
        tip = _en_ucuz_tek_tip(kalan, yuk, cikis, st.mesafe_idx, st.arac_tablosu)
        tamam = _rota_teslim_tahmini(st, items, cikis, kalan, cargo_by_dest, tip)
        gec = [d for d in kalan if tamam[d] > _durak_deadline(items, cargo_by_dest, d)]
        if not gec:
            sonuc.append((kalan, tip))
            return sonuc

        # (1) teslim tarihi sirasi (aciliyet onde) cozuyor mu?
        sirali = sorted(kalan, key=lambda d: _durak_deadline(items, cargo_by_dest, d))
        tamam_s = _rota_teslim_tahmini(st, items, cikis, sirali, cargo_by_dest, tip)
        gec_s = [d for d in sirali if tamam_s[d] > _durak_deadline(items, cargo_by_dest, d)]
        if not gec_s:
            sonuc.append((sirali, tip))
            return sonuc

        # (2) gec kalanlardan direkt yetisebilenleri ayir, kalanla yeniden dene
        ayrilan = False
        for d in gec_s:
            direkt_tip = _direkt_zamaninda_tip(st, items, cikis, d, cargo_by_dest)
            if direkt_tip is not None:
                kalan.remove(d)
                sonuc.append(([d], direkt_tip))
                ayrilan = True
        if ayrilan:
            continue

        # kacinilmaz ceza: toplam cezasi dusuk olan durak sirasini sec
        ceza_km = _rota_ceza_tahmini(st, items, cikis, kalan, cargo_by_dest, tip)
        ceza_sla = _rota_ceza_tahmini(st, items, cikis, sirali, cargo_by_dest, tip)
        sonuc.append((sirali if ceza_sla < ceza_km else kalan, tip))
        return sonuc
    return sonuc


def _gun_cikis_isle(st: _OptimizerState, items: list[Item], cikis: str, gun, dest_queues: dict) -> None:
    gun_baslangic = datetime.combine(gun, time(0, 0))
    tir_cap = st.arac_tablosu.loc[config.TIR_KAPASITE_KISITLI_TIP, "kapasite_desi"]
    kam_cap = st.arac_tablosu.loc["Kamyon", "kapasite_desi"]

    # 1) kiralık (zorunlu, tek durak, sabit hat)
    # Kiralık hat listesi üzerinden dönülür, talep kuyrukları üzerinden DEĞİL:
    # şartname "talep yetersiz olsa bile kiralık araçları çıkarmak
    # zorundasınız" diyor, dolayısıyla o gün o hatta hiç yük olmasa da araç
    # boş çıkar (dest_queues talepsiz varış için boş kuyruk üretir).
    for d in sorted(d for (o, d) in st.kiralik_lanes if o == cikis):
        dq = dest_queues[d]
        for arac_tipi, cnt in st.kiralik_lanes[(cikis, d)]:
            slot_zamani = st.kiralik_slot_zamani[(cikis, d, gun)]
            kapasite = st.arac_tablosu.loc[arac_tipi, "kapasite_desi"]
            for j in range(cnt):
                cargo = _draw(dq, kapasite, items, max_ready=slot_zamani)
                _arac_olustur(st, items, cikis, [d], {d: cargo}, arac_tipi, True, slot_zamani)
                st.departed.add((cikis, d, arac_tipi, j, gun))

    # 2) tam dolu büyük araçlar (Tır sonra Kamyon), tek durak direkt, spot
    for d, dq in list(dest_queues.items()):
        toplam = sum(c[1] for c in dq)
        while toplam >= tir_cap - 1e-6:
            depart, varis_ani = _peek_depart_ve_varis(st, items, dq, tir_cap, cikis, d)
            kaynak = _tir_kaynak_bul(st, cikis, d, depart, varis_ani)
            if kaynak is None:
                break
            zincir = kaynak if isinstance(kaynak, Vehicle) else None
            cargo = _draw(dq, tir_cap, items)
            _arac_olustur(st, items, cikis, [d], {d: cargo}, config.TIR_KAPASITE_KISITLI_TIP,
                          False, gun_baslangic, zincir_onceki=zincir)
            toplam -= tir_cap
        while toplam >= kam_cap - 1e-6:
            cargo = _draw(dq, kam_cap, items)
            _arac_olustur(st, items, cikis, [d], {d: cargo}, "Kamyon", False, gun_baslangic)
            toplam -= kam_cap

    # 3) kalan ince yükler -> milk-run
    kalan = {d: sum(c[1] for c in dq) for d, dq in dest_queues.items() if sum(c[1] for c in dq) > 1e-9}
    if not kalan:
        return

    # SLA uygunluk geri çağırımı: rota adayı hiçbir durağı geciktirmemeli.
    # Kuyruklardaki yükün en geç hazır anı ve en erken teslim hedefi üzerinden
    # kuru-koşum zamanlaması yapılır (tip: o yükü taşıyacak en ucuz spot tip).
    hazir_son = {d: max(items[c[0]].dispatch_hazir for c in dest_queues[d]) for d in kalan}
    deadline = {d: min(items[c[0]].sla_bitis_hedef for c in dest_queues[d]) for d in kalan}

    def rota_uygun(sirali_duraklar: list[str]) -> bool:
        yuk = sum(kalan[d] for d in sirali_duraklar)
        tip = _en_ucuz_tek_tip(sirali_duraklar, yuk, cikis, st.mesafe_idx, st.arac_tablosu)
        t = max(hazir_son[d] for d in sirali_duraklar)
        t = _ellecleme_fiili_baslangic(st.ellecleme_ledger, cikis, t, yuk)
        t += timedelta(minutes=utils.ellecleme_dakika(yuk))
        prev = cikis
        for d in sirali_duraklar:
            row = st.mesafe_idx.loc[(prev, d)]
            t += timedelta(minutes=utils.saat_yukari_yuvarla_dk(row[f"sure_saat_{tip}"]))
            t = _ellecleme_fiili_baslangic(st.ellecleme_ledger, d, t, kalan[d])
            t += timedelta(minutes=utils.ellecleme_dakika(kalan[d]))
            if t > deadline[d]:
                return False
            prev = d
        return True

    def rota_maliyet(sirali_duraklar: list[str]) -> float:
        yuk = sum(kalan[d] for d in sirali_duraklar)
        return _en_ucuz_tek_tip_ve_maliyet(sirali_duraklar, yuk, cikis, st.mesafe_idx, st.arac_tablosu)[1]

    rotalar = milkrun.clarke_wright(
        cikis, kalan, st.mesafe_idx, st.arac_tablosu, kam_cap, config.MILKRUN_MAX_STOPS,
        uygun_mu=rota_uygun,
    )
    # Clarke-Wright acgozlu bir kurucudur; yerel arama (rota birlestirme +
    # durak tasima) ayni SLA denetimi altinda maliyeti dusurur (olcum: tam
    # kosuda ~%1, bkz. README)
    rotalar = milkrun.yerel_arama(
        cikis, rotalar, kalan, st.mesafe_idx, kam_cap, config.MILKRUN_MAX_STOPS,
        rota_uygun, rota_maliyet,
    )
    for stops in rotalar:
        cargo_by_dest = {d: _draw(dest_queues[d], sum(c[1] for c in dest_queues[d]), items) for d in stops}
        # km-odakli rota, teslim tarihlerine karsi denetlenir; gec kalan
        # duraklar gerekirse ayri araca ayrilir (bkz. _sla_onarim)
        for alt_stops, alt_tip in _sla_onarim(st, items, cikis, stops, cargo_by_dest):
            alt_cargo = {d: cargo_by_dest[d] for d in alt_stops}
            _arac_olustur(st, items, cikis, alt_stops, alt_cargo, alt_tip, False, gun_baslangic)


def _assign_ids(vehicles: list[Vehicle]) -> None:
    """Cikis anina gore siralayip ID atar. Zincirlenmis spot Tir seferleri
    ayni fiziksel arac oldugu icin onceki seferin ID'sini devralir (zincirin
    basi her zaman daha erken ciktigindan siralamada once gelir)."""
    vehicles.sort(key=lambda v: (v.leg_dep[0] if v.leg_dep else datetime.max, v.route[0]))
    n = 0
    for v in vehicles:
        if v.zincir_onceki is not None:
            v.vid = v.zincir_onceki.vid
        else:
            n += 1
            v.vid = f"V{n:04d}"


def _plan_satirlari_olustur(items: list[Item], vehicles: list[Vehicle]) -> list[dict]:
    for v in vehicles:
        leg_yuk = [0.0] * (len(v.route) - 1)
        for idx, desi, si in v.cargo:
            for li in range(si):
                leg_yuk[li] += desi
        v._leg_yuk = leg_yuk
        v._toplam_yuk = sum(c[1] for c in v.cargo)

    id_finalize(items)

    rows = []
    for item in items:
        for (v, desi, si, nihai_id) in item.drops:
            ceza = vc.sla_cezasi(v.stop_done[si - 1], item.sla_bitis_hedef, desi)
            c_handle = (v.handle_cost + v.wait_cost) * (desi / v._toplam_yuk) if v._toplam_yuk > 1e-9 else 0.0
            for li in range(si):
                pay = v.leg_cost[li] * (desi / v._leg_yuk[li]) if v._leg_yuk[li] > 1e-9 else 0.0
                son_bacak_mi = li == si - 1
                extra = (ceza + c_handle) if son_bacak_mi else 0.0
                rows.append({
                    "Araç ID": v.vid,
                    "Araç Tipi": "Kiralık" if v.kiralik_mi else "Spot",
                    "Araç türü": v.arac_tipi,
                    "Çıkış Transfer Merkezi": v.route[li],
                    "Varış Transfer Merkezi": v.route[li + 1],
                    "Çıkış Tarihi": v.leg_dep[li].strftime("%d.%m.%Y"),
                    "Çıkış Saati": v.leg_dep[li].time(),
                    "Varış Tarihi": v.leg_arr[li].strftime("%d.%m.%Y"),
                    "Varış Saati": v.leg_arr[li].time(),
                    "Talep ID": nihai_id,
                    "Taşınan Desi": round(desi, 1),
                    "Yolculuk süresi": v.leg_travel_dk[li],
                    "Varış elleçleme süresi": v.stop_unload_dk[li] if son_bacak_mi else 0,
                    "Çıkış Elleçleme süresi": v.dep_handle_dk if li == 0 else 0,
                    "SLA cezası": round(ceza, 2) if son_bacak_mi else 0.0,
                    "Toplam maliyet": round(pay + extra, 2),
                })

    for v in vehicles:
        if not v.cargo:  # yüksüz çıkan zorunlu kiralık sefer
            rows.append({
                "Araç ID": v.vid, "Araç Tipi": "Kiralık" if v.kiralik_mi else "Spot",
                "Araç türü": v.arac_tipi,
                "Çıkış Transfer Merkezi": v.route[0], "Varış Transfer Merkezi": v.route[1],
                "Çıkış Tarihi": v.leg_dep[0].strftime("%d.%m.%Y"), "Çıkış Saati": v.leg_dep[0].time(),
                "Varış Tarihi": v.leg_arr[0].strftime("%d.%m.%Y"), "Varış Saati": v.leg_arr[0].time(),
                "Talep ID": "", "Taşınan Desi": 0,
                "Yolculuk süresi": v.leg_travel_dk[0], "Varış elleçleme süresi": 0, "Çıkış Elleçleme süresi": 0,
                "SLA cezası": 0.0, "Toplam maliyet": round(v.cost, 2),
            })

    return rows


def plan_olustur(forecast: pd.DataFrame, veri: dict) -> pd.DataFrame:
    mesafe_idx = veri["mesafe_matrisi"].set_index(["cikis", "varis"])
    arac_tablosu = veri["arac_kapasite_maliyet"]

    items = _build_items(forecast, mesafe_idx)
    _cok_gunlu_havuzlama(items, mesafe_idx)

    kiralik_df = veri["kiralik_araclar"]
    kiralik_lanes: dict = {}
    for _, row in kiralik_df.iterrows():
        kiralik_lanes.setdefault((row["cikis"], row["varis"]), []).append(
            (row["arac_tipi"], int(row["arac_sayisi"]))
        )

    # Planlama takvimi tahmin ufkundan gelir (talepten DEĞİL): kiralık araçlar
    # o gün hiç talep olmasa bile çıkmak zorunda. Havuzlama bir yükü ufkun
    # dışına taşırsa o gün de kapsanır.
    takvim = {d.date() for d in pd.date_range(config.TAHMIN_BASLANGIC, config.TAHMIN_BITIS, freq="D")}
    gunler = sorted(takvim | {it.pool_gun for it in items})
    kiralik_slot_zamani = _kiralik_slot_zamanlari(kiralik_lanes, veri["tir_kapasite"], items, gunler)

    st = _OptimizerState(
        mesafe_idx=mesafe_idx,
        arac_tablosu=arac_tablosu,
        ellecleme_ledger=EllecklemeLedger(veri["ellecleme_kapasite"]),
        tir_ledger=TirLedger(veri["tir_kapasite"]),
        kiralik_lanes=kiralik_lanes,
        kiralik_slot_zamani=kiralik_slot_zamani,
    )

    kiralik_cikislar = {o for (o, _) in kiralik_lanes}
    for gun in gunler:
        by_origin: dict = defaultdict(lambda: defaultdict(deque))
        for it in sorted(items, key=lambda x: x.dispatch_hazir):
            if it.pool_gun == gun and it.desi > 1e-9:
                by_origin[it.cikis][it.varis].append([it.idx, it.desi])
        # talebi olmayan ama kiralık hattı olan merkezler de işlenmeli
        for cikis in sorted(set(by_origin) | kiralik_cikislar):
            _gun_cikis_isle(st, items, cikis, gun, by_origin[cikis])

    _assign_ids(st.vehicles)
    rows = _plan_satirlari_olustur(items, st.vehicles)
    df = pd.DataFrame(rows)
    return df[TASIMA_PLANI_KOLONLARI]
