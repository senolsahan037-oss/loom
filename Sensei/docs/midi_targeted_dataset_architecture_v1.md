# Sensei MIDI-Targeted Dataset Architecture v1

## 1. Karar

Sensei bir audio/sample motoru değildir. Girdisi MIDI bağlamı, çıktısı MIDI
olacaktır. Görevi, Ableton Live içindeki seçilmiş MIDI hedefini tanımak;
hedefe uygun Ableton kaynak MIDI'sini bulmak veya güvenli biçimde varyasyonunu
hazırlamak; sonucu resmi Extensions SDK üzerinden yalnızca kullanıcının seçtiği
Clip Slot'a yazmaktır.

Bu tasarımın ana ilkesi şudur:

> Bir MIDI üretimi önce hedef enstrümana uygunsa geçerlidir. Teknik olarak
> yazılabiliyor olması, müzikal olarak yazılabilir olduğu anlamına gelmez.

Örnek: Live'ın herhangi bir MIDI aracı bass kanalı üzerine chord yazabilir.
Sensei, bass hedef profili chord rolünü yasakladığı için bunu teklif etmez veya
yazmaz.

## 2. Kapsam ve kapsam dışı

### Kapsam

- MIDI clip, MIDI note, groove, rack/preset ve Live Browser metadata'sı
- Drum, bass, chord/harmony, lead/melody ve arp hedef rolleri
- Session View Clip Slot'a kullanıcı tarafından başlatılan SDK yazımı
- Ableton kaynaklarından gelen MIDI referanslarının güvenli varyasyonu

### Kapsam dışı

- Audio üretimi, sample üretimi, ses tasarımı ve audio clip yazımı
- Ham model çıktısından doğrudan fiziksel MIDI yazımı
- Track adına bakarak hedef rolü tahmin edip otomatik yazma
- Eski Remote Script / filesystem queue ile Live'a yazma

## 3. Korunacak kazanımlar

Yeni mimari aşağıdaki mevcut davranışları geriletmemelidir:

1. Drum Rack pad eşlemesi, yasak `performance_pad` / `unknown_pad` ayrımı,
   choke grupları ve drum-core güvenliği.
2. Ableton Live Browser'dan gelen genre bilgisinin `source_native` altında
   korunması; dosya adı veya klasörden genre uydurulmaması.
3. Parse-doğrulanmış groove kataloğu ve kaynak dosya bütünlüğü.
4. Kullanıcının bağlam menüsünde seçtiği Clip Slot dışına yazılmaması.
5. SDK tarafında finite sayılar, MIDI note/velocity sınırları, clip length ve
   transaction içinde yazım denetimi.
6. Kaynak metadata ile türetilen analiz bilgisinin birbirine karıştırılmaması.

## 4. Hedef mimari

```text
Ableton Library + Live Browser metadata
                 │
                 ▼
          Immutable MIDI Dataset
  (clip corpus, groove catalog, target profiles)
                 │
                 ▼
SDK selected Clip Slot + target device chain
                 │
                 ▼
          Target Resolver
  (drum / bass / chord / lead / arp / unknown)
                 │
                 ▼
          Capability Gate
  (allowed roles, register, polyphony, constraints)
                 │
                 ▼
      Reference selector + variation operation
                 │
                 ▼
         SDK MIDI write payload + provenance
                 │
                 ▼
     User-selected Ableton MIDI Clip Slot only
```

Dataset katmanı yalnızca okunur bir kaynak olarak görülür. Generator veya
variation katmanı katalog kayıtlarını değiştiremez; sadece yeni bir sonucu ve
hangi kayıtlardan yararlandığını üretir.

## 5. Dört temel sözleşme

### 5.1 `CanonicalMidiClip`

Her generator adayı için tek standart MIDI referans kaydıdır. Mevcut temiz
variation corpus'taki path/genre kaydı bu sözleşmeyle genişletilecektir.

```json
{
  "schema_version": "sensei.canonical-midi-clip.v1",
  "reference_id": "...",
  "source_native": {
    "ableton_file_path": "...",
    "ableton_genres": ["Ambient"],
    "ableton_tags": ["..."]
  },
  "integrity": {"content_sha256": "...", "parse_status": "verified"},
  "timeline": {
    "loop_start": 0.0,
    "loop_end": 4.0,
    "cycle_beats": 4.0,
    "time_signature": [4, 4],
    "tempo_bpm": null
  },
  "events": [{"pitch": 36, "time": 0.0, "duration": 0.25, "velocity": 110}],
  "capabilities": {"reference": true, "variation": true, "sdk_write": true}
}
```

