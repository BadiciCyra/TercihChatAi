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
import re
import unicodedata
import time
from concurrent.futures import ThreadPoolExecutor
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


# ══════════════════════════════════════════════════════════════════════════
# LOOKUP TABLOLARI — sunucu tarafı filtreleme için id çözümleme
#
# Eskiden program/üniversite/şehir filtreleri client-side yapılıyordu: API'den
# 1000'e kadar kayıt indirilip Python'da eleniyordu. Çoğu istek 0 sonuçla
# bitiyor ve fallback zinciri devreye giriyordu (ölçüm: ~6-11 sn, 14 çağrının
# 13'ü boşa).
#
# Bu uçlar birimGrupId / universiteId / ilKodu veriyor; bunları filtrede
# kullanınca API sadece ilgili kayıtları dönüyor (ölçüm: 0.33 sn, 1 çağrı).
# Ayrıca program listesi puanTuru içeriyor → 4 puan türünü taramaya gerek yok.
#
# Listeler statiktir; süreç içinde cache'lenir.
# ══════════════════════════════════════════════════════════════════════════

_LOOKUP_BASE = os.getenv(
    "YOKATLAS_LOOKUP_BASE", "https://yokatlas.yok.gov.tr/api/tercih-kilavuz"
)
_LOOKUP_TTL = int(os.getenv("YOKATLAS_LOOKUP_TTL", "86400"))  # 24 saat

_lookup_cache: dict = {"programs": None, "unis": None, "cities": None, "ts": 0.0}


def _norm(s: Any) -> str:
    """İsim karşılaştırması için normalize: küçük harf + Türkçe→ASCII + sadeleştirme.

    Dikkat: Türkçe 'İ'.lower() → 'i' + U+0307 (birleşen nokta) üretir. Bu işaret
    temizlenmezse "ÜNİVERSİTESİ" → "uni versi tesi" gibi bölünür ve eşleşme
    tutmaz. Bu yüzden birleşen işaretler ayıklanıyor.
    """
    if not s:
        return ""
    t = str(s).replace("İ", "i").replace("I", "ı").lower().strip()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.translate(str.maketrans("çğıöşüâî", "cgiosuai"))
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return " ".join(t.split())


def _fetch_lookup(path: str) -> list:
    """Lookup listesini çek. Hata olursa boş liste (çağıran eski yola düşer)."""
    try:
        r = httpx.get(
            f"{_LOOKUP_BASE}/{path}",
            headers=_DEFAULT_HEADERS,
            timeout=15.0,
            verify=False,
            follow_redirects=True,
        )
        if r.status_code != 200:
            print(f"[YOK_LOOKUP] ⚠️ {path} HTTP {r.status_code}")
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[YOK_LOOKUP] ⚠️ {path} alınamadı: {e}")
        return []


def _ensure_lookups() -> None:
    """Lookup tablolarını (gerekirse) doldur."""
    now = time.time()
    if _lookup_cache["programs"] is not None and (now - _lookup_cache["ts"]) < _LOOKUP_TTL:
        return

    programs = _fetch_lookup("universite-programlar")
    unis = _fetch_lookup("universiteler")
    cities = _fetch_lookup("universite-iller")

    prog_idx: dict = {}
    for p in programs:
        ad = p.get("birimGrupAdi")
        gid = p.get("birimGrupId")
        if not ad or gid is None:
            continue
        prog_idx.setdefault(_norm(ad), {"id": gid, "puan": p.get("puanTuru"), "ad": ad})

    uni_idx: dict = {}
    for u in unis:
        ad = u.get("universiteAdi")
        uid = u.get("universiteId")
        if ad and uid is not None:
            uni_idx.setdefault(_norm(ad), {"id": uid, "ad": ad})

    city_idx: dict = {}
    for c in cities:
        ad = c.get("ilAdi")
        kod = c.get("ilKodu")
        if ad and kod is not None:
            city_idx.setdefault(_norm(ad), {"id": kod, "ad": ad})

    if prog_idx:
        _lookup_cache.update(
            {"programs": prog_idx, "unis": uni_idx, "cities": city_idx, "ts": now}
        )
        print(
            f"[YOK_LOOKUP] ✅ Tablolar yüklendi: {len(prog_idx)} program, "
            f"{len(uni_idx)} üniversite, {len(city_idx)} il"
        )


def _match(index: dict, query: str) -> Optional[dict]:
    """İsimden kayda eşle: tam → başlangıç → içerik (en kısa aday kazanır)."""
    q = _norm(query)
    if not q or not index:
        return None
    if q in index:
        return index[q]
    starts = [k for k in index if k.startswith(q)]
    if starts:
        return index[min(starts, key=len)]
    contains = [k for k in index if q in k or k in q]
    if contains:
        return index[min(contains, key=len)]
    return None


