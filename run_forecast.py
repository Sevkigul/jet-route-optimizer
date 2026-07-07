"""Uctan uca talep tahmini: veri yukleme -> panel -> ozellikler -> backtest -> final model -> cikti."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import config
from data_loader import load_data, validate_demand
from panel import build_panel, validate_panel
from features import add_features, validate_features
from validate import backtest
import model
from output import build_output, save_output


def main():
    print(f"Hedef tarih araligi: {config.TARGET_START.date()} -> {config.TARGET_END.date()} "
          f"(ORGANIZASYONDAN TEYIT BEKLENIYOR - config.py'de guncellenecek)\n")

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
    save_output(out_df)

    print("\n" + "=" * 60)
    print("  Pipeline tamamlandi.")
    print(f"  Backtest ortalama WMAPE: {avg_wmape:.2f}")
    print(f"  Cikti: {config.OUTPUT_FILE}")
    print("=" * 60)

    return out_df


if __name__ == "__main__":
    main()
