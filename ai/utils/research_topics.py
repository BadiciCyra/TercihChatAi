"""
utils/research_topics.py — Araştırma modunda taranacak konu başlıkları.

Neden gerekli: QueryPlanner tek bir genel sorgu kümesi üretiyor ve seçilen
sayfalar tek havuzdan geliyordu. Sonuçta "genel tanıtım" sayfaları baskın
çıkıp; ders programı, Erasmus, memnuniyet gibi başlıklar hiç taranmıyordu.

Burada her konunun KENDİ sorguları ve KENDİ sayfa kotası var; böylece her
başlık için en az bir kaynak toplanır.

Not: YÖK Atlas 2026 kılavuzunda bu bilgilerin çoğu YOK (eski .php detay
sayfaları kaldırıldı, site SPA'ya geçti). Akademik kadro/koşullar/akreditasyon
arama API'sinden geliyor (bkz. yok_atlas_client._extract_detay); aşağıdaki
konular ise web'den toplanmak zorunda.
"""
from __future__ import annotations

import unicodedata

# Resmi üniversite kaynakları — müfredat, Erasmus, kadro için en güvenilir
_OFFICIAL = (".edu.tr", "yok.gov.tr", "yokatlas.yok.gov.tr", "uniar.yok.gov.tr")
# Öğrenci deneyimi kaynakları
_STUDENT = ("eksisozluk.com", "unirehberi.com", "ogrenci.net", "sikayetvar.com",
            "unipedia.co", "universiterehberi.com")


class Topic:
    __slots__ = ("key", "label", "templates", "domains", "quota")

    def __init__(self, key, label, templates, domains=(), quota=1):
        self.key = key
        self.label = label
        self.templates = templates      # {uni} ve {bolum} yer tutucuları
        self.domains = domains          # tercih edilen domainler
        self.quota = quota              # bu konu için çekilecek sayfa sayısı


# Sıra ÖNEMLİ: üstteki konular kota dağıtımında öncelikli.
TOPICS: list[Topic] = [
    Topic(
        # Üniversitenin KENDİ sitesi birincil kaynaktır. Bu konu olmadan
        # aramalar universitekayit / dormhouse gibi üçüncü parti derleyici
        # sitelerle doluyor ve cevapta hiç resmi kaynak olmuyordu.
        "resmi_site", "Resmi Üniversite Sitesi",
        ["{uni} resmi web sitesi hakkımızda",
         "{uni} tanıtım fakülteler bölümler resmi"],
        _OFFICIAL, quota=2,
    ),
    Topic(
        "ders_programi", "Ders Programı / Müfredat",
        ["{uni} {bolum} ders planı müfredat bologna",
         "{uni} {bolum} ders içerikleri AKTS kataloğu"],
        _OFFICIAL, quota=2,
    ),
    Topic(
        "kadro", "Akademik Kadro",
        ["{uni} {bolum} öğretim üyeleri akademik kadro",
         "{uni} {bolum} bölüm hocaları akademik personel"],
        _OFFICIAL, quota=1,
    ),
    Topic(
        "erasmus", "Erasmus / Değişim Programları",
        ["{uni} erasmus değişim programı anlaşmalı üniversiteler",
         "{uni} {bolum} erasmus gidilebilen okullar"],
        _OFFICIAL, quota=1,
    ),
    Topic(
        "memnuniyet", "Memnuniyet / Öğrenci Deneyimi",
        ["{uni} üniversite memnuniyet araştırması sıralaması",
         "{uni} {bolum} öğrenci yorumları memnun mu pişman"],
        _STUDENT + ("uniar.yok.gov.tr",), quota=2,
    ),
    Topic(
        "kampus_yurt", "Kampüs / Yurt / Olanaklar",
        ["{uni} kampüs yurt imkanları ulaşım",
         "{uni} öğrenci kulüpleri sosyal olanaklar"],
        _STUDENT + _OFFICIAL, quota=1,
    ),
    Topic(
        "kariyer", "Mezuniyet / Kariyer",
        ["{uni} {bolum} mezun iş imkanları istihdam",
         "{uni} {bolum} staj olanakları sektör"],
        (), quota=1,
    ),
]

