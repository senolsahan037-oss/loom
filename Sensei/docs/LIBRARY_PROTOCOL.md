# Sensei Library Protocol v1

## 1. Amaç ve Kapsam

Sensei Library Protocol, müzik jeneratör motorlarının (Lokal Kural Motorları, LLM veya AI Agent'lar) doğrudan dosya sistemi üzerinde gezinmesi yerine, standartlaştırılmış bir sözleşme (contract) üzerinden kütüphane nesnelerini aramasını, çözmesini (resolution) ve üretim bağlamı (variation/generation context) elde etmesini sağlar.

Sistem, Ableton Live kütüphane kavramlarını (`.alc` klipleri, `.adg` presetleri) temel alarak çalışır ve geriye dönük uyumsuzlukları gidermek adına katı veri kuralları uygular.

---

## 2. Lokal Öncelikli (Local-First) ve İsteğe Bağlı LLM İlkesi

Sensei, tüm davul üretimi ve varyasyon işlemlerinde öncelikli olarak yerel kuralları, veritabanlarını ve matematiksel denklemleri kullanır. LLM kullanımı tamamen opsiyoneldir ve sadece yaratıcı/semantik yönlendirmeler için bir asistan olarak kullanılır.

1.  **Yerel Karar Yönlendirme (`core/decision_router.py`)**:
    *   Tüm jenerasyon istekleri ilk olarak yönlendiriciden geçer.
    *   Standard parametreli sorgular (`local_generation`) ve klibi değiştirme istekleri (`local_mutation`) doğrudan yerel motorlar tarafından çözülür; LLM çağrısı yapılmaz (`llm_used = false`).
    *   Sadece yaratıcı/soyut ifadeler içeren prompts durumunda ve yerel eşleşme zayıfsa `hybrid_llm` rotası seçilir.
2.  **Lokal Montaj Güvencesi (`core/assembly_engine.py`)**:
    *   LLM hiçbir zaman doğrudan fiziksel MIDI nota numarası veya pad numarası yazamaz.
    *   LLM sadece soyut roller (`kick`, `snare`, `closed_hat` vb.) döner.
    *   Bu soyut rollerin fiziksel notalara dönüştürülmesi, groove timing ve velocity offset'lerinin giydirilmesi, choke gruplarının uygulanması ve güvenlik kontrolleri tamamen **lokal motor** tarafından yapılır.
3.  **Source-Native Sınıflandırma**: Orijinal Ableton metadata verileri doğrudan korunur ve değiştirilemez.
4.  **Native vs. Heuristic Ayrımı**:
    *   Orijinal kaynaktan gelen veriler `source_native` altında tutulur.
    *   Analiz motorları tarafından türetilen bilgiler (örneğin tempo tahmini, enerji seviyesi) `derived.*` altında saklanır.
    *   Tahmini (heuristic) veriler hiçbir zaman native alanların üzerine yazılamaz; native alanlar eksikse `null` veya `unknown` bırakılır.

---

## 3. Protokol Modelleri ve Metotları

### A. Catalog & Query Modeli (`query_library_items`)
Kullanıcı veya agent sorgularını işler.

*   **Sorgu Parametreleri**:
    *   `content_type`: Sorgulanacak nesnenin tipi (örn: `clip`, `kit`).
    *   `genre`: Tür filtresi (öncelikli olarak `source_native.ableton_genres` alanına bakar).
    *   `pack`: Ableton Pack adı filtresi.
    *   `capabilities`: `has_midi_events`, `has_preview_audio`, `has_embedded_kit` gibi teknik özellik filtreleri.

### B. Reference Resolution Modeli (`resolve_reference_clip` & `resolve_kit_context`)
Bir referans id veya dosya yolu verildiğinde, üretici motorun ihtiyaç duyacağı teknik bağlamı çözümler.

1.  **`resolve_reference_clip(clip_path)`**:
    *   Klibin MIDI notalarını (`events`) ve kullanılan nota listesini (`notes_used`) çözer.
2.  **`resolve_kit_context(clip_path, selected_kit_path)`**:
    *   Klip ile ilişkili davul kitini çözümler. Varsa gömülü kit (`embedded_alc`), yoksa fallback kit (`selected_kit_path`) çözülerek `note_space` çıkarılır.

### C. Variation & Generation Context Modeli (`build_variation_context` & `build_decision_prompt`)
Çözümlenen referans klip ve kit bağlamını standart bir varyasyon sözleşmesine (Variation Contract) dönüştürür.

*   **Preserve (Korunacak Sınırlar)**: Değişmemesi gereken sınırlar (`bar_length`, `main_pulse`, `kit_note_space`).
*   **Change / Mutable (Değiştirilebilir Alanlar)**: Jeneratörün değiştirebileceği alanlar (`velocity`, `density`, `fills`, `ghost_notes`).

### D. Diagnostics Modeli
Her jenerasyon cevabında aşağıdaki tanılayıcı alanlar yer almak zorundadır:
*   `decision_route`: Seçilen rota (`local_generation`, `local_mutation`, `hybrid_llm`, `llm_fallback`).
*   `llm_used`: LLM kullanılıp kullanılmadığı (True/False).
*   `llm_reason`: LLM kullanılma gerekçesi.
*   `local_context_valid`: Lokal bağlamın geçerli olup olmadığı (True/False).
*   `dataset_candidate_used`: Kullanılan veri seti adayının ismi (varsa).
*   `mutation_used`: Lokal mutasyon uygulanıp uygulanmadığı (True/False).
*   `assembly_used`: Lokal montaj uygulanıp uygulanmadığı (True/False).
*   `groove_applied`: Lokal groove uygulanıp uygulanmadığı (True/False).
*   `choke_corrections`: Uygulanan choke kesme sayısı.
*   `raw_midi_from_llm_blocked`: LLM'den gelen ham notaların engellenip engellenmediği (True/False).
*   `resolved_roles_used`: Çözümlenen davul rolleri listesi.
*   `unavailable_roles_skipped`: Kitte bulunmayan ve atlanan roller.
*   `notes_before_safety` & `notes_after_safety`: Filtreleme öncesi ve sonrası fiziksel notalar.
*   `warnings`: Oluşan uyarılar.

---

## 4. Durum Matrisi (Status & Roadmap)

### Implemented (Tamamlananlar)
*   [x] Lokal karar yönlendiricisi (`core/decision_router.py`).
*   [x] Lokal varyasyon ve mutasyon motoru (`core/assembly_engine.py`).
*   [x] SQLite ve filesystem taramasının tek indeks altında query edilmesi.
*   [x] `.alc` dosyalarından MIDI notalarının okunması ve semantik rollere dönüştürülmesi.
*   [x] Choke gruplarının ve groove swing offset'lerinin yerel olarak uygulanması.
*   [x] LLM'den gelen ham MIDI numaralarının engellenmesi ve temizlenmesi.

### In Progress (Devam Edenler)
*   [ ] `Analysis Resolver` ile çoklu tahmin kaynaklarındaki tempo ve tür çelişkilerinin güven puanı matrisine göre otomatik olarak çözümlenmesi.

### Roadmap (Gelecek Planlar)
*   [ ] Diğer harici kütüphane kaynakları (Splice, Loopcloud) için Provider adaptör katmanlarının eklenmesi.
*   [ ] Ableton projeleri (`.als`) içindeki cihaz ve kanal ilişkilerinin otomatik indekslenmesi.
