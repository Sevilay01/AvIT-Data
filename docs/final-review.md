# 20 iş günlük staj teslimi incelemesi

İnceleme tarihi: 14 Eylül 2026. Başlangıç commit'i:
[0c38fbb](https://github.com/Sevilay01/AvIT-Data/commit/0c38fbb9a52f6ba70eefffae374572c65fd9a562).
Düzeltmeler [PR #1](https://github.com/Sevilay01/AvIT-Data/pull/1) üzerinden izlenir.

## Teknik kapsam değerlendirmesi

Proje, tek kurum ve tek süreçte çalışan ağ izleme staj projesi olarak 20 iş günü
için yeterli teknik kapsama sahiptir. Bu değerlendirme okulun veya işverenin
resmî kabul kararı değildir. Staj teslimini tamamlamak için PostgreSQL, SNMP,
SSO veya çok müşterili ürün geliştirmesi zorunlu kabul edilmemiştir.

| Öğrenme/uygulama alanı | Depodaki karşılığı |
|---|---|
| Ağ programlama | Windows/Linux ICMP adaptörü, CIDR yetkilendirmesi, IPv4/IPv6, ağsız mock |
| Backend ve veri modeli | FastAPI, SQLAlchemy, SQLite, geçmişin korunması, beş Alembic migration |
| Güvenlik | Argon2id, hashlenmiş oturum belirteçleri, CSRF/origin, admin/viewer, audit |
| Eşzamanlı ve zamanlanmış işler | Tek süreç kilidi, sınırlı kontroller, zamanlayıcı, alarm durum geçişleri |
| Operasyon | Transactional outbox, retry, bakım/susturma, sağlık, temizlik, backup/restore karantinası |
| Arayüz ve raporlama | Türkçe panel, yerel Chart.js, tarih/kaynak filtreleri, güvenli CSV |
| Test ve teslim | Geçici DB testleri, gerçek localhost HTTP, iki temiz venv, Windows/Linux CI, Chromium |

## Bulunan ve düzeltilen sorunlar

1. **Windows CI konsol kodlaması:** Türkçe çıktı ana doğrulayıcı süreçte
   UnicodeEncodeError oluşturuyordu. CLI konsolu UTF-8'e ayarlandı; ASCII ana
   konsol altında Türkçe çıktı regresyonu eklendi.
2. **Temizlik zaman sınırı:** SQLite julianday dönüşümü mikrosaniyeleri
   yuvarlayarak kesimden hemen önceki ölçümün korunmasına yol açıyordu.
   Raporda kullanılan kesin UTC metin normalleştirmesi ortaklaştırıldı;
   ölçüm, oturum ve alarm kanıtı karşılaştırmaları bu anahtarı kullanıyor.
   Eşit/bir mikrosaniye önce/bir mikrosaniye sonra sınırları korunuyor.
3. **Giriş yönlendirme döngüsü:** Geçerli oturumla eksik veya uyuşmayan CSRF
   çerezi /login ile / arasında döngü oluşturuyordu. Böyle bir durumda eski
   oturum iptal edilerek yeni giriş oturumu oluşturuluyor.
4. **CI kabul kanıtı:** Teslim doğrulayıcısının sabit not_run alanı,
   GitHub Actions bağlamını ve çalışma bağlantısını kaydedecek şekilde düzeltildi.
5. **Eksik tarayıcı kabulü:** Gerçek Chromium ile localhost mock uygulaması
   üzerinde programatik tarayıcı kontrolü ve indirilebilir kanıtlar eklendi.

Önceki migration'lar, veritabanı şeması ve varsayılan mock/gönderimsiz davranış
değiştirilmedi. Gerçek kullanıcı DB'sinde işlem yapılmadı.

## Kanıtlar ve kapsam sınırı

İlk main çalışması iki platformda başarısızdı:
[başlangıç CI](https://github.com/Sevilay01/AvIT-Data/actions/runs/34865526572).

cb8b99a commit'inin
[CI çalışmasında](https://github.com/Sevilay01/AvIT-Data/actions/runs/34867213324)
Ubuntu'da 200 test, Ruff, lock/pip, migration ve temiz ZIP kurulumu geçti.
Aynı Ubuntu işinde Chromium grafiği çizdi, kaynak filtresini değiştirdi,
CSV düğmesinden dosyayı diske indirdi, bakım/susturma oluşturup iptal etti ve
admin/viewer ekranlarını doğruladı. Üç RTT değeri 1.25 / null / 2.5 olarak grafikte,
1,25 / boş / 2,5 olarak CSV'de sınandı. Sayfa JavaScript hatası ve dış sayfa
isteği oluşmadı. Bu kayıt yalnız belirtilen commit ve işin kanıtıdır.

**Teslim alınacak son commit için** PR'ın Windows ve Ubuntu işlerinin ikisi de
başarılı olmalıdır. Önceki commit'in yeşil sonucu son commit'e aktarılmaz.
[Workflow çalışmaları](https://github.com/Sevilay01/AvIT-Data/actions/workflows/verify.yml)
ve PR Checks sekmesi güncel sonucu gösterir.

Her başarılı iş kaynak ZIP'ini, kaynak manifestini ve temiz kurulum JSON'larını
artifact olarak saklar. Ubuntu artifact'i ayrıca browser-acceptance/acceptance.json,
graph.png ve viewer-mobile.png içerir. Artifact saklama süresi 14 gündür;
teslim için ilgili son commit'in paketini ve kanıtlarını bu süre içinde indirin.

| Kontrol | Değerlendirme |
|---|---|
| Backend/mock ve temiz kurulum | Son commit'in iki platformlu CI sonucuyla doğrulanır |
| Chromium grafik, filtre, CSV düğmesi ve bakım/susturma | Otomatik kabul eklendi; Ubuntu işinde çalışır |
| Manuel görsel/klavye incelemesi, gerçek masaüstü tarayıcılar | Ekran görüntüsü üretmek manuel kabul yerine geçmez |
| Excel sayısal içe aktarımı | Gerçek Excel ortamı gerektirir; yapılmış sayılmadı |
| Şirket ağı ve gerçek SMTP | Yetkilendirilmiş hedef/hesap ve ayrı ortam kabulü gerekir; trafik/gönderim yapılmadı |
| Kurumsal kapasite, RTO/RPO, merkezi işletim | Staj prototipinin doğrulanmış kapsamı dışında; üretim öncesi kabul konusu |

Bu sürüm kurumsal ağlarda sınırsız kapasite veya kesintisiz işletim garantisi
vermez. Outbox/audit saklama ve tek süreç sınırları işletim kılavuzunda geçerlidir.

## İsteğe bağlı yerel Chromium doğrulaması

Python 3.12 ile ayrı bir doğrulama ortamında:

```powershell
python -m venv .venv-browser-review
.\.venv-browser-review\Scripts\python.exe -m pip install -r requirements-browser.txt
.\.venv-browser-review\Scripts\python.exe -m playwright install chromium
.\.venv-browser-review\Scripts\python.exe -m scripts.browser_smoke --output build/browser-review-yeni
```

Çıktı klasörü yeni olmalıdır. Script yalnız kendi geçici DB'sini, rastgele test
hesaplarını ve localhost sunucusunu kullanır. Çalışma bağımlılıklarına Playwright
eklenmez. Linux'ta tarayıcı sistem bağımlılıkları ayrıca gerekebilir; CI bunları
playwright install --with-deps chromium ile kurar. Bu otomasyon kullanıcının
açık tarayıcısını veya masaüstünü kontrol etmez.

## Staj sunumunda açıklanması gerekenler

- ICMP yanıt oranı neden SLA/gerçek çalışma süresiyle aynı değildir?
- Kontrol hatası, yanıtsızlık ve eski ölçüm neden ayrı tutulur?
- Alarm ve bildirim olayı neden aynı transaction'da kaydedilir?
- SMTP kabulünden sonraki çökmede neden kopya mesaj oluşabilir?
- CSRF çerezi tek başına neden yeterli değildir; sunucu doğrulaması nasıl yapılır?
- Migration, dry-run ve restore karantinası hangi veriyi korur?
- Tek süreç sınırı nedir; daha büyük kurulumda hangi bileşenler ayrılmalıdır?

Beş dakikalık demo için giriş → cihaz/mock kontrol → alarm → bakım/susturma →
grafik/CSV → viewer yetkileri akışı yeterlidir. Gerçekleşmemiş çalışmalar staj
defterinde yapılmış gibi gösterilmemelidir.