def resolve_program(name: str) -> tuple[Optional[int], Optional[str]]:
    """Bölüm adı → (birimGrupId, puanTuru). Bulunamazsa (None, None)."""
    if not name:
        return None, None
    _ensure_lookups()
    hit = _match(_lookup_cache.get("programs") or {}, name)
    if not hit:
        return None, None
    return hit["id"], hit.get("puan")


def resolve_universite(name: str) -> Optional[int]:
    if not name:
        return None
    _ensure_lookups()
    hit = _match(_lookup_cache.get("unis") or {}, name)
    return hit["id"] if hit else None


def resolve_il(name: str) -> Optional[int]:
    if not name:
        return None
    _ensure_lookups()
    hit = _match(_lookup_cache.get("cities") or {}, name)
    return hit["id"] if hit else None


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
        # Aşağıdaki alanlar API cevabında ZATEN geliyor; eskiden atılıyordu.
        # Ek HTTP isteği gerektirmez — araştırma cevaplarının kalitesi için
        # doğrudan kullanılabilir.
        "detay": _extract_detay(api_item),
        "_raw": api_item,  # ihtiyaç olursa
    }
    return item


def _extract_detay(api_item: dict) -> dict:
    """search cevabındaki zengin alanları derle (akademik kadro, koşullar vs.).

    YÖK Atlas'ın eski .php detay sayfaları kaldırıldı (SPA'ya geçildi), ancak
    bu bilgilerin çoğu arama cevabında zaten mevcut.
    """
    def _i(key):
        v = api_item.get(key)
        return int(v) if isinstance(v, (int, float)) else None

    prof, doc = _i("prof"), _i("doc")
    dou, argor = _i("dou"), _i("arGor")
    kadro_toplam = sum(x for x in (prof, doc, dou, argor) if x)

    # Koşul metinleri: [{"18": "uzun metin"}, ...] → kısaltılmış liste
    kosullar = []
    for entry in (api_item.get("kosulList") or [])[:8]:
        if isinstance(entry, dict):
            for kod, metin in entry.items():
                t = " ".join(str(metin).split())
                kosullar.append({"kod": str(kod), "metin": t[:300]})

    detay = {
        "akademik_kadro": {
            "profesor": prof,
            "docent": doc,
            "dr_ogretim_uyesi": dou,
            "arastirma_gorevlisi": argor,
            "toplam": kadro_toplam or None,
        },
        "ogretim_dili": api_item.get("ogrenimDiliAdi") or None,
        "ogretim_turu": api_item.get("ogrenimTuruAdi") or None,
        "ogrenim_suresi_yil": _i("ogrenimSuresi"),
        "akreditasyon": api_item.get("akreditasyon") or None,
        "akreditasyon_aciklama": api_item.get("akreditasyonAck") or None,
        "tyc": api_item.get("tyc") or None,
        "basari_sirasi_sarti": api_item.get("minBasariSirasiKosul") or None,
        "fakulte": api_item.get("fymkAdi") or None,
        "ilce": api_item.get("ilceAdi") or None,
        "kosullar": kosullar,
    }
    # Boş alanları at — LLM context'ini şişirmesin
    return {k: v for k, v in detay.items() if v not in (None, "", [], {})}