# ── KARİYER / BÖLÜM araştırması konuları ────────────────────────────────────
# uni_info üniversiteyi araştırır; career_info ise BÖLÜMÜ (Bilgisayar Müh.,
# Psikoloji ...). Bu yüzden ayrı bir konu kümesi gerekiyor: burada {bolum}
# ana özne, {uni} yok.
_KARIYER_KAYNAK = ("kariyer.net", "linkedin.com", "yenibiris.com", "secretcv.com",
                   "glassdoor.com", "indeed.com")
_RESMI_ISTATISTIK = ("yok.gov.tr", "istatistik.yok.gov.tr", "tuik.gov.tr",
                     "iskur.gov.tr", ".edu.tr")

CAREER_TOPICS: list[Topic] = [
    Topic(
        "is_imkanlari", "İş İmkânları / İstihdam",
        ["{bolum} mezunu iş imkanları hangi sektörlerde çalışır",
         "{bolum} istihdam oranı işsizlik YÖK istatistik"],
        _RESMI_ISTATISTIK + _KARIYER_KAYNAK, quota=2,
    ),
    Topic(
        "maas", "Maaş / Gelir",
        ["{bolum} mezunu maaş ne kadar kazanır",
         "{bolum} yeni mezun başlangıç maaşı 2026"],
        _KARIYER_KAYNAK, quota=2,
    ),
    Topic(
        "mufredat_c", "Müfredat / Dersler",
        ["{bolum} bölümü dersleri müfredat zor mu",
         "{bolum} ders içerikleri neler okutuluyor"],
        (".edu.tr",), quota=1,
    ),
    Topic(
        "mezun_deneyim", "Mezun Deneyimi",
        ["{bolum} okuyanlar pişman mı mezun yorumları",
         "{bolum} bölümü tavsiye eder misiniz deneyim"],
        _STUDENT, quota=2,
    ),
    Topic(
        "sektor_staj", "Sektör / Staj",
        ["{bolum} staj imkanları hangi firmalar",
         "{bolum} sektörde aranan yetkinlikler"],
        _KARIYER_KAYNAK, quota=1,
    ),
    Topic(
        "gelecek", "Gelecek / Akademik Devam",
        ["{bolum} bölümünün geleceği yapay zeka etkisi",
         "{bolum} yüksek lisans akademik kariyer yurtdışı"],
        (), quota=1,
    ),
]

# Konu anahtarı → Topic (hızlı erişim) — her iki küme birden
TOPIC_BY_KEY = {t.key: t for t in (TOPICS + CAREER_TOPICS)}


def build_career_topic_queries(bolum: str) -> list[tuple[str, list[str]]]:
    """Kariyer konuları için doldurulmuş sorgular: [(topic_key, [sorgu, ...]), ...]"""
    bolum = (bolum or "").strip()
    if not bolum:
        return []
    out: list[tuple[str, list[str]]] = []
    for t in CAREER_TOPICS:
        qs = []
        for tpl in t.templates:
            q = " ".join(tpl.format(bolum=bolum, uni="").split())
            if q and q not in qs:
                qs.append(q)
        if qs:
            out.append((t.key, qs))
    return out


def build_topic_queries(uni: str, bolum: str = "") -> list[tuple[str, list[str]]]:
    """Her konu için doldurulmuş sorguları döndür: [(topic_key, [sorgu, ...]), ...]

    bolum boşsa {bolum} yer tutucusu düşürülür (çift boşluk kalmaz).
    """
    uni = (uni or "").strip()
    bolum = (bolum or "").strip()
    out: list[tuple[str, list[str]]] = []
    for t in TOPICS:
        qs = []
        for tpl in t.templates:
            q = tpl.format(uni=uni, bolum=bolum)
            q = " ".join(q.split())  # {bolum} boşsa oluşan fazla boşlukları temizle
            if q and q not in qs:
                qs.append(q)
        if qs:
            out.append((t.key, qs))
    return out


# Üniversite adlarında ayırt edici olmayan kelimeler
_GENERIC_UNI_TOKENS = frozenset({
    "universitesi", "universite", "teknik", "buyuk", "ve", "of", "the",
    "ataturk", "cumhuriyet",  # çok yaygın; tek başına ayırt etmez
})


