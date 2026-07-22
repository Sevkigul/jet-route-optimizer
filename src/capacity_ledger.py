"""TM x gün bazında tükenen (elleçleme / tır) kapasitesini izleyen defterler."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class GunlukKapasiteLedger:
    """TM -> günlük limit; (TM, gün) -> o güne kadar tüketilen miktar."""

    kapasite_sozlugu: dict[str, float]
    kullanilan: dict[tuple[str, date], float] = field(default_factory=dict)
    asim: dict[tuple[str, date], float] = field(default_factory=dict)

    def limit(self, tm: str) -> float:
        return self.kapasite_sozlugu.get(tm, 0.0)

    def mevcut_kapasite(self, tm: str, tarih: date) -> float:
        return self.limit(tm) - self.kullanilan.get((tm, tarih), 0.0)

    def tuket(self, tm: str, tarih: date, miktar: float) -> None:
        """Kapasiteyi düşürür. Çıkış tarafı admission-control ile önceden
        kısıtlandığı için normal akışta aşım olmaz; varış tarafında (Tır
        seçimi/rota süresine bağlı, önceden tam kestirilemeyen) nadir bir
        aşım olursa çalışmayı durdurmak yerine kaydedilir (bkz. `asim`) —
        `src/dogrulama.py` (A4/A5) bunu bağımsız yeniden hesapla yakalar."""
        if miktar <= 0:
            return
        mevcut = self.mevcut_kapasite(tm, tarih)
        if miktar > mevcut + 1e-6:
            key = (tm, tarih)
            self.asim[key] = self.asim.get(key, 0.0) + (miktar - mevcut)
        key = (tm, tarih)
        self.kullanilan[key] = self.kullanilan.get(key, 0.0) + miktar


class EllecklemeLedger(GunlukKapasiteLedger):
    """Desi bazlı; bir TM'nin çıkış+varış tüm elleçlemesi ortak havuzdan düşer."""


class TirLedger(GunlukKapasiteLedger):
    """Adet (int) bazlı; sadece 'Tır' tipi araçlar için, giden/gelen ayrımı yok."""
