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

Geçmiş 130 günlük (1 Ocak 2026 – 10 Mayis 2026) güzergah-gün bazlı desi verisiyle
**LightGBM + CatBoost** ikilisi eğitilir; iki modelin ortalaması 11–17 Mayıs
için günlük tahmini üretir (iki bağımsız kütüphanenin harmanı, hata
çeşitliliğini azaltarak tüm doğrulama katmanlarında tekil modellerden daha
iyi sonuç vermiştir).

Öne çıkan tasarım kararları:

- **Panel dengeleme:** Her güzergah × her gün için tam tablo kurulur; teslimat
  olmayan gün-güzergahlar `0 desi` ile doldurulur (sıfır talep, eksik veri değil).
- **Sızıntısız özellikler:** Tahmin haftasında yakın geçmiş bulunmayacağından
  tüm gecikme (lag) özellikleri en az 7 günlüktür.
- **Tweedie hedef fonksiyonu:** Sıfır-yoğun ve sağa çarpık talep dağılımı
  LightGBM'in `tweedie` objective'i ile modellenir; log dönüşümlü
  regresyonun yarattığı sistematik eksik tahmin sapması (gerçek haftalık
  hacmin ~%26 altı) bu sayede giderilmiştir.
- **Resmi tatil özelliği:** 23 Nisan, 1 Mayıs gibi tatillerde talep belirgin
  düştüğünden takvime tatil işareti eklenmiştir; doğrulama hatasını en çok
  düşüren tek değişiklik budur.
- **Doğrulama:** Tek pencere yanıltıcı olabildiğinden model 3 ayrı zaman-katmanında
  test edilmiştir. Ortalama **WMAPE ≈ %26** (katmanlar: 22.5 / 37.0 / 19.4).
  Tahmin başarısı değerlendirme kriterlerinden biri olduğundan kasıtlı
  düşük/yüksek tahminden kaçınılmış; son doğrulama haftasında model,
  gerçekleşen toplam hacmi %11 sapmayla yakalamaktadır.

Çıktı formatı (`talep_tahmini.xlsx`): `Tarih | Çıkış TM | Varış TM | Tahmin Edilen Desi`

### Araç optimizasyonu

Tahmin edilen talep, her gün her güzergah için doğrudan (konsolidasyonsuz)
araçlara atanır:

- **Kiralık filo zorunlu ve önceliklidir:** Tanımlı kiralık araçlar, doluluk
  oranından bağımsız olarak her gün yola çıkar; kira + km maliyetleri her
  koşulda toplam maliyete dahildir.
- Kiralık kapasiteyi aşan yük, 4 araç tipi (Tır, Kamyon, Hafif Kamyon,
  Kamyonet) üzerinden **tamsayılı programlama (MIP)** ile en ucuz **spot**
  kombinasyonuna atanır. Çözücü: PuLP / CBC.
- **Minimum doluluk kısıtı:** Spot araçlar yalnızca kapasitelerinin en az
  %10'u dolu olacaksa plana eklenir (kiralık araçlar bu kısıttan muaftır).
- **Uğrama (multi-stop):** Yol üstündeki bir merkezin yükü, rotası uygun bir
  spot araca bindirilir (yük araçta kalır, aktarma yapılmaz — konsolidasyon
  değildir). Araç başına en fazla bir uğrama yapılır, sapma doğrudan
  mesafenin %15'ini aşamaz ve kapasite/doluluk kısıtları korunur. Yalnızca
  net tasarruf sağlayan birleşmeler uygulanır.
- Araç dönüşleri kapsam dışıdır; plan tek yönlüdür ve her gün bağımsız
  değerlendirilir.
- Maliyet = günlük sabit + km × mesafe (kuş uçuşu / Haversine).

Çıktı formatı (`arac_plani.xlsx`): her satır tek bir araç ataması —
`Tarih | Araç Tipi | Çıkış TM | Varış TM | Atanan Desi | Maliyet | Uğrama`
(Uğrama sütunu yalnızca uğrama yapan araçlarda doludur; ek bilgi niteliğindedir.)

---

## Sonuç

11–17 Mayıs haftası için bulunan maliyet (kiralık kira + km ve spot
günlük + km dahil):

| Bileşen | Tutar (TL) |
|---|---|
| Kiralık filo (kira + km) | 802.744 |
| Spot araçlar | 8.730.674 |
| **Toplam maliyet** | **9.533.417** |

---

## Notlar ve kapsam

- Bu teslimde **konsolidasyon kapsam dışıdır**: yükler ara merkezde
  indirilip birleştirilmez. Serbest bırakılan **uğrama** (multi-stop) ise
  kullanılmıştır; uğramada yük aynı araçta kalır ve araç son varışına devam
  eder. `optimize.py` içindeki `UGRAMA_AKTIF = False` ile bu katman
  kapatılabilir (kapalıyken toplam maliyet 10.879.942 TL'dir).
- Mesafeler Haversine (kuş uçuşu) ile hesaplanmıştır.
- Tahmin için XGBoost da denenmiş; doğrulamada LightGBM ve CatBoost'un
  gerisinde kaldığından nihai harmana (ensemble) bu iki model alınmıştır.
- SLA cezası, TM kapasiteleri, sefer süreleri ve şoför kısıtları bu aşamada
  veri setinde tanımlı olmadığından modele dahil edilmemiştir.
- İleri aşama hedefleri (stokastik optimizasyon, konsolidasyon, belirsizlik
  modellemesi) ön tasarım raporunda yer almakta olup bu teslimin kapsamı
  dışındadır.