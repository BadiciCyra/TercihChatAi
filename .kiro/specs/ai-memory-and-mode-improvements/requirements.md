# Requirements Document

## Introduction

Bu özellik, Python/FastAPI + LangGraph tabanlı AI backend'de üç temel iyileştirmeyi kapsamaktadır:

1. **Hafıza / Session ID Düzeltmesi**: Her istek için üretilen benzersiz UUID yerine, aynı chat oturumuna ait tüm mesajların aynı LangGraph thread'inde (checkpointer memory'de) tutulması.
2. **Üniversite Araştırma (uni_info) — Spesifik Mod Genişletmesi**: `is_specific` bayrağının yalnızca sabit anahtar kelimelere değil, bölüm adı içeren sorgulara da tepki verecek şekilde genişletilmesi; web sorgularının bölüme özel hale getirilmesi.
3. **Kariyer Araştırma (career_info) — Genel/Spesifik Mod Ayrımı**: `uni_info`'ya benzer şekilde, `career_info` node'unun da genel ve spesifik soruları ayırt etmesi; sistem prompt'unun soru tipine göre uygun format seçmesi.

## Glossary

- **Session_Manager**: Sohbet oturumunu ve LangGraph thread kimliğini yöneten bileşen (`gate.py` içindeki ilgili mantık).
- **Thread_ID**: LangGraph checkpointer'da konuşma geçmişini benzersiz olarak tanımlayan kimlik değeri.
- **Session_ID**: Frontend veya backend tarafından üretilen, bir chat oturumunu temsil eden kimlik değeri. Tek bir kullanıcı birden fazla oturum açabilir.
- **Uni_Info_Node**: `nodes/uni_info.py` içindeki üniversite bilgi araştırma node'u.
- **Career_Info_Node**: `nodes/career_info.py` içindeki kariyer ve bölüm araştırma node'u.
- **Specificity_Detector**: Bir sorunun genel mi yoksa spesifik mi olduğunu belirleyen mantık bileşeni.
- **Query_Builder**: Web arama sorgularını oluşturan bileşen.
- **LLM_Responder**: `llm_responder` nesnesi üzerinden yapılan LLM çağrıları.

---

## Requirements

### Gereksinim 1: Oturum Bazlı Konuşma Hafızası

**Kullanıcı Hikayesi:** Bir öğrenci olarak, chatbot'la konuşurken önceki mesajlarımın hatırlanmasını istiyorum; böylece her soru için bağlamı yeniden açıklamak zorunda kalmayacağım.

#### Kabul Kriterleri

1. WHEN bir istek geçerli (boş olmayan ve `"default_session"` olmayan) bir `session_id` alanıyla geldiğinde, THE Session_Manager SHALL LangGraph config'deki `thread_id` değerini `session_id`'nin kendisine eşit olarak ayarlamalı; request-scope UUID oluşturmamalıdır.
2. WHEN aynı `session_id` üzerinden ikinci ve sonraki istekler geldiğinde, THE Session_Manager SHALL LangGraph checkpointer'dan mevcut thread'i okuyarak en az bir önceki mesaj çiftini (user + AI) state'e dahil etmelidir.
3. WHEN frontend yeni bir chat oturumu açmak istediğinde ve `session_id` sağlamadığında, THE Session_Manager SHALL `uuid4().hex` biçiminde 32 karakterlik bir UUID string üretmeli ve bunu hem `thread_id` hem de yanıttaki `session_id` alanında döndürmelidir.
4. WHEN frontend yeni bir chat oturumu açmak istediğinde ve `session_id` sağladığında, THE Session_Manager SHALL sağlanan değeri doğrudan `thread_id` olarak kullanmalıdır.
5. IF `session_id` boş string, `None` veya `"default_session"` değerine sahipse, THEN THE Session_Manager SHALL her istek için `f"anon:{uuid4().hex}"` biçiminde benzersiz bir `thread_id` üretmeli; bu thread için geçmiş mesajlar korunmayacaktır.
6. IF `session_id` alanı istek gövdesinde hiç gönderilmezse, THEN THE Session_Manager SHALL 5. kriterdeki anonim davranışı uygulamalıdır.
7. THE Session_Manager SHALL rate-limit sayacını ve token takibini `session_id` bazında tutmaya devam etmelidir; `thread_id` değişikliği bu sayaçları etkilememelidir.

---

### Gereksinim 2: Üniversite Araştırmasında Bölüm Adı ile Spesifik Mod

**Kullanıcı Hikayesi:** Bir öğrenci olarak, "Bahçeşehir Üniversitesi Elektrik Elektronik Mühendisliği nasıldır?" veya "BAÜ bursları nasıl?" gibi odaklı sorular sorduğumda, genel üniversite analizi yerine tam olarak o konuya odaklanmış cevap almak istiyorum.

