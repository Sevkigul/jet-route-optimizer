"""CapacityLedger (elleçleme + tır, hub x gün) ve DemandQueue (SLA-öncelikli kuyruk)."""
import heapq
import itertools

import pandas as pd


class CapacityLedger:
    """Elleçleme (desi) ve tır (adet) kapasitelerini hub x gün bazında takip eder.

    Elleçleme kapasitesi tüm araç tiplerine uygulanir. Tir kapasitesi ise
    sadece config_opt.TRUCK_CAP_VEHICLE_TYPE tipindeki araçlara uygulanir -
    kiralik dahil (organizasyon dogruladi: "Kiralik araclar da tir
    kapasitesini tuketir").

    Gece yarisini asan elleçleme islemleri ORANTILI olarak gunlere
    bolunur (organizasyon Soru-Cevap: 23:30'da baslayan 10000 desilik,
    100 dakikalik bir islem, 29.06'ya 3000 desi / 30.06'ya 7000 desi
    olarak sureyle orantili dagitilir) - bkz. try_consume_handling_timed /
    force_consume_handling_timed.
    """

    def __init__(self, handling_capacity, truck_capacity):
        self.handling_capacity = handling_capacity
        self.truck_capacity = truck_capacity
        self._handling_used = {}          # (hub, date) -> kullanilan desi
        self._truck_used_vehicles = {}     # (hub, date) -> kullanilan vehicle_id seti

    def handling_remaining(self, hub, date):
        cap = self.handling_capacity[hub]
        used = self._handling_used.get((hub, date), 0.0)
        return cap - used

    def try_consume_handling(self, hub, date, desi):
        if desi <= 0:
            return True
        if desi > self.handling_remaining(hub, date) + 1e-6:
            return False
        self._handling_used[(hub, date)] = self._handling_used.get((hub, date), 0.0) + desi
        return True

    def release_handling(self, hub, date, desi):
        """Onceden tuketilmis elleçleme kapasitesini geri iade eder (konsolidasyon
        adaylarini deneyip vazgecerken kullanilir)."""
        if desi <= 0:
            return
        self._handling_used[(hub, date)] = self._handling_used.get((hub, date), 0.0) - desi

    def force_consume_handling(self, hub, date, desi):
        """Zorunlu kiralik arac sevkiyati icin kosulsuz tuketim (kapasite
        yetersiz olsa bile kiralik arac cikmak zorunda - PDF kurali).
        Ledger negatife dusebilir, bu durum cok nadir olmali (kiralik
        rota yukleri, kapasitelere kiyasla kucuk)."""
        if desi <= 0:
            return
        self._handling_used[(hub, date)] = self._handling_used.get((hub, date), 0.0) + desi

    # --- gece yarisini asan islemler icin sureyle orantili tuketim -----------

    @staticmethod
    def _day_segments(start_time, duration_minutes):
        """(start_time, start_time+duration_minutes) araligini gun sinirlarinda
        boler. Dondurur: [(gun, o gunde gecen dakika), ...]."""
        if duration_minutes <= 0:
            return [(start_time.normalize(), 0.0)]
        end_time = start_time + pd.Timedelta(minutes=duration_minutes)
        segments = []
        cursor = start_time
        while cursor < end_time:
            day = cursor.normalize()
            next_midnight = day + pd.Timedelta(days=1)
            seg_end = min(end_time, next_midnight)
            segments.append((day, (seg_end - cursor).total_seconds() / 60.0))
            cursor = seg_end
        return segments

    def _prorated_allocations(self, start_time, duration_minutes, desi):
        segments = self._day_segments(start_time, duration_minutes)
        total_min = sum(m for _, m in segments) or 1.0
        return [(day, desi * (m / total_min)) for day, m in segments]

    def try_consume_handling_timed(self, hub, start_time, duration_minutes, desi):
        """start_time'da baslayip duration_minutes suren bir elleçleme islemi
        icin, gece yarisini asan kismi sureyle orantili olarak ilgili
        gunlere bolerek tuketir. Herhangi bir gunde kapasite yetmezse
        HICBIRI uygulanmaz (hepsi ya da hicbiri)."""
        if desi <= 0:
            return True
        allocations = self._prorated_allocations(start_time, duration_minutes, desi)
        for day, d in allocations:
            if d > self.handling_remaining(hub, day) + 1e-6:
                return False
        for day, d in allocations:
            self._handling_used[(hub, day)] = self._handling_used.get((hub, day), 0.0) + d
        return True

    def force_consume_handling_timed(self, hub, start_time, duration_minutes, desi):
        """Zorunlu (kiralik) sevkiyat icin kosulsuz, sureyle orantili tuketim."""
        if desi <= 0:
            return
        allocations = self._prorated_allocations(start_time, duration_minutes, desi)
        for day, d in allocations:
            self._handling_used[(hub, day)] = self._handling_used.get((hub, day), 0.0) + d

    def release_handling_timed(self, hub, start_time, duration_minutes, desi):
        """try_consume_handling_timed/force_consume_handling_timed ile yapilan
        sureyle-orantili tuketimin tam tersini uygular (rollback icin)."""
        if desi <= 0:
            return
        allocations = self._prorated_allocations(start_time, duration_minutes, desi)
        for day, d in allocations:
            self._handling_used[(hub, day)] = self._handling_used.get((hub, day), 0.0) - d

    def truck_remaining(self, hub, date):
        cap = self.truck_capacity[hub]
        used = len(self._truck_used_vehicles.get((hub, date), set()))
        return cap - used

    def try_consume_truck(self, hub, date, vehicle_id):
        """Ayni arac (vehicle_id) ayni hub'da ayni gun, HAREKET ETMEDEN
        (boşaltilip tekrar yuklenerek) tekrar kullanilirsa sadece 1 kez
        sayilir (resmi soru-cevap: bu kural sadece bu senaryoda gecerli,
        aracin ayrilip GERI DONMESI durumunda AYRI bir tuketim sayilir -
        cagiran kod bu iki durumu farkli vehicle_id ile ayirt etmelidir)."""
        used = self._truck_used_vehicles.setdefault((hub, date), set())
        if vehicle_id in used:
            return True
        if len(used) >= self.truck_capacity[hub]:
            return False
        used.add(vehicle_id)
        return True

    def force_consume_truck(self, hub, date, vehicle_id):
        """Zorunlu kiralik arac icin kosulsuz tir kapasitesi tuketimi (PDF:
        kiralik araclar da tir kapasitesini tuketir, ama talep yetersiz olsa
        bile cikmak zorunda - iki kural celisirse zorunluluk ustun tutulur,
        ledger negatife/asima dusebilir)."""
        used = self._truck_used_vehicles.setdefault((hub, date), set())
        used.add(vehicle_id)

    def release_truck(self, hub, date, vehicle_id):
        used = self._truck_used_vehicles.get((hub, date))
        if used is not None:
            used.discard(vehicle_id)


class DemandQueue:
    """SLA teslim tarihine gore oncelik sirali kuyruk (en yakin deadline once)."""

    def __init__(self):
        self._heap = []
        self._counter = itertools.count()

    def __len__(self):
        return len(self._heap)

    def push(self, portion):
        heapq.heappush(self._heap, (portion["deadline"], next(self._counter), portion))

    def pop_all(self):
        """Kuyruktaki tum talep parcalarini deadline sirali (en aciliyetli once) dondurur ve bosaltir."""
        items = [item for _, _, item in sorted(self._heap)]
        self._heap = []
        return items
