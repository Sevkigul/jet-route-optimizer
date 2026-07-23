# Teknofest Hepsiburada Lojistik — Gelişmiş Çözüm Aşaması

Uçtan uca çözüm: talep tahmini → saat/dakika bazlı, milk-run (çok duraklı
uğrama) tabanlı, maliyet-minimize taşıma planı.

## Çalıştırma

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

`main.py` tek komutla tüm pipeline'ı çalıştırır: talep tahmini üretir,
taşıma planını optimize eder, çıktıları `outputs/` altına yazar
(`Talep-tahmini.xlsx`, `Tasima-plani.xlsx`) ve bağımsız bir doğrulayıcı
çalıştırıp özet raporu ekrana basar. Tam koşu ~35 saniye (limit: 10 dk).

Talep tahmini zaten üretildiyse (`outputs/forecast.pkl` mevcutsa) ve sadece
optimizasyonu tekrar çalıştırmak isterseniz:

```bash
python main.py --skip-forecast
```

## Proje yapısı

```
main.py                 tek komutluk pipeline (orkestrasyon)
data/                   yarışma tarafından sağlanan ham veri ve şablon dosyaları
src/
  config.py              sabitler, dosya yolları, yarışma kuralı parametreleri
  data_loader.py         Excel girdilerini temiz DataFrame'lere yükler
  demand_forecast.py     talep tahmini (haftalık ağırlıklı ortalama)
  utils.py                zaman/yuvarlama yardımcıları (dakika, gün-bölme)
  capacity_ledger.py      elleçleme + tır kapasite defterleri (TM×gün)
  vehicle_costing.py      araç maliyeti formülü ve SLA cezası hesabı
  milkrun.py              Clarke-Wright açık-rota kurucu + yerel arama cilası
  shipment.py              Item/Vehicle veri modeli, Talep ID finalize mantığı
  optimizer.py             ana motor (kiralık + peel + SLA-uyumlu milk-run + biriktirme)
  output_writer.py         şablon uyumlu Excel çıktı yazıcıları
  dogrulama.py             bağımsız kural-uyum ve özet-metrik doğrulayıcısı
notebooks/
  eda_talep_analizi.ipynb  talep deseni analizi ve yöntem kararlarının gerekçesi
outputs/                 üretilen Talep-tahmini.xlsx ve Tasima-plani.xlsx
```

## 1. Talep tahmini (`src/demand_forecast.py`)

Yöntem: iki bağımsız tahmincinin **harmanı** (ensemble):

