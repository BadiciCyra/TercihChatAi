# Requirements Document

## Introduction

Bu özellik, TercihChatAi projesindeki `uni_info_node` ve `career_info_node` bileşenlerinde kullanılan sabit anahtar kelime listeleri ve şablon tabanlı web arama sorgularının yerine LLM tabanlı dinamik bir sınıflandırma ve sorgu üretim mekanizması getirir.

Mevcut yaklaşım; yazım varyantlarını kaçırma, yeni şirket/teknoloji adlarına karşı kırılgan olma ve kullanıcının gerçek sorusundan bağımsız sabit sorgular üretme gibi sorunlara yol açmaktadır. Bu özellik, hem sorgu sınıflandırmasını hem de sorgu üretimini tek bir LLM çağrısında (mevcut `llm_responder`) çözen paylaşımlı bir `classify_and_generate_queries` bileşeni sunar. Sonuçlar Redis'te önbelleğe alınır (1 saat TTL); LLM çağrısı başarısız olursa mevcut sabit davranışa geri döner.

## Glossary

- **QueryPlanner**: Kullanıcı metnini ve varlık adını alarak `QueryPlan` döndüren paylaşımlı bileşen (`ai/utils/query_planner.py`).
- **QueryPlan**: Sınıflandırma ve sorgu listesini taşıyan veri yapısı. `is_specific: bool`, `queries: list[str]`, `source: Literal["llm", "fallback"]` alanlarını içerir.
- **classify_and_generate_queries**: `QueryPlanner`'ın ana arayüz fonksiyonu. `(user_text: str, entity_name: str, mode: Literal["uni", "career"]) -> QueryPlan` imzasına sahiptir.
- **mode**: `QueryPlanner`'a hangi araştırma bağlamında çalışacağını bildiren parametre. `"uni"` (üniversite araştırması) veya `"career"` (kariyer/bölüm araştırması) değerlerini alır.
- **llm_responder**: Proje genelinde kullanılan LangChain Gemini sarmalayıcısı. `QueryPlanner` bu nesneyi constructor injection ile alır.
- **Cache**: Redis tabanlı önbellek (`_ents_cache`). TTL 3600 saniyedir.
- **Cache_Key**: `f"ai:query_plan:{mode}:{hashlib.sha256((user_text + entity_name).encode()).hexdigest()[:16]}"` formatında üretilen deterministik Redis anahtarı.
- **Fallback**: LLM çağrısı başarısız olduğunda `QueryPlanner`'ın devreye aldığı sabit sorgu üretim mantığı.
- **human_content_signal**: Her iki node'un LLM sentez çağrısına ilettiği `"Soru tipi: SPESİFİK — sadece sorulan konuyu cevapla"` veya `"Soru tipi: GENEL — tam analiz yap"` metin sinyali.

---

## Requirements

### Requirement 1: QueryPlan Veri Yapısı

**User Story:** Bir geliştirici olarak, sınıflandırma ve sorgu sonuçlarını taşıyan standart bir veri yapısına ihtiyaç duyuyorum; böylece node'lar arasında tutarlı bir arayüz sağlanır.

#### Acceptance Criteria

1. THE `QueryPlan` SHALL `is_specific` adında bir `bool` alanı içermelidir.
2. THE `QueryPlan` SHALL `queries` adında en az 1, en fazla 5 eleman içeren `list[str]` alanı içermelidir; her bir sorgu dizesi en fazla 500 karakter uzunluğunda olmalıdır.
3. THE `QueryPlan` SHALL `source` adında `Literal["llm", "fallback"]` türünde bir alan içermelidir.
4. IF `queries` listesi 0 eleman ya da 5'ten fazla eleman içeriyorsa, THEN THE `QueryPlan` oluşturulmamalı, çağıran taraf fallback yoluna yönlendirilmelidir.
5. IF `source` değeri `"llm"` ise, THEN THE `QueryPlan.queries` listesi 1 ile 5 arasında eleman içermeli ve `is_specific` değeri `True` ya da `False` olmalıdır.
6. IF `source` değeri `"fallback"` ise, THEN THE `QueryPlan.queries` listesi 1 ile 5 arasında eleman içermeli ve `is_specific` değeri mevcut sabit mantıktan türetilmiş olmalıdır.

---

### Requirement 2: LLM Prompt Tasarımı

**User Story:** Bir geliştirici olarak, LLM'nin hem sınıflandırma hem de sorgu üretimini tek seferde yapabilmesi için yapılandırılmış bir JSON çıktısına zorlayan bir prompt tasarımına ihtiyacım var.

#### Acceptance Criteria

