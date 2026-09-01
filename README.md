# Loom

[![checks](https://github.com/senolsahan037-oss/loom/actions/workflows/checks.yml/badge.svg)](https://github.com/senolsahan037-oss/loom/actions/workflows/checks.yml)

Ableton Live için, ölçüme dayalı bir üretim sistemi ve onu tek bir araç ad
alanı altında toplayan MCP sunucusu.

Bu depo bir demo değil: her araç gerçek bir motora bağlı ve her iddia Live
açılmadan çalıştırılabilen bir testle kanıtlı.

## Ne yapar

| Katman | İş |
|---|---|
| **Sensei** | Kilitli veri kümesinden MIDI varyasyonu üretir (drum/bass/chord) |
| **ArrangementGPS** | Prompt'tan proje planı: tempo, ton, tür, kanallar, bölümler |
| **AIMixMaster** | `.als` inceleme, gain staging, klip hizalama, drum buss, **otomasyon yazma** |
| **Presetor** | Kullanıcının kendi projelerinden ölçülmüş cihaz zincirleri ve transplant |
| **AISoundDesigner** | Kullanıcının gerçekten kullandığı ses paleti |
| **Loom (Control Surface)** | Live içinde çalışan çift yönlü köprü: canlı okuma ve yazma |
| **mcp_server** | 28 araç, resources, prompts, ilerleme, iptal |

## Temel ilke

Hiçbir katman tahmin üretmez.

- Cihaz zinciri önerisi, kullanıcının **1.235 track'inden** sayılmış cihaz
  varlık oranlarıdır. Yeterli gözlem yoksa öneri **dönmez**.
- Ses paleti, **en az iki ayrı projede** görülmüş sample'lardan oluşur.
  Bounce/freeze çıktıları ve reverb impulse'ları elenir.
- Otomasyon hedefi uydurulmaz: parametrenin XML'deki **kendi**
  `AutomationTarget Id`'si kullanılır, değer **kendi** aralığına karşı
  doğrulanır.
- Cihaz XML'i sıfırdan üretilmez, projede var olan bir track'ten klonlanır.
- Yazan her araç varsayılan olarak **kuru çalışır**, uygulandığında önce
  yedek alır ve yazdıktan sonra dosyayı tekrar okuyup karşılaştırır.

## Doğrulama

İki paket var:

```bash
./scripts/check_ci.sh    # Ableton gerektirmeyen alt küme -- CI bunu koşuyor
./scripts/check_all.sh   # tamamı, gerçek bir Ableton kurulumu ister
```

```bash
./scripts/check_all.sh
```

Dokuz paket, **Ableton Live açılmadan**: prompt çözümleme, plan çıkarımı,
enstrüman kapsamı, tip kontrolü, arrangement kurucusu, Presetor,
AISoundDesigner, köprü komut katmanı, canlı köprü, MCP protokol uyumu, MCP
araçları.

Testler yan etkilerini temizler ve hangi iddiayı kanıtladıklarını, hangisini
kanıtlamadıklarını dosya başlarında açıkça yazar.

## Veri politikası

Bu depo **kod ve fixture** yayınlar, **ölçüm** yayınlamaz.

Presetor'un cihaz zinciri kanıtı ve AISoundDesigner'ın ses paleti kullanıcının
kendi projelerinden sayılır — içinde proje adları, track adları ve sample
dosya adları vardır. Sensei'nin kimlik katalogları da sizin Ableton
kurulumunuzun kütüphane yollarından üretilir. Hiçbiri depoda yoktur.

Temiz bir klonda testler **sentetik fixture** ile çalışır. Fixture uydurmadır
ve öyle olmalıdır: işi kodun doğru hesapladığını kanıtlamak, kullanıcı hakkında
bir şey söylemek değil. Bu ayrım kaybolmasın diye her yanıt hangi kaynağı
kullandığını `data_source` alanında bildirir: `measured` veya
`synthetic_fixture`.

```bash
python3 scripts/extract_device_chains.py --out Presetor/data/measured_device_chains.json
python3 scripts/extract_sound_sources.py --out AISoundDesigner/data/measured_sound_sources.json
python3 scripts/build_fixtures.py    # sadece sentetik fixture'ları tazeler
```

Sensei'nin veri kümeleri `Sensei/ableton/` altındaki üreticilerle kurulur.
Katalog yoksa ona bağlı araçlar çalışmayı sürdürür ama bunu açıkça söyler.

## Kurulum

Tek komut:

```bash
python3 install.py
```

Bu komut üç işi birden yapar:

1. Makinede bulduğu her MCP istemcisine Loom'u kaydeder (Claude Desktop,
   Antigravity, Claude Code) — her config'in yedeğini alarak, tekrar
   çalıştırılabilir şekilde
2. Live Remote Script'lerini Ableton'ın `User Library`'sine kopyalar
3. Kataloglarınızı **sizin** stok Ableton kütüphanenizden üretir

Ölçülen süre: ~3 dakika. Uzun olan tek adım MIDI korpusu (~2.5 dk), çünkü
gerçek klip dosyalarını ayrıştırır — script her adımı başlamadan önce
duyurur, donmuş görünmez.

Ne yapacağını önce görmek için:

```bash
python3 install.py --check    # hiçbir şeyi değiştirmez
```

Kurulumdan sonra iki şeyi yeniden başlatın: **MCP istemciniz** (yeni sunucuyu
görsün) ve **Ableton Live** — sonra Settings → Link/MIDI altında
`Loom`'u Control Surface olarak seçin.

### Neden sanal ortam yok

**28 aracın 27'si sıfır üçüncü-parti bağımlılıkla çalışır** ve macOS'un kendi
Python'u yeter. Tek istisna `render_verify`: gerçek ses dosyalarını ölçtüğü
için `soundfile` ister, kurulu değilse bunu söyler ve durur.

```bash
python3 -m pip install soundfile numpy   # yalnızca render_verify için
```

### Kataloglar neden yeniden üretiliyor

Loom kod ve fixture yayınlar, ölçüm yayınlamaz. Akıl yürüttüğü kataloglar
**sizin makinenizdeki stok Ableton kütüphanesinden** üretilir — Live'ın kendi
dosya indeksi okunarak, yol yapılandırmadan.

```bash
python3 scripts/setup_scan.py --check   # ne var, ne eksik
```

Aynısını istemciden `setup_scan` aracıyla da yapabilirsiniz.

MCP sunucusunun protokol ayrıntıları: [`mcp_server/README.md`](mcp_server/README.md)

## Bilinen sınırlar

Bunlar `Docs/MISSING_CONTROLS_LOG.md` içinde numaralı olarak tutulur.

- Render, Live'ın ses motorunu gerektirir — buradan yapılamaz. Sistem ne
  çıkması gerektiğini ve çıkanın uyup uymadığını söyler, arasını Live doldurur
- Şarkı ölçüsü Extensions SDK'da yok; bar→beat çevirimi 4/4 varsayar (GAP-003)
- Otomasyon yazma mikser ve cihaz parametrelerini kapsar; klip zarfları henüz yok
- Araç zaman aşımı sert değildir (Python'da iş parçacığı öldürülemez)

## Telif

© Şenol Şahan / SubverseLab. Tüm hakları saklıdır.