- **Bileşen 1 — ağırlıklı hafta-günü ortalaması:** her (çıkış TM, varış TM,
  saat) için haftanın gününe göre üstel azalan ağırlıklı ortalama (son
  haftalara daha fazla ağırlık — veri Ocak'ta "ramp-up" gösteriyor). Haftalık
  mevsimsel yapıyı güçlü yakalar.
- **Bileşen 2 — sağlam mevsimsel lag topluluğu:** her hücre için son 4 haftanın
  aynı-gün değeri (lag 7/14/21/28); tahmin `0.5·(min atılmış ortalama) +
  0.5·(medyan)` — min atma bayram çukurunu, medyan ani sıçramayı yumuşatır.
  Güncel seviyeyi (trend) daha iyi yakalar. Ufuk ≤ 7 gün olduğundan tüm lag'ler
  gözlemli veridir (sızıntı yok).

Nihai tahmin `0.85·bileşen1 + 0.15·bileşen2`. İki yöntem farklı hatalar
yaptığından harman ikisinden de düşük WMAPE verir; ağırlık 0.85, WMAPE
kazancını korurken optimizasyonda SLA cezasını sıfırda tutan en yüksek değerdir.

**Veri temizliği:** kısmi gün 28 Haziran (47 satır, normal gün ~370) ile
26 Mayıs – 1 Haziran Kurban Bayramı anomalisi (arife yarım günü, ~sıfır talepli
bayram günleri ve 1 Haziran'daki 2.64M desilik telafi patlaması) eğitim dışıdır.
Hedef haftada tatil olmadığı için bu günler haftalık ortalamada sistematik
yanlılık yaratıyordu (Pazartesi ~%6 şişkin, Çarşamba–Cumartesi ~%9 düşük).

Yarı ömür (3 hafta), hariç tutulan gün seti ve harman ağırlığı **3 ayrı temiz
haftada** backtest ile seçildi: temizlik olmadan %23.11, temizlikle %21.37,
harmanla (0.85) %21.09. Yarı ömürde 3 ile 4 hafta pratikte eşit (%21.37 /
%21.35); harman ağırlığında 0.85 sıfır-ceza sınırındaki en düşük WMAPE'dir
(0.80'den itibaren optimizasyonda ceza doğuyor).

**GBM ile karşılaştırma:** LightGBM (tweedie 1.25, lag≥7 özellikleri) aynı
3 pencerede iki varyantla denendi ve kaybetti (ort. WMAPE %30.6 ve %26.5;
harman bile %24.7). Kısa seri geçmişi + bayram deliği GBM'in lag'lerini
bozarken, güçlü haftalık sezonluk elle kurulmuş tahmincileri favori kılıyor —
ayrıntı ve tablo için `notebooks/eda_talep_analizi.ipynb` §3.

Sadece geçmiş veride görülen 289 hat için, her gün × 2 slot (09:00/17:00)
bazında bir satır üretilir (29 Haziran 09:00 – 5 Temmuz 17:00), düşük
değerli tahminler de dahil.

## 2. Taşıma planı (`src/optimizer.py` + `src/milkrun.py`)

**Amaç:** min(kiralık + spot araç maliyeti + SLA cezası), dakika çözünürlüğünde.

Her (çıkış merkezi × gün) için 09:00 ve 17:00 talebi **tek havuzda**
birleşir (doluluğu ciddi artırır), sonra sırayla:

1. **Kiralık araçlar** (zorunlu, sabit hat, tek durak, dönüşsüz) önce doldurulur.
2. **Tam dolu büyük araçlar** (Tır, sonra Kamyon) tek-duraklı direkt "peel" edilir — büyük akışlar zaten verimli, karışım optimizasyonuna gerek yok.
3. **Kalan ince yükler**, Clarke-Wright tasarruf algoritmasıyla **milk-run**
   (çok-duraklı) rotalara birleştirilir: `çıkış → durak1 → durak2 → ...`.
   Her rotaya, yükünü taşıyan en ucuz araç tipi (Tır hariç) atanır.
   **Rota kurulumu SLA-uyumludur:** her birleştirme ve 2-opt adayı, kuru-koşum
   zamanlamasıyla teslim tarihlerine karşı denetlenir; herhangi bir durağı
   geciktirecek birleştirme yapılmaz (`milkrun.clarke_wright(uygun_mu=...)`).
   Böylece km tasarrufu hiçbir zaman SLA cezası üretemez — ceza, maliyet
   düşürme kaldıracı olarak kullanılmaz. Araç oluşturma öncesi `_sla_onarim`
   emniyet geçidi aynı denetimi bağımsızca tekrarlar (normalde no-op).
4. **Yerel arama cilası** (`milkrun.yerel_arama`): Clarke-Wright açgözlü bir
   kurucudur; üretilen rotalar üzerinde rota-birleştirme + durak-taşıma
   hamleleri, aynı SLA/kapasite denetimi altında iyileşme kalmayana dek
   uygulanır (klasik "construct + improve" mimarisi). Kuru-koşum zamanlaması
   elleçleme kapasitesi ertelemesini de modeller (`_ellecleme_fiili_baslangic`
   hem gerçek uygulamada hem tahminde kullanılır — tahmin/gerçekleşme farkı
   yoktur). Ölçüm: tam koşuda maliyeti 126K TL (%0.9) düşürdü, ceza 0 kaldı.

**Neden milk-run (hub-and-spoke değil)?** Şartname çok-duraklı uğramaya
izin veriyor ("araçlara birçok farklı transfer merkezine gidecek yükleri
yükleyebilirsiniz") ve organizatör bunu spot araçlar için özellikle
onayladı. Bir ara merkezde indirip-yeniden-yükleme (hub konsolidasyonu)
yerine, aracın **fiziksel olarak durup sadece o duraktan inen yükü**
elleçlemesi — devam eden yük yeniden elleçlenmez — kıt elleçleme
kapasitesini bozmuyor ve çift elleçleme maliyetinden kaçınıyor. İlk
denemede kurulan hub mimarisi tam tersi bir sonuç verip SLA cezasını
patlatmıştı (bkz. `notebooks/eda_talep_analizi.ipynb` §4).

**Çok-günlü biriktirme:** 2 günlük SLA'sı olan ince hat-günleri, güvenlik
payıyla (deadline'a en az 6 saat kala durarak) bir sonraki güne biriktirilir
— daha dolu milk-run rotalarına binmelerini sağlar.

## Kısıtların ele alınışı

| Kısıt | Uygulama |
|---|---|
| Elleçleme kapasitesi (günlük, 00:00 reset, ortak çıkış+varış havuzu) | `capacity_ledger.EllecklemeLedger`; kapasite yetersizse elleçleme **yapısal olarak** bir sonraki güne ertelenir (aşım imkansız) |
| Elleçleme süresi 0.01 dk/desi, dakikaya yukarı yuvarlama | `utils.ellecleme_dakika` |
| Gece yarısını aşan elleçleme → orantılı gün bölünmesi | `utils.gun_gun_dagit` |
| Tır kapasitesi (günlük, giden+gelen ortak, sadece Tır tipi) | `capacity_ledger.TirLedger`; kiralık Tır ihtiyacı spot için önceden rezerve edilir; varış kapasitesi tahmini varış GÜNÜNE göre denetlenir (gece aşan sefer ertesi günden düşer) |
| Spot Tır yeniden kullanımı: aynı araç aynı TM'de aynı gün 1 sayılır (Q&A) | `optimizer._tir_kaynak_bul` — varışını bitirmiş spot Tır aynı gün aynı TM'den yeni sefere zincirlenir, aynı Araç ID'yi taşır; `dogrulama.py` A5 ziyaret-tekilleştirme ile aynı kuralı bağımsız uygular |
| SLA = talep tamamlanma anı + hedef_gün×24s, ceza = geciken desi × yukarı yuvarlanmış saat × 0,4 TL | `vehicle_costing.sla_cezasi` |
| Bekleme süresi kullanım süresine dahil (Q&A): kapasite ertelemesinde araç indirilemeden beklerse süresi ücretlenir | `optimizer._arac_olustur` (`leg_wait_dk`); `dogrulama.py` A7 bunu zaman damgalarından bağımsız geri çıkarıp doğrular |
| Kiralık: sabit hat, dönüşsüz, uğramasız, her gün zorunlu tek sefer | `optimizer._gun_cikis_isle` adım 1 |
| Talep bölme (kapasite nedeniyle) → `D0001-1`, `-2` (düz numaralandırma) | `shipment.id_finalize` |
| Araç ID `V0001`, Talep ID `D00001` format | `optimizer._assign_ids`, `demand_forecast.forecast_all` |
| "Toplam maliyet" satırlara desi-oranlı paylaştırılır (leg başına tekrar yok) | `optimizer._plan_satirlari_olustur` — kolon toplamı doğrudan gerçek maliyeti verir |

## Doğrulama (`src/dogrulama.py`)

`main.py`'nin son adımı, ürettiği planı **sıfırdan, kendi iç durumuna
bakmadan** yeniden kontrol eder: kolon şeması, Araç ID/Talep ID formatı,
desi mutabakatı (tahmin = teslim edilen), elleçleme/tır kapasite aşımı,
kiralık kuralları, maliyet mutabakatı (bağımsız yeniden hesap) ve SLA
doğruluğu (her teslimatın gecikmesi zaman damgalarından yeniden hesaplanır).
Son koşuda tüm kontroller **0 hata** ile geçti.

## Sonuç: 14.112.471 TL genel maliyet, sıfır SLA cezası

29 Haziran – 5 Temmuz tahmin dönemi için üretilen plan tüm kısıtları
ihlalsiz karşılar; bağımsız doğrulayıcı **0 hata** verir, çalışma süresi
~35 saniyedir (limit 10 dk).

| Metrik | Değer |
|---|---:|
| Genel maliyet | **14.112.471 TL** |
| SLA cezası | **0 TL** |
| Desi başına maliyet | 2.173 TL |
| Medyan doluluk | %82.0 |
| Tahmin hatası (WMAPE) | %21.1 |

(Genel maliyet, taşınan tahmini talebe orantılıdır; farklı tahminler farklı
hacim taşıdığından adil kıyas **desi başına maliyettir**. Harman tahmini
talebi %1.2 büyütürken birim maliyeti düşürmüştür.)

**Neden milk-run?** Direkt sevkiyat (49,7M TL, doluluk %4.6) ve hub-and-spoke
konsolidasyon (28,9M TL) mimarileri de kurulup ölçüldü; milk-run bunlardan
sırasıyla **%72** ve **%52** daha ucuz. Mimari karşılaştırması, adım adım
iyileştirmeler ve reddedilen denemeler dahil tüm karar gerekçeleri
`notebooks/eda_talep_analizi.ipynb`'de.

Sonuca yön veren iki ilke: **(1)** tahmin kasıtlı düşük tutulmadı —
değerlendirme gerçek talebe göre yapıldığından dürüst tahmin esastır;
**(2)** SLA cezası maliyet düşürme kaldıracı olarak kullanılmadı, teslim
tarihine duyarlı rotalama ile sıfırlandı.

## Bilinen varsayımlar

- **Milk-run'da devam eden yük yeniden elleçlenmez** — bu senaryo organizatöre
  birebir soruldu ve yazılı onaylandı (Soru-Cevap dokümanı, soru 11/1: "araç
  ilk durakta bir kısım yükü indirirken geri kalan yük araçta kalıp sonraki
  duraklarda elleçleniyor" → "Spot araçlar için evet mümkündür"). Kasıtlı
  indirip-yeniden-yükleme (konsolidasyon) ise 2× elleçleme kuralına tabidir
  ve ayrı senaryodur.
- **Spot Tır yeniden kullanımı yalnız aynı gün içinde** — Q&A "gün içerisinde
  birden fazla taşıma" ifadesini kullandığı ve kapasiteler 00:00'da
  sıfırlandığı için gün-aşırı devam eden araçta indirim varsayılmaz
  (muhafazakâr yorum). Mevcut talep deseni akşam-ağırlıklı olduğundan (hacmin
  ~%91'i 17:00 slotu) aynı-gün zincir penceresi nadiren açılır; mekanizma
  kuralın izin verdiği her durumda otomatik devreye girer.
- Spot araç dönüşleri modellenmiyor (şartname zorunlu kılmıyor).
- Çıktıdaki "Varış Saati", elleçlemenin fiilen başlayabildiği andır. Varış
  TM'sinde günlük elleçleme kapasitesi dolduğu için erteleme olan nadir
  bacaklarda bu, fiziksel varıştan geç olabilir; aradaki fark aracın
  ücretlendirilen bekleme süresidir ve `Çıkış Saati + Yolculuk süresi` ile
  karşılaştırılarak satırdan okunabilir. Yükleme ÖNCESİ bekleme ise ücretsizdir
  (şartname: çıkış elleçlemesi talep tamamlanmasından sonra herhangi bir anda
  yapılabilir; yük araçsız bekler, araç geç çağrılır).
- Milk-run rotalarında Tır kullanılmıyor (kıt tır kapasitesini çok-duraklı
  rotada tüketmemek için); Tır sadece kiralık ve tam-dolu tek-duraklı
  "peel" seferlerinde kullanılıyor.
