# Değişiklikler — 29.10.2026 (MK Final 1.5, yatırım gerekçesi + ana ortaklar, PC'den tek tuş yükleme)

30.10: hisse detayında "Şirketin durumu" (güçlü/zayıf taraflar, tasfiye değeri); `/deger` cevabında "Güçlü:" ve "Zayıf:" satırları.
1.5: gizli değer (Reysaş tipi) yeniden sıralamada; `/kural`, `/deger` metinleri ve seed verisi buna göre güncellendi; geçmiş test Nisan 598 kat.

29.10: Hisse detayına "Ana ortaklar" tablosu (KAP, sermaye ve oy payı, borsadaki ana ortağa bağlantı); "Sistemin geçmişi" bölümü kaldırıldı. `/deger KOD` cevabına ana ortaklar satırı.

Depoya olduğu gibi yüklenecek dosyalar (aynı adlarla, üzerine yazılır):

| Dosya | Ne değişti |
|---|---|
| `web/index.html` | Mini uygulama: hisse detayında "Yatırım gerekçesi" bölümü (tez, rakamlarla, fiyatı değere ne yaklaştırabilir, sistemin geçmişi, gerekçeyi bozan durumlar). Veriyi önceki gibi `/api/final` adresinden okur. |
| `app.py` | Yeni `POST /api/upload`: bilgisayardaki tek tuş güncellemesi haftalık ZIP'i buraya gönderir. `X-Upload-Key` başlığı Render'daki `UPLOAD_KEY` ile karşılaştırılır (sabit süreli, en az 32 karakter; yoksa adres kapalı). ZIP, Telegram yüklemesiyle aynı `import_zip` denetimlerinden geçer; hatada mevcut veri korunur. 5 hatalı denemede 1 saat kilit. Başarıda ADMIN_ID'ye Telegram mesajı. |
| `render.yaml` | `UPLOAD_KEY` gizli ortam değişkeni eklendi. |
| `setup_local.py` | Yerel kurulumda `UPLOAD_KEY` yoksa üretip gösterir. |
| `test_app.py` | Yükleme testleri: doğru anahtar, yanlış anahtar, bozuk ZIP (veri korunur), deneme sınırı, anahtar yok. |
| `replies.py` | `/deger KOD` cevabına kısa "Gerekçe"; `/takip` satırına karşılaştırma listeleri; eksi yüzdeler "−%88" biçiminde. |
| `seed/final_snapshot.json` | 23.10.2026 taraması + her hisseye `ctx` (fiyatın son 1 yılı, 52 hafta zirve/dip, 1 yıllık model farkı geçmişi, geçmiş seçimler) ve en üstte `pick_stats` (sistemin 2018-2026 geçmiş seçim istatistikleri). |
| `seed/final_tracking.json`, `seed/final_backtest.json` | Aynı tarama günü. |

`Dockerfile` değişmedi. Testler: `python -m unittest -v test_app.py` (14 test geçti).

Render'da bir kez: **Environment → `UPLOAD_KEY`** = bilgisayarda `bulut_yukle.py --anahtar` ile üretilen anahtar (bot tokenı değil).

Notlar:
- Seed yalnız kalıcı disk boşken (ilk kurulum) kullanılır. Bot zaten çalışıyorsa yeni veriyi göstermek için `seed/final_snapshot.json` dosyasını (veya bilgisayardaki `exports/web` içindeki üç JSON'u) zipleyip bota gönderin.
- `ctx` ve `pick_stats` alanları yoksa ekran ve bot eskisi gibi çalışır; gerekçe bölümü kısalır.
- Değerleme kuralları (MK Final 1.4) değişmedi.
