# Codex / Claude çalışma levhası — Loom

Son güncelleme: 2026-09-11. Bu dosya kanıtı olan güncel gerçekleri, açık
işleri ve çalışma yönünü tutar; kanıtsız biçimde "tamamlandı" demez.

## Başlangıç levhaları

### Önce nereye bakılır?

1. `CLAUDE.md` — değişmez çalışma kuralları, güvenlik sınırları ve test sırası.
2. `CODEX.md` — güncel durum, gerçek Live kanıtı, açık sorunlar ve sıradaki iş.
3. `Docs/ARCHITECTURE.md` — bileşen sınırları ve veri akışı.
4. `Docs/Sessions/` — kapanmış çalışmaların tarihli kanıt kayıtları.
5. `Docs/MISSING_CONTROLS_LOG.md` — SDK/Live tarafından gerçekten eksik kalan
   kontroller. Çözülen iş burada açık bırakılamaz.

`README.md` dışarıya kısa proje özeti verir; çalışma günlüğü veya agent hafızası
değildir. Aynı gerçek birden fazla yerde büyütülmez: kural `CLAUDE.md`ye, güncel
durum `CODEX.md`ye, kalıcı teknik açıklama `Docs/`a yazılır.

### Kanonik dosya ağacı

```text
Loom/
├── CLAUDE.md                 # agent kuralları; sık değişmez
├── CODEX.md                  # bugün ne doğru, ne eksik, kanıt nerede
├── README.md                 # kısa proje girişi
├── Docs/
│   ├── ARCHITECTURE.md       # kalıcı mimari
│   ├── MISSING_CONTROLS_LOG.md
│   └── Sessions/             # YYYY-MM-DD_<konu>.md kapanış kayıtları
├── extension/                # TEK Ableton Live bağlantısı ve .ablx paketi
│   ├── src/                  # SDK bridge ve extension giriş kodu
│   ├── tests/                # fake Live/headless bridge testleri
│   ├── tools/                # canlı ölçüm araçları
│   ├── vendor/               # sabitlenen Ableton SDK/CLI paketleri
│   └── dist/                 # üretilen paket; kaynak değil
├── mcp_server/               # TEK MCP sunucusu, şemalar ve extension istemcisi
│   ├── tests/                # protokol, 45 araç ve uçtan uca yol testleri
│   └── responses/            # çalışma çıktısı; kaynak değil
├── Sensei/                   # MIDI üretimi, kit/pad çözümü ve veri kanıtı
├── ArrangementGPS/           # yeni proje planı; mevcut sete katman ekleme aracı değil
├── AIMixMaster/              # .als/mix/otomasyon okuma-yazma motorları
├── Presetor/                 # cihaz zinciri kanıtı ve transplant
├── AISoundDesigner/          # ölçülmüş ses paleti
├── MusicalIntelligence/      # ölçüm ve küçük türetilmiş müzik profilleri
├── MixAnalyzer/              # ses/mix ölçümü
├── SampleAgent/              # sample analiz ve seçim motoru
├── Renderer/                 # render planı, paket ve doğrulama
├── scripts/                  # kullanıcıya dönük tek amaçlı orkestrasyon komutları
└── tests/                    # bileşenler arası reader/contract testleri
```

### Nereye ne konur?

- Live'a dokunan yeni özellik önce `extension/src/` içinde tek bridge op olur;
  karşılığı `extension/tests/` ve `mcp_server/tests/test_extension_path.py` ile
  kanıtlanır. İkinci bağlantı, Control Surface veya gizli fallback eklenmez.
- Yeni MCP aracı: şema `mcp_server/tool_schemas.py`, işleyici
  `mcp_server/server.py`, test aynı adla `mcp_server/tests/` içinde.
- Kit, preset, nota ve tonalite gerçeği `Sensei/` içinde tek okuyucuya aittir.
  MCP içinde ikinci parser yazılmaz.
- Var olan sete sınırlı müdahale `scripts/add_layers.py` / `scripts/add_fx.py`;
  sıfırdan proje kurma `project_build`. Bu iki akış birleştirilmez.
- Tek seferlik teşhis çıktıları kaynak klasörüne bırakılmaz. Yeniden üretilebilir
  sonuçlar ilgili `responses/`, `dist/`, `reports/`, `Builds/` veya `Sessions/`
  alanına gider ve Git'e alınmaz.
