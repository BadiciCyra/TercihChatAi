import re
import unicodedata
from typing import Optional, Any
from langchain_core.messages import HumanMessage
from utils.validators import get_msg_content
from utils.text_norm import tr_lower, tr_ascii

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

    # Format: "1 milyon 300 bin" / "1,3 milyon" / "2 milyon"
    # ÖNCE bu kontrol edilmeli: aksi halde "1 milyon 300 bin" ifadesinden
    # sadece "300 bin" yakalanıp 1.300.000 yerine 300.000 okunuyordu.
    m = re.search(
        r'(\d+(?:[.,]\d+)?)\s*milyon(?:\s*(\d{1,3})\s*bin)?',
        text, re.IGNORECASE,
    )
    if m:
        try:
            milyon = float(m.group(1).replace(",", "."))
            toplam = int(milyon * 1_000_000)
            if m.group(2):
                toplam += int(m.group(2)) * 1000
            return str(toplam)
        except Exception:
            pass

    # Format: 1.300.000 (Türkçe noktalı, milyonlu)
    m = re.search(r'\b(\d{1,3})\.(\d{3})\.(\d{3})\b', text)
    if m:
        return m.group(1) + m.group(2) + m.group(3)

    # Format: 150k / 150 k  (1300k → 1.300.000 için 1-4 basamak)
    m = re.search(r'(\d{1,4})\s*(?:k)\b', text, re.IGNORECASE)
    if m:
        return str(int(m.group(1)) * 1000)

    # Format: 25 bin / 25bin / 1300 bin
    m = re.search(r'(\d{1,4})\s*bin\b', text, re.IGNORECASE)
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


def _extract_ogretim_turu_from_text(text: str) -> Optional[str]:
    """Mesajdan öğretim türü yakala: 'acik' | 'uzaktan' | 'orgun' | 'uolp' | None.

    YÖK Atlas 2026 verisinde ÖLÇÜLEN öğrenim türleri (başka tür yok;
    "İkinci Öğretim" artık mevcut değil):
      Örgün Öğretim   id=86   (5450 program)
      Uzaktan Öğretim id=182  (22)   — sıralama 2.253.998'e kadar
      Açık Öğretim    id=203  (6)    — sıralama 1.686.098'e kadar
      UOLP            id=188  (1)    — uluslararası ortak lisans

    Açık/uzaktan programlar örgün havuzda aranınca hiç sonuç çıkmıyordu.
    """
    if not text:
        return None
    t = tr_ascii(text)
    nospace = t.replace(" ", "")
    words = t.split()

    # Sıra önemli: "açık öğretim" ifadesi "öğretim" kelimesini de içerdiği için
    # önce spesifik olanlar kontrol edilir.
    if "acikogretim" in nospace or "aof" in words or "acik ogretim" in t:
        return "acik"
    if "uzaktan" in t or "online egitim" in t or "cevrimici" in nospace:
        return "uzaktan"
    if "uolp" in words or "ortak lisans" in t or "cift diploma" in t:
        return "uolp"
    # Örgün: "örgün", "normal öğretim", "yüz yüze", "kampüste okumak"
    if ("orgun" in nospace or "yuzyuze" in nospace
            or "normal ogretim" in t or "kampuste" in t):
        return "orgun"
    return None


def _extract_program_from_text(text: str) -> Optional[str]:
    """Regex ile mesajdan ham bölüm yakala (NER kaçırırsa fallback).
    Önce bileşik isimleri (uzun) kontrol eder, sonra tekli kelimeler.
    Böylece 'havacılık elektrik elektroniği' → Elektrik değil, doğru bölüm dönüyor."""
    if not text:
        return None
    # tr_lower: "İnşaat Mühendisliği".lower() birleşen nokta üretiyor ve
    # eşleşme kayıyordu — "İnşaat" sorgusu "Diş Hekimliği" döndürüyordu.
    t = tr_lower(text)

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
    # DİKKAT: "İzmir".lower() → "i̇zmir" (i + U+0307 birleşen nokta). Bu işaret
    # temizlenmezse _CITY_VARIANTS anahtarlarıyla ("izmir") eşleşme TUTMAZ ve
    # şehir filtresi sessizce uygulanmaz. Aynı tuzak daha önce üniversite adı
    # ve token çıkarımında da yaşandı.
    t = ' ' + tr_lower(text) + ' '
    # Kesme işareti ve uzun ekler DESTEKLENMELİ: "İzmir'de", "İstanbul'dakiler"
    # gibi yaygın yazımlar eskiden hiç eşleşmiyordu (kesme işareti desende yoktu,
    # 'daki/'dakiler ekleri de listede değildi) → şehir filtresi sessizce
    # uygulanmıyordu.
    _EK = (r"['’´`]?"
           r"(?:dakiler|dekiler|takiler|tekiler|daki|deki|taki|teki|"
           r"dan|den|tan|ten|da|de|ta|te|ya|ye|a|e)?")
    for variant in sorted(_CITY_VARIANTS.keys(), key=len, reverse=True):
        pattern = (r'(?:^|[\s,\.\?!])' + re.escape(variant) + _EK
                   + r"(?=[\s,\.\?!'’´`]|$)")
        if re.search(pattern, t):
            return _CITY_VARIANTS[variant]
    return None


def _extract_score_type_from_text(text: str) -> Optional[str]:
    """Mesajdan puan türü yakala: SAY, EA, SOZ, DIL, TYT.
    Basit substring kontrolü — regex word boundary sorunlarını tamamen atlatır."""
    if not text:
        return None
    # Büyük harfli girdiler ("SAY puanı", "EŞİT AĞIRLIK") eskiden hiç
    # yakalanmıyordu; tr_lower/tr_ascii bunu çözer.
    t = " " + tr_lower(text) + " "
    t_norm = " " + tr_ascii(text) + " "

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


