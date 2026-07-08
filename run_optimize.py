"""Uctan uca rota/arac optimizasyonu: veri yukleme -> zamanlama -> konsolidasyon -> hub-merge -> cikti.

Faz A (kiralik + spot, paylasimli elleçleme/tir kapasitesi, SLA cezasi, dusuk
hacimli rotalarda SLA riski yoksa biriktirme) tek basina gecerli ve teslim
edilebilir. Faz B (tek-sevkiyat konsolidasyonu) ve Faz C (coklu-sevkiyat
hub-birlestirme) bunun ustune eklenen, config_opt.CONSOLIDATION_ENABLED ile
acilip kapatilabilir iyilestirme katmanlaridir - basarisiz olursa/kapatilirsa
Faz A sonucu degismeden kalir.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "opt"))

import config_opt as cfg
from data_loader_opt import load_data, validate_all
from scheduler import run_schedule
from consolidation import run_consolidation
from hub_merge import run_hub_merge
from hub_split import run_hub_split
from output_opt import save_output, build_demand_lookup


def main():
    print(f"Hedef tarih araligi: {cfg.TARGET_START.date()} -> {cfg.TARGET_END.date()} "
          f"(talep tahmini asamasiyla ayni pencere)\n")

    print("[1/6] Veri yukleniyor ve dogrulaniyor...")
    data = load_data()
    validate_all(data)

    print("\n[2/6] Zamanlama (kiralik + spot, kapasite kisitli, SLA-riskisiz biriktirme) calistiriliyor...")
    dispatches, leftover, ledger = run_schedule(data)

    demand_lookup = build_demand_lookup(data["forecast"], data["distance"])
    hubs = list(data["handling_capacity"])

    print("\n[3/6] Konsolidasyon (Faz B: tek-sevkiyat reroute) araniyor...")
    dispatches, savings_b, n_b = run_consolidation(
        dispatches, data["distance"], data["vehicle_specs"], ledger, demand_lookup, hubs
    )
    print(f"  {n_b} konsolidasyon uygulandi, tahmini net kazanc: {savings_b:,.2f} TL")

    print("\n[4/6] Hub-birlestirme (Faz C: ayni-hedef coklu-sevkiyat merge) araniyor...")
    dispatches, savings_c, n_c = run_hub_merge(
        dispatches, data["distance"], data["vehicle_specs"], ledger, demand_lookup, hubs
    )
    print(f"  {n_c} hub-birlestirme uygulandi, tahmini net kazanc: {savings_c:,.2f} TL")

    print("\n[5/6] Hub-bolme (Faz D: ayni-cikis coklu-sevkiyat split) araniyor...")
    dispatches, savings_d, n_d = run_hub_split(
        dispatches, data["distance"], data["vehicle_specs"], ledger, demand_lookup, hubs
    )
    print(f"  {n_d} hub-bolme uygulandi, tahmini net kazanc: {savings_d:,.2f} TL")

    print("\n[6/6] Cikti uretiliyor ve sema kontrolu yapiliyor...")
    out_df = save_output(dispatches, data["forecast"], data["distance"])

    rental_cost_total = sum(d["cost"] for d in dispatches if d["vehicle_class"] == "Kiralık")
    spot_cost_total = sum(d["cost"] for d in dispatches if d["vehicle_class"] == "Spot")
    sla_total = out_df["SLA cezası"].sum()
    grand_total = rental_cost_total + spot_cost_total + sla_total

    print("\n" + "=" * 60)
    print("  Pipeline tamamlandi.")
    print(f"  Kiralik maliyet : {rental_cost_total:>14,.2f} TL")
    print(f"  Spot maliyet    : {spot_cost_total:>14,.2f} TL")
    print(f"  SLA cezasi      : {sla_total:>14,.2f} TL")
    print(f"  TOPLAM          : {grand_total:>14,.2f} TL")
    print(f"  Cikti: {cfg.TASIMA_OUTPUT_FILE}")
    print("=" * 60)

    return out_df


if __name__ == "__main__":
    main()
