# 0.6.0 işletim kılavuzu

Bu paket tek kurum / tek süreç / SQLite için yerel, mock senaryolarla doğrulanır.
Gerçek SMTP ve kurum ağı kabulü ayrı, açıkça yetkilendirilmiş çalışmalardır.
Başlangıçta otomatik izleme **duraklatılmıştır**. `MONITOR_MODE=mock`,
`NOTIFICATION_MODE=off` varsayılanları değişmedi.

## Bildirim akışı ve anlamı

Alarm açılışı/çözülmesi, ölçüm, sayaç, audit ve outbox aynı kısa SQLite yazma
transaction'ında kaydedilir. Rollback hepsini geri alır. Worker yalnız commit edilmiş
olayları okur; web isteği ve kontrol döngüsü SMTP sonucunu beklemez.
[Transactional outbox yaklaşımı](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html).

| Durum | İşletim anlamı |
|---|---|
| Kapalı modda kaydedildi (`disabled`) | Arşiv olayı; sonradan açılan bildirim ayarı bunu göndermez |
| Gönderim bekliyor / gönderiliyor | Kalıcı sırada / kısa claim commit'i yapılmış |
| Yeniden denenecek (`retry`) | Geçici hata veya belirsiz SMTP kabulü; sıradaki deneme zamanı görünür |
| SMTP kabul etti (`accepted`) | SMTP sunucusu DATA sonrasında 250 döndü; gelen kutusu teslimi bilinmiyor |
| MOCK kaydı tamamlandı (`mock_sent`) | Ağa bağlanmayan sağlayıcı işlendi; gerçek mesaj yok |
| Denemeler tükendi (`failed`) | Otomatik tekrar durdu; işletim incelemesi gerekli |
| Bastırıldı (`suppressed`) | Bakım/susturma veya güncel ölçüm bekleniyor; eski olay gönderilmez |
| Gönderilmeden kapatıldı (`discarded`) | Mod/hedef veya cihazın idari durumu değişti |

Her olay kimliği alarm geçişinden kararlı biçimde türetilir. DB'deki
`UNIQUE(event_id, channel, target_key)` aynı geçiş/kanal/hedefi çoğaltmaz. İlk sürüm
tek e-posta alıcı grubunu hedef kabul eder (en fazla 20 yalın ASCII adres); her RCPT
kabul edilmeden DATA gönderilmez. Grup değişirse eski kuyruk yeni gruba yönelmez.
Hedefin tek yönlü SHA-256 parmak izi saklanır; adresler, SMTP sunucusu/kullanıcı adı,
parola veya bağlantı dizgesi outbox'a yazılmaz. Parola yalnız çalışma yapılandırmasındadır.

Varsayılan eşzamanlılık 2, yoklama 2 sn, tüm gönderim süresi 10 sn, en çok 5 deneme;
başarısızlık sonrası beklemeler 30/60/120/240 sn (üst sınır 3600 sn). Aynı alarmın
sonraki olayı öncekinin retry/sending durumunu geçemez. Farklı alarmlar sınırlı
paralel işlenir. Terminal başarısızlık sırayı serbest bırakır; bu olayın teslimi
garanti edilmez. Sahte sağlayıcılarla bu davranışlar test edilir.

Süreç kilidini alan worker, yarım kalmış `sending` kayıtlarını artan beklemeyle
yeniden dener veya deneme sınırında `failed` yapar. Aynı süreçte sonuç yazımı
başarısızsa, o turun tüm görevleri bittikten sonra sonraki tur bu claim'i kurtarır.
Normal kapanış devam eden, timeout ile sınırlı gönderimleri ve DB işlerini bitirir;
bundan sonra süreç kilidi bırakılır. Servis durdurma süresi gönderim deadline'ı ve
DB kilit beklemesini karşılamalıdır (varsayılan için en az 30 sn önerilir).

**Tam olarak bir kez teslim garantisi yoktur.** SMTP kabulünden sonra durum commit'i
öncesinde çökme yaşanırsa aynı Message-ID ile kopya mesaj oluşabilir. Message-ID
korelasyon içindir; alıcının deduplikasyon yapacağını varsaymayın. Bounce, spam,
karantina ve gelen kutusu teslim teyidi bu pakette yoktur. Denemeleri tükenen olayları
SQL ile `pending` yapmayın; nedenini inceleyin ve güncel durumu panelden değerlendirin.
Kontrollü elle yeniden gönderim ekranı sonraki pakettedir.

## SMTP hazırlığı (bu oturumda gerçek gönderim yapılmadı)

