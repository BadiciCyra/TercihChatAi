"""
program_name.py — YÖK Atlas program adlarını tablo için kısaltır.

Amaç: Program adını dar bir tablo sütununa sığdırırken öğrenciyi YANILTACAK
kritik nitelemeleri (UOLP/uluslararası ortak program + partner üniversite,
ikinci öğretim, KKTC/M.T.O.K. kontenjanı, öğretim dili) ASLA gizlememek.

Örnek — eski körlemesine kesme (bol[:22]) şunu üretiyordu:
    "Bilgisayar Mühendisliği (İngilizce) (UOLP-Uluslararası Saraybosna
     Üniversitesi) (Ücretli)"  ->  "Bilgisayar Müh. (İngil.."
Bu, satırın aslında Saraybosna ortak programı olduğunu gizliyordu.

Bu modül sadece stdlib (re) kullanır; hem gateway (tools.py) hem retriever
(retrieve.py) tarafından güvenle import edilebilir (döngüsel import yok).
"""
import re

# "(UOLP-Uluslararası Saraybosna Üniversitesi)" -> "(UOLP-Saraybosna)"
_UOLP_RE = re.compile(r"UOLP-Uluslararası\s+(.+?)\s+Üniversitesi", re.IGNORECASE)
# Ücret etiketleri — Detay sütununda ayrıca gösterildiği için isimden düşülür
_FEE_RE = re.compile(r"\s*\((?:Ücretli|Burslu|%\s*\d+(?:\s*İndirimli)?)\)", re.IGNORECASE)
_PAREN_RE = re.compile(r"\([^)]*\)")


def format_program_adi(raw_name, max_len: int = 48) -> str:
    """Program adını tabloya sığacak şekilde kısaltır; kritik nitelemeleri korur.

    Adımlar:
      1. Yaygın uzun kelimeleri kısalt (Mühendisliği→Müh., Öğretmenliği→Öğr.).
      2. Uzun UOLP ifadesini kısalt ama KORU (yanıltmanın ana kaynağı budur).
      3. İkinci Öğretim→İÖ, Açıköğretim→AÖ.
      4. Ücret etiketlerini isimden çıkar (Detay sütununda zaten var).
      5. Hâlâ uzunsa: parantezli nitelemeleri KORUYARAK yalnızca taban adı kes.
    """
    s = str(raw_name or "-").strip()
    if s in ("", "-"):
        return "-"

    s = s.replace("Mühendisliği", "Müh.").replace("Öğretmenliği", "Öğr.")
    s = _UOLP_RE.sub(r"UOLP-\1", s)
    s = s.replace("İkinci Öğretim", "İÖ").replace("Açıköğretim", "AÖ")
    s = _FEE_RE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s).strip()

    if len(s) <= max_len:
        return s

    # Hâlâ uzun: parantezli nitelemeleri (UOLP, dil, kampüs...) ayır ve KORU;
    # yalnızca taban program adını kırp.
    quals = _PAREN_RE.findall(s)
    base = _PAREN_RE.sub("", s).strip()
    base = re.sub(r"\s{2,}", " ", base)
    tail = " ".join(quals)

    room = max_len - len(tail) - 1
    if room < 10:
        room = 10  # taban için asgari okunabilir alan
    if len(base) > room:
        base = base[:room].rstrip() + "…"

    return (base + (" " + tail if tail else "")).strip()
