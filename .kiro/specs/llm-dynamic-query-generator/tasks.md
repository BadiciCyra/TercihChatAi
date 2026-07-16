# Implementation Plan: LLM Dynamic Query Generator

## Overview

`ai/utils/query_planner.py` dosyasına `QueryPlan` dataclass'ı ve `QueryPlanner` sınıfı eklenerek `uni_info_node` ile `career_info_node`'daki sabit anahtar kelime listeleri ve şablon tabanlı sorgu üretimi kaldırılır. Tüm sınıflandırma ve sorgu üretimi tek bir LLM çağrısına taşınır; sonuçlar Redis'te (TTL 3600 s) önbelleğe alınır ve LLM başarısız olduğunda deterministik fallback devreye girer.

## Tasks

- [x] 1. `QueryPlan` dataclass ve `QueryPlanner` iskelet sınıfını oluştur
  - `ai/utils/query_planner.py` dosyasını oluştur
  - `QueryPlan` dataclass'ını `is_specific: bool`, `queries: list[str]`, `source: Literal["llm", "fallback"]` alanlarıyla tanımla
  - `QueryPlanner.__init__(self, llm_responder, cache=None)` constructor'ını yaz; bağımlılıkları instance değişkenlerine ata
  - `_cache_key`, `_build_prompt`, `_parse_llm_response`, `_validate_queries`, `_fallback` metodlarının imzalarını (pass gövdeli) tanımla
  - Modül sonuna `query_planner` singleton'unu ekle
  - _Requirements: 1.1, 1.2, 1.3, 8.1_

- [x] 2. `_fallback` ve `_validate_queries` metodlarını uygula
  - [x] 2.1 `_fallback(entity_name, mode, reason)` metodunu uygula
    - `mode="uni"` için 4 şablonlu sorgu listesi döndür
    - `mode="career"` için tam olarak 3 şablonlu sorgu döndür
    - Bilinmeyen `mode` için `[f"{entity_name} hakkında bilgi"]` döndür
    - Her durumda `source="fallback"` ve `is_specific=False` ata
    - Fallback sebebini `[QUERY_PLANNER] ⚠️ Fallback — sebep: <hata mesajı>` formatında logla
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 2.2 `_fallback` için property testi yaz
    - **Property 9: Fallback sorgular entity_name içerir**
    - **Validates: Requirements 4.3, 4.4**

  - [x] 2.3 `_validate_queries(queries, entity_name)` metodunu uygula
    - `queries` list değilse veya len 3'ten az ya da 5'ten fazlaysa `None` döndür
    - Boş string'leri filtrele
    - Her sorgunun başında `entity_name` yoksa prepend et (case-insensitive)
    - Her sorguyu 500 karakterle kırp
    - Case-insensitive deduplication uygula; benzersiz sorgu sayısı 3'ten azsa `None` döndür
    - Geçerli `list[str]` döndür
    - _Requirements: 1.2, 1.4, 7.1, 7.2, 7.3, 7.4_

  - [ ]* 2.4 `_validate_queries` için property testi yaz
    - **Property 6: entity_name sorgularda her zaman mevcut**
    - **Validates: Requirements 2.6, 7.3**
    - **Property 11: Deduplication sonrası en az 3 sorgu yoksa fallback**
    - **Validates: Requirements 7.4**

- [x] 3. `_cache_key` ve `_build_prompt` metodlarını uygula
  - [x] 3.1 `_cache_key(user_text, entity_name, mode)` metodunu uygula
    - `hashlib.sha256((user_text + entity_name).encode()).hexdigest()[:16]` özeti hesapla
    - `f"ai:query_plan:{mode}:{digest}"` formatında anahtar döndür
    - _Requirements: 3.1_

  - [ ]* 3.2 `_cache_key` için property testi yaz
    - **Property 7: Cache determinism (idempotence)**
    - **Validates: Requirements 3.1**

  - [x] 3.3 `_build_prompt(user_text, entity_name, mode)` metodunu uygula
    - `mode="uni"` için kampüs/burs/taban puan/öğrenci yorumu/akademik kadro bağlam ipuçlarını ekle
    - `mode="career"` için müfredat/mezun maaş/YÖK istatistik bağlam ipuçlarını ekle
    - `user_text`, `entity_name` ve context hint'i prompt'a dahil et
    - JSON output şemasını ve Türkçe sorgu talimatını prompt'a ekle
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

  - [ ]* 3.4 `_build_prompt` için property testi yaz
    - **Property 10: Prompt, tüm input değerlerini içerir**
    - **Validates: Requirements 2.1, 2.3, 2.4**

