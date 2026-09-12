# Loom

**Loom** is a measurement-based production system for Ableton Live by
[Şenol Şahan / SubverseLab](https://subverselab.com/loom): a local MCP server
that reads your own `.als` projects and library, answers with counts instead of
guesses, and writes MIDI, device chains, automation and arrangement markers into
a running Live session, verifying every write by reading it back.
<!-- mcp-name: io.github.senolsahan037-oss/loom -->
<!-- The MCP Registry proves package ownership by finding this line in the
     README that PyPI shows as the package description. It must match `name` in
     server.json exactly; the namespace is the reverse-DNS form of the domain,
     authenticated through the GitHub account. com.subverselab/loom is the
     on-brand name and stays open: it needs a TXT record on the apex of
     subverselab.com, whose DNS lives at the registrar. -->

Canonical home: **https://subverselab.com/loom** · Cite: [`CITATION.cff`](CITATION.cff) ·
Attribution terms: [`NOTICE`](NOTICE). Copies and derivatives must keep the attribution.

[![checks](https://github.com/senolsahan037-oss/loom/actions/workflows/checks.yml/badge.svg)](https://github.com/senolsahan037-oss/loom/actions/workflows/checks.yml)

Ableton Live için ölçüme dayalı bir üretim sistemi ve onu tek bir araç ad
alanı altında toplayan MCP sunucusu. Live bağlantısı **tek**: Live 12.4 beta
içinde çalışan Loom extension. Control Surface, otomatik fallback ya da ikinci
bir yazıcı yolu yok.

## Loom ne yapar

| Katman | İş |
|---|---|
| **Sensei** | Kilitli veri kümesinden MIDI varyasyonu (drum / bass / chord) |
| **ArrangementGPS** | Prompt'tan proje planı: tempo, ton, tür, kanallar, bölümler (Node) |
| **AIMixMaster** | `.als` inceleme, gain staging, klip hizalama, drum buss, otomasyon yazma (dosya üzerinde) |
| **Presetor / AISoundDesigner** | Kullanıcının kendi projelerinden ölçülmüş cihaz zincirleri ve ses paleti |
| **MusicalIntelligence** | Gerçek kayıtlardan ölçülmüş tür kanıtı; proje anahtarına göre part önerisi |
| **Mix Check / SampleAgent** | Ses ölçümü ve profil karşılaştırması; YouTube → dilimlenmiş sample paketi |
| **Loom extension** | Live'ın içinde çalışan Ableton Extension: MCP'nin Live'a tek bağlantısı |
| **mcp_server** | 45 araç, resources, prompts, ilerleme, iptal |

Hiçbir katman tahmin üretmez: kanıt yoksa öneri dönmez, SDK'nın yapamadığı iş
emüle edilmez, yazan her araç varsayılan olarak kuru çalışır.

Mimari, resmi çağrı akışı ve protokol: [`Docs/ARCHITECTURE.md`](Docs/ARCHITECTURE.md).

## Kurulum (tek yol)

```bash
python3 install.py            # kurar
python3 install.py --check    # hiçbir şeyi değiştirmez, durumu raporlar
```

`install.py`:

1. Bulduğu her MCP istemcisine (Claude Desktop, Antigravity, Claude Code)
   Loom'u kaydeder; config'in yedeğini alır, tekrar çalıştırılabilir.
2. Extension paketini (`extension/dist/loom.ablx`) hazırlar (toolchain varsa
   derler) ve Live'da kurulu olanın sürümünü ve köprü protokolünü bu
   checkout'unkiyle karşılaştırır. Live'ın gördüğü ad **Loom**, kimlik
   `subverselab.loom`. Eski paket (`loom.sensei-midi-writer`) kuruluysa bunu
   söyler: MCP eskisine mutasyon göndermez (`LEGACY_EXTENSION`).
3. Katalogları sizin stok Ableton kütüphanenizden üretir.

Live'ın kendi adımı tek: `.ablx` dosyasını Live 12.4 beta'nın Extensions
ayarından ekleyin ve Live'ı yeniden başlatın. Extension kendi depolama
dizininde bir dosya köprüsü açar; MCP her çağrıda o köprüyü bulur.

Sürüm tek yerden yazılır: `manifest.json` (paket sürümü). `package.json` onunla
eşleşmek zorundadır (build bunu doğrular) ve köprü protokolü
(`loom.bridge/3`) ayrı bir kavramdır; SDK API sürümü (1.0.0) üçüncüsüdür.

## Bağlantı teşhisi (tek yol)

MCP aracı **`live_bridge_status`**: hangi köprüye konuştuğunu ve neden,
durumun yaşı, oturum kimliği, extension'ın yayımladığı yetenekler,
`mutations_allowed` ve protokol kararı (`OK` / `UPGRADE_REQUIRED` /
`PROTOCOL_MISMATCH` / `STALE_STATE` / `NO_STATE`), günlüğün durumu,
kuyrukta ve işlemde bekleyenler. Aynı bilgiyi `python3 install.py --check`
terminalde verir.

Eski bir extension (protokol yayımlamayan 0.1.0 / 0.2.0) **okunur ama
değiştirilmez**: her mutasyon `UPGRADE_REQUIRED` ile, istek dosyası yazılmadan
reddedilir. Eski kimlikle kurulu paket (`loom.sensei-midi-writer`) da öyle:
`LEGACY_EXTENSION`; yenisi kurulup eskisi Live'dan kaldırıldıktan sonra eski
günlük `live_command op=journal_import` ile yeni köprüye taşınır (unutulmaz,
silinmez). Birden fazla Loom köprüsü görülürse `AMBIGUOUS_BRIDGE`.
`LOOM_BRIDGE_ROOT` yalnız testler içindir.

## Desteklenen MCP araçları

| Durum | Araçlar | Test |
|---|---|---|
| **Live üzerinden (extension)** | `live_state`, `live_bridge_status`, `live_command` (set_tempo, set_mixer, set_device_parameter, list_device_parameters, create_locator, create_midi_track, import_audio_clip, render_pre_fx, drum_pads, **build_drum_kit**, journal_import), `midi_write_arrangement`, `midi_write_to_live` (session clip), `crate_to_live`, `mix_from_live`, `project_build`, `midi_generate` (auto_write) | `mcp_server/tests/test_bridge_consumer_real.py` (gerçek bridge.ts), `test_extension_path.py`, `extension/tests/bridge.test.ts` |
| **Live'sız motorlar** | `project_*`, `automation_*`, `drumbuss_*`, `chain_*`, `render_*`, `palette_read`, `library_search`, `genre_evidence`, `part_suggest`, `plan_create`, `plan_verify`, `projects_arrangement_shapes`, `mix_measure/analyze/profiles`, `crate_fetch/read/spots/chop/agent`, `setup_scan`, `gap_record` | `mcp_server/tests/test_mcp_tools.py` (45 araç, stdio), motorların kendi pytest paketleri |
| **OS düzeyi, kullanıcı isteğiyle** | `live_project` (Live'ı aç / kapat / durum; Live'ın kendi logundan doğrular; set değiştirmek extension host'u düşürür) | `test_live_project.py` |
| **Makineye bağlı** | `mix_capture` (`method="tap"`: Core Audio süreç musluğu, LaunchServices üzerinden `LiveTap.app` olarak başlar; macOS'ta "Ekran ve Sistem Sesi Kaydı" izni **LiveTap** girişine verilir, MCP'yi çalıştıran uygulamaya değil; 2026-09-06'da çalan Live'dan ölçüldü) | yalnız gerçek makinede |

Extension'ın kendi içindeki tek kullanıcı komutu **"Loom: Generate"**
(sağ tık, Session slot) aynı `write_clip` uygulamasından ve aynı sahiplik
defterinden geçer: Loom'un yazmadığı klip üzerine yazılmaz.

### Kit ve preset akışı

SDK preset (.adg/.adv) yüklemez; Loom bunu "yapamaz" saymaz, yolu ayırır:

- **Hazır kit**: `project_build(kit="Boom Bap Kit")` ya da bir `.adg` yolu.
  Kit, preset'in kendi XML'inden okunur (pad, nota, ad, sample dosyası;
  Sensei'nin `.adg` okuyucusu), dosyalar bu makinede çözülür ve pad'ler
  extension'da chain + Simpler + sample olarak yeniden kurulur. Cevap neyin
  taşındığını ve neyin **taşınmadığını** söyler: pad başına efektler, macro'lar,
  choke grupları, Simpler parametreleri, dönüş zincirleri. Bu, preset'i olduğu
  gibi yüklemek değildir ve öyle sunulmaz.
- **Sample'lardan kit**: `live_command op=build_drum_kit pads=[{note, sample}]`.
- **Enstrüman preset'i** (bass/chord): SDK yüklemez. Ya `device_map` ile
  yerel cihaz (`Operator`, `Electric`, `Wavetable`…) ya da preset'i Live'da
  kendin yükleyip aynı planı yeniden çalıştırırsın; kanal benimsenir, cihazı
  durumdan okunur. Cevapta `needs_preset` iki yolu da yazar.
- **Davul notaları kit'in pad'lerine**: Sensei'nin davul kanıtı GM pad
  düzeninde; 77–92 gibi bir kitte notalar pad rolüne (kick/snare/hat) göre
  eşlenir (`pad_mapping: by_role`), eşlenemeyen rol düşürülür ve yazılır;
  hiç nota kalmazsa klip yazılmaz (`no_notes_for_pads`).
- **Sadeleştirme açık**: `tracks=[...]` ve `device_map` verilirse cevaptaki
  `simplification` bloğu hangi kanalların neden düşürüldüğünü ve hangi cihazın
  hangi preset'in yerine geçtiğini yazar. Şablonla gelen boş kanallara
  dokunulmaz.
- **Dosya yolu (B)**: `.als` üzerinde çalışan yazıcılar (`automation_write`,
  `drumbuss_build`, `chain_apply`) diskteki seti değiştirir; açık set
  değişmez, set yeniden açılmalıdır. Preset XML'i elle sete dönüştürülmez;
  bu Live'ın işidir.

## SDK nedeniyle desteklenmeyenler

Extensions SDK 1.0.0-beta.1 şunları vermez; Loom bunları **emüle etmez**,
istek dosyası yazılmadan `UNSUPPORTED_BY_SDK` ve gereken yetenek adıyla
cevaplar:

- transport (play/stop/position) → `live_command op=transport`, `mix_capture follow_transport`
- song key yazma → `live_command op=set_key`; `project_build` adımı `UNSUPPORTED_BY_SDK` olarak raporlanır
- preset/browser yükleme → `create_midi_track` yalnız yerel cihazı varsayılan preset'iyle ekler (`not_loadable_in_extension`); kit için `build_drum_kit kit=` yeniden kurar (yukarıda), enstrüman için `device_map` ya da kullanıcı adımı
- ölçü işareti → bar→beat çevirimi açık `beats_per_bar` ister, `.als` verilmişse oradan okur, yoksa 4/4 varsaydığını `beats_per_bar_source` ile söyler
- meter, kayıt (record mode / resampling) → `mix_capture method="resample"`, `capture_*` op'ları

Centercode'a 2026-09-03'te bildirildi.

## Köprü sözleşmesi (kısa)

Her Live cevabı yapılandırılmış bir `outcome` taşır:
`{kind: applied|refused|failed|indeterminate, code, applied, verified, side_effects, next_step}`.
MCP durumu bundan türer: `OK`, `REFUSED_IN_LIVE` (Live'a dokunulmadı),
`FAILED_IN_LIVE` (denendi, eski içerik geri kondu), `INDETERMINATE`
(uygulanmış olabilir; yan etki ve güvenli sonraki adım cevapta), `NOT_CONSUMED`,
`INVALID_RESULT`. Belirsiz sonuç hiçbir yerde otomatik yeniden denenmez.

Anahtarlı istekler (build adımları, `idempotency_key`) mutasyondan **önce**
günlüğe yazılır; aynı anahtar + aynı içerik → saklanan sonuç, farklı içerik →
çakışma, başka oturum → ret, yarım kalmış → `INDETERMINATE`. Günlük kaybolmuş
ya da bozuksa anahtarlı mutasyonlar reddedilir ve cevap ne yapılacağını söyler;
günlüğü silmek hiçbir yerde önerilmez. Ayrıntı: `Docs/ARCHITECTURE.md`.

## Test

```bash
./scripts/check_ci.sh    # Ableton ve kişisel veri gerektirmeyen paket; CI bunu koşar
./scripts/check_all.sh   # tamamı; gerçek bir Ableton kurulumu ister
```

`check_ci.sh` her paketi passed / failed / skipped olarak sayar; bağımlılığı
eksik paket geçmiş sayılmaz. Extension'ın kendi kuyruk kodu (`bridge.ts`)
hem kendi başına (`npm run test:bridge`) hem de MCP'nin karşısında gerçek
tüketici olarak (`test_bridge_consumer_real.py`) çalıştırılır; Python sahtesi
yalnız hız için vardır ve tek başına protokol kanıtı sayılmaz.

Testler geçici dizin ve izole fixture kullanır; gerçek Live'a, kurulu
extension'a ve kullanıcı verisine dokunmaz.

Gerçek Live kabul betiği: `extension/tools/measure_bridge.py`
(çalışan Live'ın açık setine yazar; boş bir sette çalıştırın).

## Veri politikası

Bu depo kod ve fixture yayınlar, ölçüm yayınlamaz. Presetor'un cihaz zinciri
kanıtı, AISoundDesigner'ın ses paleti ve Sensei'nin katalogları kullanıcının
kendi projelerinden ve Ableton kurulumundan üretilir; hiçbiri depoda yoktur.
Temiz klonda testler sentetik fixture ile çalışır ve her yanıt kaynağını
`data_source` alanında söyler: `measured` ya da `synthetic_fixture`.

```bash
python3 scripts/extract_device_chains.py --out Presetor/data/measured_device_chains.json
python3 scripts/extract_sound_sources.py --out AISoundDesigner/data/measured_sound_sources.json
python3 scripts/setup_scan.py --check      # kataloglar: ne var, ne eksik
```

## Bilinen sınırlar

- **Gerçek Live kabulü 2026-09-07'de geçti.** Boş bir set'ten başlayıp gerçek
  `.adg`/`.adv` preset'leri açtı, pad'leri çözdü (16/16), MIDI ve locator yazdı;
  set kaydedilip yeniden açıldığında hepsi yerindeydi. Extension o gün 0.4.x idi.
  **Açık kalan kenar:** 0.4.4'ün getirdiği `delete_track` / `delete_locator`
  (2026-09-11) yalnız headless test edildi; onlar için gerçek Live kabulü henüz
  yapılmadı.

  Bu madde 2026-09-07'den 2026-09-12'ye kadar "kabul henüz yapılmadı, kurulu
  extension 0.1.0" diyordu — kabulü geçiren commit'in kendisi tarafından
  yazılmış ve geçtikten sonra geri dönülmemişti. Depo herkese açık olduğu için
  beş gün boyunca projeyi çalışmıyor gösterdi. Sürüm ve kabul iddiaları
  `extension/manifest.json` ile birlikte güncellenir.
- Render Live'ın ses motorunu gerektirir; `render_plan` ne çıkması gerektiğini,
  `render_verify` çıkanın uyup uymadığını söyler.
- Otomasyon yazma (dosya üzerinde) mikser ve cihaz parametrelerini kapsar;
  klip zarfları yok.
- Araç zaman aşımı sert değildir (Python'da iş parçacığı öldürülemez); zaman
  aşımına uğramış çağrı bir daha Live'a istek yazamaz.
- SDK klibin otomasyon / renk / launch ayarlarını okuyamadığı için
  `replace_owned` yalnız aynı nesneyi yerinde değiştirir; silme gerektiren
  değiştirme reddedilir. Tam koruma garantisi verilmez, dar politika budur.
- Günlük exactly-once değildir: uygulandığı bilinmeyen iş uygulanmış da
  sayılmaz, sayılmamış da; durumu okumak çağırana kalır.
- `render_verify` ve ses ölçümü `soundfile`/`numpy` ister; diğer araçlar
  macOS'un kendi Python'uyla çalışır.

Gap kaydı: `Docs/MISSING_CONTROLS_LOG.md` (başındaki not tarihsel girişleri ayırır).

## Telif ve atıf

© Şenol Şahan / SubverseLab. Tüm hakları saklıdır. Kanonik adres:
https://subverselab.com/loom . Kopyalayan, uyarlayan ya da bu koddan türeyen her
iş bu atfı ve `NOTICE` dosyasını korur; sunucunun her yanıtındaki `_source` alanı
silinmez. Akademik ya da yazılı atıf için `CITATION.cff`.
