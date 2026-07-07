"""CapacityLedger (elleçleme + tır, hub x gün) ve DemandQueue (SLA-öncelikli kuyruk)."""
import heapq
import itertools


class CapacityLedger:
    """Elleçleme (desi) ve tır (adet) kapasitelerini hub x gün bazında takip eder.

    Elleçleme kapasitesi tüm araç tiplerine uygulanir. Tir kapasitesi ise
    sadece config_opt.TRUCK_CAP_VEHICLE_TYPE tipindeki SPOT araçlara
    uygulanir (kiralik araclar config_opt.RENTAL_EXEMPT_FROM_TRUCK_CAP=True
    oldugu surece muaf).
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

    def truck_remaining(self, hub, date):
        cap = self.truck_capacity[hub]
        used = len(self._truck_used_vehicles.get((hub, date), set()))
        return cap - used

    def try_consume_truck(self, hub, date, vehicle_id):
        """Ayni arac (vehicle_id) ayni hub'da ayni gun tekrar kullanilirsa
        sadece 1 kez sayilir (resmi soru-cevaba gore)."""
        used = self._truck_used_vehicles.setdefault((hub, date), set())
        if vehicle_id in used:
            return True
        if len(used) >= self.truck_capacity[hub]:
            return False
        used.add(vehicle_id)
        return True

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
