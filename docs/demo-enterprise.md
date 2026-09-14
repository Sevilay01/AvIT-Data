# Tamamen mock kısa demo — 0.5.0

README sonundaki PowerShell komutları yeni ve benzersiz demo DB oluşturur.
`python -m app.enterprise_demo --path YENI_DOSYA.db` sahte saat kullanır, uyumaz,
ping/SMTP çağırmaz. Mevcut dosya varsa üzerine yazmadan reddeder. Bakım içinde
alarm açar; bitişte eski ölçümü kullanmaz; yeni ölçümle bir `current_status` mock
kaydı üretir. İkinci bakımda alarm çözülür; bitişte bir `resolved_summary` mock
kaydı oluşur. JSON bu iki sonucu ve bastırılmış olayları gösterir.

Kullanılabilir varsayılan kullanıcı yoktur. `demo-author` pasiftir; parolası rastgele
oluşur ve açıklanmaz. CLI ile kendi admin/viewer hesabınızı oluşturun. Uygulamayı
bu DB'ye karşı `MONITOR_MODE=mock`, `MOCK_DEMO=false`, `NOTIFICATION_MODE=mock` ve
tek worker ile açın. Başlangıç yine duraklatılmıştır.

## Kullanıcının tamamlayacağı tarayıcı adımları

Bu oturumda bilgisayar/tarayıcı kontrolü yeniden başlatılmadı. Aşağıdakiler
otomatik API testlerinin yerine geçtiği iddia edilmeyen **manuel kabul** listesidir.

1. Admin ile girin. Genel Durum'da duraklama, son kontrol ve bildirim çalışanını;
   Bildirim geçmişinde bastırılmış olayları ve iki MOCK tamamlanmış kaydı görün.
2. `192.0.2.2` cihazında üç manuel kontrolle yeni alarm açın. Alarm detayını seçin.
   Görüldü yapın; bitişi ileri, gerekçeli susturma ekleyin. Açık/görüldü/susturuldu
   etiketlerinin birlikte kaldığını doğrulayın.
3. Aynı cihaza başlangıcı şimdi, bitişi ileri bakım ekleyin. Saatler Europe/Istanbul
   etiketli olmalıdır. Susturmayı iptal etseniz de bakım bitmeden gönderim olmamalı;
   ölçüm sayısı artmaya devam etmelidir.
4. Bakımı iptal edip yeni manuel kontrol yapın. Sonraki worker turunda tek güncel
   durum mock kaydı oluşmalıdır. Gerekçeyi, olay ID'sini ve audit'i görün.
5. Duraklatılmışken iki aralık bekleyin veya kısa mock aralığı kullanın. Eski veri
   arıza/iyileşme sayılmamalı. `192.0.2.3` teknik mock hata üretir; alarm çözmez.
6. Viewer bakım/susturma/bildirim geçmişini görebilmeli; oluşturma ve iptal formları
   bulunmamalı. Backend 403/CSRF testleri ayrıca otomatik çalıştırılır.
7. Açık kalan grafik/filtre, CSV'nin gerçekten diske inmesi ve Excel UTF-8/ayırıcı/
   ondalık virgül/boş RTT/UTC/sayısal sütun kontrollerini
   [eski kabul raporundaki adımlarla](acceptance.md) tamamlayın. Bu işler açık kalır.

Gerçek şirket/lab ağı, gerçek SMTP, SSO, PostgreSQL, çok müşteri ve SNMP bu demoda
kullanılmaz. Bu belge onların kabul kanıtı değildir.
