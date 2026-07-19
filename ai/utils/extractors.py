import re
from typing import Optional, Any
from langchain_core.messages import HumanMessage
from utils.validators import get_msg_content

# Üniversite kısaltma → tam isim mapping
UNI_MAPPING = {
    "İTÜ": "İstanbul Teknik Üniversitesi",
    "ESTÜ": "Eskişehir Teknik Üniversitesi",
    "ODTÜ": "Orta Doğu Teknik Üniversitesi",
    "KÜ": "Koç Üniversitesi",
    "Koç": "Koç Üniversitesi",
    "SÜ": "Sabancı Üniversitesi",
    "Sabancı": "Sabancı Üniversitesi",
    "BÜ": "Bilkent Üniversitesi",
    "Bilkent": "Bilkent Üniversitesi",
    "ÖZÜ": "Özyeğin Üniversitesi",
    "Özyeğin": "Özyeğin Üniversitesi",
    "YÜ": "Yeditepe Üniversitesi",
    "Yeditepe": "Yeditepe Üniversitesi",
    "BEÜ": "Bahçeşehir Üniversitesi",
    "Bahçeşehir": "Bahçeşehir Üniversitesi",
    "AÜ": "Ankara Üniversitesi",
    "MEF": "MEF Üniversitesi",
    "İÜ": "İstanbul Üniversitesi",
    "MÜ": "Medipol Üniversitesi",
    "Medipol": "Medipol Üniversitesi",
    "BKÜ": "Başkent Üniversitesi",
    "Başkent": "Başkent Üniversitesi",
    "Haliç": "Haliç Üniversitesi",
    "Üsküdar": "Üsküdar Üniversitesi",
    "Acıbadem": "Acıbadem Üniversitesi",
    "Piri Reis": "Piri Reis Üniversitesi",
}