# "istemiyorum", "olmasın", "hariç" gibi olumsuzlama sinyalleri
_NEG_PAT = (r"(?:isteme|istemiyor|olmasın|olmasin|istemem|hariç|haric|"
            r"dışında|disinda|değil|degil|çıkar|cikar|yok)")


def _extract_uni_type_from_text(text: str) -> Optional[str]:
    """Mesajdan üniversite türü yakala: Devlet/Vakıf.

    OLUMSUZLAMA destekli. Eskiden sadece kelime aranıyordu ve "vakıf" önce
    kontrol edildiği için "devlet olsun VAKIF İSTEMİYORUM" cümlesi "Vakıf"
    döndürüyordu — yani kullanıcının istediğinin tam TERSİ filtreleniyordu.
    """
    if not text:
        return None
    t = tr_lower(text)

    def _negated(kelime_pat: str, diger_pat: str) -> bool:
        """Olumsuzlama BU türe mi ait?

        "vakıf olsun devlet olmasın" cümlesinde 'olmasın' DEVLET'e aittir.
        Bu yüzden arada diğer tür kelimesi geçiyorsa eşleşme sayılmaz.
        """
        ara = r"(?:(?!" + diger_pat + r")[^.,;!?]){0,24}?"
        return bool(re.search(kelime_pat + ara + _NEG_PAT, t))

    vakif_pat = r'(?:\bvakıf\b|\bvakif\b|\bözel\b|\bozel\b)'
    devlet_pat = r'\bdevlet\b'
    vakif_var = bool(re.search(vakif_pat, t))
    devlet_var = bool(re.search(devlet_pat, t))

    # Olumsuzlanan tür, karşıtını ima eder
    if vakif_var and _negated(vakif_pat, devlet_pat):
        return "Devlet"
    if devlet_var and _negated(devlet_pat, vakif_pat):
        return "Vakıf"

    if vakif_var:
        return "Vakıf"
    if devlet_var:
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


# "X Üniversitesi" kalıbında ADIN PARÇASI OLAMAYACAK kelimeler.
# Selamlama/dolgu (kanka, selam...) ve jenerik niteleyiciler (yalnızca, devlet...)
# aksi halde üniversite adına yapışıp tüm arama sorgularını bozuyor.
_UNI_NAME_STOPWORDS = frozenset({
    # selamlama / hitap / dolgu
    "kanka", "selam", "merhaba", "slm", "mrb", "naber", "abi", "abla", "hocam",
    "ya", "yaa", "peki", "acaba", "bir", "bana", "bize", "sen", "siz", "ben",
    "lütfen", "lutfen", "şey", "sey", "de", "da", "ki", "ise", "ve", "ile",
    "için", "icin", "hakkında", "hakkinda", "bu", "şu", "su", "o",
    # soru / değerlendirme kelimeleri
    "hangi", "hangisi", "nasıl", "nasil", "nedir", "iyi", "kötü", "kotu",
    "en", "çok", "cok", "daha", "mi", "mı", "mu", "mü",
    # jenerik niteleyiciler
    "yalnızca", "yalnizca", "sadece", "sırf", "sirf", "devlet", "vakıf", "vakif",
    "özel", "ozel", "tüm", "tum", "bütün", "butun", "hepsi", "diğer", "diger",
})


def _tr_title(s: str) -> str:
    """Türkçe kurallara göre baş harfleri büyüt ('istanbul' → 'İstanbul').

    str.title() 'i' harfini 'I' yapıyor; Türkçe'de doğrusu 'İ'.
    """
    out = []
    for w in s.split():
        if not w:
            continue
        first = "İ" if w[0] == "i" else w[0].upper()
        out.append(first + w[1:])
    return " ".join(out)


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
    #
    # DİKKAT: Eski regex `([A-Za-z...\s]+?)\s+üniversit` idi. Karakter sınıfında
    # \s bulunduğu ve search leftmost eşleşmeyi aldığı için cümlenin BAŞINDAN
    # itibaren her şeyi yutuyordu:
    #   "Kanka selam istanbul rumeli Üniversitesi"
    #     → "Kanka selam istanbul rumeli Üniversitesi"
    # Bu ad tüm konu sorgularına giriyor ve aramalar boşa çıkıyordu.
    # Çözüm: "üniversite" kelimesinden ÖNCEKİ en fazla 3 kelimeyi al, baştaki
    # selamlama/dolgu kelimelerini at.
    # Konumu ORİJİNAL metin üzerinden bul. t_lower kullanılamaz: Türkçe 'İ'
    # küçültülünce 'i'+U+0307 (iki karakter) olur ve indeksler kayar — bu
    # yüzden "İstanbul Teknik Üniversitesi" → "İstanbul Teknik Ü" oluyordu.
    _m_uni = _re.search(r'[üu]niversit', text, _re.IGNORECASE)
    pos = _m_uni.start() if _m_uni else -1
    if pos > 0:
        before = text[:pos].strip()
        # Noktalama sonrası son cümleciği al ("Merhaba, Boğaziçi Üniversitesi")
        for sep in (",", ".", "!", "?", ":", ";"):
            if sep in before:
                before = before.rsplit(sep, 1)[-1]
        words = [w for w in before.split() if w]
        cand = words[-3:]  # üniversite adları genelde 1-3 kelime
        while cand and cand[0].lower().strip(".,!?:;'\"") in _UNI_NAME_STOPWORDS:
            cand.pop(0)
        while cand and cand[-1].lower().strip(".,!?:;'\"") in _UNI_NAME_STOPWORDS:
            cand.pop()
        name = " ".join(cand).strip()
        if len(name) >= 3:
            return f"{_tr_title(name)} Üniversitesi"
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
