# 0.6.0 ikinci kurumsallaşma paketi — 14 Eylül 2026

> Bu belge ilk yerel 0.6.0 paketinin tarihsel kabulünü korur. GitHub'a gönderim
> sonrasında bulunan hatalar, düzeltmeler, Chromium kabulü ve son commit'in CI
> kanıtlarına erişim [teslim incelemesinde](final-review.md) açıklanır.

Kapsam: tek kurum, tek süreç, SQLite; ağsız mock ve gerçek localhost HTTP.
[0.5.0 kabul raporu](acceptance-enterprise-0.5.0.md) 162 test ve o sürümün ZIP/HTTP
kanıtlarını tarihsel olarak korur; eski sonuçlar bu sürüme aktarılmış doğrulama
sayılmaz. [0.4.0 kabulü](acceptance.md) de korunur.

## Tamamlanan işler

| Alan | 0.6.0 sonucu |
|---|---|
| Bağımlılıklar | Runtime/dev doğrudan `.in` girdileri ayrı; tam transitif kapanış `==` lock dosyaları; mevcut sürümler korunur. Python/platform/güncelleme yöntemi `dependencies.md` içinde. |
| Temizlik | Açık DB dosyası; fiziksel salt okunur dry-run varsayılanı, açık `--apply`, saat dilimli kesim, sınırlı partiler, koruma/aday/kalan sayımları. Otomatik temizlik yok. |
| Korunan veri | Aktif oturumlar; işlenmemiş sonuçlar; açık alarm/seri kanıtları; her hedef/mod/generasyonun son ölçümü; bütün outbox, audit ve operasyon kayıtları. |
| Outbox | Başarılı/terminal satırlar da dedup ve bakım uzlaştırması için silme dışında; yeniden olay işleme ikinci bildirim üretmiyor. |
| Rapor/CSV | Yalnız saklanan ölçümler; dönem kapsamı bilinmiyor. Boş ölçümde oran/RTT null; CSV satırında yeni `data_scope` sütunu ve HTTP kapsam başlığı. |
| Backup/restore | Online Backup API, süre sınırı, exclusive hedef, yarım işlem ayrımı, integrity/FK ve SHA-256 manifesti. Ayrı restore hedefi, iptal edilen oturumlar, kalıcı mock/gönderimsiz karantina. |
| Ortak kontrol/CI | `scripts.verify local` ve son ZIP'ten iki yeni venv oluşturan `delivery`; hata exit code. Windows/Linux Python 3.12 GitHub Actions, resmî kaynaklardan SHA ile action'lar, yalnız contents:read. |

Yeni migration: **`20260914_0005`** (`recovery_guard`). Önceki dört migration'ın
hash'leri eski teslim manifestleriyle eşleşti. Sıra: kontrollü durdurma, doğrulanmış
eski DB yedeği, `alembic upgrade head`, `alembic check`, tek worker ile açılış.
Migration/temizlik/restore bu görevde yalnız geçici veya yeni sentetik DB'lerde
çalıştırıldı. Proje kökünde mevcut `.env` veya DB bulunmadığı dosya listesinde
görüldü; önceki build/demo DB'leri, `.venv.broken` ve ZIP'ler korundu. Commit, push,
yayınlama veya bilgisayar kontrolünü yeniden başlatma yapılmadı.

## Gerçekte çalıştırılan kaynak kontrolleri

İlk bütünleşik kaynak çalışması Windows x64 / CPython 3.12.12 mevcut geliştirme
venv'inde **185 başarılı test** verdi; 1 mevcut Starlette/AnyIO deprecation uyarısı
vardı. Sonra araç/ortam/ZIP güvenliği için 7 senaryo eklendi ve 7/7 geçti.
Parti içinde gereksiz tam sayım kaldırıldıktan sonra 23 yeni operasyon testi de
tekrar geçti. Nihai suite **192 senaryo** içerir; bütün suite'in son ZIP içindeki
temiz ortam sonucu ZIP'e bağlı acceptance JSON dosyasında kaydedilir.

Doğrulanan riskler: dry-run byte/hash değişmezliği, kesilme/tekrar çalışma ve parti
sınırı; açık alarm/seri/son ölçüm/aktif oturum; 9 outbox durumu ve mükerrer bildirim;
kesirli saniye kesimi; açık WAL yedeği ve hedef koruması; kilit timeout, bozuk
SQLite/FK/manifest/SHA, manifest yazma hatası; restore kopyasında SMTP yapılmaması;
v4→v5 eski satırların korunması ve Alembic model/şema uyumu. Geliştirmede Windows
fsync için yalnız okuma yerine `r+b` dosya tanıtıcısı kullanılarak uyumluluk hatası
giderildi; üstteki testler bu düzeltmeyi içerir.

İlk gerçek localhost HTTP kontrolü geçti: `build/runtime-development-check.json`.
Bu **geliştirme ortamı** kanıtında `runtime_dependencies_only=false` açıkça yazılıdır.
Migration, rastgele geçici admin/giriş, cihaz ekleme, mock alarm açılma/çözülme,
bildirim, bakım/susturma/iptal, sağlık, rapor/CSV yanıtı, canlı yedek ve restore
kopyasının HTTP açılışı kapsandı; parola/cookie/CSRF loglanmadı.

