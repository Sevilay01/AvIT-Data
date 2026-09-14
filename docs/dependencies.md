# 0.6.0 tekrarlanabilir bağımlılıklar

Hedef: CPython 3.12.x, Windows x64 ve Linux x86_64 (glibc; GitHub Ubuntu runner).
Bu teslimde fiilî işletim sistemi doğrulaması Windows'tadır; Linux workflow'u
hazırlanmış olup uzakta çalıştırıldığı iddia edilmez. macOS, ARM, Alpine/musl ve
başka Python minor sürümleri bu paketin doğrulanmış hedefi değildir. `pyproject.toml`
Python aralığını `>=3.12,<3.13` olarak sınırlar. Node.js 24 yalnız JS sözdizimi/CI
içindir; uygulama çalışırken Node veya geliştirme araçları gerekmez.

`requirements.in` doğrudan çalışma, `requirements-dev.in` yalnız doğrudan geliştirme
girdisidir. `requirements.txt` çalışma kapanışının tamamını; `requirements-dev.txt`
çalışma + geliştirme kapanışını tam `==` sürümleriyle kaydeder. Sürümler mevcut
0.5.0 geliştirme ortamından çıkarılmıştır; toplu yükseltme yapılmadı. Windows/Linux
işaretçileri ve Argon2 gibi extras geçişleri çözümlenir. İki hedefin birleşimindeki
paketler sabitlenir (ör. geliştirme lock'unda colorama Linux'ta da kurulabilir).

`python -m scripts.lock_dependencies --check`, kurulu paket metadata'sından iki
girdi dosyasının kapanışını yeniden hesaplayıp lock dosyalarıyla karşılaştırır.
Çalışma ortamında pytest/Ruff/httpx/httpx2 bulunmadığı ayrıca HTTP doğrulamasında
denetlenir. `httpx2` mevcut Starlette test istemcisi için geliştirme bağımlılığıdır;
çalışma lock'una eklenmez. HTTP kabul istemcisi Python standart kütüphanesini kullanır.

[pip tekrarlanabilir kurulum rehberindeki](https://pip.pypa.io/en/stable/topics/repeatable-installs/)
tam sürüm sabitleme yaklaşımı kullanılır. Bu lock **wheel içerik hash lock'u değildir**:
aynı sürümleri kaydeder, paket indeksinin gelecekte kullanılabilirliğini veya
indeks içeriğinin değişmezliğini garanti etmez. Kaynak ZIP'in ayrıca dosya SHA-256
manifesti vardır; bu bağımlılık wheel hash'i yerine geçmez. Çevrimdışı wheel deposu,
hash zorunlu kurulum, CVE taraması ve SBOM zenginleştirmesi sonraki işletim işidir.

## Kontrollü güncelleme

1. Yeni ayrı Python 3.12 geliştirme venv'ine mevcut `requirements-dev.txt` kurun.
2. İlgili upstream sürüm notlarını inceleyin; yalnız gereken `.in` girdisini değiştirin.
3. Bu ayrı ortamda seçili paketi ve gerçekten gereken transitif değişikliklerini
   kurun. Kör `pip install --upgrade -r ...` ile bütün ağacı yükseltmeyin.
4. `python -m scripts.lock_dependencies` çalıştırın; iki lock diff'ini, değişen
   transitif sürümleri ve lisans/güvenlik durumunu inceleyin.
5. `python -m scripts.verify local --evidence build/yeni-kalite.json` ve yeni ZIP
   üzerinde `scripts.verify delivery` çalıştırın. Windows ve Linux CI sonuçlarını
   ayrı kaydedin; HTTP çalışma ortamına test bağımlılıklarını kurmayın.

Doğrulama alt süreçleri sınırlı OS ortam listesiyle açılır; uygulama/SMTP/DB/Python
enjeksiyon değişkenleri taşınmaz. `AVIT_NO_ENV_FILE=1` dotenv okumayı kapatır,
`pip --isolated` + boş `PIP_CONFIG_FILE` kullanıcı pip ayarlarını dışarıda tutar.
Kurulum `--only-binary=:all:` ile hazır wheel ister; wheel yoksa sessizce kaynak
derlemeye düşmez. pip sürümü temel CPython dağıtımına aittir ve kanıtta ortamla
birlikte kaydedilir; uygulama lock'una dahil değildir.

## CI kaynakları

Workflow: `.github/workflows/verify.yml`, Windows/Linux ve Python 3.12. Yalnız
`contents: read`; checkout kimlik bilgisi kalıcı bırakılmaz. Sır, dış bildirim
servisi veya şirket ağı gerekmez. Bağımlılık/action indirmeleri internet ister.
Kaynak ZIP oluşturulur; ayrı runtime ve development venv'leri aynı `delivery`
giriş noktasından sınanır. Testler boş şema ve v1/v2/v3/**v4** veri korumalı
yükseltmeyi, Ruff, pip check, lock check, Alembic model karşılaştırması ve mevcut
JS dosyalarının Node sözdizimi kontrollerini kapsar.

14 Eylül 2026'da resmî release/commit sayfalarından doğrulanan ve tam SHA ile
sabitlenen action'lar:

- [checkout v6.1.0](https://github.com/actions/checkout/releases/tag/v6.1.0):
  `d23441a48e516b6c34aea4fa41551a30e30af803`.
- [setup-python v6.3.0](https://github.com/actions/setup-python/releases/tag/v6.3.0):
  `ece7cb06caefa5fff74198d8649806c4678c61a1`.
- [setup-node v6.3.0](https://github.com/actions/setup-node/releases/tag/v6.3.0):
  `53b83947a5a98c8d113130e565377fae1a50d02f`.

[GitHub Python CI rehberi](https://docs.github.com/en/actions/tutorials/build-and-test-code/python).
Workflow hazırlandı; yerel Windows sonucu kabul raporunda; push yapılmadığı için
GitHub üzerinde doğrulanmış sayılmaz.
