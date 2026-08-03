"""
utils/text_norm.py — Türkçe-güvenli metin normalizasyonu.

NEDEN GEREKLİ:
Python'da "İ".lower() TEK karakter değil, İKİ karakter üretir:
    "İ".lower() == "i̇"   ('i' + U+0307 COMBINING DOT ABOVE)

Bu birleşen nokta metinde kalınca kelime eşleşmeleri SESSİZCE başarısız olur:
    "İstanbul".lower()  → "i̇stanbul"  ≠  "istanbul"
    "İŞLETME".lower()   → "i̇şletme"   ≠  "işletme"
Hata vermez, sadece eşleşme tutmaz — bu yüzden fark edilmesi zordur.

Bu tuzak projede defalarca ayrı ayrı ortaya çıktı:
  * üniversite adı çıkarımı  ("İstanbul Teknik Ü" gibi bozuk isimler)
  * research token eşleme    (token 'stanbul' olarak kalıyordu)
  * şehir tespiti            ("İzmir'de" hiç yakalanmıyordu)
  * bölüm tespiti            ("İnşaat Mühendisliği" → "Diş Hekimliği"!)
  * cevap cache anahtarı     (aynı soru iki farklı anahtara yazılıyordu)

Bu yüzden tek bir yerde toplandı. Kullanıcı metniyle eşleşme yapan HER YERDE
`.lower()` yerine buradaki fonksiyonlar kullanılmalı.
"""
from __future__ import annotations

import unicodedata

# Türkçe'ye özgü küçültme: büyük İ/I doğru karşılıklarına elle eşlenir.
_UPPER_MAP = str.maketrans({"İ": "i", "I": "ı"})

# Türkçe → ASCII sadeleştirme (arama/eşleşme için)
_ASCII_MAP = str.maketrans({
    "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
    "â": "a", "î": "i", "û": "u",
})


def _strip_combining(s: str) -> str:
    """Birleşen işaretleri (U+0307 vb.) ayıkla."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(ch))


def tr_lower(text: str) -> str:
    """Türkçe-güvenli küçük harf. Türkçe harfleri KORUR.

    "İSTANBUL" → "istanbul"   (birleşen nokta bırakmaz)
    "İŞLETME"  → "işletme"
    """
    if not text:
        return ""
    return _strip_combining(text.translate(_UPPER_MAP).lower())


def tr_ascii(text: str) -> str:
    """Türkçe-güvenli küçük harf + ASCII sadeleştirme.

    "İŞLETME" → "isletme"    "Boğaziçi" → "bogazici"
    Aksanlı/Türkçe yazım farklarına toleranslı eşleşme için.
    """
    if not text:
        return ""
    return tr_lower(text).translate(_ASCII_MAP)