# Türkiye'nin 81 şehri + yaygın yazım hataları
_CITY_VARIANTS = {
    'istanbul': 'İstanbul', 'ist': 'İstanbul', 'istbul': 'İstanbul',
    'ankara': 'Ankara', 'ank': 'Ankara',
    'izmir': 'İzmir', 'izm': 'İzmir',
    'bursa': 'Bursa', 'antalya': 'Antalya', 'adana': 'Adana',
    'konya': 'Konya', 'gaziantep': 'Gaziantep', 'mersin': 'Mersin',
    'kayseri': 'Kayseri', 'eskişehir': 'Eskişehir', 'eskisehir': 'Eskişehir',
    'diyarbakır': 'Diyarbakır', 'diyarbakir': 'Diyarbakır',
    'samsun': 'Samsun', 'denizli': 'Denizli', 'şanlıurfa': 'Şanlıurfa',
    'sanliurfa': 'Şanlıurfa', 'urfa': 'Şanlıurfa',
    'malatya': 'Malatya', 'kahramanmaraş': 'Kahramanmaraş',
    'kahramanmaras': 'Kahramanmaraş', 'maraş': 'Kahramanmaraş', 'maras': 'Kahramanmaraş',
    'erzurum': 'Erzurum', 'van': 'Van', 'batman': 'Batman',
    'elazığ': 'Elazığ', 'elazig': 'Elazığ', 'sakarya': 'Sakarya',
    'manisa': 'Manisa', 'sivas': 'Sivas', 'balıkesir': 'Balıkesir',
    'balikesir': 'Balıkesir', 'tekirdağ': 'Tekirdağ', 'tekirdag': 'Tekirdağ',
    'trabzon': 'Trabzon', 'aydın': 'Aydın', 'aydin': 'Aydın',
    'muğla': 'Muğla', 'mugla': 'Muğla', 'afyon': 'Afyonkarahisar',
    'afyonkarahisar': 'Afyonkarahisar', 'çanakkale': 'Çanakkale',
    'canakkale': 'Çanakkale', 'kocaeli': 'Kocaeli', 'izmit': 'Kocaeli',
    'çorum': 'Çorum', 'corum': 'Çorum', 'kütahya': 'Kütahya',
    'kutahya': 'Kütahya', 'düzce': 'Düzce', 'duzce': 'Düzce',
    'rize': 'Rize', 'isparta': 'Isparta', 'bolu': 'Bolu',
    'kırıkkale': 'Kırıkkale', 'kirikkale': 'Kırıkkale',
    'kırklareli': 'Kırklareli', 'kirklareli': 'Kırklareli',
    'edirne': 'Edirne', 'ordu': 'Ordu', 'giresun': 'Giresun',
    'aksaray': 'Aksaray', 'amasya': 'Amasya', 'bingöl': 'Bingöl',
    'bingol': 'Bingöl', 'bitlis': 'Bitlis', 'bartın': 'Bartın',
    'bartin': 'Bartın', 'çankırı': 'Çankırı', 'cankiri': 'Çankırı',
    'hatay': 'Hatay', 'osmaniye': 'Osmaniye', 'kilis': 'Kilis',
    'karaman': 'Karaman', 'karabük': 'Karabük', 'karabuk': 'Karabük',
    'kars': 'Kars', 'iğdır': 'Iğdır', 'igdir': 'Iğdır',
    'ardahan': 'Ardahan', 'artvin': 'Artvin', 'gümüşhane': 'Gümüşhane',
    'gumushane': 'Gümüşhane', 'bayburt': 'Bayburt', 'erzincan': 'Erzincan',
    'tunceli': 'Tunceli', 'şırnak': 'Şırnak', 'sirnak': 'Şırnak',
    'hakkari': 'Hakkari', 'siirt': 'Siirt', 'mardin': 'Mardin',
    'muş': 'Muş', 'mus': 'Muş', 'ağrı': 'Ağrı', 'agri': 'Ağrı',
    'adıyaman': 'Adıyaman', 'adiyaman': 'Adıyaman', 'niğde': 'Niğde',
    'nigde': 'Niğde', 'nevşehir': 'Nevşehir', 'nevsehir': 'Nevşehir',
    'kırşehir': 'Kırşehir', 'kirsehir': 'Kırşehir', 'yozgat': 'Yozgat',
    'tokat': 'Tokat', 'çanakkale': 'Çanakkale', 'kastamonu': 'Kastamonu',
    'sinop': 'Sinop', 'zonguldak': 'Zonguldak', 'bilecik': 'Bilecik',
    'yalova': 'Yalova', 'usak': 'Uşak', 'uşak': 'Uşak',
    'burdur': 'Burdur',
}

_FOLLOWUP_PATTERNS = (
    "başka", "baska", "sadece", "ekstra", "daha", "bunlar mı", "bunlardan",
    "var mı", "yok mu", "diğer", "diger", "alternatif", "benzer", "farklı",
    "fark", "diğeri", "diğerleri", "şunlar", "bunlar", "bunu", "şunu",
    "peki", "ya da", "bunlardan başka", "yukarıdaki", "yukardaki",
    "listede", "tabloda", "geri kalan", "geri kalanı",
    "tane mi", "kadar mı", "hepsi mi", "tamamı mı",
)


def _extract_rank_from_text(text: str) -> Optional[str]:
    """Regex ile mesajdan ham sıralama yakala (NER kaçırırsa fallback).

    Desteklenen formatlar:
    - ``150k`` / ``150 k``  → "150000"
    - ``25 bin`` / ``25bin`` → "25000"
    - ``200000``             → "200000"  (ham rakam, 4-7 basamak)
    - ``50.000``             → "50000"   (Türkçe noktalı format)
    """
    if not text:
        return None
    # Format: 150k / 150 k
    m = re.search(r'(\d{1,3})\s*(?:k)\b', text, re.IGNORECASE)
    if m:
        return str(int(m.group(1)) * 1000)
    # Format: 25 bin / 25bin
    m = re.search(r'(\d{1,3})\s*bin\b', text, re.IGNORECASE)
    if m:
        return str(int(m.group(1)) * 1000)
    # Format: 50.000 / 150.000 (Turkish dot-separated thousands)
    m = re.search(r'\b(\d{1,3})\.(\d{3})\b', text)
    if m:
        return m.group(1) + m.group(2)
    # Format: 200000 (plain integer 4-7 digits)
    m = re.search(r'\b(\d{4,7})\b', text)
    if m:
        return m.group(1)
    return None


