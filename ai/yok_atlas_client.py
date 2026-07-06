# yok_atlas_client.py
# YENİ YÖK Atlas API client (2026+ endpoint).
# Eski yokatlas-py paketi POST /server_processing-atlas2016-TS-t4.php → 405 dönüyor.
# Yeni resmi endpoint: POST /api/tercih-kilavuz/search (JSON body).
#
# Bu modül:
# 1. Yeni endpoint'e konuşur (JSON in, JSON out).
# 2. Cevabı eski yokatlas-py "lisans result" formatına dönüştürür
#    (uni_adi, program_adi, tbs, taban, kontenjan, current_rank vs.)
#    böylece retrieve.py'daki mevcut işleme akışı değişmez.

import os
import json
from typing import Any, Optional

import httpx


YOK_API_URL = os.getenv(
    "YOKATLAS_API_URL",
    "https://yokatlas.yok.gov.tr/api/tercih-kilavuz/search",
)

_DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Origin": "https://yokatlas.yok.gov.tr",
    "Referer": "https://yokatlas.yok.gov.tr/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}


# ── Puan türü normalize: yokatlas-py içi formatından yeni API formatına ──
_PUAN_TURU_MAP = {
    "say": "SAY",
    "sayisal": "SAY",
    "sayısal": "SAY",
    "ea": "EA",
    "tm": "EA",
    "soz": "SOZ",
    "söz": "SOZ",
    "sozel": "SOZ",
    "sözel": "SOZ",
    "dil": "DIL",
    "tyt": "TYT",
}