- Kalıcı bir karar yalnız kanıtı ve sahibi belli olduğunda `Docs/`a taşınır;
  geçici çalışma notu `CODEX.md`nin en üstündeki açık işler bölümünde kalır.

### Ağaç temizliği kuralları

- Kaynak değildir: `.DS_Store`, `__pycache__/`, `.pytest_cache/`, `node_modules/`,
  `.venv/`, `dist/`, `responses/`, geçici ölçüm ve rapor çıktıları.
- Bu klasörler silinse de kaynak kaybolmaz; ihtiyaçta yeniden üretilir. Ancak
  çalışan geliştirme ortamından topluca silinmez, yalnız açık temizlik göreviyle.
- `Sessions/MixCaptures/`, ölçülmüş kataloglar ve kullanıcıya ait veri Git'e
  sokulmaz; kanıt değeri varsa kullanıcı onayı olmadan silinmez.
- Kök dizine yeni geçici script bırakılmaz. Tekrar kullanılacak komut
  `scripts/`a, bileşene özel araç kendi `<bileşen>/tools/` klasörüne gider.
- Eski görünen kod import, subprocess, manifest, CI ve paketleme aramalarıyla
  sahipsiz olduğu kanıtlanmadan taşınmaz veya silinmez.

## 0. Kalan belirsizlikler (önce bunlar)

**Güncelleme 2026-09-11 (headless; gerçek Live'da koşmadı):**
- **`delete_track` / `delete_locator` extension op'ları eklendi** (7 Eylül'ün
  "sonraki iş"i: başıboş "10-Riser Basic" kanalı). Kapılar: ad tam bir track'e
  düşmeli (`track_not_found` / `ambiguous_track`), verilen `index` tutmalı
  (`index_mismatch`), **session ya da arrangement clip'i olan track hiçbir
  koşulda silinmez** (`track_has_clips`, override yok), cihazlı track yalnız
  `expected_devices` cihaz listesini birebir verince silinir
  (`track_has_devices` / `devices_differ`). Locator: beat'te cue yoksa
  `locator_not_found`, ad verilip tutmazsa `name_mismatch`. SDK reddederse
  `failed` (track/cue hâlâ orada, doğrulanmış), liste tutarsızsa
  `indeterminate`. Silme sonrası track/cue listesinden geri okunarak
  doğrulanır. Yetenekler state'te `track_delete` / `locator_delete`.
  MCP: `live_command` op'ları + `index`, `expected_devices` alanları.
- Kanıt: bridge.test.ts 139 (126+13), test_extension_path 121 (+9),
  check_ci 12/12, tsc temiz, `git diff --check` temiz. Paket
  `extension/dist/loom.ablx` **0.4.4** üretildi, **kurulmadı**; Live'da
  `Song.deleteTrack`'in gerçekten ne yaptığı (seçim, undo, son track) ölçülmedi.
- Set adı okuyucu filtresi (App-Resources / "Default MIDI Track") hâlâ açık.
- Ağaç hâlâ **commit'lenmedi** (7 Eylül + bugün).

**Güncelleme 2026-09-07 gece (Diplomat seti, kullanıcının kendi projesi):**
- **Var olan bir sete katman eklemek `project_build` işi DEĞİL.** project_build
  proje kurar (tempo, key denemesi, plan locator'ları, her bölüme clip);
  kullanıcının oturmuş setine çalıştırılınca fazladan cue'lar açtı, planı
  diskteki dosyadan türettiği için clip'ler 1 bar kaydı (kullanıcı kaydettikten
  sonra düzenlemeyi kaydırmıştı). Kullanıcı hepsini sildi. Doğru araçlar:
  `scripts/add_layers.py` (bölümler CANLI cue'lardan, gerçek preset, yalnız
  seçilen bölümlere MIDI, tempo/key/locator yok) ve `scripts/add_fx.py`
  (kural yerleşimli riser/hit — korpus kanıtı değil, öyle raporlanır; Sensei'de
  FX rolü yok). `scripts/plan_from_set.py` de var ama project_build'e girdi
  verdiği için aynı tuzağı taşır; setin içine katman için kullanma.