1. THE `QueryPlanner` SHALL LLM'e gönderilen prompt'ta `user_text`, `entity_name` ve `mode` değerlerini içermelidir.
2. THE `QueryPlanner` SHALL LLM'den yalnızca aşağıdaki JSON şemasına uyan bir çıktı talep etmelidir:
   ```json
   { "is_specific": true, "queries": ["sorgu1", "sorgu2", "sorgu3"] }
   ```
3. WHEN `mode` değeri `"uni"` olduğunda, THE `QueryPlanner` SHALL prompt'ta şu bağlama özgü talimatları içermelidir: kampüs olanakları, burs ve harç bilgisi, taban puan/kontenjan, öğrenci yorumları, akademik kadro.
4. WHEN `mode` değeri `"career"` olduğunda, THE `QueryPlanner` SHALL prompt'ta şu bağlama özgü talimatları içermelidir: müfredat ve ders içerikleri, mezun maaş ve kariyer yolları, YÖK istihdam istatistikleri.
5. THE `QueryPlanner` SHALL LLM'den tam olarak 3 ile 5 arasında sorgu üretmesini talep etmelidir; bu aralık dışında kalan yanıtlar Requirement 4'teki fallback davranışını tetiklemelidir.
6. THE `QueryPlanner` SHALL sorgular içinde `entity_name` değerinin bulunmasını talep etmelidir; LLM bunu atlarsa Requirement 7 Criterion 3 devreye girer.
7. THE `QueryPlanner` SHALL prompt'u ve üretilen sorguları Türkçe olarak oluşturmalıdır.
8. IF `mode` değeri `"uni"` veya `"career"` dışında bir değer ise, THEN THE `QueryPlanner` SHALL LLM çağrısı yapmadan `source="fallback"` olan bir `QueryPlan` döndürmelidir.
9. IF LLM yanıtı `is_specific` veya `queries` alanlarından birini içermiyorsa, THEN THE `QueryPlanner` SHALL bu yanıtı geçersiz sayarak Requirement 4 Criterion 2'deki fallback yoluna yönlendirmelidir.

---

### Requirement 3: Cache Stratejisi

**User Story:** Bir geliştirici olarak, aynı kullanıcı metni + varlık adı + mod kombinasyonu için LLM'e tekrarlı çağrı yapmak istemiyorum; bu nedenle sonuçların Redis'te önbelleğe alınması gerekiyor.

#### Acceptance Criteria

1. THE `QueryPlanner` SHALL her istek için `f"ai:query_plan:{mode}:{hashlib.sha256((user_text + entity_name).encode()).hexdigest()[:16]}"` formatında deterministik bir cache anahtarı oluşturmalıdır.
2. WHEN Redis'te ilgili cache anahtarı için geçerli bir `QueryPlan`'a ayrıştırılabilen bir kayıt mevcutsa, THE `QueryPlanner` SHALL LLM çağrısı yapmadan cache'teki `QueryPlan`'ı döndürmelidir.
3. IF Redis'teki kayıt geçerli bir `QueryPlan`'a ayrıştırılamazsa (bozuk/uyumsuz veri), THEN THE `QueryPlanner` SHALL o kaydı yok sayarak LLM çağrısına devam etmelidir.
4. WHEN LLM çağrısı başarıyla tamamlanıp tüm zorunlu alanları dolu olan geçerli bir `QueryPlan` üretildiğinde, THE `QueryPlanner` SHALL sonucu Redis'e 3600 saniye TTL ile kaydetmelidir.
5. IF Redis bağlantısı mevcut değilse veya Redis okuma/yazma işlemi bir istisna fırlatırsa, THEN THE `QueryPlanner` SHALL istisnayı sessizce yakalayarak LLM çağrısına veya akışın geri kalanına kesintisiz devam etmelidir.

---

### Requirement 4: Fallback Davranışı

**User Story:** Bir geliştirici olarak, LLM çağrısı başarısız olduğunda sistemin mevcut sabit davranışa geçerek hizmet kesintisi yaşanmamasını istiyorum.

#### Acceptance Criteria