#### Kabul Kriterleri

1. WHEN kullanıcı sorusu bir üniversite adı VE aşağıdakilerden en az birini içerdiğinde — (a) tespit edilebilir bir bölüm/program adı (`ner_context.program` dolu veya regex ile çıkarıldı) VEYA (b) mevcut konu anahtar kelimelerinden biri (`kulüp`, `yurt`, `staj`, `burs`, `ücret`, `kampüs`, `yemek`, `ulaşım`, `spor`, `müfredat`, `hoca`) — THE Specificity_Detector SHALL `is_specific` bayrağını `True` olarak işaretlemelidir.
2. WHEN `is_specific` `True` olduğunda, THE Query_Builder SHALL sorgu stratejisini tespit edilen bağlama göre dinamik olarak seçmelidir:
   - **Yalnızca bölüm tespit edildi** (konu keyword'ü yok): `"{uni} {program}"` ön ekli sorgular — müfredat, taban puanı/kontenjan, öğrenci yorumları
   - **Yalnızca konu keyword'ü tespit edildi** (bölüm yok): `"{uni} {keyword}"` ön ekli sorgular — mevcut davranış korunur
   - **Hem bölüm hem konu keyword'ü tespit edildi**: `"{uni} {program} {keyword}"` birleşik ön ekli sorgular (örn. "BAÜ EEM bursları")
   - Her senaryoda en az 3 web arama sorgusu üretilmelidir.
3. WHEN `is_specific` `True` olduğunda, THE Uni_Info_Node SHALL `human_content`'e `"Soru tipi: SPESİFİK — sadece sorulan konuyu cevapla"` sinyalini eklemelidir; LLM çıktısında genel üniversite analizi başlıkları (📌 Genel Bakış, 🎓 Akademik Yapı, ⚖️ Artılar & Eksiler) yer almamalıdır.
4. IF `is_specific` `False` ise, THEN THE Uni_Info_Node SHALL mevcut genel üniversite analizi davranışını koruyarak çalışmaya devam etmelidir.

---

### Gereksinim 3: Kariyer Araştırmasında Genel/Spesifik Mod Ayrımı

**Kullanıcı Hikayesi:** Bir öğrenci olarak, "Bilgisayar mühendisliği mezunu Amazon'da çalışabilir mi?" gibi spesifik bir soru sorduğumda, sabit bir şablon yerine doğrudan o soruya yanıt almak istiyorum.

#### Kabul Kriterleri

1. WHEN kullanıcının `career` modundaki sorusu aşağıdaki sinyal kategorilerinden en az birini içerdiğinde — belirli bir şirket/işveren adı, spesifik bir pozisyon veya unvan, ülke/şehir bazlı iş koşulu, belirli bir ders/teknoloji adı veya "zor/kolay/tavsiye" gibi değerlendirme sıfatı — THE Specificity_Detector SHALL `is_career_specific` bayrağını `True` olarak işaretlemelidir.
2. WHEN kullanıcının sorusu yalnızca tek bir bölüm adından ve genel bir soru fiilinden (nasıl, nedir, anlat, hakkında) oluştuğunda, THE Specificity_Detector SHALL `is_career_specific` bayrağını `False` olarak işaretlemelidir.
3. WHEN `is_career_specific` `True` olduğunda, THE Career_Info_Node SHALL `human_content`'e `"Soru tipi: SPESİFİK — sadece sorulan konuyu cevapla, tam şablon doldurma"` satırını eklemeli ve web arama sorgularını kullanıcının ham sorusuyla + bölüm adıyla birleştirerek oluşturmalıdır.
4. WHEN `is_career_specific` `True` olduğunda, THE Career_Info_Node SHALL LLM'e gönderdiği sistem prompt'unda spesifik soru formatını aktive etmeli; LLM çıktısı yalnızca sorulan konuya odaklı bölümler içermeli, müfredat/istihdam şablonunu içermemelidir.
5. WHEN `is_career_specific` `False` olduğunda, THE Career_Info_Node SHALL mevcut üç sorgu (müfredat, kariyer/maaş, istihdam) ve tam şablon davranışını koruyarak çalışmaya devam etmelidir.
6. IF `is_career_specific` `True` iken bölüm adı (`dept`) tespit edilemezse, THEN THE Career_Info_Node SHALL yalnızca bir kez kullanıcıdan bölüm veya alan adını netleştiren bir mesaj döndürmeli; web araması veya LLM çağrısı yapmadan işlemi sonlandırmalıdır.