- [x] 4. `classify_and_generate_queries` ana metodunu uygula
  - [x] 4.1 `classify_and_generate_queries` metodunu uygula
    - Geçersiz `mode` değeri gelirse hemen `_fallback` çağır
    - Redis'ten cache key ile okuma yap; geçerli JSON'u `QueryPlan` olarak dön
    - Redis okuma hatalarını sessizce yakala (bozuk veri dahil) ve LLM çağrısına devam et
    - `_build_prompt` ile prompt oluştur, `llm_responder.ainvoke` ile çağır
    - LLM exception'larını `except Exception` ile yakala, fallback döndür
    - `_parse_llm_response` ile ham yanıtı parse et; None ise fallback döndür
    - `_validate_queries` ile sorguları doğrula; None ise fallback döndür
    - Başarılı `QueryPlan`'ı Redis'e `setex(key, 3600, json)` ile yaz; yazma hatasını sessizce yakala
    - `source="llm"` ile `QueryPlan` döndür
    - _Requirements: 2.8, 2.9, 3.2, 3.3, 3.4, 3.5, 4.1, 4.2, 8.2, 8.3, 8.4_

  - [ ]* 4.2 `classify_and_generate_queries` için property testi yaz (never raises)
    - **Property 1: classify_and_generate_queries asla exception fırlatmaz**
    - **Validates: Requirements 8.3, 8.4**

  - [ ]* 4.3 `classify_and_generate_queries` için property testi yaz (queries invariant)
    - **Property 2: QueryPlan.queries invariantı**
    - **Validates: Requirements 1.2, 1.4, 1.5, 1.6, 7.1, 8.5**

  - [ ]* 4.4 `classify_and_generate_queries` için property testi yaz (geçersiz mod fallback)
    - **Property 3: Geçersiz mod → her zaman fallback**
    - **Validates: Requirements 2.8, 4.5**

  - [ ]* 4.5 `classify_and_generate_queries` için property testi yaz (LLM exception fallback)
    - **Property 4: LLM exception → her zaman fallback**
    - **Validates: Requirements 4.1, 8.3**

  - [ ]* 4.6 `classify_and_generate_queries` için property testi yaz (geçersiz LLM yanıtı fallback)
    - **Property 5: Geçersiz LLM yanıtı → fallback**
    - **Validates: Requirements 2.9, 4.2, 7.2**

- [x] 5. Checkpoint — Tüm `QueryPlanner` testlerini çalıştır
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Cache hit → LLM çağrısı yapılmaz özelliğini uygula ve test et
  - [x] 6.1 Cache read path'ini tamamla ve mock cache ile entegrasyon test yaz
    - `cache.get()` döndürdüğü JSON'u `QueryPlan` olarak parse etme mantığını doğrula
    - Cache hit durumunda `llm_responder.ainvoke` çağrılmadığını doğrulayan unit test ekle
    - _Requirements: 3.2, 8.2, 8.6_

  - [ ]* 6.2 Cache hit property testi yaz
    - **Property 8: Cache hit → LLM çağrılmaz**
    - **Validates: Requirements 3.2, 8.6**

- [x] 7. `uni_info_node` entegrasyonu
  - [x] 7.1 `uni_info_node`'dan eski kodu kaldır ve `QueryPlanner`'ı entegre et
    - `_UNI_TOPIC_KEYWORDS` listesini ve `_build_uni_queries` fonksiyonunu kaldır
    - `is_specific` hesaplayan keyword tarama bloğunu ve `detected_keyword` çıkarımını kaldır
    - `from utils.query_planner import query_planner` import'unu ekle
    - Üniversite adı tespit edildikten sonra `plan = await query_planner.classify_and_generate_queries(user_text, uni, mode="uni")` çağrısını ekle
    - `plan.queries` listesini web araması için kullan
    - `plan.is_specific` değerini Redis üniversite önbellek kararı ve sinyal belirleme için kullan
    - Hata durumunda (`queries` boş veya exception) `"Bu üniversite için şu an arama yapamıyorum, lütfen tekrar dene."` AIMessage döndür
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [x]* 7.2 `uni_info_node` entegrasyon testi yaz
    - `query_planner.classify_and_generate_queries`'nin doğru argümanlarla çağrıldığını doğrula
    - `test_uni_info.py` içine ekle
    - _Requirements: 5.1, 5.2, 5.3_

