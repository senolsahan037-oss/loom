# Loom — çalışma kuralları (agent'lar için)

Loom, Şenol Şahan'ın **kendi üretim hattıdır**, ürün değildir. Ölçüt: gerçek
bir darboğazı kaldırıyor mu. Ürün yüzeyi (READMEs, kurulum rehberleri,
yabancıya yazılmış uyarılar) üretme.

Önce oku: `CODEX.md` (kalan belirsizlikler en başta), sonra `Docs/ARCHITECTURE.md`.

## Tek çalışma yolu

- **Bir MCP** (`mcp_server/server.py`, 45 araç), **bir Live bağlantısı**: Loom
  Extension (`extension/`, Live id `subverselab.loom`, protokol `loom.bridge/3`,
  sürüm `extension/manifest.json`; kurulu sürüm bridge state'te `surface_version`).
- Control Surface yok, ikinci writer yok, otomatik fallback yok. Bunları
  geri getirme; eksik yeteneği SDK'yı genişleterek veya OS yoluyla çöz.
- SDK preset yükleyemez (`insertDevice` yalnız native cihaz). Gerçek preset
  yolu: extension track'i açar → MCP `open -a "Ableton Live 12 Beta" <preset>`
  → Live seçili (yeni) track'e yükler → state'ten **hedef index+ad kilitli**
  doğrulanır. Yanlış yere inerse `PRESET_LANDED_ELSEWHERE`, build durur.
  Yüklenemezse `PRESET_LOAD_REQUIRED`. Testlerde (`LOOM_BRIDGE_ROOT`) kapalı.
- **Asla:** temsili cihaz koyma, plan kitini Simpler'la yeniden kurma, kiti
  "16 pad kuruldu" diye başarı sayma. `live_command build_drum_kit` yalnız
  açık istekle ve `preset_preserved=false` ile.

## Sabit sıra (`project_build`)

plan (ArrangementGPS) → kanal başına gerçek preset dosyası → tempo → track
(cihazsız) → preset yükleme → doğrulama → `drum_pads` (çözümlü notalar) →
rol→pad eşlemesi → MIDI (yalnız Live'ın bildirdiği padlere) → clip geri
okuma → **locator'lar en son** → state geri okuma. Bir adımın doğrulanmış
sonucu yoksa sonraki mutasyon yapılmaz. `INDETERMINATE` asla retry edilmez.

## Gerçekler ve sahipleri

- Her gerçeğin **tek okuyucusu** var; `tests/test_reader_contract.py` bunu
  pinler. `.als` gerçekleri → `AIMixMaster/aimixmaster/project_analyzer.py`;
  kit/pad gerçekleri → `Sensei/ableton/kit_resolver.py`.
- **`ReceivingNote` XML'de terstir: `nota = 128 − raw`** (Live 12.4'te 16/16
  pad ile ölçüldü). Tek decoder `kit_resolver.decode_receiving_note`. Pad
  kayıtlarında `raw_receiving_note` ve `decoded_receiving_note` ayrı alanlardır;
  ham değeri asla nota olarak kullanma. `SendingNote` ters değildir.
- SDK `className` DrumCell için `"Device"` döndürür; cihaz sınıfı SDK'dan
  kanıtlanamaz. Pad kimliği: Live'ın cihaz adı == dosyanın o padeki sample'ı.
- SDK set adını/yolunu vermez. `live_state.set` ve `live_bridge_status.open_set`
  iki OS kaynağından okur: Live'ın Log.txt'sindeki son gerçek "Loading document"
  (uygulama içi dosyalar elenir; yeni-set şablonu = kaydedilmemiş set) ve
  pencere başlığı (System Events, Accessibility izni). İkisi `agreement`
  ile karşılaştırılır; testlerde (`LOOM_BRIDGE_ROOT`) kapalı.
- Versiyonlar ayrı şeylerdir: paket sürümü, protokol (`loom.bridge/3`),
  kayıt şeması (`sensei.bridge.v2`), SDK API (`1.0.0`).

## Kanıt kuralları

- Fake Live'da geçen test, Live kabulü değildir. tsc/paket, presetin
  yüklendiğini kanıtlamaz. Nota sayısı pad hedefini kanıtlamaz. Ses,
  save/reopen, cihaz readback ayrı kanıtlardır.
- Gerçek Live'ın cevapları: `~/Library/Application Support/Ableton/Extensions
  Data/subverselab.loom/bridge/{done,errors,state/journal.jsonl}`; Live'ın
  log'u: `~/Library/Preferences/Ableton/Live <sürüm>/Log.txt` (`Repair`,
  `corrupt`, `Exception` ara; MixConsoleLive2/RemoteScript satırlarını ele).
- "Tamamlandı / gerçek kit yüklendi / hazır" deme; kanıtı yaz. Raporun
  başına kalan belirsizlikleri koy.
- Sırayı ve ölçümü varsayma: kodu, `.adg` verisini, journal'ı ve Live
  readback'ini karşılaştır.

## Sınırlar

Açık Live setine, kurulu extension'a, MCP istemci ayarlarına ve kullanıcı
dosyalarına/arşivlerine dokunma (yalnız oku). `install.py`, commit, push,
deployment çalıştırma; `git reset/clean`, stash kullanma. Ağ veya ücretli
model gerektiren adımı izinsiz başlatma. Kod silme yalnız kanıtla (import,
dynamic import, subprocess, CLI, CI, manifest, paketleme, dosya-yolu yüklemesi).
XML/preset motorlarını "SDK desteklemiyor" diye silme. Belirsiz kodu
gerekçesiyle bırak. Yeni çıktı yalnız `LOOM_OUTPUT_ROOT` / scratchpad'e.

## Testler (bu sırayla; PASSED / FAILED / SKIPPED ayrı raporla)

```
cd Sensei && python3 -m pytest tests/test_kit_resolver.py tests/test_kit_build_regressions.py -q
python3 tests/test_reader_contract.py
python3 mcp_server/tests/test_extension_path.py        # LOOM_TEST_DETAIL=4000 ile tam hata detayı
python3 mcp_server/tests/test_bridge_consumer_real.py
cd extension && npx --offline tsc --noEmit && npx --offline tsx tests/bridge.test.ts
python3 mcp_server/tests/test_mcp_protocol.py
python3 mcp_server/tests/test_mcp_tools.py
bash scripts/check_ci.sh
cd extension && npm run package                         # dist/loom.ablx; sürümü manifest/package/lock'ta birlikte artır
git diff --check
```
Testler `LOOM_OUTPUT_ROOT` ve `LOOM_BRIDGE_ROOT` ile izole çalışır; gerçek
Live'a ulaşmazlar. Koşmayan test başarı sayılmaz.

## Gerçek Live koşusu

Kullanıcı `.ablx`'i kurar ve Live'ı başlatır (set değişimi Extension Host'u
düşürür; "Restart" kullanıcıda). Boş set açıkken:

```
python3 scripts/build_live_project.py --plan-path <plan.json> --tracks Kit "Main Bass" Keys --fallback-genres Trap
```
Beklenen: her track `preset.status=OK` (`via=os_open`), Kit
`kit.status=native_preset_loaded`, `device_evidence.pads_whose_device_is_named_after_the_files_sample=16`,
`notes_outside_pads=[]`, locator'lar en son. Dosya yolu (`project_file_build`,
`Presetor/presetor/preset_transplant.py`) ayrı bir araçtır; kullanıcının
istediği akış canlı akıştır.

## Çalışma tarzı

Türkçe yaz, kısa ve ölçümle. Bahane ve yeni plan üretme; tutarsızlığı
bulduğun yerde düzelt. Onay gerektiren şey: sıra değişikliği, yeni bağlantı
yolu, kullanıcı alanına yazma. Bir işi "kullanıcı elle yapsın"a çevirme;
araç yapamıyorsa yapamadığını yapılandırılmış durumla söyle.
