# Implementation Plan: AI Memory and Mode Improvements

## Overview

Bu plan, Python/FastAPI + LangGraph backend'ine üç odaklı iyileştirme ekler:
1. Oturum bazlı konuşma hafızası (`resolve_thread_id` refactor).
2. `uni_info` node'unda bölüm adı farkındalıklı spesifik mod.
3. `career_info` node'unda genel/spesifik mod ayrımı.

Tüm değişiklikler `ai/` Python paketinin içinde kalmakta; yeni bir veritabanı şeması veya dış servis gerekmemektedir.

---

## Tasks

- [x] 1. Temel tipleri ve state güncellemelerini oluştur
  - `ai/app/core/state.py` dosyasında `AgentState` TypedDict'e `is_specific: Optional[bool]` ve `is_career_specific: Optional[bool]` alanlarını ekle
  - `ai/app/gate.py` içinde `ThreadResolution` dataclass'ını tanımla: `thread_id`, `is_anonymous`, `effective_session_id`
  - Test framework'ünün (`pytest` + `hypothesis`) `ai/tests/` altında mevcut olduğunu doğrula; yoksa `conftest.py` oluştur
  - _Requirements: 1.1, 1.3, 1.5_

- [x] 2. Session Manager — `resolve_thread_id` implementasyonu
  - [x] 2.1 `resolve_thread_id(session_id: str | None) -> ThreadResolution` fonksiyonunu `ai/app/gate.py` içine yaz
    - Geçerli (boş olmayan, `"default_session"` olmayan) `session_id` → `thread_id = session_id`
    - `None`, `""`, `"default_session"` veya 256 karakterden uzun → `thread_id = f"anon:{uuid4().hex}"`
    - `effective_session_id` alanına ne kullanıldıysa/üretildiyse onu ata
    - _Requirements: 1.1, 1.3, 1.4, 1.5, 1.6_
  - [ ]* 2.2 Property test yaz: `resolve_thread_id` — Geçerli session_id doğrudan thread_id olarak kullanılır
    - **Property 1: Valid session_id is used as thread_id directly**
    - **Validates: Requirements 1.1, 1.4**
    - `ai/tests/test_session_manager.py` dosyasına yaz
  - [ ]* 2.3 Property test yaz: `resolve_thread_id` — Geçersiz session_id benzersiz anon thread_id üretir
    - **Property 2: Invalid session_id always produces a unique anonymous thread_id**
    - **Validates: Requirements 1.3, 1.5, 1.6**
    - İki bağımsız çağrının farklı `thread_id` döndürdüğünü doğrula

- [x] 3. `ask_intelligent_system` endpoint'ini yeni `resolve_thread_id`'ye bağla
  - [x] 3.1 `ai/app/gate.py` içindeki `ask_intelligent_system` fonksiyonunda inline `f"{req.session_id}:{uuid4().hex}"` ifadesini `resolve_thread_id(req.session_id)` çağrısıyla değiştir
    - `ThreadResolution.thread_id`'yi LangGraph config'e aktar
    - `ThreadResolution.effective_session_id`'yi response `session_id` alanına yaz
    - Rate-limit ve token sayaçlarını hâlâ `session_id` anahtarıyla tut (değişiklik yok)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_
  - [ ]* 3.2 Birim testi yaz: rate-limit sayacı `session_id` bazlı, `thread_id` değişikliğinden etkilenmez
    - _Requirements: 1.7_

- [x] 4. Checkpoint — Hafıza değişiklikleri
  - Tüm hafıza testlerinin geçtiğini doğrula, gerekirse kullanıcıya sor.

- [x] 5. `uni_info` — Spesifik mod genişletmesi
  - [x] 5.1 `ai/nodes/uni_info.py` içinde `is_specific` bayrağı mantığını güncelle
    - `detected_program = ner_ctx.get("program") or _extract_program_from_text(user_text)` hesapla
    - `is_specific = bool(detected_program or any(kw in user_text.lower() for kw in _UNI_TOPIC_KEYWORDS))`
    - `ner_web_query` boş olsa bile tetiklenmesini sağla (önceki accidental gate kaldırıldı)
    - `program_extraction` exception'larını yakala, `None` döndür
    - _Requirements: 2.1_
  - [x] 5.2 Property test yaz: Uni spesifik dedektör — program veya keyword ile tetiklenir, ikisi birden gerekli değil
    - **Property 3: Uni specificity detector fires on program or keyword, never both required**
    - **Validates: Requirements 2.1**
    - `ai/tests/test_uni_info.py` dosyasına yaz

- [x] 6. `uni_info` — `_build_uni_queries` query builder
  - [x] 6.1 `ai/nodes/uni_info.py` içine `_build_uni_queries(uni: str, program: str | None, keyword: str | None, user_text: str) -> list[str]` fonksiyonunu yaz
    - Yalnızca program → `"{uni} {program}"` önekli en az 3 sorgu (müfredat, taban puan/kontenjan, öğrenci yorumları)
    - Yalnızca keyword → `"{uni} {keyword}"` önekli en az 3 sorgu (mevcut davranış korunur)
    - Her ikisi de var → `"{uni} {program} {keyword}"` önekli en az 3 sorgu
    - Hiçbiri yok → `"{uni}"` önekli en az 4 genel sorgu
    - _Requirements: 2.2_
  - [ ] 6.2 Property test yaz: Uni query builder en az 3 sorgu üretir ve doğru öneke sahip
    - **Property 4: Uni query builder produces at least 3 queries with correct prefix**
    - **Validates: Requirements 2.2**
  - [x] 6.3 `uni_info_node` içinde var olan sorgu üretim kodunu `_build_uni_queries` çağrısıyla değiştir
    - `is_specific=True` iken `human_content`'e `"Soru tipi: SPESİFİK — sadece sorulan konuyu cevapla"` sinyalini ekle
    - `is_specific=False` iken mevcut genel davranış korunur
    - _Requirements: 2.3, 2.4_
  - [x] 6.4 Property test yaz: `is_specific=True` iken `human_content` SPESİFİK sinyali içerir, genel başlıklar içermez
    - **Property 5: Specific uni signal always appears in human_content when is_specific=True**
    - **Validates: Requirements 2.3**
    - Web/LLM mock'la; (uni, user_text, True/False) çiftleri üret

