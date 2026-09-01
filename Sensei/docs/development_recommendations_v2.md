# Sensei Drum Ürün Geliştirme Önerileri Raporu v2

Bu rapor, Sensei Drum projesinin son durumunu, basitleştirilmiş ve kararlı mimarisini, "Same-Kit Variation" (Aynı Kit İçinde Varyasyon) odağını ve gelecek dönem yol haritasını özetler.

---

# Completed
Aşağıdaki bileşenler ve özellikler tamamen tasarlanmış, entegre edilmiş ve test edilmiş kararlı duruma ulaşmıştır:
-   **Aynı Kit Odaklı Önizleme (Same-Kit Safe Preview)**: Davul kitinin kimliğini bozmadan, sadece seçilen kitteki pad normalizasyonlarına (`drum_core` grubu) göre güvenli nota tespiti ve önizleme oluşturulması.
-   **Choke Safety (apply_choke_groups)**: Open/Closed hi-hat'lerin ve diğer choke grubu ortaklarının birbiri üzerine çakışmasını engelleyen ve timing kesmelerini otomatik uygulayan motor.
-   **Basitleştirilmiş Route Katmanı**: İş mantığının `app.py` içinden tamamen temizlenip `preview_builder.py` içine taşınması ve CWD bağımsız dinamik fixture çözümlemesi.
-   **Groove Presets Entegrasyonu**: XML tabanlı `.agr` dosyalarından dinamik offset, velocity ve random timing humanization uygulanabilmesi.
-   **Device Chain Analizi**: Racks ve presetler içindeki Simpler/Sampler tespiti, macro kontrolleri, velocity/key zoneları ve choke gruplarının XML üzerinden otomatik inspect edilmesi.
-   **Veri Tabanı İndeksi Koruma**: `ableton_dataset_index.json` dosyasının versiyon takibinden çıkarılması ve `.gitignore` ile yerel olarak muhafaza edilmesi.

---

# Partially Implemented
Geliştirilmiş ancak detaylandırılması veya genişletilmesi gereken özellikler:
-   **Groove Arama ve Filtreleme**: Built-in ve taranmış groove presetleri dropdown olarak listelenmektedir ancak kategoriye veya swing yüzdesine göre sorgulama katmanı kısıtlıdır.
-   **Tempo Çözümleme ve Önceliklendirme**: Farklı kaynaklardan gelen BPM verileri taranmaktadır ancak güven derecelerine göre kanonik değerlerin otomatik çözümlenmesi hedeflenmiştir.
-   **Analysis Resolver Katmanı**: Tasarımı mimari olarak `pattern_assembly_design.md` içine eklenmiştir ancak üretim kodunda `core/analysis_schema.py` bazında entegrasyonu beklemektedir.

---

# Still Missing
Henüz hayata geçirilmemiş, geliştirilmesi gereken özellikler:
-   **MIDI İhracatı (MIDI Export v1)**: Uygulanan groove ve varyasyonları içeren MIDI klibini disk üzerine `.mid` formatında yazma yeteneği.
-   **Diagnostics UI Detayları**: UI üzerinde eksik, eşleşen veya yedek kullanılan notaları gösteren renk kodlu grafik durum paneli.
-   **Rastgele Timing İnsani Sınır Ayarı (Humanization Config)**: Random timing kaymalarının milisaniye sınırının UI veya konfigürasyondan dinamik ayarlanabilmesi.

---

# Wrong or Outdated Recommendations
Önceki raporda yer alan ancak projenin yeni ürün yönü doğrultusunda **geçersiz kabul edilen** ve **iptal edilen** öneriler:
-   *İptal*: **Audio BPM ve Sinyal Analizi**: MIDI/alc klipleri için ses sinyali tabanlı analiz önerisi kaldırılmıştır. Tempo çözümü için Ableton XML ve metadata katmanları yetkilidir. Audio analizi sadece ses döngüleri (audio loops) için opsiyoneldir.
-   *İptal*: **Yeni Drum Rack Kurulumu / Kit Değiştirme**: Sensei Drum ses kimliğini korumak adına pad'leri karıştırmayacak veya yeni cihaz zincirleri oluşturmayacaktır. Özel rack sentezleme önerisi tamamen kaldırılmıştır.
-   *İptal*: **Cross-Kit Assembly**: Farklı davul kitleri arasında geçişli nota montajı v1 için devre dışı bırakılmıştır. Ritimler sadece kitle tam uyumlu pad'ler üzerinde çalacaktır.

---

# Architectural Principles

Projenin uzun vadeli tasarım felsefesi aşağıdaki prensipler üzerine kuruludur:

