# Loom MCP Server

Tek dosyalık stdio MCP sunucusu (`server.py`). Loom'ın beş alt sistemini
tek bir araç ad alanı arkasında toplar: Sensei, AIMixMaster, ArrangementGPS,
Presetor, AISoundDesigner.

## Çalıştırma

Antigravity kaydı `~/.gemini/config/mcp_config.json` içinde:

```json
"ai-producer-mcp": {
  "command": "~/Desktop/Loom/Sensei/.venv/bin/python",
  "args": ["~/Desktop/Loom/mcp_server/server.py"]
}
```

Sensei'nin venv'i kullanılır; AIMixMaster, Presetor ve AISoundDesigner
`sys.path`'e eklenir. `numpy`/`soundfile` gerektiren ses ölçüm yolları bu
venv'de yok — saf XML analizleri çalışır, ses ölçümü çalışmaz ve araçlar bunu
yanıtlarında açıkça söyler.

## Protokol

| Özellik | Durum |
|---|---|
| `tools/list`, `tools/call` | 24 araç, sayfalı (10/sayfa) |
| `resources/list`, `resources/read` | 4 kaynak |
| `prompts/list`, `prompts/get` | 3 şablon |
| `notifications/progress` | uzun taramalarda |
| `notifications/cancelled` | işbirlikçi iptal |
| Sürüm pazarlığı | 2025-06-18 / 2025-03-26 / 2024-11-05 |
| `structuredContent` | her araç yanıtında |

Desteklenmeyen: `resources/subscribe`, `completion/complete`,
`tools/list_changed`, sunucudan istemciye sampling.

### Zaman aşımı sert değildir

Araç çağrıları varsayılan 300 sn (tarayıcılarda 900 sn) sonra istemciye
`tool_timeout` döndürür ve istek iptal işaretlenir. Ama Python'da iş parçacığı
dışarıdan öldürülemez: `check_cancelled()` çağırmayan bir araç arka planda
bitene kadar çalışmaya devam eder, sonucu atılır.

### İptal işbirlikçidir

Python'da bir iş parçacığı dışarıdan durdurulamaz. `notifications/cancelled`
bir bayrak koyar; uzun döngüler `check_cancelled()` çağırıp kendileri durur.
Bu çağrıyı yapmayan bir araç iptal edilemez — sadece tarayıcılar ve zincir
adımları bunu yapıyor.

## Güvenlik sınırları

Dosya yolu alan her araç `resolve_als_path()` / `resolve_scan_root()`
üzerinden geçer:

- Yol `AI_PRODUCER_DIR`, `~/Desktop`, `~/Documents`, `~/Music` veya köprü
  dizininin altında olmak zorunda
- `.ssh`, `.aws`, `.gnupg`, `.config`, `Keychains`, `.env` içeren yollar
  koşulsuz reddedilir
- `als_path` uzantısı `.als` olmak zorunda

Sebep: `.als` içindeki metin modeli yönlendirebilir. Bu bir prompt-injection
yüzeyidir, yerel sunucu olması bunu değiştirmez.

## Yazma yapan araçlar

Üçü diske yazar; üçü de varsayılan olarak **kuru çalışır** ve `apply: true`
verildiğinde önce zaman damgalı yedek alır, yazdıktan sonra dosyayı tekrar
okuyup doğrular:

- `drumbuss_build`
- `chain_apply`
- `chain_apply`
- `automation_write` (mikser `volume`/`pan`; hedef PointeeId
  parametrenin kendi id'sidir, uydurulmaz)
- `midi_write_to_live` (köprüye yazar ve Live'ın **gerçekten
  tükettiğini bekler**: `WRITTEN_TO_LIVE` / `REJECTED_BY_LIVE` /
  `NOT_CONSUMED`)

## Yanıt boyutu

24.000 karakteri aşan yanıtlar kırpılır; tamamı `mcp_server/responses/`
altına yazılır ve yol yanıta eklenir. Ölçülen en büyük yanıt
`project_analyze_mixer` ile ~21.8 KB.

## Test

```bash
python3 mcp_server/tests/test_mcp_protocol.py   # protokol uyumu (sayfalama, iptal, yol kısıtı)
python3 mcp_server/tests/test_mcp_tools.py      # her aracın işi
../scripts/check_all.sh                          # hepsi + diğer katmanlar
```

İkisi ayrı tutuluyor çünkü ayrı şeyleri kanıtlıyorlar: 171 araç kontrolü
geçerken sunucu bildirimlere yanıt veriyordu ve hiçbiri bunu yakalamamıştı.

Testler yan etkilerini temizler: köprüye yazdığı isteği siler, gap loguna
eklediği satırı geri alır, ürettiği build dizinini ve render job dosyasını
kaldırır.

## Bilinen sınırlar

- Köprünün **okuma** yönü hâlâ yok: Live'ın o anki durumu (seçili track,
  playhead, cihaz listesi) okunamıyor (GAP-001'in kalan yarısı)
- Otomasyon yazma sadece mikser `volume`/`pan` için; cihaz parametreleri ve
  klip zarfları hâlâ yok (GAP-006)
- Şarkı ölçüsü SDK'da yok; bar→beat çevirimi 4/4 varsayar (GAP-003)
- Render Live'ın ses motorunu gerektirir, buradan yapılamaz.
  `render_plan` ne çıkması gerektiğini,
  `render_verify` çıkanın uyup uymadığını söyler — arası Live'da
- Sert zaman aşımı yok (yukarıda)