1. IF `llm_responder.ainvoke` çağrısı herhangi bir istisna fırlatırsa (ağ hatası, API zaman aşımı, kimlik doğrulama hatası dahil), THEN THE `QueryPlanner` SHALL `source="fallback"` olan bir `QueryPlan` döndürmelidir.
2. IF LLM yanıtı geçerli JSON içermiyorsa ya da `is_specific` veya `queries` alanlarından biri eksikse, THEN THE `QueryPlanner` SHALL `source="fallback"` olan bir `QueryPlan` döndürmelidir.
3. WHEN `mode` değeri `"uni"` ve fallback devredeyken, THE `QueryPlanner` SHALL şu yapıda en az 3 sorgu üretmelidir: `f"{entity_name} genel bilgi akademik kadro"`, `f"{entity_name} öğrenci yorumları"`, `f"{entity_name} burs ücret 2024 2025"`, `f"{entity_name} kariyer mezun iş imkânları"`.
4. WHEN `mode` değeri `"career"` ve fallback devredeyken, THE `QueryPlanner` SHALL şu yapıda tam olarak 3 sorgu üretmelidir: `f"{entity_name} bölümü müfredat ders içerikleri"`, `f"{entity_name} mezunu kariyer maaş iş ilanları"`, `f"{entity_name} istihdam oranı YÖK istatistik"`.
5. IF `mode` değeri `"uni"` veya `"career"` dışında bir değer ise ve fallback devredeyken, THEN THE `QueryPlanner` SHALL `queries=[f"{entity_name} hakkında bilgi"]` ve `is_specific=False` içeren bir `QueryPlan` döndürmelidir.
6. WHEN fallback devredeyken, THE `QueryPlanner` SHALL `[QUERY_PLANNER] ⚠️ Fallback — sebep: <hata mesajı>` formatında log kaydı tutmalıdır.

---

### Requirement 5: `uni_info_node` Entegrasyonu

**User Story:** Bir geliştirici olarak, `uni_info_node`'un sorgu oluşturma ve sınıflandırma mantığının `QueryPlanner`'ı kullanmasını istiyorum; böylece sabit anahtar kelime listesi ve `_build_uni_queries` fonksiyonu kaldırılır.

#### Acceptance Criteria

1. WHEN `uni_info_node` bir kullanıcı mesajını işlediğinde, THE `uni_info_node` SHALL üniversite adı ve kullanıcı metni ile `classify_and_generate_queries(user_text=user_text, entity_name=uni, mode="uni")` çağrısını yapmalıdır.
2. THE `uni_info_node` SHALL `QueryPlan.queries` listesini doğrudan web araması için kullanmalıdır.
3. THE `uni_info_node` SHALL `QueryPlan.is_specific` değerini Redis üniversite önbellek kararı ve `human_content_signal` belirleme amacıyla kullanmalıdır.
4. IF `QueryPlan.is_specific` değeri `False` ise, THEN THE `uni_info_node` SHALL mevcut `_uni_info_cache_key(uni)` Redis önbellek kontrolünü uygulamaya devam etmelidir.
5. THE `uni_info_node` SHALL `human_content` içinde `QueryPlan.is_specific` değerine göre tam olarak şu sinyallerden birini kullanmalıdır: `is_specific=True` → `"Soru tipi: SPESİFİK — sadece sorulan konuyu cevapla, genel üniversite analizi yapma"`, `is_specific=False` → `"Soru tipi: GENEL — tam üniversite analizi yap"`.
6. THE `uni_info_node` SHALL `_build_uni_queries` fonksiyonunu ve `_UNI_TOPIC_KEYWORDS` listesini kendi kodundan kaldırmalıdır; bu mantık `QueryPlanner` tarafından üstlenilir.
7. IF `classify_and_generate_queries` çağrısı başarısız olursa veya boş `queries` listesi döndürürse, THEN THE `uni_info_node` SHALL `"Bu üniversite için şu an arama yapamıyorum, lütfen tekrar dene."` içeren bir `AIMessage` döndürmelidir.

---

### Requirement 6: `career_info_node` Entegrasyonu

**User Story:** Bir geliştirici olarak, `career_info_node`'un sorgu oluşturma ve sınıflandırma mantığının `QueryPlanner`'ı kullanmasını istiyorum; böylece `_detect_career_specificity` ve sabit sinyal listeleri kaldırılır.

#### Acceptance Criteria

1. WHEN `career_info_node` bir kullanıcı mesajını işlediğinde, THE `career_info_node` SHALL bölüm adı ve kullanıcı metni ile `classify_and_generate_queries(user_text=user_text, entity_name=dept, mode="career")` çağrısını yapmalıdır.
2. THE `career_info_node` SHALL `QueryPlan.queries` listesini doğrudan web araması için kullanmalıdır.
3. THE `career_info_node` SHALL `QueryPlan.is_specific` değerini rota seçimi için kullanmalıdır: `is_specific=True` ve `dept` dolu → Yol C (spesifik); `is_specific=False` → Yol D (genel).
4. THE `career_info_node` SHALL `human_content` içinde `QueryPlan.is_specific` değerine göre tam olarak şu sinyallerden birini kullanmalıdır: `is_specific=True` → `"Soru tipi: SPESİFİK — sadece sorulan konuyu cevapla, tam şablon doldurma"`, `is_specific=False` → `"Soru tipi: GENEL — tam kariyer analizi yap"`.
5. THE `career_info_node` SHALL `_detect_career_specificity` fonksiyonunu ve tüm sinyal listelerini (`_COMPANY_SIGNALS`, `_POSITION_SIGNALS`, `_GEO_JOB_SIGNALS`, `_TECH_SIGNALS`, `_EVAL_SIGNALS`) kendi kodundan kaldırmalıdır.
6. THE `career_info_node` SHALL `QueryPlanner` entegrasyonundan sonra bölüm tespit edilemediğinde (dept boş) mevcut açıklama mesajı davranışını koruyarak sürdürmelidir: `is_career_specific=True` ve `dept=""` → Yol A açıklaması; `is_career_specific=False` ve `dept=""` → Yol B açıklaması.
7. IF `classify_and_generate_queries` çağrısı `dept` boşken yapılacaksa, THEN THE `career_info_node` SHALL `classify_and_generate_queries` çağrısını atlayarak doğrudan açıklama mesajı döndürmelidir.