def _extract_program_from_text(text: str) -> Optional[str]:
    """Regex ile mesajdan ham bölüm yakala (NER kaçırırsa fallback).
    Önce bileşik isimleri (uzun) kontrol eder, sonra tekli kelimeler.
    Böylece 'havacılık elektrik elektroniği' → Elektrik değil, doğru bölüm dönüyor."""
    if not text:
        return None
    t = text.lower()

    # ÖNCELİK 1: Bileşik/spesifik isimler (uzun → kısa sırada)
    compound_aliases = [
        # Havacılık ailesi
        ('havacılık elektrik elektroniği', 'Havacılık Elektrik ve Elektroniği'),
        ('havacılık elektrik', 'Havacılık Elektrik ve Elektroniği'),
        ('havacılık elektroniği', 'Havacılık Elektrik ve Elektroniği'),
        ('uçak mühendisliği', 'Uçak Mühendisliği'),
        ('uçak müh', 'Uçak Mühendisliği'),
        ('uçak bakım', 'Uçak Bakım ve Onarım'),
        ('uzay mühendisliği', 'Uzay Mühendisliği'),
        ('havacılık ve uzay', 'Havacılık ve Uzay Mühendisliği'),
        ('havacılık yönetimi', 'Havacılık Yönetimi'),
        ('hava trafik', 'Hava Trafik Kontrolü'),
        ('sivil hava ulaştırma', 'Sivil Hava Ulaştırma İşletmeciliği'),
        ('pilotaj', 'Pilotaj'),
        ('pilot', 'Pilotaj'),
        # Endüstri ürün/sistem
        ('endüstri ürünleri tasarım', 'Endüstri Ürünleri Tasarımı'),
        ('endüstri sistemleri', 'Endüstri Sistemleri Mühendisliği'),
        # Bilgi/yazılım çeşitleri
        ('bilgisayar bilimleri', 'Bilgisayar Bilimleri'),
        ('yazılım geliştirme', 'Yazılım Geliştirme'),
        ('yazilim gelistirme', 'Yazılım Geliştirme'),
        ('yazılım mühendisliği', 'Yazılım Mühendisliği'),
        ('yapay zeka', 'Yapay Zeka Mühendisliği'),
        ('veri mühendisliği', 'Veri Mühendisliği'),
        ('veri bilimi', 'Veri Bilimi'),
        ('siber güvenlik', 'Siber Güvenlik'),
        ('bilişim sistemleri ve teknolojileri', 'Bilişim Sistemleri ve Teknolojileri'),
        ('bilişim teknolojileri', 'Bilişim Sistemleri ve Teknolojileri'),
        ('bilişim sistemleri', 'Yönetim Bilişim Sistemleri'),
        # Sağlık
        ('diş hekimliği', 'Diş Hekimliği'),
        ('beslenme ve diyetetik', 'Beslenme ve Diyetetik'),
        ('fizyoterapi', 'Fizyoterapi ve Rehabilitasyon'),
        ('hemşirelik', 'Hemşirelik'),
        ('ebelik', 'Ebelik'),
        ('odyoloji', 'Odyoloji'),
        ('ergoterapi', 'Ergoterapi'),
        ('sağlık yönetimi', 'Sağlık Yönetimi'),
        # Mühendislik aileleri
        ('elektrik elektronik mühendisliği', 'Elektrik-Elektronik Mühendisliği'),
        ('elektrik-elektronik', 'Elektrik-Elektronik Mühendisliği'),
        ('elektrik mühendisliği', 'Elektrik Mühendisliği'),
        ('elektronik mühendisliği', 'Elektronik Mühendisliği'),
        ('inşaat mühendisliği', 'İnşaat Mühendisliği'),
        ('makine mühendisliği', 'Makine Mühendisliği'),
        ('endüstri mühendisliği', 'Endüstri Mühendisliği'),
        ('kimya mühendisliği', 'Kimya Mühendisliği'),
        ('gıda mühendisliği', 'Gıda Mühendisliği'),
        ('çevre mühendisliği', 'Çevre Mühendisliği'),
        ('biyomedikal mühendislik', 'Biyomedikal Mühendisliği'),
        ('mekatronik', 'Mekatronik Mühendisliği'),
        ('jeoloji mühendisliği', 'Jeoloji Mühendisliği'),
        ('jeofizik', 'Jeofizik Mühendisliği'),
        ('petrol ve doğalgaz', 'Petrol ve Doğalgaz Mühendisliği'),
        ('maden mühendisliği', 'Maden Mühendisliği'),
        ('metalurji', 'Metalurji ve Malzeme Mühendisliği'),
        ('malzeme mühendisliği', 'Malzeme Mühendisliği'),
        ('tekstil mühendisliği', 'Tekstil Mühendisliği'),
        ('orman mühendisliği', 'Orman Mühendisliği'),
        ('ziraat mühendisliği', 'Ziraat Mühendisliği'),
        ('gemi mühendisliği', 'Gemi İnşaatı ve Gemi Makineleri Mühendisliği'),
        ('harita mühendisliği', 'Harita Mühendisliği'),
        # Eğitim
        ('matematik öğretmenliği', 'Matematik Öğretmenliği'),
        ('sınıf öğretmenliği', 'Sınıf Öğretmenliği'),
        ('okul öncesi', 'Okul Öncesi Öğretmenliği'),
        ('rehberlik ve psikolojik', 'Rehberlik ve Psikolojik Danışmanlık'),
        # Hukuk/Sosyal
        ('uluslararası ilişkiler', 'Uluslararası İlişkiler'),
        ('siyaset bilimi', 'Siyaset Bilimi ve Kamu Yönetimi'),
        ('kamu yönetimi', 'Kamu Yönetimi'),
        ('iletişim tasarımı', 'İletişim Tasarımı'),
        ('grafik tasarım', 'Grafik Tasarım'),
        ('iç mimarlık', 'İç Mimarlık'),
        ('şehir ve bölge', 'Şehir ve Bölge Planlama'),
        ('peyzaj mimarlığı', 'Peyzaj Mimarlığı'),
        # Sosyal
        ('moleküler biyoloji', 'Moleküler Biyoloji ve Genetik'),
        ('genetik', 'Moleküler Biyoloji ve Genetik'),
        ('biyokimya', 'Biyokimya'),
        ('antropoloji', 'Antropoloji'),
        ('sosyoloji', 'Sosyoloji'),
        ('felsefe', 'Felsefe'),
        ('gastronomi ve mutfak', 'Gastronomi ve Mutfak Sanatları'),
        ('gastronomi', 'Gastronomi ve Mutfak Sanatları'),
        ('mutfak sanatları', 'Gastronomi ve Mutfak Sanatları'),
        ('turizm ve otel', 'Turizm ve Otel İşletmeciliği'),
        ('turizm işletme', 'Turizm İşletmeciliği'),
        ('turizm rehberliği', 'Turizm Rehberliği'),
        # Kısaltmalar
        ('ybs', 'Yönetim Bilişim Sistemleri'),
        ('pdr', 'Rehberlik ve Psikolojik Danışmanlık'),
    ]

    for kw, full in compound_aliases:
        if kw in t:
            return full

    # ÖNCELİK 1.5: Öğrenci jargonu kısaltmaları — kelime sınırıyla eşleşir.
    # Substring kullanılamaz: "ceng" substring'i "Cengiz"i de yakalardı.
    # Ekler tolere edilir ("cengler", "cenge"), isimler dışlanır (cengiz, cengaver).
    slang_patterns = [
        (r'\bceng(?!iz|aver)\w*', 'Bilgisayar Mühendisliği'),   # "ceng", "cengler"
        (r'\bcs\b', 'Bilgisayar Mühendisliği'),
        (r'\beee\b', 'Elektrik-Elektronik Mühendisliği'),
        (r'\bmbg\b', 'Moleküler Biyoloji ve Genetik'),
        (r'\bie\b', 'Endüstri Mühendisliği'),
        (r'\bmakina\b', 'Makine Mühendisliği'),
        (r'\bpsiko\b', 'Psikoloji'),
        (r'\bbilg\.?\s*müh\w*', 'Bilgisayar Mühendisliği'),      # "bilg müh", "bilg. müh"
    ]
    for pattern, full in slang_patterns:
        if re.search(pattern, t):
            return full

    # ÖNCELİK 2: Tekli kelimeler (en son fallback)
    single_aliases = [
        ('bilgisayar', 'Bilgisayar Mühendisliği'),
        ('yazılım', 'Yazılım Mühendisliği'),
        ('yazlim', 'Yazılım Mühendisliği'),
        ('yazlm', 'Yazılım Mühendisliği'),
        ('endüstri', 'Endüstri Mühendisliği'),
        ('endustri', 'Endüstri Mühendisliği'),
        ('makine', 'Makine Mühendisliği'),
        ('inşaat', 'İnşaat Mühendisliği'),
        ('insaat', 'İnşaat Mühendisliği'),
        ('tıp', 'Tıp'),
        ('tip', 'Tıp'),
        ('hukuk', 'Hukuk'),
        ('diş', 'Diş Hekimliği'),
        ('dis', 'Diş Hekimliği'),
        ('psikoloji', 'Psikoloji'),
        ('mimar', 'Mimarlık'),
        ('eczacı', 'Eczacılık'),
        ('eczaci', 'Eczacılık'),
        ('veteriner', 'Veteriner'),
        ('işletme', 'İşletme'),
        ('isletme', 'İşletme'),
        ('iktisat', 'İktisat'),
        ('ekonomi', 'Ekonomi'),
        ('havacılık', 'Havacılık ve Uzay Mühendisliği'),
        ('havacilik', 'Havacılık ve Uzay Mühendisliği'),
        ('uçak', 'Uçak Mühendisliği'),
        ('ucak', 'Uçak Mühendisliği'),
        ('elektrik', 'Elektrik-Elektronik Mühendisliği'),
        ('elektron', 'Elektrik-Elektronik Mühendisliği'),
        ('biyoloji', 'Biyoloji'),
        ('kimya', 'Kimya'),
        ('fizik', 'Fizik'),
        ('matematik', 'Matematik'),
        ('gastronomi', 'Gastronomi ve Mutfak Sanatları'),
        ('turizm', 'Turizm ve Otel İşletmeciliği'),
        ('tarih', 'Tarih'),
        ('coğrafya', 'Coğrafya'),
        ('cografya', 'Coğrafya'),
        ('edebiyat', 'Türk Dili ve Edebiyatı'),
    ]
    for kw, full in single_aliases:
        if kw in t:
            return full
    return None