`tempo_bpm`, key veya metre kaynakta doğrulanamıyorsa değer tahmin edilmez;
`null` kalır. Türetilmiş analizler ayrı `derived` alanına ve güven derecesine
yazılır.

### 5.2 `InstrumentTargetProfile`

Bu sözleşme Sensei'nin “bu kanala ne yazabilirim?” kararının tek otoritesidir.
Drum pad profili bu modelin `drum` uzmanlaşmasıdır.

```json
{
  "schema_version": "sensei.instrument-target-profile.v1",
  "profile_id": "ableton.bass.monophonic.v1",
  "target_role": "bass",
  "allowed_roles": ["bass", "sub_bass", "bass_riff"],
  "blocked_roles": ["drum", "chord", "pad", "lead"],
  "polyphony_limit": 1,
  "preferred_pitch_range": [28, 55],
  "write_policy": "safe",
  "evidence": {"kind": "verified_device_or_preset", "references": []}
}
```

Başlangıç profilleri:

| Hedef | İzinli ana roller | Temel sınır |
|---|---|---|
| Drum Rack | drum, percussion | doğrulanmış pad map ve choke |
| Bass | bass, sub_bass, bass_riff | varsayılan tek seslilik ve düşük register |
| Chord | chord, voicing, pad, arp_input | kontrollü çok seslilik |
| Lead | lead, melody, motif | tek seslilik tercihi, üst register |
| Arp | arp_pattern, arp_input | grid ve note sayısı sınırı |
| Unknown | hiçbiri | kullanıcı profili seçmeden yazma yok |

Track ismi kanıt değildir. Profil yalnız cihaz/rack/preset incelemesi veya
kullanıcının açık seçimiyle `safe` hale gelir. Belirsiz profil otomatik yazımı
engeller.

### 5.3 `VariationContract`

Bir aday ve hedef profilin birlikte kullanılabilirliğini açıklar.

```json
{
  "schema_version": "sensei.variation-contract.v1",
  "source_reference_id": "...",
  "target_profile_id": "ableton.bass.monophonic.v1",
  "requested_role": "bass",
  "allowed_operations": ["transpose", "octave_shift", "density", "timing", "velocity", "tile"],
  "constraints": {"max_polyphony": 1, "pitch_range": [28, 55]},
  "result_role": "bass"
}
```

Bu sözleşme olmadan kaynak clip hedefe uygulanamaz. Böylece chord kaynağı bass
profilinden, drum kaynağı lead profilinden geçemez.

### 5.4 `SdkMidiWritePayload`

SDK uzantısının tükettiği son ve dar sınırdır.

```json
{
  "schema_version": "sensei.sdk-midi-write.v1",
  "clip_length": 16,
  "notes": [{"pitch": 36, "time": 0, "duration": 0.25, "velocity": 110}],
  "provenance": {
    "target_profile_id": "...",
    "source_reference_id": "...",
    "variation_contract_id": "..."
  }
}
```

Uzantı `notes` ve `clip_length` dışında hiçbir alanı komut kabul etmez.
`provenance` denetim verisidir. Hedef slot uzantının çağrıldığı slot olduğu için
payload ile track veya slot seçilemez.

## 6. Dataset paketleri

### A. Native library index

Tüm Ableton kaynaklarının envanteri ve Live Browser metadata'sı. Bu katman
source-native veriyi saklar, müzikal rol iddia etmez.

### B. Verified MIDI corpus

Parse edilebilen `.alc` MIDI clip'leri `CanonicalMidiClip` olarak saklar.
Mevcut temiz corpus bunun giriş filtresidir: gerçek MIDI clip + Live Browser
kaydı + native genre gerektiğinde bu şartı sağlayan kaynaklar.

Genre zorunluluğu yalnız genre-filtreli retrieval için geçerlidir. Bass/chord/
lead varyasyonu için native genre etiketi olmayan fakat MIDI yapısı doğrulanmış
bir clip, "genre bilinmiyor" durumuyla ayrı bir corpus bölümünde tutulabilir.

### C. Verified groove catalog

Parse edilen `.agr` timing/velocity şablonları. Groove bir enstrüman rolü
değildir; `VariationContract` içine opsiyonel bir zaman/velocity dönüşümü
olarak eklenir.

### D. Instrument capability catalog

`InstrumentTargetProfile` kayıtlarının sürümlü koleksiyonudur. Cihaz/rack
incelemesiyle eşleşebilen kanıtlar tutulur. İlk sürümde konservatif varsayılan
profil aileleri bulunabilir, fakat `safe` yazım için cihaz veya kullanıcının
açık profil seçimi şarttır.