- [x] 7. Checkpoint — Uni mode değişiklikleri
  - Tüm uni_info testlerinin geçtiğini doğrula, gerekirse kullanıcıya sor.

- [x] 8. `career_info` — Spesifik dedektör
  - [x] 8.1 `ai/nodes/career_info.py` içine `_detect_career_specificity(user_text: str) -> bool` fonksiyonunu yaz
    - Şirket/işveren adı içeren sorgular → `True`
    - Pozisyon/unvan keyword'ü → `True`
    - Coğrafi iş koşulu → `True`
    - Spesifik ders/teknoloji adı → `True`
    - Değerlendirme sıfatı ("zor", "kolay", "tavsiye", "değer mi", "mantıklı mı") → `True`
    - Yalnızca `<bölüm_adı> + (nasıl | nedir | anlat | hakkında)` → `False`
    - _Requirements: 3.1, 3.2_
  - [x] 8.2 Property test yaz: Career spesifik dedektör sinyal kategorilerine göre doğru sınıflandırır
    - **Property 6: Career specificity detector correctly classifies queries by signal category**
    - **Validates: Requirements 3.1, 3.2**
    - `ai/tests/test_career_info.py` dosyasına yaz

- [x] 9. `career_info` — Genel/Spesifik mod yönlendirmesi
  - [x] 9.1 `ai/nodes/career_info.py` içinde spesifik yolu (dept mevcut) uygula
    - `is_career_specific=True` ve `dept` boş değil → `human_content`'e `"Soru tipi: SPESİFİK — sadece sorulan konuyu cevapla, tam şablon doldurma"` ekle
    - Sorguları `f"{dept} {user_text[:80]}"` + 2 destekleyici sorgu olarak oluştur
    - _Requirements: 3.3, 3.4_
  - [x] 9.2 `ai/nodes/career_info.py` içinde dept-yok yolunu uygula
    - `is_career_specific=True` ancak `dept` boş → tek bir `AIMessage` açıklama isteği döndür, web araması veya LLM çağrısı yapma
    - _Requirements: 3.6_
  - [x] 9.3 `ai/nodes/career_info.py` içinde genel yolun değişmediğini doğrula
    - `is_career_specific=False` → 3 sabit sorgu (müfredat, kariyer/maaş, istihdam) + tam şablon davranışı
    - `human_content`'e `"Soru tipi: GENEL — tam kariyer analizi yap"` sinyali ekle
    - _Requirements: 3.5_
  - [x] 9.4 Property test yaz: `is_career_specific=True` ve dept mevcut iken `human_content` SPESİFİK sinyali içerir
    - **Property 7: Specific career signal appears in human_content when is_career_specific=True and dept is known**
    - **Validates: Requirements 3.3**
    - `_ddg_quick` ve `llm_responder`'ı mock'la
  - [x] 9.5 Property test yaz: Dept yokken spesifik kariyer modu açıklama döndürür, arama yapmaz
    - **Property 8: Missing dept in specific career mode always returns clarification, never searches**
    - **Validates: Requirements 3.6**
    - Mock çağrılarının yapılmadığını assert et

- [x] 10. `career_info` sistem prompt güncellemesi
  - [x] 10.1 `ai/prompts/career_info.py` içindeki `_CAREER_INFO_SYSTEM_PROMPT`'a koşullu FORMAT SEÇİMİ bölümü ekle
    - `"Soru tipi: SPESİFİK"` sinyali → kısa ve odaklı yanıt, tam şablon doldurma
    - `"Soru tipi: GENEL"` sinyali → mevcut tam şablon (Müfredat, Kariyer Yolları, İstihdam, vb.)
    - _Requirements: 3.4, 3.5_
  - [x] 10.2 Birim testi yaz: Prompt şablonunun her iki sinyal için doğru format yönlendirmesini içerdiğini doğrula
    - _Requirements: 3.4, 3.5_

- [x] 11. Final Checkpoint — Tüm değişiklikleri entegre et
  - Tüm testlerin geçtiğini doğrula, gerekirse kullanıcıya sor.

---

## Notes

- `*` ile işaretli görevler isteğe bağlıdır ve hızlı MVP için atlanabilir.
- Her görev, izlenebilirlik için belirli gereksinimlere referans vermektedir.
- Checkpoint'ler artımlı doğrulama sağlar.
- Property testleri evrensel doğruluk özelliklerini, birim testleri ise belirli örnekleri ve edge case'leri doğrular.
- Tüm web/LLM çağrıları property ve birim testlerinde mock'lanmalıdır.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "5.1", "8.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "5.2", "8.2"] },
    { "id": 3, "tasks": ["3.1", "6.1", "9.1", "9.2", "9.3"] },
    { "id": 4, "tasks": ["3.2", "6.2", "6.3", "9.4", "9.5"] },
    { "id": 5, "tasks": ["6.4", "10.1"] },
    { "id": 6, "tasks": ["10.2"] }
  ]
}
```