def _extract_city_from_text(text: str) -> Optional[str]:
    """Mesajdan şehir adı yakala. 'istanbulda', 'ankarada' gibi ek almış halleri de tanır.
    Eksiz hali de yakalar: 'İzmir burslu' → İzmir."""
    if not text:
        return None
    t = ' ' + text.lower() + ' '
    for variant in sorted(_CITY_VARIANTS.keys(), key=len, reverse=True):
        pattern = r'(?:^|[\s,\.\?!])' + re.escape(variant) + r'(?:da|de|ta|te|dan|den|tan|ten|a|e|ya|ye)?(?=[\s,\.\?!]|$)'
        if re.search(pattern, t):
            return _CITY_VARIANTS[variant]
    return None


def _extract_score_type_from_text(text: str) -> Optional[str]:
    """Mesajdan puan türü yakala: SAY, EA, SOZ, DIL, TYT.
    Basit substring kontrolü — regex word boundary sorunlarını tamamen atlatır."""
    if not text:
        return None
    t = text.lower()
    t_norm = (t.replace("ı", "i").replace("ö", "o").replace("ü", "u")
              .replace("ş", "s").replace("ğ", "g").replace("ç", "c"))

    say_markers = ("sayisal", "sayisalda", "sayisaldan", "sayısal", "sayisalla",
                   " say ", " sayda ", " sayla ", " sayi ", "sayisali")
    if any(m in t or m in t_norm for m in say_markers):
        return "SAY"

    soz_markers = ("sozel", "sözel", "sozelde", "sözelde", "sozelden", "sözelden",
                   " soz ", " söz ", "sozelli", "sözelli")
    if any(m in t or m in t_norm for m in soz_markers):
        return "SOZ"

    ea_markers = (" ea ", " ea'", " eada", " eade", " eadan", "eaden",
                  " tm ", " tm'", " tmde", "tmden",
                  "esit agirlik", "eşit ağırlık", "esit-agirlik", "eşit-ağırlık")
    if any(m in t or m in t_norm for m in ea_markers):
        return "EA"

    dil_markers = (" dil ", " dilde", " dilden", "yabanci dil", "yabancı dil")
    if any(m in t or m in t_norm for m in dil_markers):
        return "DIL"

    if " tyt " in (" " + t + " ") or " tyt " in (" " + t_norm + " "):
        return "TYT"

    return None