İlk küçük sentetik DB 139.264 bayt: 1 cihaz, 9 ölçüm, 1 alarm, 1 bakım, 1 susturma,
4 outbox, 1 kullanıcı, 2 oturum ve 27 audit. Servis içi kopya/denetim/SHA süresi
yedek için 0,016 sn, restore için 0,047 sn ölçüldü. Manifest yayımlanmasını da
kapsayan toplam süre nihai kanıttaki `backup_wall_seconds` / `restore_wall_seconds`
alanlarında ayrıca kaydedilir. Bu değerler kurumsal RTO/RPO, kapasite veya SLA değildir.

## Nihai kaynak ZIP ve temiz kurulum

Teslim: `dist/AvITData-0.6.0-source-20260914-02.zip`. İzin listesi `release-files.txt`;
arşiv içi `SOURCE-MANIFEST.json` ve dış `.verification.json`, CRC/dosya hash'leri/
ZIP SHA-256 kanıtını içerir. Gerçek ayar, DB/yedek/manifest/log/oturum/sırlar, venv
ve eski ZIP'ler dahil edilmez. Test sabitleri gerçek/demo hesap sırrı değildir;
HTTP hesabının parolası her çalıştırmada rastgele üretilir.

**Nihai doğrulama kaynağı** ZIP'in yanındaki
`AvITData-0.6.0-source-20260914-02.acceptance.json` dosyasıdır. `status=passed` ve
`archive_sha256` eşleşmesi o ZIP için temiz kurulumun tamamlandığını gösterir;
yalnız `.verification.json` temiz kurulum kanıtı değildir. Kaynak ZIP, kendisi
üretildikten sonra oluşacak kendi hash'ini/sonucunu içerdiğini iddia etmez.

`scripts.verify delivery`, ZIP'i `build/clean-060-final-02/source` içine çıkarır;
eski checkout dışında kurulu **CPython 3.12.10** ile iki yeni venv oluşturur.
İlkinde yalnız runtime lock kurulur ve pytest/Ruff/httpx/httpx2 bulunmadığı sınanır.
Gerçek localhost HTTP ve restore tamamlanmadan geliştirme ortamı oluşturulmaz.
İkinci venv'de 192 test, Ruff, pip check, lock check, sıfır DB migration, Alembic
check ve üç JS sözdizimi kontrolü ortak girişten çalışır. Python/SQLite/pip/OS,
test sayısı, süreler ve tablo sayıları JSON'da bulunur. Kaynak dosyaları kontrolden
sonra ZIP manifestiyle tekrar karşılaştırılır.

Geçici HTTP DB'leri ve yalnız doğrulamanın açtığı sunucular temizlenir; ayrı kaynak/
venv klasörleri ve kanıtlar inceleme için kalır. Hata düzeltmesi son ZIP'i değiştirirse
paket yeniden üretilip tekrar aynı teslim girişinden doğrulanır.

## CI ve kalan kabul

| Kontrol | Durum |
|---|---|
| Workflow hazırlandı | Evet: `.github/workflows/verify.yml`, Windows/Linux Python 3.12 |
| Yerelde çalıştırıldı | Windows; kaynak kanıtı ve son ZIP'e bağlı acceptance JSON kapsamı |
| GitHub üzerinde doğrulandı | **Hayır.** Push/uzak çalışma yapılmadı; Linux sonucu bekliyor. |
| Tarayıcı, grafik/filtre, bakım/susturma formu, klavye | **Açık.** Bilgisayar kontrolü yeniden başlatılmadı. |
| CSV düğmesiyle diske kayıt | **Açık.** HTTP CSV ayrıştırması yerine sayılmaz. |
| Excel içe aktarımı ve sayısal RTT | **Açık.** Excel kullanılmadı. |
| Şirket/lab ağı ve yeni sürüm loopback ICMP | **Çalıştırılmadı.** Yalnız mock + localhost HTTP. |
| Gerçek SMTP/alıcı teslimi | **Çalıştırılmadı.** Testlerde sahte stream/mock gönderici. |
| İşletim | Kurumun saklama/arşiv, yedek erişimi/şifreleme/off-host, gerçek veriyle kapasite ve RTO/RPO tatbikatı, servis/merkezi log/bağımsız watchdog açık. |
| Bağımlılık güvenliği | Tam sürüm lock var; wheel hash lock/offline depo, CVE ve özel secret scanner/SBOM sonraki iş. |

Tek süreç kilidi, kimlik doğrulama, CSRF ve alarm davranışı korunur. PostgreSQL,
çok müşteri, SSO ve SNMP kapsam dışıdır. Normal izleme başlangıçta duraklatılmış,
bildirimler varsayılan kapalıdır. Outbox ve audit büyümeye devam eder; kurumun
saklama/kapasite politikası tamamlanmış sayılmaz.