`NOTIFICATION_MODE=smtp` yalnız kurumun izin verdiği SMTP hedefi/alıcılarıyla ayrı
kabulde açılır. `SMTP_TLS=starttls` + 587 veya `implicit` + sunucunun TLS portu
kullanılır; port ayarı operatöre aittir. Sistem CA deposuyla sertifika ve sunucu adı
doğrulanır. STARTTLS yoksa veya sertifika doğrulanmazsa bağlantı başarısız olur;
şifresiz bağlantıya düşmez. Kimlik doğrulama gerekiyorsa TLS üzerinde AUTH PLAIN
desteklenir. OAuth2, AUTH LOGIN ve SMTPUTF8 bu sürümde yoktur. İç CA gerekiyorsa
kurumun yönetilen güven deposu kullanılır; sertifika denetimi kapatılmaz.

`.env` veya servis secret kaynağının erişimini servis hesabıyla sınırlandırın.
Parolayı komut satırına, bakım gerekçesine, Git'e ya da destek kaydına koymayın.
Ayar değişikliği yeniden başlatma gerektirir. `off` ve `mock` geçmişi gerçek gönderime
yükseltilmez; SMTP ayarlansa bile **mock ölçümlerin olayları mock kalır**.

## Bakım ve susturma

Panel saatleri **Europe/Istanbul (UTC+03:00)**; DB saatleri UTC ISO-8601'dir.
API timezone içermeyen tarihleri reddeder. Bakım aralığı `[başlangıç, bitiş)`;
tam başlangıçta etkindir, tam bitişte değildir. Geçmiş başlangıç girilirse etkin
başlangıç oluşturma anına çekilir; geçmişteki gerçek gönderimler geri alınmaz.
Bitiş gelecekte, gerekçe boş olmayan en fazla 500 karakter olmalıdır.

Admin cihazı seçer, başlangıç/bitiş/gerekçe ile bakım oluşturur veya iptal eder.
Alarm detayını seçerek bitiş ve gerekçeyle süreli susturma tanımlar. Çakışan
pencereler ve susturmalar birlikte değerlendirilir: **son etkin bastırma kalkana
kadar** gönderim yapılmaz. İptal gerçek iptal saatinde sona erdirir; kayıt silinmez.
Viewer aynı durumları ve bildirim geçmişini okuyabilir. Backend admin ve mevcut
CSRF/Origin kontrollerini uygular; başarılı değişiklikler işlem kimliğiyle audit'e yazılır.

Ölçüm ve alarm geçmişi bakımda da devam eder. `open/resolved/closed`, görüldü,
bakım ve susturma ayrı boyutlardır. Gerekçe bakım/susturma kaydında, referansı
outbox'ta kalır. Gönderimden hemen önce bastırma yeniden kontrol edilir. Bu denetimden
sonra başlamış ve SMTP'ye ulaşmış bir gönderim geri çağrılamaz.

Bakım/susturma kalkınca:

- Açık alarm için bitişten sonra alınmış taze `no_reply` ölçümü gerekir; tek güncel
  durum bildirimi oluşur. Eski no_reply, teknik error veya sadece eski open kaydı yetmez.
- Açılışı veya önceki güncel durumu aynı hedefte kabul edilmiş alarm bakım sırasında
  çözülmüşse, taze ve teknik hata olmayan ölçümle tek çözülme özeti oluşur. Bu özet
  o alarmın kaydedilmiş çözümünü anlatır; başka alarmın güncel durumunu değiştirmez.
- Baştan sona bastırma içinde kalan, açılışı gönderilmemiş alarmın eski mesajları
  topluca gönderilmez. Bitiş sonrası normal bir çözülme olayı zaten sıradaysa ayrıca
  ikinci çözülme özeti üretilmez.
- Güncel veri yoksa uzlaştırma bekler; panel gecikmiş/ölçümsüz cihazları ve bekleyen
  uzlaştırmayı gösterir. Varsayılan duraklatma nedeniyle yeni ölçüm için admin'in
  manuel kontrolü veya izlemeyi başlatması gerekebilir. Hiçbiri cihazı otomatik
  olarak arızalı veya düzelmiş yapmaz.

Sunucu bütün bakım boyunca kapalıysa saklanan pencereler ve geçiş kayıtları açılışta
uzlaştırılır. Eski ölçümler tekrar probe sonucuna veya alarm geçişine çevrilmez.

## Sağlık, log ve servis izleme

`/health` mevcut `{status: ok}` ve DB `SELECT 1` davranışını korur. `/ready` yalnız
şema head'i ve bildirim görevinin çalışır olmasını sınar; 200/503 ile asgari durum
verir. **Readiness cihaz ölçümlerinin güncel olduğunu kanıtlamaz.** Yetkili
`/api/summary` ve panel son zamanlayıcı turunu, tamamlanan taramayı/kontrolü,
gecikmiş cihazları, teknik hataları, kuyruk/retry/başarısızlık ve worker durumunu verir.

