"""LightGBM + CatBoost ensemble (Tweedie hedef fonksiyonu) - egitim ve tahmin (Faz 3)."""
import sys
from pathlib import Path

import numpy as np
import lightgbm as lgb
from catboost import CatBoostRegressor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from features import CATEGORICAL, FEATURES


def _prep_lgb(df):
    """Kategorik kolonlari LightGBM'in tanidigi 'category' tipine cevirir."""
    df = df.copy()
    for c in CATEGORICAL:
        df[c] = df[c].astype("category")
    return df


def train(train_df):
    """LightGBM + CatBoost ikilisini egitir, ikisini de dondurur."""
    y = train_df["desi"].values
    m_lgb = lgb.LGBMRegressor(**config.LGB_PARAMS)
    m_lgb.fit(_prep_lgb(train_df)[FEATURES], y)
    m_cat = CatBoostRegressor(**config.CAT_PARAMS, cat_features=CATEGORICAL)
    m_cat.fit(train_df[FEATURES], y)
    return m_lgb, m_cat


def predict(model, df):
    """Iki modelin ortalama tahminini uretir, negatif degerleri kirpar."""
    m_lgb, m_cat = model
    p = (m_lgb.predict(_prep_lgb(df)[FEATURES]) + m_cat.predict(df[FEATURES])) / 2
    return np.clip(p, 0, None)


def train_lgb_only(train_df):
    y = train_df["desi"].values
    m_lgb = lgb.LGBMRegressor(**config.LGB_PARAMS)
    m_lgb.fit(_prep_lgb(train_df)[FEATURES], y)
    return (m_lgb,)


def predict_lgb_only(model, df):
    (m_lgb,) = model
    p = m_lgb.predict(_prep_lgb(df)[FEATURES])
    return np.clip(p, 0, None)


def train_separate_slots(train_df):
    """SLOT_MODE='separate' ablasyonu: her slot icin bagimsiz LightGBM modeli."""
    models = {}
    for slot_code in config.SLOT_CODES:
        sub = train_df[train_df["slot"] == slot_code]
        y = sub["desi"].values
        m = lgb.LGBMRegressor(**config.LGB_PARAMS)
        m.fit(_prep_lgb(sub)[FEATURES], y)
        models[slot_code] = m
    return models


def predict_separate_slots(models, df):
    pred = np.zeros(len(df))
    for slot_code, m in models.items():
        mask = (df["slot"] == slot_code).values
        if mask.sum() == 0:
            continue
        pred[mask] = m.predict(_prep_lgb(df[mask])[FEATURES])
    return np.clip(pred, 0, None)
