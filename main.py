"""Tüm pipeline'i tek komutta çalıştırır: talep tahmini -> araç/rota optimizasyonu.

Girdi verisi (data dosyaları) proje kök dizininde bulunmalıdır (bkz. src/config.py
ve src/config_opt.py'deki dosya yolları).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import config
from data_loader import load_data, validate_demand
from panel import build_panel, validate_panel
from features import add_features, validate_features
from validate import backtest
import model
from output import build_output, save_output as save_forecast_output

import config_opt as cfg
from data_loader_opt import load_data as load_opt_data, validate_all
from scheduler import run_schedule
from milkrun import run_milkrun
from consolidation import run_consolidation
from hub_merge import run_hub_merge
from hub_split import run_hub_split
from rental_piggyback import run_rental_piggyback
from output_opt import save_output as save_plan_output, build_demand_lookup


def run_forecast():
    print(f"Hedef tarih araligi: {config.TARGET_START.date()} -> {config.TARGET_END.date()}\n")

    print("[1/5] Veri yukleniyor...")
    data = load_data()
    validate_demand(data["demand"])

    print("\n[2/5] Panel kuruluyor...")
    panel = build_panel(data)
    validate_panel(panel, data)

    print("\n[3/5] Ozellikler ekleniyor...")
    feat = add_features(panel, data["distance"])
    validate_features(feat)

    print("\n[4/5] Coklu katman backtest (ensemble)...")
    fold_scores, avg_wmape = backtest(feat, train_fn=model.train, predict_fn=model.predict,
                                       label="final-ensemble")

    print("[5/5] Final model tum egitim verisiyle egitiliyor ve hedef pencere tahmin ediliyor...")
    egitim = feat[feat["desi"].notna()].copy()
    hedef_seti = feat[(feat["date"] >= config.TARGET_START) & (feat["date"] <= config.TARGET_END)].copy()

    final_model = model.train(egitim)
    hedef_seti["tahmin"] = model.predict(final_model, hedef_seti)

    out_df = build_output(hedef_seti)
    save_forecast_output(out_df)

    print(f"\nTalep tahmini tamamlandi. Backtest ortalama WMAPE: {avg_wmape:.2f}")
    print(f"Cikti: {config.OUTPUT_FILE}")
    return out_df


def run_optimization():
    print(f"\nHedef tarih araligi: {cfg.TARGET_START.date()} -> {cfg.TARGET_END.date()} "
          f"(talep tahmini asamasiyla ayni pencere)\n")

    print("[1/8] Veri yukleniyor ve dogrulaniyor...")
    data = load_opt_data()
    validate_all(data)

    print("\n[2/8] Zamanlama (kiralik + spot, kapasite kisitli, SLA-riskisiz biriktirme) calistiriliyor...")
    dispatches, leftover, ledger = run_schedule(data)

    demand_lookup = build_demand_lookup(data["forecast"], data["distance"])
    hubs = list(data["handling_capacity"])

    print("\n[3/8] Milk-run (Faz F: cok-duraklı ugrama, yeniden ellecleme yok) araniyor...")
    dispatches, savings_f, n_f = run_milkrun(
        dispatches, data["distance"], data["vehicle_specs"], ledger, demand_lookup
    )
    print(f"  {n_f} milk-run rotasi olusturuldu, tahmini net kazanc: {savings_f:,.2f} TL")

    print("\n[4/8] Konsolidasyon (Faz B: tek-sevkiyat reroute) araniyor...")
    dispatches, savings_b, n_b = run_consolidation(
        dispatches, data["distance"], data["vehicle_specs"], ledger, demand_lookup, hubs
    )
    print(f"  {n_b} konsolidasyon uygulandi, tahmini net kazanc: {savings_b:,.2f} TL")

    print("\n[5/8] Hub-birlestirme (Faz C: ayni-hedef coklu-sevkiyat merge) araniyor...")
    dispatches, savings_c, n_c = run_hub_merge(
        dispatches, data["distance"], data["vehicle_specs"], ledger, demand_lookup, hubs
    )
    print(f"  {n_c} hub-birlestirme uygulandi, tahmini net kazanc: {savings_c:,.2f} TL")

    print("\n[6/8] Hub-bolme (Faz D: ayni-cikis coklu-sevkiyat split) araniyor...")
    dispatches, savings_d, n_d = run_hub_split(
        dispatches, data["distance"], data["vehicle_specs"], ledger, demand_lookup, hubs
    )
    print(f"  {n_d} hub-bolme uygulandi, tahmini net kazanc: {savings_d:,.2f} TL")

    print("\n[7/8] Kiralik bos kapasite kullanimi (Faz E) araniyor...")
    dispatches, savings_e, n_e = run_rental_piggyback(
        dispatches, data["distance"], data["vehicle_specs"], ledger, demand_lookup
    )
    print(f"  {n_e} yuk bindirildi, tahmini net kazanc: {savings_e:,.2f} TL")

    print("\n[8/8] Cikti uretiliyor ve sema kontrolu yapiliyor...")
    out_df = save_plan_output(dispatches, data["forecast"], data["distance"])

    rental_cost_total = sum(d["cost"] for d in dispatches if d["vehicle_class"] == "Kiralık")
    spot_cost_total = sum(d["cost"] for d in dispatches if d["vehicle_class"] == "Spot")
    sla_total = out_df["SLA cezası"].sum()
    grand_total = rental_cost_total + spot_cost_total + sla_total

    print(f"\nKiralik maliyet : {rental_cost_total:>14,.2f} TL")
    print(f"Spot maliyet    : {spot_cost_total:>14,.2f} TL")
    print(f"SLA cezasi      : {sla_total:>14,.2f} TL")
    print(f"TOPLAM          : {grand_total:>14,.2f} TL")
    print(f"Cikti: {cfg.TASIMA_OUTPUT_FILE}")
    return out_df, grand_total


def main():
    print("[1/2] Talep tahmini calistiriliyor...")
    print("=" * 60)
    run_forecast()

    print("\n[2/2] Arac/rota optimizasyonu calistiriliyor...")
    print("=" * 60)
    _, grand_total = run_optimization()

    print("\n" + "=" * 60)
    print("  Pipeline tamamlandi.")
    print(f"  Toplam maliyet: {grand_total:,.2f} TL")
    print("=" * 60)


if __name__ == "__main__":
    main()