- **OS preset yükleme yalnız az önce açılan track'e çalışır.** Var olan boş
  "FX" track'ine denendi: Live yeni bir "10-Riser Basic" kanalı açtı
  (`PRESET_LANDED_ELSEWHERE` yakaladı, MIDI yazılmadı). Artık `just_created`
  olmayan hedef baştan reddediliyor. Başıboş kanalı kullanıcı elle siler;
  SDK'da `Song.deleteTrack` / `deleteCuePoint` VAR; 2026-09-11'de
  `delete_track` / `delete_locator` op'u olarak eklendi (yukarıda, headless).
- **Bass anahtarı sessizce uygulanmıyordu:** kanonik korpus satırlarında
  `key_root/key_mode` yok, klip ADI taşıyor ("Downer Bass 01 A Minor 165 bpm").
  Wobble clip'leri B minör değil kaynak tonda yazıldı (kullanıcı sildi).
  Şimdi `key_from_name` (Sensei loader, `key_source="name"`), sonuçta `key`
  bloğu (`applied/source/target/semitones`), tonu bilinmeyen kaynakla
  key-aware yazma `writable_to_live=false` (`key_unknown_for_source`).
- **Aynı adlı iki bölüm** ("ES" ×2) idempotency anahtarında çakışıyordu;
  tekrarlı ad `@start_bar` ekiyle ayrıldı, tekil adlar eski anahtarını korur.
- Kit okuyucu: sample'sız (synth) padler artık pad sayılıyor
  (`reference_state="no_sample"`; Halfstep Kit = 16 Drift pad); rolü
  bilinmeyen doğrulanmış kitte GM dizilimi *çıkarım* olarak raporlanıyor.
- Set adı okuyucu bu sette "Default MIDI Track" dedi (log'daki son yükleme
  bir track varsayılanıydı; App-Resources filtresi bunu kaçırdı — pencere
  başlığı "Diplomat" doğruydu). Filtre genişletilmeli (sonraki iş).
- Bu gecenin değişiklikleri (yukarıdakiler + `add_layers.py`, `add_fx.py`,
  `plan_from_set.py`) headless geçti (extension_path 113, Sensei 29, reader
  contract) ama **commit'lenmedi**.

Setin son hali (kullanıcı kaydedip kapattı): FX Riser (Riser Basic, 2 clip),
FX Drop (Dropping Vehicle, 4 vuruş) — yerleşim kuralla; kullanıcının kit/808/
FX kanalları dokunulmadı; "10-Riser Basic" başıboş kanalı silinmeli.

### Önceki maddeler


**Kullanıcı kabulü, 2026-09-07 ~04:50:** set kaydedildi, yeniden açıldı,
değişiklik yok ("ONAYLANDI … SÜREÇ BAŞARILI GÖZÜKÜYOR"). Bu, save/reopen
kanıtıdır ve kullanıcının gözlemidir; ölçüm değildir. Ses kulakla kabul
edildi sayılmadı — kullanıcı ayrıca söylemedi.


**Güncelleme 04:42 — extension 0.4.3, boş set, tam koşu (50/50 ok, 13+6 clip,
1540+420 nota, 7/7 locator).** `drum_pads` artık pad başına Live'daki cihaz
ADINI bildiriyor: Live bir Drum Sampler'ı sample'ının adıyla adlandırıyor.
**16/16 pad:** Live'ın cihaz adı == `.adg`'nin o *çözümlü* notaya yazdığı
sample (36 Kick BNYX 1 / raw 92 … 51 Hihat Open BNYX 2 / raw 77). Bu,
ReceivingNote çözümünün pad pad gerçek Live'la doğrulanmasıdır ve her padde
dosyanın sample'ının oturduğunun kanıtıdır.

Kalan:
- **Cihaz SINIFI SDK'dan okunamıyor**: `className` 16 zincirde de `"Device"`.
  "DrumCell" iddiası dosya tarafından (XML `source_devices`) ve Live'ın ad
  davranışından geliyor; SDK üzerinden sınıf kanıtı yok — yanıtta
  `device_class_verified: null` olarak duruyor.
- Bu koşunun MCP yanıtı sample-ad karşılaştırmasını henüz içermiyordu
  (kod koşudan sonra eklendi); 16/16 ölçümü Live'ın `done/` cevabından
  çevrimdışı yapıldı. Bir sonraki koşuda `device_evidence.pads_whose_device_is_named_after_the_files_sample=16` beklenir.
