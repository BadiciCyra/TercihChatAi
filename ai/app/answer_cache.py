"""
app/answer_cache.py — Cevap önbelleği (Redis).

Aynı/benzer soruları LLM çağrısı yapmadan cache'ten döndürür. Cache client'ı
`redis_client.cache` üzerinden call-time'da okur; böylece testler tek noktadan
mock'layabilir.
"""
import hashlib
import os
import re
from typing import Optional

from app import redis_client
from app.monitoring import (
    record_cache_hit,
    record_cache_miss,
    record_cache_set,
    record_error,
)

# Cache TTL — redis_client ile aynı env değişkeninden beslenir
_CACHE_TTL_SECONDS = redis_client._CACHE_TTL_SECONDS


def _normalize_for_cache(text: str) -> str:
    """Benzer soruları aynı cache key'e eşle: küçült, fazla boşluk kaldır,
    yaygın typo'ları normalize et.

    NOT: Kelimeler SIRALAMA olmadan korunur — Türkçe'de kelime sırası anlam
    değiştirir ("yazılım geliştirme nasıl" ≠ "nasıl yazılım geliştirme bölümü").
    Sadece küçük harf + Türkçe ASCII dönüşümü + noktalama temizliği yapılır.
    """
    if not text:
        return ""
    # tr_ascii: "İSTANBUL ÜNİVERSİTESİ" ile "istanbul üniversitesi" eskiden
    # FARKLI cache anahtarı üretiyordu ('i̇stanbul' → 'i stanbul'), yani aynı
    # soru iki kez cache'leniyor ve isabet oranı düşüyordu.
    from utils.text_norm import tr_ascii as _tr_ascii
    t = _tr_ascii(text).strip()
    # Sadece alfanumerik + boşluk kalsın
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    # Çoklu boşlukları teke indir — kelime sırasını KORU (sıralama yok)
    return ' '.join(t.split())


# Cache namespace versiyon: değiştirilirse tüm eski cache geçersiz olur (auto-invalidation)
# v4: score_type suffix toleransı + web supplement zorunlu
# v5: kelime sıralama normalizasyonu kaldırıldı, casual cevaplar cache'lenmiyor
# v6: uni_info_node spesifik sorgu düzeltmesi, planner max 2 alt soru
_CACHE_NS = os.getenv("AI_CACHE_NS", "v6")

# Cache'lenmemesi gereken "kötü cevap" göstergeleri (bunlar cache'e yazılmaz)
_BAD_ANSWER_MARKERS = (
    "bulunamadı",
    "kriterlere uygun",
    "teknik bir sorun",
    "an error occurred",
    "hata oluştu",
    "tekrar dene",
    "tekrar sorar mısınız",
    "yeterli kaynak",          # "web'den yeterli kaynak çıkmadı" fallback mesajları
    "yeterli bilgi",           # "yeterli bilgi toplayamadım" fallback mesajları
    "web araması yapamıyorum", # servis hatası fallback'leri
    # Casual/sohbet cevapları — bunlar kullanıcıya özel, cache'e girmemeli
    "kanka ",
    "kanka,",
    "üzülme",
    "hayal kırıklığı",
    "yalnız değilsin",
    "birlikte bakalım",
    "beraber bakalım",
)


def _is_bad_answer(answer: str) -> bool:
    if not answer:
        return True
    a = answer.lower()
    return any(m in a for m in _BAD_ANSWER_MARKERS)


# Soruda geçen spesifik konu kelimeleri — cevabın başlığında yoksa cache geçersiz say
_TOPIC_KEYWORDS = {
    "kulüp": ["kulüp", "topluluk", "sosyal"],
    "yurt": ["yurt", "barınak", "konaklama"],
    "staj": ["staj", "pratik", "internship"],
    "burs": ["burs", "indirim", "ücretsiz"],
    "ücret": ["ücret", "fiyat", "tl", "para"],
    "puan": ["puan", "taban", "sıralama"],
    "yorum": ["yorum", "deneyim", "öğrenci", "memnun"],
    "kariyer": ["kariyer", "iş", "mezun", "istihdam"],
    "müfredat": ["müfredat", "ders", "program", "eğitim"],
}