### E. Dataset release manifest

Tek bir release manifest şunları sabitler:

- corpus ve katalog dosyalarının SHA-256 değerleri
- giriş sayıları, reddedilme nedenleri ve schema sürümleri
- yaratılma zamanı ve Ableton Library/Live Browser kaynak sürümü
- uyumlu SDK payload schema sürümü

Generator sadece manifestte onaylı dataset release'lerini tüketir.

## 7. SDK ve Ableton MIDI generator ilişkisi

Extensions SDK, MIDI clip notalarına ve kullanıcının seçtiği Clip Slot'a
transaction içinde erişim/yazım için uygundur. Yerel SDK belgelerinde
Ableton'ın MIDI Generator veya MIDI Transformation cihazını uzantıdan doğrudan
çalıştıran özel bir API görülmemektedir.

Bu nedenle Sensei'nin kalıcı tasarımı bir Ableton aracını çağırmaya bağımlı
olmamalıdır. Sensei, aynı hedef kısıtlarıyla MIDI referansı ve varyasyon
hazırlar; SDK bunu yazabilir. İleride generator araçları için resmi bir API
eklenirse, bu araç `VariationContract` tüketen ikinci bir yürütücü olarak
eklenir; dataset veya güvenlik sözleşmesi değişmez.

## 8. İşlem akışları

### Bass Clip Slot

1. Kullanıcı bass instrument track'inde Clip Slot'a sağ tıklar.
2. SDK hedef slotu ve bağlı cihaz/rack bilgisini çözümlemek için Sensei'ye
   bağlam sağlar.
3. Target Resolver güvenli bir bass profili bulur; bulamazsa yazımı engeller
   ve profil seçimi ister.
4. Selector yalnız `bass` ile uyumlu, parse-doğrulanmış MIDI adaylarını arar.
5. Variation Contract tek-seslilik, register, uzunluk ve mevcut key/scale
   kısıtlarını uygular.
6. Sonuç `SdkMidiWritePayload` olur; SDK yalnız çağrılan Clip Slot'a yazar.

### Drum Clip Slot

Mevcut drum pad çözümleme ve choke korumaları korunur. Bu akışta target profile
doğrulanmış Drum Rack pad map'idir; yalnız drum-core roller fiziksel notaya
çevrilebilir.

### Chord veya Lead Clip Slot

Akış aynıdır, fakat profile göre max polyphony ve register değişir. Chord
profili bass rolünü varsayılan olarak kabul etmez; lead profili de geniş chord
stack kabul etmez.

## 9. Dataseti kilitleme kapıları

Dataset release'i "kilitli" sayılmadan önce tümü geçmelidir:

1. Her kayıt parse edilmiş, schema doğrulanmış ve content hash'e sahip.
2. Native bilgi ile `derived` bilgi ayrılmış.
3. Corpus kayıtları yalnızca manifestteki sürüm üzerinden okunuyor.
4. Her target profile için allowed/blocked role testleri var.
5. Drum regression testleri: pad map, choke, yasak pad ve SDK slot sınırı.
6. Bass regression testleri: chord yazmayı reddetme, monophony, register.
7. Chord/lead regression testleri: polyphony ve register sınırları.
8. SDK payload testleri: finite değer, sınırlar, clip length ve seçili slot.
9. Dataset üzerinde bir değişiklik manifest checksum'ını ve release sürümünü
   değiştirmeden kabul edilmiyor.

## 10. Uygulama sırası

1. Dataset release manifest ve `CanonicalMidiClip` exporter.
2. Instrument capability catalog şeması; önce drum ve bass profilleri.
3. Target Resolver: SDK bağlamı + profil eşlemesi, `unknown` için fail-closed.
4. Bass corpus filtreleri ve Bass Variation Contract testleri.
5. SDK uzantısına target-profile-aware menü/ön kontrol.
6. Chord, lead, arp profilleri ve corpus sınıflandırmaları.
7. Bu katmanların tümü kilitlendikten sonra arrangement veya üst seviye
   generator kararları.

## 11. Başarı ölçütü

- Bass Clip Slot çağrısı yalnız bass-uyumlu MIDI sonucu verebilir.
- Drum Clip Slot çağrısı mevcut pad güvenliğini kaybetmez.
- Chord ve lead hedefleri birbirinin MIDI rolünü varsayılan olarak alamaz.
- Dataset değişirse test/manifest denetimi bunu görünür kılar.
- SDK write işlemi dataset seçimi yapmaz; yalnız onaylı sonucu seçilen slot'a
  transaction içinde uygular.