İki kontrol aralığını aşan veya hiç bulunmayan ölçüm güncel değildir. Freshness hedef
sürümü, aktiflik, IP ve probe modu ile sınırlanır. `no_reply` cihazdan yanıt yok;
`error` kontrol mekanizması hata verdi; `stale` yeni ölçüm yok; `paused` izleme
duraklatıldı anlamındadır. Duraklama ve en son ölçüm sonucu birlikte gösterilir.
Ping programının kendi watchdog süresini aşması teknik `error` sayılır; ping'in
güvenilir biçimde bildirdiği hedef zaman aşımı `no_reply` olarak kalır.

`avit.operations` JSON logları `operation`, `event_id`, durum, cihaz/alarm ID ve
deneme sayısı gibi izinli alanlarla sınırlıdır. Ölçüm ID'si veya UUID işlem/olay
korelasyonu sağlar. Ham exception, SMTP sunucu yanıtı, parola, cookie, oturum ve
bağlantı sırları loglanmaz. Merkezi toplayıcıya bu akışı yönlendirme, döndürme ve
saklama süreleri kurum işletim paketinde tamamlanmalıdır.
[OWASP loglama rehberi](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html).

Uygulama tamamen durunca kendi uyarısını gönderemez. **Bağımsız servis izleme**
kurun: başka makine/servis `/health` ve `/ready` erişimini, süreç yaşamını ve yetkili
bir sağlık hesabıyla tarama/ölçüm yaşını sınamalıdır. İzleme duraklaması planlı
işletim kararı olarak ayrı ele alınmalıdır. Aynı makinedeki ikinci cron tek başına
makine kesintisini algılayamaz. Dış watchdog bildirimi ayrı sistemden gitmelidir.

Tek Uvicorn worker kullanın; servis çökme sonrası kontrollü restart, ayrı servis
hesabı, localhost arkasında HTTPS ve log rotasyonu pilot önkoşullarıdır. Bu sürüm
başlangıçta izlemeyi otomatik sürdürmez. Gözetimsiz yeniden başlatma politikası
[yol haritasındadır](enterprise-roadmap.md); worker sayısını artırarak aşılmaz.

## Yükseltme, yedekleme ve geri yükleme

0.6.0, `20260914_0005` ile yalnız `recovery_guard` tablosunu ekler. Önceki dört
migration değiştirilmedi. Sıra: uygulamayı kontrollü durdurun, yeni CLI ile mevcut
DB'nin tutarlı yedeğini alın; sonra `python -m alembic upgrade head`, `python -m
alembic check`, tek worker ile başlangıç ve yetkili yerel kabul. Bu teslimde gerçek
DB yükseltilmedi. Güncel yedekle tatbikat için yükseltmeden sonra ayrıca yeni yedek
alın; ön yükseltme yedeğini koruyun. 0.5.0'a geri dönüş eski kaynak + eski yedekle
yapılır; yeni yazmaları kaybetmemek için önce ayrıca yedekleyip uzlaştırın.
`downgrade` olağan geri dönüş yöntemi değildir; 0005 düşürmek karantina korumasını kaldırır.

```powershell
.\.venv\Scripts\python.exe -m app.cli backup --source .\network_monitor.db `
  --target .\backup-060.db --timeout 30
.\.venv\Scripts\python.exe -m app.cli restore-drill --source .\backup-060.db `
  --target .\restore-060.db --timeout 30