def _is_relevant_to_query(query: str, answer: str) -> bool:
    """Cache'ten gelen cevabın soruyla alakalı olup olmadığını kontrol et.

    Soruda spesifik bir konu kelimesi varsa (kulüp, yurt, staj vb.),
    cevabın ilk 800 karakterinde (başlık/özet kısmında) o konuya dair
    bir kelime geçmiyorsa alakasız say → cache miss.
    """
    q = query.lower()
    a_head = answer.lower()[:800]  # Sadece başlık/özet kısmına bak

    for topic, indicators in _TOPIC_KEYWORDS.items():
        if topic in q:
            if not any(ind in a_head for ind in indicators):
                print(f"[CACHE] ⚠️ Alakasız cache: soruda '{topic}' var ama cevap başında yok")
                return False
    return True


_MODE_PREFIX: dict = {
    "wizard":   "wiz",
    "research": "res",
    "career":   "car",
    None:       "ner",
    # guidance: cache kullanılmaz — _cache_key None döner
}


def _cache_key(mode: Optional[str], query: str) -> Optional[str]:
    """Mode-prefixed cache key üret.

    guidance modunda None döner (cache skip sinyali):
    cache_get / cache_set çağrıları None sonucunda işlemi atlamalıdır.

    Diğer modlar için format:
        "ai:answer:{_CACHE_NS}:{prefix}:{md5(normalized_query)}"
    Örnekler:
        wizard   → "ai:answer:v6:wiz:<md5>"
        research → "ai:answer:v6:res:<md5>"
        career   → "ai:answer:v6:car:<md5>"
        None/NER → "ai:answer:v6:ner:<md5>"
        guidance → None  (cache skip)
    """
    if mode == "guidance":
        return None
    prefix = _MODE_PREFIX.get(mode, "ner")
    norm = _normalize_for_cache(query)
    return f"ai:answer:{_CACHE_NS}:{prefix}:{hashlib.md5(norm.encode()).hexdigest()}"


def cache_get(query: str, mode: Optional[str] = None) -> "str | None":
    _cache = redis_client.cache
    if not _cache:
        return None
    key = _cache_key(mode, query)
    if key is None:
        # guidance modu veya başka cache-skip sinyali — okuma atla
        return None
    try:
        val = _cache.get(key)
        if val and _is_bad_answer(val):
            # Eski "bulunamadı" tarzı cache'i sil, taze cevap üretsin
            _cache.delete(key)
            print(f"[CACHE] 🗑️ Eski kötü cevap silindi: '{query[:50]}'")
            record_cache_miss()
            return None
        if val and not _is_relevant_to_query(query, val):
            # Cevap soruyla alakasız — sil ve yeniden hesaplat
            _cache.delete(key)
            print(f"[CACHE] 🗑️ Alakasız cache silindi: '{query[:50]}'")
            record_cache_miss()
            return None
        if val:
            print(f"[CACHE] ⚡ HIT: '{query[:50]}' → cache'ten dönülüyor")
            record_cache_hit()
        return val
    except Exception as e:
        print(f"[CACHE] get hatası: {e}")
        record_error(type(e).__name__, source="cache_get")
        return None


def cache_set(query: str, answer: str, mode: Optional[str] = None) -> None:
    _cache = redis_client.cache
    if not _cache or not answer:
        return
    key = _cache_key(mode, query)
    if key is None:
        # guidance modu veya başka cache-skip sinyali — yazma atla
        return
    if _is_bad_answer(answer):
        print(f"[CACHE] ⛔ Kötü cevap (bulunamadı/hata) cache'lenmedi: '{query[:50]}'")
        return
    # Çok kısa yanıtları cache'leme — fallback/hata mesajları genellikle kısadır
    if len(answer) < 200:
        print(f"[CACHE] ⛔ Çok kısa yanıt ({len(answer)} byte) cache'lenmedi: '{query[:50]}'")
        return
    try:
        _cache.setex(key, _CACHE_TTL_SECONDS, answer)
        print(f"[CACHE] 💾 SET: '{query[:50]}' ({len(answer)} byte, {_CACHE_TTL_SECONDS}sn)")
        record_cache_set()
    except Exception as e:
        print(f"[CACHE] set hatası: {e}")
        record_error(type(e).__name__, source="cache_set")