def _extract_fee_from_text(text: str) -> Optional[str]:
    """Mesajdan ücret/burs durumu yakala. Hem '%50' hem 'yüzde 50' yakalar."""
    if not text:
        return None
    t = text.lower()
    m = re.search(r'(?:%|y[üu]zde)\s*(25|50|75|100)\s*(?:lik|luk)?\s*(?:burs|indirim)?', t)
    if m:
        pct = m.group(1)
        if pct == "100":
            return "Burslu"
        return f"%{pct} İndirimli"
    if re.search(r'\bburslu\b|\btam\s*burs\b', t):
        return "Burslu"
    if re.search(r'\bücretsiz\b|\bucretsiz\b', t):
        return "Ücretsiz"
    if re.search(r'\bücretli\b|\bucretli\b|\bparalı\b|\bparali\b', t):
        return "Ücretli"
    return None


def _extract_uni_type_from_text(text: str) -> Optional[str]:
    """Mesajdan üniversite türü yakala: Devlet/Vakıf."""
    if not text:
        return None
    t = text.lower()
    if re.search(r'\bvakıf\b|\bvakif\b|\bözel\b|\bozel\b', t):
        return "Vakıf"
    if re.search(r'\bdevlet\b', t):
        return "Devlet"
    return None


def _extract_all_entities_from_text(text: str) -> dict:
    """Bir metinden tüm entity'leri çıkar — history scan için."""
    return {
        "rank": _extract_rank_from_text(text),
        "program": _extract_program_from_text(text),
        "city": _extract_city_from_text(text),
        "score_type": _extract_score_type_from_text(text),
        "fee_type": _extract_fee_from_text(text),
        "uni_type": _extract_uni_type_from_text(text),
    }


