"""Dispatch kayitlarindan TAŞIMA PLANI.xlsx formatina donusum + sema korumasi."""
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config_opt as cfg
from costs import sla_deadline, delay_hours_ceiled, sla_penalty

REQUIRED_COLUMNS = [
    "Araç ID", "Araç Tipi", "Araç türü", "Çıkış Transfer Merkezi", "Varış Transfer Merkezi",
    "Çıkış Tarihi", "Çıkış Saati", "Varış Tarihi", "Varış Saati", "Talep ID", "Taşınan Desi",
    "Yolculuk süresi", "Varış elleçleme süresi", "Çıkış Elleçleme süresi", "SLA cezası",
    "Toplam maliyet",
]


def _assign_vehicle_ids(dispatches):
    ordered = sorted(dispatches, key=lambda d: (d["depart_at"], d["origin"], d["destination"]))
    for i, d in enumerate(ordered, start=1):
        d["arac_id"] = f"V{i:04d}"
    return ordered


def build_demand_lookup(forecast_df, distance):
    lookup = {}
    for _, r in forecast_df.iterrows():
        sla_gun = distance[(r["origin"], r["destination"])]["sla_gun"]
        lookup[r["talep_id"]] = {
            "created_at": r["created_at"],
            "deadline": sla_deadline(r["created_at"], sla_gun),
            "destination": r["destination"],
        }
    return lookup


def build_output(dispatches, forecast_df, distance):
    dispatches = _assign_vehicle_ids(dispatches)
    demand_lookup = build_demand_lookup(forecast_df, distance)

    # duz satirlar: her (arac, portion) icin bir satir. chain_id, portion'un
    # HANGI ORIJINAL sevkiyattan geldigini izler (rental.py/scheduler.py'da o
    # portion ilk kez bir araca atandiginda o aracin vehicle_internal_id'si ile
    # etiketlenir) - kac bacak degistirirse degistirsin (konsolidasyon: 1
    # orijinal -> 2 bacak; hub-merge: 2 orijinal -> ortak bacakta birlesir)
    # ayni chain_id korunur. Boylece gercek bolunme (ayni talebin FARKLI
    # chain_id'lerde/orijinal sevkiyatlarda gecmesi) ile tek-teslimatin
    # cok-bacakli-rotasi (ayni chain_id, birden fazla satir) birbirine karismaz.
    flat_rows = []
    for d in dispatches:
        for p_idx, (talep_id, desi, chain_id) in enumerate(d["portions"]):
            flat_rows.append({
                "dispatch": d,
                "p_idx": p_idx,
                "original_talep_id": talep_id,
                "chain_id": chain_id,
                "desi": desi,
                "is_terminal": d["destination"] == demand_lookup[talep_id]["destination"],
            })

    # bolunme kontrolu: ayni talep_id FARKLI chain_id'lerde geciyorsa -N son eki
    # (ayni chain_id'nin birden fazla bacagi varsa - konsolidasyon/hub-merge - SPLIT sayilmaz)
    from collections import defaultdict
    groups = defaultdict(list)
    for row in flat_rows:
        groups[row["original_talep_id"]].append(row)

    for talep_id, rows in groups.items():
        distinct_chains = sorted({r["chain_id"] for r in rows},
                                  key=lambda cid: min(r["dispatch"]["depart_at"] for r in rows
                                                       if r["chain_id"] == cid))
        if len(distinct_chains) > 1:
            suffix_by_chain = {cid: i for i, cid in enumerate(distinct_chains, start=1)}
            for row in rows:
                row["display_talep_id"] = f"{talep_id}-{suffix_by_chain[row['chain_id']]}"
        else:
            for row in rows:
                row["display_talep_id"] = talep_id

    # arac basina maliyeti sadece ilk portion satirinda goster (cifte sayimi onlemek icin)
    seen_vehicle_cost = set()

    out_rows = []
    for row in flat_rows:
        d = row["dispatch"]
        info = demand_lookup[row["original_talep_id"]]
        # SLA cezasi SADECE zincirin son (gercek varis noktasina ulasan) bacaginda
        # hesaplanir - konsolidasyonda ara bacagin "delivered_at"i asil teslimat degildir.
        if row["is_terminal"]:
            delay_h = delay_hours_ceiled(d["delivered_at"], info["deadline"])
            penalty = sla_penalty(row["desi"], delay_h)
        else:
            penalty = 0.0

        cost_this_row = 0.0
        if d["arac_id"] not in seen_vehicle_cost:
            cost_this_row = d["cost"]
            seen_vehicle_cost.add(d["arac_id"])

        out_rows.append({
            "Araç ID": d["arac_id"],
            "Araç Tipi": d["vehicle_class"],
            "Araç türü": d["vehicle_type"],
            "Çıkış Transfer Merkezi": d["origin"],
            "Varış Transfer Merkezi": d["destination"],
            "Çıkış Tarihi": d["depart_at"].strftime(cfg.OUTPUT_DATE_FORMAT),
            "Çıkış Saati": d["depart_at"].strftime(cfg.OUTPUT_TIME_FORMAT),
            "Varış Tarihi": d["arrive_at"].strftime(cfg.OUTPUT_DATE_FORMAT),
            "Varış Saati": d["arrive_at"].strftime(cfg.OUTPUT_TIME_FORMAT),
            "Talep ID": row["display_talep_id"],
            "Taşınan Desi": round(row["desi"], cfg.DESI_ROUND_DECIMALS),
            "Yolculuk süresi": round(d["travel_hours"], 2),
            "Varış elleçleme süresi": round(d["inbound_handling_min"], 2),
            "Çıkış Elleçleme süresi": round(d["outbound_handling_min"], 2),
            "SLA cezası": round(penalty, cfg.COST_ROUND_DECIMALS),
            "Toplam maliyet": round(cost_this_row, cfg.COST_ROUND_DECIMALS),
            "_original_talep_id": row["original_talep_id"],
            "_chain_id": row["chain_id"],
        })

    full_df = pd.DataFrame(out_rows)
    full_df = full_df.sort_values(["Çıkış Tarihi", "Araç ID"]).reset_index(drop=True)
    out_df = full_df[REQUIRED_COLUMNS].copy()
    return out_df, full_df