def _build_payload(params: dict, resolved: Optional[dict] = None) -> dict:
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
            # DİKKAT: API'nin min/maxBasariSirasi alanları YERLEŞME sırasını
            # değil, programın "başarı sırası şartı"nı (baraj) filtreliyor.
            # Buraya kullanıcının sıralamasını yazmak neredeyse her istekte 0
            # kayıt döndürüyordu (ölçüldü: 14 aramanın 13'ü boş, ~6-11 sn kayıp).
            # Sıralama aralığı artık CLIENT-SIDE uygulanıyor (aşağıda).
            "maxBasariSirasi": None,
            "minBasariSirasi": None,
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

    # Sunucu tarafı filtreler — lookup tablolarından çözümlenen id'ler.
    # Çözümlenemezse ilgili filtre boş kalır ve client-side eleme devreye girer.
    if resolved:
        if resolved.get("birimGrupId"):
            payload["filters"]["birimGrupId"] = [resolved["birimGrupId"]]
        if resolved.get("universiteId"):
            payload["filters"]["universiteId"] = [resolved["universiteId"]]
        if resolved.get("ilKodu"):
            payload["filters"]["ilKodu"] = [resolved["ilKodu"]]
        # Program listesinden gelen puan türü, params'takinden daha güvenilir
        if not payload["filters"].get("puanTuru") and resolved.get("puanTuru"):
            payload["filters"]["puanTuru"] = resolved["puanTuru"]

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
        # ── 1) İsimleri id'ye çöz (sunucu tarafı filtreleme) ────────────────
        resolved: dict = {}
        try:
            gid, ptur = resolve_program(params.get("program") or "")
            if gid:
                resolved["birimGrupId"] = gid
                if ptur:
                    resolved["puanTuru"] = ptur
            uid = resolve_universite(params.get("universite") or "")
            if uid:
                resolved["universiteId"] = uid
            ilk = resolve_il(params.get("sehir") or "")
            if ilk:
                resolved["ilKodu"] = ilk
        except Exception as e:
            print(f"[YOK_CLIENT] ⚠️ id çözümleme atlandı: {e}")

        payload = _build_payload(params, resolved)
        all_items: list = []

        # ── 2) İlk sayfa: toplam kayıt sayısını da öğreniriz ────────────────
        def _fetch_page(page_no: int):
            p = dict(payload)
            p["page"] = page_no
            try:
                r = httpx.post(
                    YOK_API_URL, json=p, headers=_DEFAULT_HEADERS,
                    timeout=30.0, verify=False, follow_redirects=True,
                )
                if r.status_code != 200:
                    print(f"[YOK_CLIENT] ⚠️ HTTP {r.status_code} sayfa {page_no}")
                    return None
                return r.json()
            except Exception as e:
                print(f"[YOK_CLIENT] 💥 HTTP hatası (sayfa {page_no}): {e}")
                return None

        first = _fetch_page(0)
        if not first:
            return []
        all_items.extend(first.get("content") or [])

        size = payload["size"] or 200
        total = first.get("totalElements")
        if isinstance(total, int) and total > 0:
            need = (total + size - 1) // size          # toplam sayfa
        else:
            need = 5 if len(all_items) >= size else 1  # bilinmiyorsa eski varsayım
        need = min(need, 5)  # üst sınır korunuyor

        # ── 3) Kalan sayfalar PARALEL (eskiden sıralıydı: 1.67sn → 0.49sn) ──
        if need > 1 and len(all_items) >= size:
            with ThreadPoolExecutor(max_workers=min(4, need - 1)) as ex:
                for data in ex.map(_fetch_page, range(1, need)):
                    if data:
                        all_items.extend(data.get("content") or [])

        # Eski format'a çevir
        results = [_transform_item(it) for it in all_items]

        # Client-side filter — SADECE sunucuda filtrelenemeyen alanlar için.
        # birimGrupId/universiteId/ilKodu çözümlendiyse API zaten sadece ilgili
        # kayıtları döndü; aynı alanı tekrar elemek yanlış negatif üretir
        # (örn. "Bilgisayar Mühendisliği (İngilizce)" metin eşleşmesinde elenirdi).
        program_q = None if resolved.get("birimGrupId") else params.get("program")
        uni_q = None if resolved.get("universiteId") else params.get("universite")
        sehir_q = None if resolved.get("ilKodu") else params.get("sehir")
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

        # Sıralama aralığı — API tarafında filtrelenemediği için burada.
        # ust_bs = daha iyi (küçük sayı), alt_bs = daha kötü (büyük sayı).
        ust = _safe_int(params.get("ust_bs"))
        alt = _safe_int(params.get("alt_bs"))
        if ust or alt:
            before = len(results)
            def _in_range(item: dict) -> bool:
                tbs = item.get("tbs") or {}
                for v in tbs.values():
                    r = _safe_int(v)
                    if r is None:
                        continue
                    if ust and r < ust:
                        return False
                    if alt and r > alt:
                        return False
                    return True
                return False  # geçerli sıralaması olmayan kayıt aralık dışı
            results = [r for r in results if _in_range(r)]
            print(f"[YOK_CLIENT] 🎯 Sıralama aralığı [{ust or '-'}, {alt or '-'}]: {before} → {len(results)}")

        srv = ",".join(k for k in ("birimGrupId", "universiteId", "ilKodu") if resolved.get(k))
        print(
            f"[YOK_CLIENT] ✅ {len(results)} sonuç (ham {len(all_items)}, "
            f"sunucu filtresi: {srv or 'yok'})"
        )
        return results

    except Exception as e:
        print(f"[YOK_CLIENT] 💥 Genel hata: {e}")
        return []