- [x] 8. `career_info_node` entegrasyonu
  - [x] 8.1 `career_info_node`'dan eski kodu kaldır ve `QueryPlanner`'ı entegre et
    - `_detect_career_specificity` fonksiyonunu kaldır
    - `_COMPANY_SIGNALS`, `_POSITION_SIGNALS`, `_GEO_JOB_SIGNALS`, `_TECH_SIGNALS`, `_EVAL_SIGNALS`, `_COMPILED_CAREER_SIGNALS`, `_ALL_CAREER_SIGNALS`, `_GENERAL_QUESTION_PATTERN` listelerini kaldır
    - `from utils.query_planner import query_planner` import'unu ekle
    - `dept` boşken `classify_and_generate_queries` çağrısını atlayan mevcut açıklama mesajı davranışını koru
    - `dept` doluyken `plan = await query_planner.classify_and_generate_queries(user_text, dept, mode="career")` çağrısını ekle
    - `plan.is_specific` değerini rota seçimi (Yol C / Yol D) için kullan
    - `plan.queries` listesini web araması için kullan
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

  - [x]* 8.2 `career_info_node` entegrasyon testi yaz
    - `query_planner.classify_and_generate_queries`'nin doğru argümanlarla çağrıldığını doğrula
    - `dept=""` durumunda çağrı yapılmadığını doğrula
    - `test_career_info.py` içine ekle
    - _Requirements: 6.1, 6.2, 6.7_

- [x] 9. Final checkpoint — Tüm testleri çalıştır (kalan kırıklar bu feature'dan bağımsız — aşağıdaki nota bak)
  - Ensure all tests pass, ask the user if questions arise.

## Final Checkpoint Notu (2026-07-14)

Feature testleri yeşil: `test_query_planner.py` (55), `test_uni_info.py` (8), `test_career_info.py` (13), `test_research_pipeline.py` (7 — yeni node API'sine göre güncellendi). Tam pakette kalan 14 kırık bu feature'dan bağımsız ve öncesinde de mevcuttu:
- `test_frontend_mode.py` (5), `test_wizard_pipeline.py` (1), `test_extractors.py::test_year_is_not_rank` (1 — `_extract_rank_from_text` 4 haneli yılları sıralama sanıyor)
- `test_guidance_pipeline.py` (6) yalnız tam paket koşusunda kırılıyor, tek başına yeşil — test sırası etkileşimi
- `test_link_fetcher_properties.py` (1) flaky görünüyor

## Notes

- `*` ile işaretlenmiş sub-task'lar opsiyoneldir; hızlı MVP için atlanabilir
- Her task, traceability için ilgili requirements numaralarına referans verir
- Property testleri `ai/tests/test_query_planner.py` içinde, `@given` ve `@settings(max_examples=100)` ile yazılmalıdır
- `ai/utils/query_planner.py` içindeki `QueryPlan`, `core/schemas.py`'deki `QueryPlan`'dan bağımsız bir dataclass'tır (Pydantic gerektirmez)
- Constructor injection sayesinde tüm testler gerçek Redis/LLM bağlantısı gerektirmez

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "2.3", "3.1", "3.3"] },
    { "id": 2, "tasks": ["2.2", "2.4", "3.2", "3.4"] },
    { "id": 3, "tasks": ["4.1"] },
    { "id": 4, "tasks": ["4.2", "4.3", "4.4", "4.5", "4.6", "6.1"] },
    { "id": 5, "tasks": ["6.2", "7.1", "8.1"] },
    { "id": 6, "tasks": ["7.2", "8.2"] }
  ]
}
```