def _normalize_puan_turu(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    return _PUAN_TURU_MAP.get(str(v).lower().strip(), None)


def _safe_int(v: Any) -> Optional[int]:
    """Güvenli int çevirme — None, '---', 'Dolmadı' vb. için None döner."""
    if v is None:
        return None
    try:
        s = str(v).replace(".", "").replace(",", "").strip()
        if not s or s in ("---", "Dolmadı", "None", "0", "..."):
            return None
        return int(float(s))
    except Exception:
        return None


def _transform_item(api_item: dict) -> dict:
    """Yeni API'nin tek bir kayıt formatını eski yokatlas-py formatına çevir.

    Eski format alanları (retrieve.py'nin beklediği):
      yop_kodu, uni_adi, program_adi, sehir, universite_turu, ucret_burs,
      tbs={'2025': '...'}, taban={'2025': '...'}, kontenjan={'2025': '...'},
      current_year, current_rank (clean_and_prioritize_years tarafından doldurulur)
    """
    year = str(api_item.get("yil") or "2025")

    # Üniversite + program isimleri
    uni_adi = api_item.get("universiteAdi") or ""
    program_adi = api_item.get("birimAdi") or api_item.get("birimGrupAdi") or ""

    # Şehir
    sehir = api_item.get("ilAdi") or api_item.get("uniIlAdi") or ""

    # Üniversite türü: "DEVLET" / "VAKIF" / "KKTC" / "YURT DIŞI"
    uni_turu_raw = (api_item.get("universiteTuru") or "").upper()
    if "VAKIF" in uni_turu_raw or "VAKIF" in uni_adi.upper():
        universite_turu = "Vakıf"
    elif "DEVLET" in uni_turu_raw:
        universite_turu = "Devlet"
    elif "KKTC" in uni_turu_raw or "KIBRIS" in uni_adi.upper():
        universite_turu = "KKTC"
    else:
        universite_turu = uni_turu_raw.capitalize() or "Devlet"

    # Burs / ücret durumu
    burs_adi = (api_item.get("bursOraniAdi") or "").strip()
    if "burslu" in burs_adi.lower() or "burslu" in program_adi.lower():
        ucret_burs = "Burslu"
    elif "%50" in burs_adi or "%50" in program_adi:
        ucret_burs = "%50 İndirimli"
    elif "%25" in burs_adi or "%25" in program_adi:
        ucret_burs = "%25 İndirimli"
    elif "ücretli" in burs_adi.lower() or "ücretli" in program_adi.lower():
        ucret_burs = "Ücretli"
    elif universite_turu == "Vakıf":
        ucret_burs = "Ücretli"
    else:
        ucret_burs = "Ücretsiz"

    # Sıralama, puan, kontenjan
    basari_sirasi = _safe_int(api_item.get("basariSirasi"))
    min_puan = api_item.get("minPuan") or ""
    kontenjan = api_item.get("kontenjan") or 0

    # YOP kodu (varsa)
    yop_kodu = str(api_item.get("kilavuzKodu") or api_item.get("birimId") or "")

    item = {
        "yop_kodu": yop_kodu,
        "uni_adi": uni_adi,
        "program_adi": program_adi,
        "sehir": sehir,
        "universite_turu": universite_turu,
        "ucret_burs": ucret_burs,
        "puan_turu": api_item.get("puanTuru") or "",
        "tbs": {year: str(basari_sirasi) if basari_sirasi else "---"},
        "taban": {year: str(min_puan) if min_puan else "---"},
        "kontenjan": {year: str(kontenjan)},
        "_raw": api_item,  # ihtiyaç olursa
    }
    return item


def _build_payload(params: dict) -> dict:
    """yokatlas-py wrapper'ı tarzı params dict → yeni API'nin JSON gövdesi.

    Beklenen params anahtarları (yokatlas-py uyumlu):
      program (str), universite (str), sehir (str),
      universite_turu (str), ucret (str), ogretim_turu (str),
      puan_turu (str), ust_bs (str/int), alt_bs (str/int),
      page (int), length (int).
    """
    puan = _normalize_puan_turu(params.get("puan_turu"))

    # Sıralama aralığı: yokatlas-py'de "ust_bs" daha iyi (küçük), "alt_bs" daha kötü (büyük)
    # Yeni API'de: minBasariSirasi (alt sınır), maxBasariSirasi (üst sınır)
    ust_bs = _safe_int(params.get("ust_bs"))
    alt_bs = _safe_int(params.get("alt_bs"))

    page = int(params.get("page") or 0)
    size = int(params.get("length") or 50)
    # Yeni API'de page 0-indexli; biz 1-indexli alıyor olabiliriz
    if page > 0 and params.get("start") is None:
        # page params olarak gelirse 1-indexli; 0-indexli'ye çevir
        page = max(0, page - 1)
    if params.get("start") is not None:
        page = int(int(params["start"]) / max(1, size))

    payload = {
        "filters": {
            "puanTuru": puan,
            "universiteId": [],
            "birimGrupId": [],
            "ilKodu": [],
            "birimTuruId": None,
            "bursOraniId": [],
            "kilavuzKodu": None,
            "maxBasariSirasi": alt_bs,  # daha kötü (büyük sayı)
            "minBasariSirasi": ust_bs,  # daha iyi (küçük sayı)
            "ogrenimTuruId": None,
            "universiteTuru": None,
        },
        "page": page,
        "size": min(size, 200),  # API tarafı 200'ün üstünü dönmeyebilir
        "sortBy": "basariSirasi",
        "direction": "ASC",
    }

    # Üniversite türü filtre
    uni_turu = (params.get("universite_turu") or "").upper()
    if "DEVLET" in uni_turu:
        payload["filters"]["universiteTuru"] = "DEVLET"
    elif "VAKIF" in uni_turu:
        payload["filters"]["universiteTuru"] = "VAKIF"

    # NOT: program, universite, sehir filtreleri yeni API'de ID bekliyor (birimGrupId vs.).
    # Bu ID'leri elde etmek için ayrı bir lookup API'si gerekir.
    # Şimdilik bu filtreleri kullanmıyoruz; client-side filtering yapacağız _filter_by_query'de.
    return payload


def _matches_query(item: dict, raw_text: str) -> bool:
    """Client-side filter: kullanıcı bir string verdiyse, gelen kaydın
    program/uni/sehir adı bu string ile uyumlu mu kontrol et."""
    if not raw_text:
        return True
    needle = str(raw_text).lower().strip()
    if not needle:
        return True
    hay = " ".join(
        [
            str(item.get("uni_adi") or ""),
            str(item.get("program_adi") or ""),
            str(item.get("sehir") or ""),
        ]
    ).lower()
    return needle in hay


def search_lisans_programs(params: dict, smart_search: bool = True) -> list:
    """yokatlas-py'nin search_lisans_programs fonksiyonunu replace eden modern client.

    Yeni resmi YÖK Atlas API'sini (POST /api/tercih-kilavuz/search) kullanır.
    Eski yokatlas-py params formatını alır, eski return formatını döndürür.
    Böylece retrieve.py'da koşullu hiçbir değişiklik gerekmez.
    """
    try:
        payload = _build_payload(params)
        all_items: list = []

        # Tek bir sayfa yetmeyebilir (limit 200). Birden fazla sayfa çekelim.
        max_pages = 5  # 1000 kayıt yeter
        for page_no in range(max_pages):
            payload["page"] = page_no
            try:
                resp = httpx.post(
                    YOK_API_URL,
                    json=payload,
                    headers=_DEFAULT_HEADERS,
                    timeout=30.0,
                    verify=False,
                    follow_redirects=True,
                )
            except Exception as e:
                print(f"[YOK_CLIENT] 💥 HTTP hatası: {e}")
                break

            if resp.status_code != 200:
                print(f"[YOK_CLIENT] ⚠️ HTTP {resp.status_code} sayfa {page_no}")
                break

            try:
                data = resp.json()
            except Exception as e:
                print(f"[YOK_CLIENT] ⚠️ JSON parse hatası: {e}")
                break

            page_content = data.get("content") or []
            if not page_content:
                break

            all_items.extend(page_content)
            if len(page_content) < payload["size"]:
                break  # son sayfa

        # Eski format'a çevir
        results = [_transform_item(it) for it in all_items]

        # Client-side filter: program/uni/sehir/ucret/universite_turu eşleşmesi
        program_q = params.get("program")
        uni_q = params.get("universite")
        sehir_q = params.get("sehir")
        ucret_q = (params.get("ucret") or "").lower().strip()
        uni_turu_q = (params.get("universite_turu") or "").lower().strip()

        def _ok(item: dict) -> bool:
            if program_q and not _matches_query(item, program_q):
                return False
            if uni_q and not _matches_query(item, uni_q):
                return False
            if sehir_q:
                # Şehir karşılaştırması spesifik alan üzerinden
                item_city = (item.get("sehir") or "").lower()
                if sehir_q.lower() not in item_city and item_city not in sehir_q.lower():
                    return False
            if ucret_q:
                ub = (item.get("ucret_burs") or "").lower()
                # %50 → "%50 İndirimli" eşleşir, "burslu" → "Burslu" eşleşir
                if ucret_q not in ub:
                    return False
            if uni_turu_q:
                ut = (item.get("universite_turu") or "").lower()
                if uni_turu_q not in ut:
                    return False
            return True

        if program_q or uni_q or sehir_q or ucret_q or uni_turu_q:
            results = [r for r in results if _ok(r)]

        print(f"[YOK_CLIENT] ✅ {len(results)} sonuç (ham {len(all_items)}, filtre sonrası)")
        return results

    except Exception as e:
        print(f"[YOK_CLIENT] 💥 Genel hata: {e}")
        return []
