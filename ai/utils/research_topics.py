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
    t = uni_adi.lower()
    for a, b in (("ç", "c"), ("ğ", "g"), ("ı", "i"), ("ö", "o"),
                 ("ş", "s"), ("ü", "u"), ("İ", "i")):
        t = t.replace(a, b)
    words = [w for w in "".join(c if c.isalnum() else " " for c in t).split()
             if len(w) > 3 and w not in _GENERIC_UNI_TOKENS]
    return words


def score_url_for_topic(url: str, topic_key: str, uni_toks: list[str] | None = None) -> int:
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
        if any(tok in u for tok in uni_toks):
            score += 12
        elif ".edu.tr" in u:
            score -= 20
    return score
