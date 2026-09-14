# 0.6.0 veri saklama ve kontrollü temizlik

Bu sürümde otomatik temizlik yoktur. Süreler kurum tarafından henüz belirlenmedi;
aşağıdaki süre seçenekleri zorunlu kurum politikası değildir. CLI kesim zamanı
verilmeyen sınıftan hiçbir kayıt silmez. Tüm tarihler saat dilimli ISO-8601 olmalıdır;
kesim anına eşit kayıt kalır, yalnız daha eski kayıt adaydır. Gelecek kesim reddedilir.

| Veri sınıfı | Büyüme / süre seçimi | Silme davranışı ve istisnalar |
|---|---|---|
| Ölçüm | Her tamamlanan manuel/otomatik kontrolde artar. Kurumun belirleyeceği kesim: `--measurements-before`. Örnek olarak 90 gün düşünülebilir; varsayılan değildir. | Yalnız değerlendirilmiş ve koruma dışında kalan eski ölçümler. Her cihaz/mod/hedef sürümü/IP/is_current grubunun en yüksek ID'li son ölçümü, açık alarmın ilk yanıtsızlıktan itibaren kanıtları ve bekleyen yanıtsızlık serisinin kanıtları korunur. |
| Oturum / giriş öncesi oturum | Giriş ekranı ve başarılı girişlerde artar. `--sessions-before`, oluşturulma zamanına değil mutlak bitiş veya iptal zamanına uygulanır. | Kesimden önce mutlak süresi dolmuş veya iptal edilmiş kayıtlar silinebilir. Aktifler ve daha yakın zamanda bitenler korunur. Güncel idle ayarından geçmişe dönük süre tahmini yapılmaz. |
| Outbox — tüm durumlar | Alarm geçişleri, bakım/susturma ve uzlaştırmayla artar. Süre sınırsız; sonraki politikaya kadar korunur. | **Silme dışında.** pending/sending/retry ve uzlaştırılmamış suppressed işlem için gereklidir. accepted/mock_sent/failed/disabled/discarded ve uzlaştırılmış suppressed de silinmez. |
| Audit | Giriş, yönetim ve alarm geçişleriyle artar; otomatik kontrollerin her biri ek audit üretmez. | Bu pakette manuel veya otomatik audit silme etkin değildir. Kurumun erişim/arşiv/süre politikası beklenir. |
| Alarm, bakım, susturma | Olay/operatör işlemi başına artar. | FK ilişkileri, olay kimlikleri ve bakım uzlaştırması için korunur; bu komut silmez. |
| Cihaz, kullanıcı, izleme durumu, heartbeat, recovery_guard | Envanter/hesap sayısı veya güncel durumla sınırlı; bazı geçmiş hedef grupları ölçümde kalır. | Silme dışında. Sayaç, güncel durum, atıf, FK ve geri yükleme güvenliği korunur. |

**Tekilleştirme ömrü:** outbox'ın `UNIQUE(event_id, channel, target_key)` kaydı
kalıcı olay defteridir. Başarılı açılış bilgisi bakım sırasında çözülmenin özetini
gönderip göndermemeyi de belirler. Yalnız tamamlanan satırları silmek mükerrer
bildirime veya eksik uzlaştırmaya yol açabilir. Ayrı bir tombstone/arşiv ve
uzlaştırma tasarımı olmadan bu sınıf güvenli silinemez; bu yüzden bütün outbox
korunur. Sadece kısa süreli bir dedup TTL kullanılmaz.

Ölçüm FK'si cihaza `RESTRICT` yönündedir; ölçümü gösteren doğrudan alarm FK'si yoktur.
Bu, ölçümün iş açısından gereksiz olduğunu göstermez. Koruma sorguları alarm zamanı,
hedef/mod, değerlendirilme bayrağı, sayaç ve son ölçümü birlikte ele alır. Tarih
karşılaştırması SQLite `julianday` ile saniye/kesirli saniye gösterimlerini eşler;
çok yakın zamanlar SQLite çözünürlüğünde eşitse koruma yönünde kalır. Bilinmeyen
veya geçersiz tarihler silme adayı olmaz. Grup başına en yüksek ölçüm ID'sinin
korunması ID yeniden kullanımını da önler.

## Kullanım ve kesilme

```powershell
# Tarihler sadece örnek; kurumun onaylı kesimini seçin. Varsayılan dry-run.
.\.venv\Scripts\python.exe -m app.cli cleanup --database .\demo.db `
  --measurements-before "2026-06-01T00:00:00Z" --sessions-before "2026-09-01T00:00:00Z"
# İncelenen adaylara uygula; çağrı başına en fazla 20 x 500 silme.
.\.venv\Scripts\python.exe -m app.cli cleanup --database .\demo.db `
  --measurements-before "2026-06-01T00:00:00Z" --sessions-before "2026-09-01T00:00:00Z" `
  --apply --batch-size 500 --max-batches 20
```

Dry-run `mode=ro` ve `query_only` bağlantısıyla çalışır; audit dahil yazma yapmaz.
JSON çıktı her sınıfın kesimini, aday/korunan sayısını, ölçüm koruma nedenlerini,
silinen sayıları ve kalan adayları verir. Koruma nedeni sayıları örtüşebilir; bunlar
toplanarak farklı kayıt sayısı hesaplanmaz. Kesimden yeni ölçümler de korunur.

Apply kısa `BEGIN IMMEDIATE` transaction'ları kullanır. Her partide korumalar
yeniden değerlendirilir; canlı uygulama iki parti arasında yazabilir. İlk dry-run
anlık bir önizlemedir; sonraki apply aynı sayıda silme garantisi değildir. Parti
boyutu 1–5000, toplam parti sınırı 1–10000; sınır her iki veri sınıfının toplamıdır.
Ölçümler önce işlenir; sınır dolduysa oturumlar sonraki çağrıya kalabilir.

Kesilmede commit edilmiş partiler kalır, tamamlanmamış transaction geri alınır.
Aynı kesimle yeniden çalıştırmak kalan adaylardan devam eder; checkpoint dosyası
gerekmez. Otomatik zamanlayıcı eklenmemiştir. Gerçek veri üzerinde bu teslimde
temizlik çalıştırılmamıştır. Silme öncesi doğrulanmış yedek alın; silinen veri
yalnız yedekten dönebilir. `VACUUM` çalıştırılmaz; boş sayfalar SQLite içinde yeniden
kullanılır, dosya boyutu hemen küçülmeyebilir. Büyük audit/outbox, sayım süresi ve
kilit beklemeleri için kurum kapasite ölçümü hâlâ gerekir.

## Rapor ve CSV anlamı

Raporlar yalnız saklanan ölçümleri kapsar. `data_scope.basis=retained_measurements_only`,
`complete_period=false`; sıfır satırda RTT ve yanıt oranı `null` kalır. Yanıt oranı
saklanan reply/(reply+no_reply) ölçümlerinin oranıdır; dönem erişilebilirliği değildir.
Temizlik boşlukları doldurulmaz, kayıp ölçüm başarılı kontrol sayılmaz. Korunmuş eski
bir kanıtın varlığı aradaki dönemin tamamını kapsamaz.

CSV HTTP yanıtında `X-Data-Scope`, her satırında `data_scope` sütunu bulunur:
`retained_only_period_coverage_unknown`. Boş CSV başlık içerir. Bu ek sütun 0.6.0
format değişikliğidir; isimle sütun seçen içe aktarımlar kullanın. Panel de aynı
sınırlamayı gösterir. Gerçek tarayıcı indirmesi ve Excel içe aktarımı ayrı kabul bekler.