```

Kaynak dosya zaten var olmalıdır; yanlış yol yeni boş kaynak DB oluşturmaz.
Hedef/manifest mevcutsa üzerine yazılmaz. [SQLite Online Backup API](https://www.sqlite.org/backup.html)
128 sayfalık adımlarla tutarlı anlık kopya alır; WAL/journal'ı elle kopyalamaz.
Kilitte tekrarlar dahil deadline varsayılan 30 sn, en çok 3600 sn'dir; SQL bütünlük
denetimi ve SHA-256 aşaması da süreyi kontrol eder. Dosya sisteminin çekirdek I/O
takılmasına karşı sert süreç watchdog garantisi değildir. Hata çıktısı sır/ham
bağlantı bilgisi içermez; CLI sıfır olmayan çıkış kodu verir.

Geçici `.partial` dosya doğrulanıp flush edilir. `integrity_check=ok` ve boş
`foreign_key_check` sonucu gerekir. Sürüm, migration, UTC zaman, boyut, tablo
sayıları, süre ve SHA-256 `<hedef>.manifest.json` içine yazılır. Her iki dosya
üzerine yazmayan atomik hard-link ile yayımlanır; NTFS veya hard-link destekli yerel
Linux dosya sistemi gerekir. Desteklenmeyen dosya sisteminde başarısız olur;
güvensiz kopyaya düşmez. Normal hatada yalnız bu çağrının yarattığı dosyalar kaldırılır.
Süreç zorla öldürülürse `.partial` veya manifestsiz hedef kalabilir: **başarılı yedek
sayılmaz**, restore aracı kabul etmez. Mevcut bir hedefi ezerek tekrar denemeyin.

Backup sürümü eski şemayı da kaydeder; `restore-drill` yalnız güncel `0005` şemalı,
SHA-256 ve tamamlanma manifesti doğrulanan yedeği kabul eder. Tatbikat yeni dosyada
çalışır ve gerçek veritabanını değiştirmez. Cihaz, ölçüm, alarm, bakım, susturma,
outbox dahil tüm iş tabloları satır bazında karşılaştırılır. Bilinçli farklar:
`recovery_guard` işareti eklenir ve bütün eski oturumlar iptal edilir. Aynı kopya
HTTP sunucusuyla açılarak giriş, sağlık/rapor ve kuyruğun bekletilmesi doğrulanır.

**Kalıcı karantina:** `recovery_guard` işareti DB içindedir; dosyayı yeniden adlandırmak
veya terminalde ICMP/SMTP seçmek bunu kaldırmaz. Uygulama başlangıçta mock + off
ayarlarını uygular, izleme duraklar. Bildirim worker'ı recover/claim/reconcile/send
işlemez; eski pending/retry/sending durumları inceleme için olduğu gibi kalır.
Panel sağlık kartı ve `operations.recovery_hold` durumu gösterir. Karantinayı kaldıran
otomatik CLI/UI yolu bu pakette yoktur; üretim geri dönüşünün onaylı uzlaştırma
akışı ayrı işletim çalışmasıdır. Normal DB'de boş guard tablosu davranışı değiştirmez.

**Eski yedekten dönüşün kimlik etkisi:** yedekten sonraki hesap pasife almaları,
rol ve parola değişiklikleri kaybolabilir; eski parola hash'leri yeniden geçerli
olabilir. Tatbikat bütün oturumları iptal ederek eski cookie'lerin canlanmasını
engeller; kullanıcı hesabı/rol/parola değişikliklerini otomatik yeniden üretmez.
Üretime dönmeden önce hesap envanterini ve yetkileri güncel kaynakla uzlaştırın,
gereken parolaları sıfırlayın. Yedek dosyası parola hash'leri, kullanıcı ve oturum
bilgisi içerir; uygulama DB'si kadar kısıtlı dosya izinleri, şifreli depolama ve ayrı
off-host yedek politikası kurum sorumluluğudur. Gerçek yedekler Git/ZIP'e girmez.

**Bildirim etkisi:** yedek alındıktan sonra gerçekten gönderilen bir olay yedekte
hâlâ pending/sending/retry görünebilir. Karantina bu pakette gönderimi engeller;
canlıya dönüşten önce olay kimlikleri/SMTP kabul kayıtları uzlaştırılmalıdır.
Kuyruğu SQL ile topluca pending yapmayın. SMTP at-least-once sınırı devam eder.

Ölçülen küçük sentetik yedek/restore süreleri, Python/SQLite/OS ve satır sayıları
kabul kanıtında bulunur. Bunlar RTO/RPO/SLA değildir. Gerçek kurum verisiyle hacim,
eşzamanlı yazma, disk arızası, depolama erişimi ve off-host tatbikat açık kalır.

## Saklama ve otomatik doğrulama

[Saklama kılavuzu](retention.md): kesim verilmezse silme yok; varsayılan dry-run,
apply açık seçenek; sınırlı partiler, kesilmeden sonra tekrar çalıştırma. Aktif
oturum, açık alarm kanıtı, son ölçüm ve işlenmemiş sonuçlar korunur. Outbox kalıcı
tekilleştirme/uzlaştırma defteri olduğu için tamamlanmış kayıtları da silinmez.
Audit, bakım, alarm ve izleme durumu temizlenmez. Otomatik temizlik etkin değildir.
Rapor/CSV yalnız saklanan ölçümlere dayanır; kayıp dönemler erişilebilirlik sayılmaz.

`python -m scripts.verify local` ortak kalite girişidir. `scripts.verify delivery`
son ZIP'i iki yeni Python 3.12 ortamında doğrular; eski checkout'un Python paketlerine
ve dosyalarına ihtiyaç duymaz. `--python` olarak checkout dışındaki Python'u seçin.
Başarısız alt komut başarısız exit code döndürür. Çocuk süreç ortamı uygulama/SMTP/DB
değişkenlerini dışarıda tutar; dotenv okumaz. Geçici hesap sırları loglanmaz.
Yalnız kendi başlattığı süreç ve TemporaryDirectory verileri temizlenir; teslim
çalışma klasörü/kanıtlar bırakılır. `.github/workflows/verify.yml` Windows/Linux için
aynı komutu hazırlar. Uzak CI sonucu push/çalışma yapılmadan iddia edilmez.