def uni_tokens(uni_adi: str) -> list[str]:
    """Üniversite adından ayırt edici tokenlar çıkar (URL eşlemesi için).

    "Boğaziçi Üniversitesi" → ["bogazici"]
    """
    if not uni_adi:
        return []
    # Türkçe 'İ'.lower() → 'i' + U+0307 (birleşen nokta) üretir. Nokta
    # temizlenmezse "İstanbul" → "i stanbul" olarak bölünüp token 'stanbul'
    # kalıyordu. Önce büyük harfleri elle eşle, sonra birleşen işaretleri at.
    t = uni_adi.replace("İ", "i").replace("I", "ı").lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    for a, b in (("ç", "c"), ("ğ", "g"), ("ı", "i"), ("ö", "o"),
                 ("ş", "s"), ("ü", "u")):
        t = t.replace(a, b)
    words = [w for w in "".join(c if c.isalnum() else " " for c in t).split()
             if len(w) > 3 and w not in _GENERIC_UNI_TOKENS]
    return words


def score_url_for_topic(url: str, topic_key: str, uni_toks: list[str] | None = None,
                        title: str = "") -> int:
    """URL'nin bu konu için ne kadar uygun olduğunu puanla (büyük = iyi)."""
    t = TOPIC_BY_KEY.get(topic_key)
    if not t or not url:
        return 0
    u = url.lower()
    score = 0
    for d in t.domains:
        if d in u:
            score += 10
            break
    # Resmi üniversite sayfaları müfredat/Erasmus/kadro için ekstra değerli
    if topic_key in ("ders_programi", "erasmus", "kadro") and ".edu.tr" in u:
        score += 8
    # PDF genelde ders planı / katalog demek
    if topic_key == "ders_programi" and u.endswith(".pdf"):
        score += 5

    # ── Alaka kontrolü ──────────────────────────────────────────────────
    # DDG "X üniversitesi müfredat" sorgusuna BAŞKA üniversitelerin
    # (yasar.edu.tr, gumushane.edu.tr ...) sayfalarını döndürebiliyor.
    # Doğru okulun alan adı geçiyorsa ödüllendir; .edu.tr olup hiçbir token
    # tutmuyorsa büyük olasılıkla başka bir üniversitedir → ağır ceza.
    if uni_toks:
        # Başlıkta üniversite adı geçiyor mu? Kısaltma alan adı kullanan
        # okulların (boun.edu.tr, ieu.edu.tr, metu.edu.tr) KENDİ siteleri
        # URL'den tanınamıyor; başlık bunu kurtarıyor. Bu kontrol olmadan
        # resmi siteleri "başka üniversite" sanıp eliyorduk.
        title_norm = _norm_tr(title)
        title_match = bool(title_norm) and all(tok in title_norm for tok in uni_toks)

        if _is_own_domain(u, uni_toks) or (".edu.tr" in u and title_match):
            # Üniversitenin KENDİ resmi sitesi her konuda birincil kaynak.
            score += 30
        elif any(tok in u for tok in uni_toks) or title_match:
            score += 12
        elif ".edu.tr" in u:
            score -= 20
    return score


def _norm_tr(s: str) -> str:
    """Başlık karşılaştırması için Türkçe-duyarlı normalize."""
    if not s:
        return ""
    t = s.replace("İ", "i").replace("I", "ı").lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    for a, b in (("ç", "c"), ("ğ", "g"), ("ı", "i"), ("ö", "o"),
                 ("ş", "s"), ("ü", "u")):
        t = t.replace(a, b)
    return t


def _is_own_domain(url_lower: str, uni_toks: list[str]) -> bool:
    """URL, bu üniversitenin kendi resmi alan adı mı?

    Resmi alan adları genelde tokenları BİTİŞİK yazar:
      "İstanbul Rumeli Üniversitesi" → istanbulrumeli.edu.tr
      "Boğaziçi Üniversitesi"        → boun.edu.tr (token tutmaz, es geçilir)
    """
    if ".edu.tr" not in url_lower:
        return False
    # Alan adı kısmını al (şema ve yoldan arındır)
    host = url_lower.split("//")[-1].split("/")[0]
    host_compact = host.replace("-", "").replace(".", "")
    if not uni_toks:
        return False
    # Tüm tokenlar bitişik halde alan adında geçiyorsa kendi sitesidir
    joined = "".join(uni_toks)
    if joined and joined in host_compact:
        return True
    # Tek ayırt edici token varsa (örn "hacettepe") o da yeterli
    if len(uni_toks) == 1 and uni_toks[0] in host_compact:
        return True
    return False