- Ses, save/reopen, cihaz parametre readback ölçülmedi. Keys (12.0.5 preset) dinlenmedi.
- OS yolu "seçili track" davranışına dayanır; index+ad kilidi bu koşuda da
  üç kez tuttu (Kit 1,7 s, Keys 1,2 s, Main Bass 1,3 s).

### Önceki maddeler (04:37 koşusu, 0.4.2)


**Güncelleme 04:37 — bu turun kodu gerçek Live'da koştu (extension 0.4.2).**
Kanıt aşağıda §2b. Kalanlar:

- **DrumCell kimliği Live tarafında hâlâ kanıtsız.** SDK her zincir cihazı
  için `className="Device"` döndürüyor (DrumCell'i özelleştirmiyor);
  `device_evidence.verified=false` çıktı, MIDI pad eşitliğiyle yazıldı.
  0.4.3 cihazın Live adını da bildiriyor ("Drum Sampler" beklenir); henüz
  kurulmadı, dolayısıyla henüz ölçülmedi.
- Ses, save/reopen, cihaz parametre readback ölçülmedi.
- OS yolu "seçili track" davranışına dayanır (belgesiz); bu koşuda index+ad
  kilidi ve tek-track-değişti doğrulaması üç kez tuttu (Kit, Keys, Main Bass).
- Keys (12.0.5 preset) yüklendi, dinlenmedi.

### Önceki (04:3x, bu koşudan önce yazılmış) maddeler


1. **Bu turun kodu gerçek Live'da henüz koşmadı.** Gerçek Live kanıtı olan
   son koşu 04:04'teki `project_build` (aşağıda journal'ı var); o koşu bu
   turdan ÖNCEKİ kodla yapıldı (hedef kilidi, zincir-cihaz kanıtı, ham/çözümlü
   ayrımı, `PRESET_LOAD_REQUIRED` o kodda yoktu). Yeni kapılar yalnız fake
   Live'da kanıtlı.
2. **Extension 0.4.2 kurulu değil.** `drum_pads` artık pad başına zincir
   cihaz sınıflarını (DrumCell mi) bildiriyor; kurulu olan 0.4.1 bildirmiyor.
   0.4.1 ile `device_evidence.reported_by_extension=false` döner; kit pad
   eşitliğiyle yazılır, "native DrumCell" iddiası kanıtsız kalır ve öyle
   raporlanır.
3. **OS yükleme yolu "seçili track"e dayanır.** Artık kanıtsız kabul
   edilmiyor: hedef index+ad ile kilitleniyor, yükleme sonrası yalnız o
   track'in tam o cihazı kazandığı doğrulanıyor; aksi `PRESET_LANDED_ELSEWHERE`
   ve build iptal. Ama Live'ın "seçili track" davranışının kendisi
   belgelenmiş bir API değil, iki ölçüme dayanıyor (1,7 s, iki kez).