-   **Preserve Identity, Change Behaviour (Kimliği Koru, Davranışı Değiştir)**: Sensei Drum, Ableton kitlerinin özgün ses karakterini ve sample zincirlerini asla değiştirmez. Ritim zenginliğini ve çeşitliliği sadece MIDI notalarının zamanlaması, hızı ve yapısı üzerinde oynayarak sağlar.
-   **Same-Kit First (Aynı Kit Önceliği)**: Tüm varyasyonlar, geçişler ve doldurmalar (fills) seçilen kitin kendi pad'leri içinde kalmak zorundadır.
-   **Single Source of Truth (Tek Doğru Kaynak)**: Bir klip veya kit ile ilgili her analiz alanının (örn: tempo, pad rolü) yalnızca bir tane kanonik değeri olabilir.
-   **Analysis Resolver (Analiz Çözümleyici)**: Farklı analiz motorlarının ürettiği tüm tahminler (`tempo`, `pad_role` vb.) resolver tarafından güven derecelerine göre puanlanarak tek bir karara bağlanır.
-   **Canonical Metadata (Kanonik Metadata)**: Generator ve preview katmanları ham analiz sonuçlarını doğrudan kullanamaz; yalnızca resolver tarafından onaylanmış kanonik alanları tüketir.
-   **XML First (Önce XML)**: Klipler ve kitlerin nota, loop ve tempo verilerinde dosyanın orijinal Ableton XML yapısı en yetkili referanstır.
-   **MIDI Before Audio (Önce MIDI)**: Sistem her zaman MIDI veri akışını önceler. Ses analizi ve audio özellikleri ikinci plandadır.
-   **Safe Generation (Güvenli Üretim)**: Kick, snare/clap ve hat pad'lerinin varlığı, pad semantic gruplarının doğruluğu ve choke gruplarının korunması güvence altına alınmadan kitte MIDI yazımı yapılmaz.
-   **Explainable Decisions (Açıklanabilir Kararlar)**: Üretim ve preview kararlarının neden alındığı (Safe/Unsafe sebepleri, re-map kararları) loglarda ve UI üzerinde açıkça doğrulanabilir olmalıdır.
-   **Thin UI / Fat Engine (İnce UI / Kalın Motor)**: Arayüz ve route katmanı ince, asenkron ve yalnızca sunum odaklı tutulur; tüm iş zekası izole edilmiş kütüphane motorları içindedir.

---

# Yol Haritası (Roadmap)

### Phase 1: Parser & Dataset (Tamamlandı)
-   `.alc` XML dekompresyonu ve nota okuma.
-   Kütüphane tarayıcısının kurulması ve index merge mantığı.

### Phase 2: Metadata & Safety (Tamamlandı)
-   Choke grupları, semantic pad resolver ve kit yazım güvenliği sınırlarının çizilmesi.
-   Arayüz entegrasyonu ve kararlı test seti.

### Phase 3: Decision Engine (Devam Ediyor)
-   `Analysis Resolver` entegrasyonu ile birden fazla tahmin kaynağından gelen tempo ve rol çelişkilerinin güven skoru matrisine göre çözümlenmesi.

### Phase 4: Pattern Assembly (Gelecek Aşama)
-   "Same-Kit Variation Strategy" çerçevesinde, kitin orijinal pad'lerine göre MIDI ritimlerinin varyasyon ve doldurma (fill) döngülerinin oluşturulması.

### Phase 5: Export Layer (Gelecek Aşama)
-   Ritimlerin standart MIDI (.mid) olarak dışa aktarılabilmesi ve nota re-map yapısı.

### Phase 6: Ableton Integration (Gelecek Aşama)
-   Max for Live websocket bağlantısı ile arayüzdeki MIDI verilerinin Ableton oturumuna doğrudan fırlatılması.

---

# Önerilen Next Patch: "Analysis Candidate Schema v1"

### Neden Bu Patch?
-   **Küçüktür**: `core/analysis_schema.py` altında yalnızca `AnalysisCandidate` ve `CanonicalResult` veri modellerini kuran basit bir altyapı yamasıdır.
-   **Yüksek Etkilidir**: Yol haritasındaki **Phase 3 (Decision Engine)** aşamasına geçişi doğrudan başlatır ve veri tabanı/generator katmanlarındaki çelişkili tahminleri çözümler.
-   **Düşük Risklidir / Test Edilebilirdir**: Mevcut preview veya scan akışlarını bozmaz; izole şekilde test edilebilir ve güven skoru doğrulamalarını kolaylaştırır.
-   **Mevcut Mimariyi Korur**: Sadece veri tiplerini ve çözümleme mantığını standartlaştırır.