def guard_against_template(out_df):
    template = pd.read_excel(cfg.TASIMA_TEMPLATE_FILE)
    if list(out_df.columns) != list(template.columns):
        raise AssertionError(
            f"Kolon uyumsuzlugu!\n  beklenen: {list(template.columns)}\n  uretilen: {list(out_df.columns)}"
        )

    bad_arac = out_df["Araç ID"][~out_df["Araç ID"].str.match(r"^V\d{4}$")]
    if len(bad_arac) > 0:
        raise AssertionError(f"Araç ID formatına uymayan değerler: {bad_arac.tolist()[:5]}")

    bad_talep = out_df["Talep ID"][~out_df["Talep ID"].str.match(r"^D\d{5}(-\d+)?$")]
    if len(bad_talep) > 0:
        raise AssertionError(f"Talep ID formatına uymayan değerler: {bad_talep.tolist()[:5]}")

    print(f"[output_opt] şema kontrolu OK: {len(out_df)} satır, kolonlar şablona birebir uyuyor")


def validate_desi_conservation(full_df, forecast_df):
    """Bolunen (ve bolunmeyen) taleplerin desi toplaminin orijinal talebe esit oldugunu dogrular.

    Ayni chain_id'nin birden fazla bacagi (konsolidasyon/hub-merge) AYNI desiyi
    tasidigindan, once (original_talep_id, chain_id) bazinda TEK bir deger
    alinir (max - hepsi esit olmali), sonra bu degerler talep_id bazinda
    toplanir. Boylece cok bacakli bir teslimat desiyi birden fazla kez saymaz.
    """
    per_delivery = full_df.groupby(["_original_talep_id", "_chain_id"])["Taşınan Desi"].max()
    produced = per_delivery.groupby("_original_talep_id").sum()
    expected = forecast_df.set_index("talep_id")["desi"]

    merged = expected.to_frame("expected").join(produced.rename("produced"), how="left")
    merged["produced"] = merged["produced"].fillna(0.0)
    merged["fark"] = (merged["expected"] - merged["produced"]).abs()

    kotu = merged[merged["fark"] > 0.5]
    if len(kotu) > 0:
        print(f"[output_opt] UYARI: {len(kotu)} talepte desi tutarsızlığı var (ilk 5):")
        print(kotu.head())
    else:
        print(f"[output_opt] desi korunumu OK: {len(merged)} talebin tamamı tutarlı")
    return kotu


def save_output(dispatches, forecast_df, distance):
    out_df, full_df = build_output(dispatches, forecast_df, distance)
    guard_against_template(out_df)
    validate_desi_conservation(full_df, forecast_df)

    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_df.to_excel(cfg.TASIMA_OUTPUT_FILE, index=False)
    print(f"[output_opt] kaydedildi: {cfg.TASIMA_OUTPUT_FILE}")
    print(f"[output_opt] {len(out_df)} satır, {out_df['Araç ID'].nunique()} araç, "
          f"toplam SLA cezası: {out_df['SLA cezası'].sum():,.2f} TL, "
          f"toplam maliyet: {out_df['Toplam maliyet'].sum():,.2f} TL")
    return out_df
