# HepsiJET Anahat Lojistik Optimizasyonu

Transfer merkezleri arası kargo taşımacılığında **11–17 Mayıs** haftası için
talep tahmini ve maliyet-minimize araç planlaması.

İki aşamalı çözüm:
1. **Talep tahmini** — geçmiş veriyle her güzergahın günlük desi talebini öngörmek.
2. **Araç optimizasyonu** — bu talebi kiralık ve spot araçlara en düşük maliyetle dağıtmak.

---

## Kurulum

```bash
pip install -r requirements.txt
```

## Çalıştırma

4 veri dosyasını `data/raw/` klasörüne koyun. Tüm pipeline tek komutla çalışır:

```bash
python main.py
```

Adımları ayrı ayrı çalıştırmak isterseniz:

```bash
python src/utils.py       # merkezler arası mesafe matrisi
python src/forecast.py    # talep tahmini  -> outputs/talep_tahmini.xlsx
python src/optimize.py    # araç planı     -> outputs/arac_plani.xlsx
```

Çıktılar `outputs/` klasöründe oluşur.

---

## Klasör yapısı

```
├── data/
│   ├── raw/          # 4 orijinal Excel (versiyon kontrolüne dahil değil)
│   └── processed/    # mesafe_matrisi.csv (üretilir)
├── notebooks/
│   └── 01_eda.ipynb  # keşifsel veri analizi
├── src/
│   ├── data_loader.py   # veri yükleme ve standartlaştırma
│   ├── utils.py         # mesafe matrisi
│   ├── features.py      # panel dengeleme ve özellik üretimi
│   ├── forecast.py      # talep tahmin modeli
│   └── optimize.py      # araç optimizasyonu
├── outputs/          # teslim dosyaları (üretilir)
├── main.py           # uçtan uca pipeline
├── requirements.txt
└── README.md
```

---

## Yöntem

### Talep tahmini

Geçmiş 130 günlük (1 Ocak 2026 – 10 Mayıs 2026) güzergah-gün bazlı desi verisiyle
**LightGBM** modeli eğitilir ve 11–17 Mayıs için günlük tahmin üretilir.

Öne çıkan tasarım kararları:

- **Panel dengeleme:** Her güzergah × her gün için tam tablo kurulur; teslimat
  olmayan gün-güzergahlar `0 desi` ile doldurulur (sıfır talep, eksik veri değil).
- **Sızıntısız özellikler:** Tahmin haftasında yakın geçmiş bulunmayacağından
  tüm gecikme (lag) özellikleri en az 7 günlüktür.
- **Log dönüşümü:** Sağa çarpık talep dağılımı için hedef `log1p` ile dönüştürülür.
- **Doğrulama:** Tek pencere yanıltıcı olabildiğinden model 3 ayrı zaman-katmanında
  test edilmiştir.

**Doğrulama sonucu:** ortalama **WMAPE ≈ %47** (güzergah-gün granülerliğinde,
yüksek varyanslı talep için makul bir aralık).

### Araç optimizasyonu

Tahmin edilen talep, her gün her güzergah için doğrudan (konsolidasyonsuz)
araçlara atanır:

- Önce sabit **kiralık filo** kapasitesi doldurulur (batık maliyet).
- Kalan yük, 4 araç tipi (Tır, Kamyon, Hafif Kamyon, Kamyonet) üzerinden
  **tamsayılı programlama (MIP)** ile en ucuz **spot** kombinasyonuna atanır.
  Çözücü: PuLP / CBC.
- Spot maliyet = araç sayısı × (spot sabit günlük + spot km × mesafe).

---

## Sonuç

11–17 Mayıs haftası için bulunan maliyet:

| Bileşen | Tutar (TL) |
|---|---|
| Spot araç (değişken) | 9.195.279 |
| Kiralık filo (sabit) | 802.744 |
| **Toplam maliyet** | **9.998.023** |

> Raporlanan ana değer kiralık ve spot maliyetlerin toplamıdır. Yalnızca değişken
> maliyet baz alınırsa spot toplamı geçerlidir.

---

## Notlar ve kapsam

- Bu teslimde **konsolidasyon kapsam dışıdır**; güzergahlar doğrudan servis edilir.
- Mesafeler Haversine ile hesaplanmıştır.
- Tahmin için XGBoost ensemble denenmiş; doğrulama LightGBM lehine sonuç
  verdiğinden tek model kullanılmıştır.
- Kiralık filo varsayılan olarak yönlü (tek yön) modellenmiştir.
- İleri aşama hedefleri (stokastik optimizasyon, konsolidasyon, belirsizlik
  modellemesi) ön tasarım raporunda yer almakta olup bu teslimin kapsamı dışındadır.