def _scan_history_for_entities(messages: list, current_idx: int = -1) -> dict:
    """Önceki HumanMessage'lardan entity çıkar (en yakın → en uzak)."""
    history_entities = {}
    for i in range(len(messages) - 2, -1, -1):
        msg = messages[i]
        is_user = isinstance(msg, HumanMessage) or (isinstance(msg, tuple) and msg[0] == "user")
        if not is_user:
            continue
        text = get_msg_content(msg)
        if not text:
            continue
        extracted = _extract_all_entities_from_text(text)
        for k, v in extracted.items():
            if v and not history_entities.get(k):
                history_entities[k] = v
        if all(history_entities.get(k) for k in ("rank", "program", "city")):
            break
    return history_entities


def _is_followup_question(text: str) -> bool:
    """Takip sorusu mu? (bağlam gerektiren, kendi başına yetersiz soru)"""
    if not text:
        return False
    t = text.lower().strip()
    if len(t.split()) < 12 and any(p in t for p in _FOLLOWUP_PATTERNS):
        return True
    return False


def _extract_uni_from_text(text: str) -> Optional[str]:
    """Mesajdan üniversite adı yakala (kısaltma + tam isim).
    Kısaltmalar için word boundary kontrolü — 'BÜ' substring'i 'Bülent' içinde eşleşmemeli.
    """
    if not text:
        return None

    # Kısaltmaları kontrol et — word boundary ile (kısaltma tek başına kelime olmalı)
    t_lower = text.lower()
    for short, full in UNI_MAPPING.items():
        s = short.lower()
        # Kısaltma kelime sınırlarıyla eşleşmeli (örn "BÜ" → "Bülent" içinde eşleşmemeli)
        import re as _re
        pattern = r'(?<![a-zçğışöüA-ZÇĞİŞÖÜ])' + _re.escape(s) + r'(?![a-zçğışöüA-ZÇĞİŞÖÜ])'
        if _re.search(pattern, t_lower):
            return full

    # "X üniversitesi" pattern'i
    import re as _re
    m = _re.search(r'([A-ZÇĞİÖŞÜa-zçğıöşü\s]+?)\s+üniversit', text, _re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        if len(name) >= 3:
            return f"{name} Üniversitesi"
    return None


async def _extract_program_llm_fallback(text: str) -> Optional[str]:
    """Regex alias tablosu başarısız olduğunda LLM ile bölüm adı çıkar.

    llm_ner (hızlı model) + RouterOutput.program field kullanılır.
    Sadece regex None döndürdüğünde çağrılmalı — ekstra token/süre harcar.

    Returns:
        Bölüm adı string'i veya None (tespit edilemezse).
    """
    if not text or not text.strip():
        return None

    try:
        # Lazy import — circular import'tan kaçın
        from models import llm_ner
        from schemas import RouterOutput
        from langchain_core.messages import SystemMessage, HumanMessage as _HM

        prompt = SystemMessage(content=(
            "Kullanıcının mesajından akademik bölüm veya program adını çıkar. "
            "Sadece program alanını doldur, diğer alanları boş bırak. "
            "Bölüm adı yoksa program=null döndür."
        ))
        structured = llm_ner.with_structured_output(RouterOutput)
        result: RouterOutput = await structured.ainvoke([
            prompt,
            _HM(content=text),
        ])
        program = getattr(result, "program", None)
        if program and len(program.strip()) >= 2:
            print(f"[EXTRACTOR] 🤖 LLM fallback bölüm tespit etti: {program!r}")
            return program.strip()
    except Exception as e:
        print(f"[EXTRACTOR] ⚠️ LLM fallback hatası: {e}")

    return None


async def _extract_uni_llm_fallback(text: str) -> Optional[str]:
    """Regex alias tablosu başarısız olduğunda LLM ile üniversite adı çıkar.

    llm_ner (hızlı model) + RouterOutput.uni field kullanılır.
    Sadece regex None döndürdüğünde çağrılmalı.
    """
    if not text or not text.strip():
        return None

    try:
        from models import llm_ner
        from schemas import RouterOutput
        from langchain_core.messages import SystemMessage, HumanMessage as _HM

        prompt = SystemMessage(content=(
            "Kullanıcının mesajından üniversite adını çıkar. "
            "Sadece uni alanını doldur, diğer alanları boş bırak. "
            "Üniversite adı yoksa uni=null döndür."
        ))
        structured = llm_ner.with_structured_output(RouterOutput)
        result: RouterOutput = await structured.ainvoke([
            prompt,
            _HM(content=text),
        ])
        uni = getattr(result, "uni", None)
        if uni and len(uni.strip()) >= 2:
            print(f"[EXTRACTOR] 🤖 LLM fallback üniversite tespit etti: {uni!r}")
            return uni.strip()
    except Exception as e:
        print(f"[EXTRACTOR] ⚠️ LLM fallback hatası: {e}")

    return None