---

### Requirement 7: Sorgu Kalitesi

**User Story:** Bir sistem tasarımcısı olarak, LLM tarafından üretilen sorguların kullanıcının gerçek sorusuna dayanan, varlık adı içeren ve web aramalarında kullanılabilir biçimde olmasını istiyorum.

#### Acceptance Criteria

1. THE `QueryPlanner` SHALL üretilen veya fallback ile oluşturulan her sorgunun en az 1 karakter uzunluğunda boş olmayan bir string olmasını garanti etmelidir; boş string içeren sorgular listeden çıkarılmalıdır.
2. IF LLM tarafından üretilen `queries` listesi 3'ten az veya 5'ten fazla eleman içeriyorsa, THEN THE `QueryPlanner` SHALL bu yanıtı geçersiz sayarak `source="fallback"` olan bir `QueryPlan` döndürmelidir.
3. IF LLM tarafından üretilen sorguların herhangi biri `entity_name` değerini (büyük/küçük harf duyarsız) içermiyorsa, THEN THE `QueryPlanner` SHALL o sorgunun başına `entity_name + " "` ekleyerek düzeltmelidir.
4. THE `QueryPlanner` SHALL büyük/küçük harf duyarsız (case-insensitive) karşılaştırma ile yinelenen sorguları listeden kaldırmalıdır; tekrar kaldırma sonrası liste 3'ten az sorgu içerirse fallback devreye girmelidir.

---

### Requirement 8: Test Edilebilirlik

**User Story:** Bir geliştirici olarak, `QueryPlanner` bileşenini LLM ve Redis'e gerçek bağlantı kurmadan test edebilmek istiyorum; böylece hızlı ve güvenilir bir test paketi elde ederim.

#### Acceptance Criteria

1. THE `QueryPlanner` SHALL constructor parametreleri aracılığıyla `llm_responder` ve `_ents_cache` bağımlılıklarını dışarıdan kabul etmelidir; testlerde bu parametrelere mock nesneler geçilebilmelidir.
2. WHEN `llm_responder.ainvoke` mock'lanarak `{"is_specific": true, "queries": ["q1","q2","q3"]}` JSON'unu içeren bir yanıt döndürdüğünde, THE `QueryPlanner` SHALL `source="llm"` ve `queries=["q1","q2","q3"]` içeren bir `QueryPlan` döndürmelidir.
3. WHEN `llm_responder.ainvoke` mock'lanarak herhangi bir istisna fırlatıldığında, THE `QueryPlanner` SHALL `source="fallback"` olan bir `QueryPlan` döndürmelidir ve istisna çağıran tarafa iletilmemelidir.
4. THE `QueryPlanner.classify_and_generate_queries` fonksiyonu, `user_text` en az 1 karakter olan bir string, `entity_name` en az 1 karakter olan bir string ve `mode` değeri `"uni"` ya da `"career"` olduğu sürece, her zaman bir `QueryPlan` nesnesi döndürmeli ve asla istisna fırlatmamalıdır.
5. THE `QueryPlan.queries` listesi, `classify_and_generate_queries` fonksiyonu tarafından döndürüldüğünde her zaman 1 ile 5 arasında eleman içermelidir; bu değişmez, LLM veya fallback yolundan bağımsız geçerlidir.
6. WHILE aynı `(user_text, entity_name, mode)` kombinasyonu için cache'de bir kayıt mevcutken `classify_and_generate_queries` ikinci kez çağrıldığında, THE `QueryPlanner` SHALL `llm_responder.ainvoke`'u çağırmadan cache'teki `QueryPlan`'ı döndürmelidir; döndürülen `QueryPlan`'ın `source` ve `queries` alanları ilk çağrıyla aynı olmalıdır.
