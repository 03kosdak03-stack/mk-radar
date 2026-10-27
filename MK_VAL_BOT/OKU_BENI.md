# MK VAL — sıfırdan Telegram botu

Bu paket bağımsız bot + Telegram mini uygulaması + haftalık ZIP veri yüklemesidir.
Eski bota dosya kopyalamak gerekmez. İlk veri tarihi: 23 Ekim 2026.
Değerleme motorunu değiştirmez ve finansal veri toplamaz. Yeni haftalık sonuçları
hesaplayan MK VAL motorunun çıktısını yayınlar. Hazır 1.4 verisi aynen taşınmıştır;
getiriler yeniden test edilmemiştir, geçmiş test doğrulanmış başarı değildir.

## Bir kez kurulum — bilgisayar kapalıyken de çalışsın

1. Telegram'da resmi @BotFather üzerinden /newbot ile bot oluştur.
   BotFather tokenını yalnızca sunucunun gizli ortam değişkenine gir; GitHub'a yükleme.
2. Bu klasörün içeriğini yeni bir GitHub deposunun köküne yükle.
   Mevcut mk-radar deposunu silmek/değiştirmek gerekmez.
3. Render hesabında New → Blueprint ile bu depoyu seç. render.yaml kurulumu tanımlar.
   Plan ücretlidir; sunucu ve kalıcı disk ücretini onaylamadan kurulum başlamaz.
4. TELEGRAM_BOT_TOKEN alanına tokenı; ADMIN_ID alanına kendi sayısal Telegram
   kullanıcı kimliğini gir (telefon numarası/kullanıcı adı değil).
   Kimliğini bilmiyorsan önce ADMIN_ID=1 ile başlat, bota /start yaz:
   bot senin sayısal kimliğini gösterir. Sonra ADMIN_ID'yi bu sayıyla değiştir.
5. Sunucu hazır olduğunda bota /start yaz, “MK VAL Uygulamasını Aç” düğmesine bas.
   Render HTTPS adresi otomatik kullanılır. Başka sunucuda PUBLIC_URL ayarla.

Bot ilk çalışırken menüyü otomatik kurar. Aynı tokenla yalnız bir bot süreci çalıştır.
Kalıcı diski kaldırma: haftalık sürümler ve Telegram işlem konumu orada tutulur.
Bu paket henüz canlı bir hesaba bağlanmadı; token olmadan canlı test yapılamaz.

## Her hafta — telefondan

Yeni tarama sonucunu içeren ZIP'i botla özel sohbetine dosya olarak gönder.
Bot tarih/biçim denetimi yapar, sonucu kaydeder ve başarı mesajı verir.
Uygulamayı kapatıp yeniden aç. GitHub'a her hafta girmen gerekmez.
ZIP içinde (alt klasörde de olabilir):
- final_snapshot.json: zorunlu, tam tarama çıktısı.
- final_tracking.json: isteğe bağlı haftalık karşılaştırma listesi geçmişi.
- final_backtest.json: isteğe bağlı geçmiş test çıktısı.

Eksik isteğe bağlı dosyalar yeni sürümde gösterilmez; eski raporla karıştırılmaz.
ZIP en fazla 15 MB, açılmış toplam boyut en fazla 100 MB olmalıdır.
Aynı isimli özdeş dosyalar kabul edilir; farklı içerikli kopyalar reddedilir.
Eski tarama tarihi reddedilir; aynı gün düzeltilmiş veri yüklenebilir.
Kod/HTML dosyaları ZIP güncellemesinden çalıştırılmaz. Yazılım değişikliği için
GitHub'daki kaynak kodunu güncellemek gerekir. Son 12 veri sürümü diskte saklanır.
Paket içindeki HAFTALIK_VERI_ORNEK.zip gerçek mevcut 23 Ekim çıktısıdır;
yeni haftalık veri değildir. Tam proje ZIP'i büyükse yalnız exports/web JSON'larını ziple.

## Ekran ve komutlar

/start, /mkfinal: uygulamayı aç; /secim: beşli liste; /deger KOD: ayrıntılar;
/varlik, /faaliyet: ilk 15; /takip: yüklenen haftalık listeler;
/mkbacktest: yüklenen geçmiş test; /durum: veri tarihi/kapsam; /guncelle: yükleme yardım.
Mini uygulama: listeler, arama, kalite uyarıları, sanal portföy.
Portföy Telegram CloudStorage'da, destek yoksa o cihazın tarayıcısında saklanır.
/takip kişisel portföy değil, dosyadaki geçmiş haftalık listelerin takibidir.
Fiyatlar anlık değildir; kâr/zarar son yüklenen fiyatla hesaplanır.
Model değerine erişildiği yalnız mevcut fiyatla karşılaştırılır; otomatik satış yoktur.

Bot komutları ve ZIP yükleme yalnız ADMIN_ID'ye açıktır. Web ekranı ve tarama verisi
HTTPS adresini bilenlere açıktır; kişisel portföy sunucuda herkese sunulmaz.

## Windows alternatifi

Python 3.12 kur ve BASLAT.bat dosyasını aç. Token/kimlik bir kez sorulur.
Botun çalışması için bilgisayar ve pencere açık kalmalıdır. Mini uygulama için
ayrıca erişilebilir HTTPS sunucusu gerekir; yerel komut botu HTTPS olmadan çalışır.
storage/local_config.json token içerir, paylaşma. Bulut kurulumu bu ihtiyacı kaldırır.

## Geliştirici doğrulaması

python -m unittest -v test_app.py
python -m py_compile app.py replies.py setup_local.py
Standart Python kütüphanesi kullanılır; pip bağımlılığı yoktur.
