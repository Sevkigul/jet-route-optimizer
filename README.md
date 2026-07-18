# Teknofest HepsiJET — Gelişmiş Çözüm Aşaması

Transfer merkezleri arası kargo taşımacılığında **29 Haziran – 5 Temmuz 2026**
haftası için talep tahmini ve maliyet-minimize araç/rota optimizasyonu.

İki aşamalı çözüm:
1. **Talep tahmini** — geçmiş veriyle her güzergah × slot (09:00/17:00) için
   günlük desi talebini öngörmek.
2. **Araç/rota optimizasyonu** — bu talebi kiralık ve spot araçlara,
   organizasyonun Soru-Cevap oturumunda netleştirdiği kurallara (kullanım
   süresi = elleçleme+yolculuk+bekleme, dakikaya yukarı yuvarlama, gece
   yarısı proransı, tır kapasitesi) tam uyumlu şekilde, en düşük maliyetle
   dağıtmak.

---

## Kurulum

```bash
pip install -r requirements.txt
```

## Çalıştırma

8 veri dosyasını proje kök dizinine koyun (bkz. `src/config.py` ve
`src/config_opt.py`'deki dosya adları). Tüm pipeline tek komutla çalışır:

```bash
python main.py
```

Adımları ayrı ayrı çalıştırmak isterseniz:

```bash
python -c "import sys; sys.path.insert(0,'src'); from main import run_forecast; run_forecast()"
python -c "import sys; sys.path.insert(0,'src'); from main import run_optimization; run_optimization()"
```

Çıktılar `outputs/` klasöründe oluşur: `talep_tahmini_gelismis.xlsx`,
`tasima_plani_gelismis.xlsx`.

---

## Klasör yapısı

```
├── data/                     # eski aşamadan kalan veri klasörü (bu pipeline kök dizindeki dosyaları kullanır)
├── notebooks/
│   └── 01_eda.ipynb
├── src/
│   ├── config.py             # talep tahmini ayarları
│   ├── data_loader.py        # veri yükleme ve standartlaştırma
│   ├── panel.py               # güzergah x gün x slot panel dengeleme
│   ├── features.py            # özellik üretimi (lag, mevsimsellik, tatil)
│   ├── model.py                # LightGBM + CatBoost Tweedie topluluğu
│   ├── validate.py             # çoklu-katman backtest
│   ├── output.py               # Talep Tahmini şablonuna dönüşüm
│   │
│   ├── config_opt.py         # optimizasyon ayarları
│   ├── data_loader_opt.py    # veri yükleme + çapraz doğrulama
│   ├── state.py                # CapacityLedger (elleçleme/tır kapasitesi, hub x gün)
│   ├── costs.py                 # kullanım süresi / maliyet / SLA cezası formülleri
│   ├── portions.py              # talep portion havuzları (FIFO)
│   ├── rental.py                 # zorunlu kiralık araç sevkiyatı
│   ├── spot_assign.py            # rota-dalga bazlı spot araç MIP'i
│   ├── scheduler.py              # ana orkestrasyon (Faz A)
│   ├── milkrun.py                 # çok-duraklı uğrama (Faz F, Clarke-Wright)
│   ├── consolidation.py           # tek-sevkiyat reroute (Faz B)
│   ├── hub_merge.py                # aynı-hedef çoklu-sevkiyat birleştirme (Faz C)
│   ├── hub_split.py                # aynı-çıkış çoklu-sevkiyat bölme (Faz D)
│   ├── rental_piggyback.py         # kiralık boş kapasite kullanımı (Faz E)
│   └── output_opt.py               # Taşıma Planı şablonuna dönüşüm + doğrulama
│
├── outputs/                  # teslim dosyaları (üretilir)
├── main.py                   # uçtan uca pipeline
├── requirements.txt
└── README.md
```

---

## Yöntem

### Talep tahmini

Geçmiş güzergah-gün-slot bazlı desi verisiyle **LightGBM + CatBoost** (Tweedie
hedef fonksiyonu) topluluğu eğitilir. Panel her güzergah × slot × gün için
tam dengelenir (teslimat olmayan satırlar `0 desi`), sızıntı önlemek için
tüm lag özellikleri tahmin ufkundan uzun tutulur, resmi tatiller (Kurban
Bayramı dahil) takvime işlenir. Çoklu-katman backtest ile doğrulanır.

Çıktı (`talep_tahmini_gelismis.xlsx`): `Talep ID | Tarih | Talep Tamamlama
Saati | Çıkış TM | Varış TM | Tahmin Edilen Desi` — 289 güzergah × 7 gün ×
2 slot = 4046 satır, hiçbiri atılmaz.

### Araç/rota optimizasyonu

Kademeli (decomposition) bir mimari — tek dev MILP yerine, paylaşımlı bir
elleçleme/tır kapasite defteri (`CapacityLedger`) üzerinde sırayla çalışan
6 faz:

1. **Zamanlama (Faz A):** kiralık araçlar önce (zorunlu, sabit hat), kalan
   yük rota-dalga bazlı küçük bir MIP ile spot araçlara atanır. Düşük
   hacimli rotalarda, SLA riski yoksa, birkaç günlük talep biriktirilip
   daha dolu tek araçla gönderilir.
2. **Milk-run (Faz F):** aynı gün farklı hedeflere giden ince yükler
   Clarke-Wright tasarruf algoritmasıyla tek (çok duraklı) araca birleştirilir.
3. **Konsolidasyon (Faz B):** tek bir düşük-dolu sevkiyat için ara merkez
   üzerinden daha ucuz bir alternatif aranır.
4. **Hub-birleştirme (Faz C) / Hub-bölme (Faz D):** farklı kökenli/aynı
   hedefli ya da aynı kökenli/farklı hedefli düşük-dolu sevkiyatlar ortak
   bir ara merkezde birleştirilir.
5. **Kiralık boş kapasite (Faz E):** zorunlu kiralık araçların boş
   kapasitesine, aynı çıkıştan giden başka bir yük bindirilir.

Maliyet formülü organizasyonun Soru-Cevap netleştirmesine göredir: Kullanım
Süresi = çıkış elleçleme + yolculuk + varış elleçleme (+bekleme), tüm
süreler dakikaya yukarı yuvarlanır, elleçleme kapasitesi gece yarısını
aşan işlemlerde süreyle orantılı bölünür. Tır kapasitesi kiralık araçları
da kapsar. SLA cezası = geciken desi × geciken saat (yukarı yuvarlı) ×
0,4 TL.

Çıktı (`tasima_plani_gelismis.xlsx`): şablonla birebir eşleşen 16 kolon,
şema ve desi-korunum kontrolünden geçmiş.

---

## Sonuç

29 Haziran – 5 Temmuz 2026 haftası için bulunan maliyet:

| Bileşen | Tutar (TL) |
|---|---|
| Kiralık maliyet | 399.159 |
| Spot maliyet | 12.784.635 |
| SLA cezası | 1.149.834 |
| **Toplam maliyet** | **14.333.627** |

---

## Notlar ve kapsam

- Bu pipeline, önceki aşamanın (`11–17 Mayıs`, Haversine mesafe, günlük
  sabit maliyet) kod tabanının yerini almıştır — kurallar (kullanım süresi,
  kapasite defterleri, SLA cezası) bu aşamada organizasyonun resmi
  netleştirmelerine göre yeniden tasarlanmıştır.
- Boş spot araçların dönüşü modellenmez/maliyetlenmez (kural: zorunlu
  değil, döndürülmez). Kiralık araçlarda dönüş yoktur.
- Optimizasyon için bir zaman sınırlaması yoktur — tahmin penceresinin
  sonunda hâlâ kapasite kısıtından dolayı gönderilemeyen yük, hedef
  pencere dışında (SLA cezasıyla) teslim edilebilir.