4. **Ses, save/reopen, cihaz-içi parametre okuma** hiç ölçülmedi.
5. **Keys preseti (Electric Piano Daze) 12.0.5 yazımı**; Live 12.4 yükledi
   (state'te görünüyor), sesi dinlenmedi.
6. Journal yalnız mutasyonları tutar; okumalar (`get_state`, `drum_pads`)
   `done/` dosyalarında. Sıra kanıtı ikisinden birlikte çıkarılıyor.

## 0b. Açık setin adı (18:5x)

SDK'da set adı/yolu yok (Song: tracks/tempo/scale; Environment: dizinler).
`live_project.open_set()`: Live'ın Log.txt'sinde son gerçek "Loading document"
(App-Resources içindeki track varsayılanları elenir; `DefaultLiveSet.als`
şablonu = yeni kaydedilmemiş set) + pencere başlığı (System Events).
`live_state.set` ve `live_bridge_status.open_set` taşıyor; `agreement`
ikisinin aynı seti adlandırıp adlandırmadığını söyler. Ölçüm: Diplomat —
`~/Desktop/solo/Diplomat Project/Diplomat.als`, 18:43:28,
başlık "Diplomat", agreement true. Testler: test_live_project 12 (şablon →
set → track varsayılanları sırası; File > New; başlık uyuşmazlığı; log yok).
Sınır: pencere başlığı Accessibility izni ister; izin yoksa yalnız log.

## 0c. İki okuyucu düzeltmesi (Diplomat analizinde bulundu, 2026-09-07 21:xx)

- `project_inspect_arrangement` / `scripts/extract_arrangement_shapes.read_project`:
  (1) session clip'leri (`ClipSlot/Value`) zaman çizgisi olayı sayıyordu;
  (2) konumu `CurrentStart`'tan okuyordu — Live `Time` niteliğini okur;
  (3) audio track'lerin klipleri (`MainSequencer/Sample/…`) ayrı yoldadır.
  Şimdi yalnız `ArrangerAutomation/Events` altındaki klipler, `Time` ile;
  track başına midi/audio/nota sayıları; 16 bar boşluktan sonraki klipler
  `outlier_clips` (Diplomat: bar 188'deki 2 beat'lik unutulmuş clip; önce
  total_bars 188 diyordu, şimdi 60 + outlier); locator'lardan bölümler.
- `project_detect_genre`: yalnız track adına bakıyordu (Diplomat: kick/snare/hat
  0). Şimdi `role_evidence`: track adı + sample dosya adları (rol sözlüğü
  Sensei `kit_resolver.resolve_pad_role`) + Drum Rack track'lerinin MIDI
  perdeleri (GM haritası). Yanıt kaynak ve örnekleri taşıyor; hâlâ sezgisel,
  sınıflandırma değil (yanıtta öyle yazıyor).
- Reader contract'a 4 sentetik-.als kontrolü eklendi.

## 1. `ReceivingNote` hatasının kök nedeni

Live, `DrumBranch/BranchInfo/ReceivingNote` (set) ve
`DrumBranchPreset/ZoneSettings/ReceivingNote` (.adg) değerini **ters**
saklar: `decoded_note = 128 − raw`. Ölçüm (Live 12.4.15b1, BNYX Boot Kit.adg):
ham 77–92, Live'ın SDK üzerinden bildirdiği padler 36–51; 92→36 Kick,
89→39 Clap, 86→42 Closed Hat, 82→46 Open Hat, 77→51 (Open Hat 2 / ride
konumu). Üç okuyucu (`profile_exporter` dal okuyucu + BranchInfo okuyucu,
`alc_inspector.extract_drum_pads_loose`) ham değeri nota sanıyordu. Sonuç:
Core Library kitleri "GM değil" görünüyor, rol eşlemesi devreye giriyor, bu
gecenin Simpler kurulumları 77–92'ye (yanlış padlere) gidiyordu.

Tek sahip: `Sensei/ableton/kit_resolver.py::decode_receiving_note`. Üç
okuyucu onu çağırır. Alanlar ayrıldı: pad kayıtlarında
`raw_receiving_note` (XML'deki), `decoded_receiving_note` (= `note`,
MIDI), `note_source="preset_xml"`, `note_confidence="read"`;
`pad_mapping` → `targets[{gm_note, role, target_note}]`, `target_notes`;
`resolve_pad_notes` → `note_semantics`. Hiçbir tüketici artık ham değeri
nota olarak kullanmıyor (`grep ReceivingNote`: yalnız decode çağrıları).
Transplant manifesti çözümlü notaları taşıyor.

## 2. Gerçek çağrı sırası — journal + done/ kanıtı (04:04:51–04:05:18, extension 0.4.1)

```
04:04:51 get_state                       (bridge/state taze mi)
04:04:53 set_tempo                       ok
04:04:53 create_midi_track  Kit          ok  (cihazsız açıldı)
04:04:53 get_state  / 04:04:55 get_state (preset öncesi/sonrası snapshot)
04:04:56 drum_pads          Kit          pads=[36..51]   ← gerçek BNYX, çözümlü notalarla bire bir
04:04:56 create_midi_track  Keys         ok
04:04:57 get_state / 04:04:58 get_state  (Electric Piano Daze OS ile indi, doğrulandı)
04:04:58–04:05:04 write_arrangement_clip ×13 (Kit 6, Keys 7), her biri Live geri okumasıyla nota-nota
04:05:05–04:05:08 create_locator ×7     (en son)
04:05:09 set_tempo / create_midi_track Main Bass / clips ×6 / locators ×7 (ikinci genre grubu)
```
`build_drum_kit` bu koşuda hiç çağrılmadı. 48 istek, hepsi `ok`. Ses/save
ölçülmedi.

## 2b. Gerçek Live kanıtı — bu turun kodu, 04:36:58–04:37:41, extension 0.4.2, boş set

50 istek, 50 `ok`. `done/` dosyalarından (okumalar dahil):

```
04:37:12 get_state  → boş set (1-MIDI, 2-MIDI, 3-Audio, 4-Audio)
04:37:14 set_tempo 90
04:37:14 create_midi_track Kit         → index 1, cihazsız
04:37:15 get_state  (önce)  Kit []
         open -a Live "BNYX Boot Kit.adg"
04:37:16 get_state  (sonra) Kit ['BNYX Boot Kit'] — değişen tek track Kit, kazandığı tek cihaz preset (1,4 s)
04:37:17 drum_pads Kit → pads [36..51] = dosyanın çözümlü padleri; zincir cihaz sınıfı 16× "Device"
04:37:17 create_midi_track Keys → 04:37:18 Keys [] → 04:37:19 Keys ['Electric Piano Daze'] (1,3 s)
04:37:19–25 write_arrangement_clip ×13 (Kit 6, Keys 7) — her biri Live geri okumasıyla nota-nota
04:37:26–29 create_locator ×7 (en son)
04:37:30–39 Main Bass grubu: tempo, track, get_state önce/sonra ['Basic Analog Bass'] (1,1 s), clips ×6, locators ×7
04:37:41 get_state  → Kit / Keys / Main Bass, tempo 90, 7 locator
```

Kit clip'lerinin yazılan perdeleri (Live'a giden payload'lardan):
Intro {36,39,42} · Verse 1 {36,37,38,42,46} · Hook {36,38,42,46} · Verse 2
{36,37,42,46} · Bridge {36,37,39,42} · Final Hook {36,37,38,39,42,46} —
**hepsi 36–51 içinde, `outside_pads=[]`**, toplam 1822 nota; Main Bass 404
nota. `build_drum_kit` çağrılmadı; hiçbir track'e SDK cihazı eklenmedi
(`instrument: skipped`).

Kanıtlanan: sıra; preset'in hedef track'e indiği; padlerin 36–51 olduğu
(ReceivingNote çözümü Live'la bire bir); MIDI'nin yalnız o padlere yazıldığı;
yeniden kurma olmadığı. Kanıtlanmayan: pad cihazının DrumCell olduğu
(SDK sınıfı "Device"), ses, save/reopen.

## 3. Native preset/kit ile reconstruction farkı

- **Native (bu tur `project_build`'in tek yolu):** `.adg` dosyası OS ile
  Live'a verilir, Live rack'i kendisi kurar: DrumCell'ler, macro'lar
  (isim+değer), choke, return chain, zincir efektleri — dosyadaki neyse o.
  Kanıt: state'te rack adı, `drum_pads` padleri = dosyanın çözümlü padleri;
  0.4.2 ile zincir cihaz sınıfları (DrumCell) pad başına.
- **Reconstruction (`live_command build_drum_kit`, yalnız açık istekle):**
  SDK ile Drum Rack + pad başına Simpler + tek sample. DrumCell yok, macro
  yok, choke yok, envelope/filtre uygulanmaz. `preset_preserved=false` ve
  `fidelity_summary` bu yanıtta kalır. **project_build bunu artık hiç
  yapmaz;** `allow_lossy_kit` verilirse "ignored" diye raporlar. Yüklenemeyen
  kit → track boş kalır, yazmalar `PRESET_LOAD_REQUIRED` (kod alanıyla).
- Bu gecenin önceki Simpler kurulumları (pad 77–92) geçerli sonuç değildir:
  yanlış padlere, yanlış cihazla.

## 4. Sıra ve kapılar (`project_build`, canlı yol)

plan → kanal başına gerçek preset dosyası (`_preset_file_for`: drum → kit
katalogu, diğerleri → identity katalogu; `device_map` açık override) →
tempo → track (cihazsız) → preset yükleme (manifest ya da OS; hedef
index+ad kilitli; `already_present` / `OK` / `PRESET_LOAD_REQUIRED` /
`PRESET_LANDED_ELSEWHERE`) → doğrulama yoksa o track'e yazma yok, yanlış
yere indiyse build iptal → `drum_pads` (çözümlü padler; dosyayla eşit
değilse `INDETERMINATE`) → rol→pad eşlemesi (`targets`) → clip yazımı
(yazılan her nota Live'ın bildirdiği bir pad olmalı, değilse
`notes_outside_pads` ve yazma yok) → clip geri okuma → locator'lar en son →
state geri okuma. `step_order` yanıtta.

## 5. Değişen dosyalar (bu tur)

Sensei/ableton/kit_resolver.py, Sensei/ableton/inspector/profile_exporter.py,
Sensei/ableton/inspector/alc_inspector.py, Presetor/presetor/preset_transplant.py,
mcp_server/server.py, mcp_server/bridge_client.py (durum sözlüğü),
mcp_server/tool_schemas.py, mcp_server/tests/fake_extension_bridge.py,
mcp_server/tests/test_extension_path.py, mcp_server/tests/test_mcp_tools.py,
tests/test_reader_contract.py, Sensei/tests/test_kit_resolver.py,
extension/src/bridge.ts, extension/src/extension.ts, extension/manifest.json,
extension/package.json, extension/package-lock.json, extension/dist/loom.ablx (0.4.3 kurulu ve ölçüldü),
scripts/build_live_project.py, scripts/build_project_file.py, scripts/kit_transplant.py.

## 6. Test sonuçları

PASSED (headless, izole; gerçek Live yok):
- tests/test_reader_contract.py — geçti (+ decode pinleri: 92→36, 77→51, 89→39; 200 reddedilir)
- Sensei/tests/test_kit_resolver.py + test_kit_build_regressions.py — 10 passed
  (fixture pinleri 80/79/82 → 48/49/46: test ham XML katmanını değil Live pad
  notasını doğruluyordu, ona göre düzeltildi)
- mcp_server/tests/test_extension_path.py — **112** (yeni: BNYX 16 pad ham↔çözümlü;
  roller 36/39/42/46; yüklenemeyen kit → PRESET_LOAD_REQUIRED, boş track,
  build_drum_kit yok, allow_lossy_kit ignored; sıra tempo→track→clip→locator;
  simüle yükleyici doğru track → native_preset_loaded + device_evidence
  verified + target_notes ⊆ padler + notes_outside_pads=[]; yanlış track →
  PRESET_LANDED_ELSEWHERE, hiç clip yok; manifest yolu)
- mcp_server/tests/test_mcp_tools.py — 98 (stdio dry-run: preset yükleme
  yolu ve çözümlü/ham padler ayrı, rebuild yok)
- mcp_server/tests/test_mcp_protocol.py — CI içinde geçti
- extension: tsc temiz, bridge.test.ts 126 ok
- scripts/check_ci.sh — 12 passed, 0 failed, 0 skipped
- git diff --check temiz

SKIPPED: test_mcp_tools içindeki otomasyon kontrolleri (fixture yok).
FAILED: yok.

## 7. Headless kanıt ≠ gerçek Live kanıtı

- Fake Live'da geçen 112 kontrol, kapıların mantığını kanıtlar; Live'ın
  preseti nereye koyduğunu kanıtlamaz.
- tsc/paket, presetin Live'da yüklendiğini kanıtlamaz.
- 1718 nota sayısı, pad hedefinin doğruluğunu kanıtlamaz; onu `drum_pads`
  = çözümlü padler eşitliği + `notes_outside_pads=[]` kanıtlar (ikincisi
  yalnız yeni kodda; gerçek Live'da henüz koşmadı).
- "16 pad" native kiti kanıtlamaz; zincir cihaz sınıfı (0.4.2) kanıtlar —
  gerçek Live'da henüz okunmadı.
- Ses, save/reopen, cihaz readback ayrı kanıtlardır; hiçbiri yapılmadı.

## 8. Sıradaki gerçek-Live ölçümü (kullanıcı kurar/başlatır)

0.4.2 kur → Live yeniden başlat → boş set → `scripts/build_live_project.py
--plan-path … --tracks Kit "Main Bass" Keys --fallback-genres Trap`.
Beklenen yanıt: her track `preset.status=OK` (`via=os_open`, `index`
eşleşmesi), Kit `kit.status=native_preset_loaded`, `device_evidence.verified=true`
(DrumCell ×16), `target_notes ⊆ [36..51]`, `notes_outside_pads=[]`,
locator'lar en son. Sonra ses ve ⌘S/yeniden açma.

## Çalışma sınırı

Açık Live setine, kurulu extension'a, kullanıcı projelerine, istemci
ayarlarına dokunulmadı. Commit/push/deployment/install.py yok. Control
Surface veya ikinci bağlantı eklenmedi. XML/preset motorları silinmedi.
