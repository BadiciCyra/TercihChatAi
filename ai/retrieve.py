# retrieve.py (FULL VERSION: NO LINES SKIPPED)
# İçerik: YÖK Atlas API + Smart Wrapper + Manuel Fallback + Web Search + Re-Ranker + Scraper + Metadata Fixer + Full Logging

# --- Standart Kütüphaneler ---
import asyncio
import json
import os
import re
import ssl
import sys
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

# --- Üçüncü Taraf Kütüphaneler ---
import aiohttp
import html2text
import httpx
import redis.asyncio as redis
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from fastapi import FastAPI
try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    async_playwright = None
    HAS_PLAYWRIGHT = False
    print("[SERVER] ⚠️ Playwright yüklü değil, JS scraping devre dışı")
from pydantic import BaseModel
from readability import Document

from program_name import format_program_adi

def filter_broken_markdown(content: str) -> str:
    """Bozuk/uzun markdown tablolarını ve gereksiz tekrarları filtreler."""
    if not content or len(content) < 80:
        return content
    # Çok uzun tablo satırlarını ve tekrarları temizle
    lines = content.splitlines()
    filtered = []
    seen = set()
    for line in lines:
        # Tablo satırı ise ve çok uzun ise atla
        if line.strip().startswith('|') and len(line) > 300:
            continue
        # Aynı satır tekrar ediyorsa atla
        key = line.strip()[:120]
        if key in seen:
            continue
        seen.add(key)
        filtered.append(line)
    # Markdown tablosu varsa, max 50 satırdan fazlasını at
    if any(l.strip().startswith('|') for l in filtered):
        table_lines = [l for l in filtered if l.strip().startswith('|')]
        if len(table_lines) > 50:
            table_start = filtered.index(table_lines[0])
            table_end = filtered.index(table_lines[-1])
            filtered = filtered[:table_start] + table_lines[:50] + filtered[table_end+1:]
    return '\n'.join(filtered)

# --- 1. KÜTÜPHANE VE WRAPPER KONTROLÜ ---
# 2026'da YÖK Atlas eski endpoint'i (server_processing-atlas2016-TS-t4.php) kaldırıldı,
# yeni endpoint /api/tercih-kilavuz/search'e geçti. Eski yokatlas-py paketi 405 alıyor.
# Bizim modern client'ımız (yok_atlas_client.py) yeni endpoint'i kullanıyor.
print("\n[INIT] 📦 Yeni YÖK Atlas client yükleniyor (modern API)...")
try:
    from yok_atlas_client import search_lisans_programs
    HAS_WRAPPER = True
    print("[INIT] ✅ 'yok_atlas_client' (yeni endpoint) yüklendi.")
except Exception as e:
    print(f"[INIT] ❌ HATA: yok_atlas_client yüklenemedi: {e}")
    HAS_WRAPPER = False
    def search_lisans_programs(*args, **kwargs): return []

# --- 2. CONFIG VE PATH AYARLARI ---
RERANKER_SERVICE_URL = os.environ.get('RERANKER_SERVICE_URL', 'http://127.0.0.1:8002/rerank')

# Config dosyasını bulmak için path ayarları
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from config import settings
except ImportError:
    try:
        # Bir üst dizine bak
        from config import settings
    except ImportError:
        # Docker path'i için mutlak yol ekle
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
        from config import settings

redis_client = None

# Global Playwright browser (lifecycle ile yönetilir)
pw_playwright = None
pw_browser = None

# --- LOG STREAM YARDIMCILARI ---
LOG_STREAM_KEY = "scraper:logs"

async def push_log(message: str, log_type: str = "info", extra: dict = None):
    """Log mesajını Redis stream'e ekler."""
    global redis_client
    if not redis_client:
        return
    log_entry = {
        "timestamp": str(datetime.utcnow()),
        "type": log_type,
        "message": message,
    }
    if extra:
        log_entry.update({f"extra_{k}": str(v) for k, v in extra.items()})
    try:
        await redis_client.xadd(LOG_STREAM_KEY, log_entry, maxlen=1000, approximate=True)
    except Exception as e:
        print(f"[LOG] Redis log yazılamadı: {e}")

async def get_recent_logs(limit: int = 50):
    """Son logları Redis stream'den okur."""
    global redis_client
    if not redis_client:
        return []
    try:
        logs = await redis_client.xrevrange(LOG_STREAM_KEY, count=limit)
        return [entry[1] for entry in logs]
    except Exception as e:
        print(f"[LOG] Redis log okunamadı: {e}")
        return []

# --- 3. YARDIMCI FONKSİYONLAR ---

def turkce_upper(text):
    """Türkçe karakterleri bozmadan büyütür."""
    if not text: return ""
    mapping = {'i': 'İ', 'ı': 'I', 'ğ': 'Ğ', 'ü': 'Ü', 'ş': 'Ş', 'ö': 'Ö', 'ç': 'Ç'}
    result = ""
    for char in text:
        result += mapping.get(char, char.upper())
    return result

def normalize_puan_turu(tur_list):
    """SAY, EA, SOZ gibi türleri normalize eder."""
    if not tur_list: return []
    tur = tur_list[0].lower().strip()
    mapping = {
        "soz": "söz", "söz": "söz", "sozel": "söz", "sözel": "söz",
        "say": "say", "sayısal": "say", "sayisal": "say",
        "ea": "ea", "tm": "ea", "esit agirlik": "ea", "eşit ağırlık": "ea",
        "dil": "dil", "yabancı dil": "dil"
    }
    return mapping.get(tur, tur)

def parse_burs_durumu(api_val, prog_name, default):
    """Metin içinden burs/ücret durumunu analiz eder."""
    text = (str(api_val) + " " + str(prog_name)).lower()
    if "burslu" in text or "tam burs" in text: return "Burslu"
    if "%50" in text or "50 indirim" in text: return "%50 İndirimli"
    if "%25" in text or "25 indirim" in text: return "%25 İndirimli"
    if "ücretli" in text: return "Ücretli"
    return default

def fix_uni_metadata(item):
    """
    API'den gelen eksik veya hatalı Üniversite Türü ve Burs bilgisini düzeltir.
    Özellikle Vakıf üniversitelerini isimlerinden tanır.
    """
    uni_adi = item.get("uni_adi", "")
    program_adi = item.get("program_adi", "")
    api_tur = item.get("universite_turu", "")
    api_ucret = item.get("ucret_burs", "")

    # Vakıf Üniversiteleri Anahtar Kelimeleri
    vakif_keywords = [
        "Vakıf", "Koç", "Sabancı", "Bilkent", "Özyeğin", "Yeditepe", "Bahçeşehir", 
        "Acıbadem", "Medipol", "Başkent", "Atılım", "Aydın", "Gelişim", "Beykent",
        "Nişantaşı", "Okan", "Işık", "Kadir Has", "Bilgi", "Ticaret", "Arel", 
        "Topkapı", "Esenyurt", "Piri Reis", "Kültür", "MEF", "Fatih Sultan", "Haliç",
        "Üsküdar", "Atlas", "Fenerbahçe", "Gedik", "İstinye", "Kent", "Maltepe"
    ]
    
    is_vakif = False
    
    # 1. API zaten Vakıf diyorsa
    if "Vakıf" in api_tur:
        is_vakif = True
    # 2. İsimde Vakıf keywordü geçiyor ve Devlet kelimesi yoksa
    elif any(vk.lower() in uni_adi.lower() for vk in vakif_keywords) and "Devlet" not in uni_adi:
        is_vakif = True
    # 3. KKTC ve Yurt Dışı kontrolü
    elif "KKTC" in api_tur or "Kıbrıs" in uni_adi:
        return "KKTC", parse_burs_durumu(api_ucret, program_adi, default="Ücretli")
    elif "Yurt Dışı" in api_tur:
        return "Yurt Dışı", "Ücretli"

    final_tur = "Vakıf" if is_vakif else "Devlet"
    
    # Vakıf ise varsayılan Ücretli, Devlet ise varsayılan Ücretsiz
    default_fee = "Ücretli" if is_vakif else "Ücretsiz"
    final_ucret = parse_burs_durumu(api_ucret, program_adi, default=default_fee)

    return final_tur, final_ucret

def _pick_best_year_rank(tbs_dict):
    """tbs sözlüğünden en güncel (en büyük) yılın geçerli sıralamasını seçer.

    YÖK Atlas veriyi her sene yeni yıla taşıyor (2025 → 2026 → ...). Yılı sabit
    yazmak yerine kayıttaki en yeni yılı dinamik seçiyoruz ki her sezon otomatik
    çalışsın. Geçerli sıralama bulunamazsa (None, None) döner.
    """
    invalid = {"---", "Dolmadı", "...", "None", "0", ""}
    # Yıl anahtarlarını sayısal olarak büyükten küçüğe sırala
    def _year_key(k):
        try:
            return int(k)
        except Exception:
            return -1
    for year in sorted(tbs_dict.keys(), key=_year_key, reverse=True):
        val = tbs_dict.get(year)
        if val is None or str(val).strip() in invalid:
            continue
        try:
            r = int(float(str(val).replace(".", "").replace(",", "")))
            return str(year), r
        except Exception:
            continue
    return None, None


def clean_and_prioritize_years(data_list):
    """
    Ham veriyi temizler, en güncel yılı (2026/2025/...) dinamik seçer ve
    metadata düzeltmelerini uygular.
    """
    print(f"[CLEANER] 🧹 {len(data_list)} adet ham veri işleniyor...")
    clean_list = []
    seen_programs = set() # Tekrar eden programları engellemek için

    for item in data_list:
        prog_code = item.get('yop_kodu', '')
        # Aynı YOP kodu geldiyse atla (Duplicate koruması)
        if prog_code and prog_code in seen_programs:
            continue
        seen_programs.add(prog_code)

        tbs_dict = item.get('tbs', {}) or {}

        # Kayıttaki en güncel yılı dinamik seç (2025/2026/... fark etmez).
        # Geçerli sıralama yoksa öğeyi atla.
        best_year, best_rank = _pick_best_year_rank(tbs_dict)
        if best_year is None:
            continue

        # Veriyi güncelle
        item['current_rank'] = best_rank
        item['current_year'] = best_year
        
        # Metadata düzeltmelerini uygula
        corrected_type, corrected_fee = fix_uni_metadata(item)
        item['uni_type'] = corrected_type
        item['burs'] = corrected_fee
        
        clean_list.append(item)
    
    print(f"[CLEANER] ✨ Temizlik bitti. Filtrelerden geçen veri sayısı: {len(clean_list)}")
    return clean_list


def generate_markdown_table_from_results(data_list):
    """Basit ve hafif bir Markdown tablo çıktısı üretir (tools.generate_markdown_table_python'a benzer).
    Bu fonksiyon, tools modülünü import etmek yerine retrieve içinde tanımlandı
    ki döngüsel import problemleri oluşmasın.
    """
    if not data_list:
        return "Veri bulunamadı."

    # Build rows as list of columns so we can compute column widths
    headers = ["Üniversite", "Bölüm", "Yıl", "Kont.", "Puan", "Sıralama", "Detay"]
    rows = []

    count = 0
    for item in data_list:
        try:
            if not isinstance(item, dict):
                continue

            uni_raw = str(item.get('uni_adi', '-'))
            uni_clean = uni_raw.replace("ÜNİVERSİTESİ", "Ü.").replace("YÜKSEK TEKNOLOJİ ENSTİTÜSÜ", "İYTE").replace("TEKNİK Ü.", "TÜ.")
            uni = f"**{uni_clean[:25]}**"

            # Program adını kısalt ama UOLP/ortak program, kampüs, İÖ gibi
            # öğrenciyi yanıltacak kritik nitelemeleri KORU (bkz. program_name.py)
            bol = format_program_adi(item.get('program_adi', '-'))

            yil = str(item.get('current_year', '-'))
            kont = str(item.get('kontenjan', {}).get(yil, '-')) if isinstance(item.get('kontenjan'), dict) else '-'

            puan_val = str(item.get('taban', {}).get(yil, '-'))[:6] if isinstance(item.get('taban'), dict) else '-'
            puan = f"_{puan_val}_"

            sira_val = str(item.get('current_rank', '-'))
            sira = f"**{sira_val}**" if sira_val not in ['-', '9999999', '0'] else '-'

            program_adi_tam = str(item.get('program_adi', '')).upper()
            api_tur = str(item.get("universite_turu", "")).upper()

            if "BURSLU" in program_adi_tam: ucret_detay = "%100"
            elif "%50" in program_adi_tam: ucret_detay = "%50"
            elif "%25" in program_adi_tam: ucret_detay = "%25"
            elif "ÜCRETLİ" in program_adi_tam: ucret_detay = "Paralı"
            else: ucret_detay = "Ücretsiz"

            if "KKTC" in api_tur: tur = "KKTC"
            elif "YURT DIŞI" in api_tur: tur = "YDışı"
            elif "VAKIF" in api_tur: tur = "Vakıf"
            elif any(k in uni_raw.upper() for k in ["KOÇ", "SABANCI", "BİLKENT", "BAHÇEŞEHİR", "YEDİTEPE", "ÖZYEĞİN", "MEDİPOL", "AYDIN", "GELİŞİM"]):
                tur = "Vakıf"
            else: tur = "Devlet"

            if tur == "Vakıf" and ucret_detay == "Ücretsiz": ucret_detay = "Paralı"

            sehir = str(item.get("sehir", "")).capitalize()
            detay = f"{sehir}, {tur}, {ucret_detay}"

            rows.append([uni, bol, yil, str(kont), puan, sira, detay])
            count += 1
        except:
            continue

    if count == 0:
        return "Veriler formatlanırken bir sorun oluştu."

    # Beautify table by computing column widths and padding
    def strip_md(s: str) -> str:
        return s.replace("**", "").replace("_", "")

    # compute widths
    cols = list(zip(*([headers] + rows)))
    widths = [max(len(strip_md(str(cell))) for cell in col) for col in cols]

    # build markdown lines with padding
    def pad(cell, w, align='left'):
        raw = str(cell)
        pad_len = w - len(strip_md(raw))
        if align == 'right':
            return ' ' * pad_len + raw
        elif align == 'center':
            left = pad_len // 2
            right = pad_len - left
            return ' ' * left + raw + ' ' * right
        return raw + ' ' * pad_len

    header_line = '| ' + ' | '.join(pad(h, widths[i], 'left') for i, h in enumerate(headers)) + ' |\n'
    align_line = '| ' + ' | '.join(('---:' if i in (4,5) else ':---' if i in (0,1) else ':---:') for i in range(len(headers))) + ' |\n'

    body = ''
    for r in rows:
        body += '| ' + ' | '.join(pad(r[i], widths[i], 'right' if i in (4,5) else 'left') for i in range(len(r))) + ' |\n'

    footer = f"\n*✅ Toplam {count} sonuç listelendi. (Veriler YÖK Atlas güncel taban puanlarıdır)*\n"
    return '\n\n' + header_line + align_line + body + footer

# --- 4. DIŞ SERVİSLER (SEARCH & RERANK) ---

async def search_ddg(query, is_review_search=False):
    """DuckDuckGo üzerinden Web veya Yorum araması yapar."""
    prefix = "🗣️ [YORUM]" if is_review_search else "🌍 [WEB]"
    print(f"[RETRIEVER] 🔍 DDG arama başlatılıyor: '{query}'")
    
    def _search():
        try:
            if is_review_search:
                final_query = (
                    query
                    + " site:eksisozluk.com OR site:unirehberi.com OR site:ogrnot.com"
                    + " OR yorum OR öğrenci deneyimi OR pişman"
                )
                timelimit = None
            else:
                final_query = query
                timelimit = 'y'
            limit = 15  # 30 → 15: daha az sonuç, daha hızlı yanıt
            
            start = time.time()
            print(f"[RETRIEVER] ⏳ DDGS().text() çağrılıyor... (freshness: {'son 1 yıl' if timelimit else 'tümü'})")
            results = list(DDGS().text(final_query, max_results=limit, region="tr-tr", timelimit=timelimit))
            elapsed = time.time() - start
            print(f"[RETRIEVER] ⏱️ DDG arama süresi: {elapsed:.2f} sn")
            
            if results:
                print(f"[RETRIEVER] {prefix} '{query}' -> ✅ {len(results)} sonuç bulundu.")
            else:
                print(f"[RETRIEVER] {prefix} '{query}' -> ⚠️ Sonuç YOK.")
            
            return [{"url": r['href'], "title": r['title'], "snippet": r['body'], "source_type": "web"} for r in results]
        except Exception as e:
            print(f"[RETRIEVER] ❌ Web Arama Hatası ('{query}'): {e}")
            traceback.print_exc()
            return []
    
    try:
        loop = asyncio.get_running_loop()
        # 10s → 25s'den daha kısa: agent pipeline'da 2 çağrı × 10s = 20s max
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _search),
            timeout=10.0
        )
        return result
    except asyncio.TimeoutError:
        print(f"[RETRIEVER] ⏰ DDG arama TIMEOUT (10s): '{query}'")
        return []
    except Exception as e:
        print(f"[RETRIEVER] ❌ DDG arama hatası: {e}")
        traceback.print_exc()
        return []

async def call_external_reranker(query: str, documents: List[Dict]):
    """Bulunan web sonuçlarını alaka düzeyine göre yeniden sıralar (fallback BM25 scoring)."""
    if not documents: return []
    
    payload = {"original_query": query, "documents": documents}
    
    print(f"[RETRIEVER] 📡 Re-Ranker Servisine ({RERANKER_SERVICE_URL}) Bağlanılıyor... ({len(documents)} doküman)")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(RERANKER_SERVICE_URL, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status == 200:
                    data = await response.json()
                    ranked_docs = data.get("ranked_documents", [])
                    if ranked_docs:
                        top_score = ranked_docs[0]['score']
                        print(f"[RETRIEVER] ⚡ Re-Ranker Başarılı! En iyi skor: {top_score:.4f}")
                        return [item["document"] for item in ranked_docs]
                    else:
                        print("[RETRIEVER] ⚠️ Re-Ranker boş liste döndürdü, fallback scoring.")
                        return _fallback_scoring(query, documents)
                else:
                    print(f"[RETRIEVER] ⚠️ Re-Ranker HTTP {response.status}, fallback scoring.")
                    return _fallback_scoring(query, documents)
    except Exception as e:
        print(f"[RETRIEVER] ❌ Re-Ranker Hatası: {e}, fallback scoring.")
        return _fallback_scoring(query, documents)

def _fallback_scoring(query: str, documents: List[Dict]) -> List[Dict]:
    """Basit token-match scoring (BM25 benzeri)."""
    q_tokens = set(re.findall(r'\w+', query.lower()))
    scored = []
    for doc in documents:
        text = ' '.join([str(doc.get(k, '')) for k in ('title', 'snippet', 'url')]).lower()
        score = sum(1 for t in q_tokens if t in text)
        scored.append((score, doc))
    scored.sort(reverse=True, key=lambda x: x[0])
    print(f"[RETRIEVER] 🔢 Fallback scoring: top score={scored[0][0] if scored else 0}")
    return [doc for _, doc in scored] 

# --- 5. ANA FONKSİYON: YÖK ATLAS ARAMASI ---

async def search_yok_atlas(entities):
    """
    YÖK Atlas verilerini çeker. Smart Wrapper, Fallback ve Filtreleme mekanizmalarını içerir.
    """
    print("\n" + "="*60)
    print("[RETRIEVER] 🏛️ YÖK ATLAS API SORGUSU BAŞLATILIYOR")
    
    # 1. Entity Kontrolleri
    if not HAS_WRAPPER:
        print("[RETRIEVER] ❌ KRİTİK HATA: Wrapper kütüphanesi eksik! Sorgu yapılamaz.")
        return None

    if not entities: 
        print("[RETRIEVER] ⚠️ HATA: Entities boş geldi. Ne arayacağımı bilmiyorum.")
        return None

    print(f"[RETRIEVER] 📥 Gelen Entityler: {entities}")

    # 2. Parametre Hazırlığı
    target_rank = None
    user_rank_list = entities.get("USER_RANK") or []
    if user_rank_list:
        try:
            # "120k" -> "120000" dönüşümü
            val = user_rank_list[0]
            raw_rank = str(val).lower().replace("k", "000").replace(".", "").replace(",", "").strip()
            if raw_rank.isdigit():
                target_rank = int(raw_rank)
                print(f"[RETRIEVER] 🎯 Hedef Sıralama Algılandı: {target_rank}")
        except: pass

    # Router'dan gelen listelerin ilk elemanlarını al
    program_query = (entities.get("BOL") or [""])[0]
    uni_query = (entities.get("UNI") or [""])[0]
    city_query = turkce_upper((entities.get("IL") or [""])[0])
    uni_type_filter = (entities.get("UNI_TYPE") or [""])[0]
    burs_filter = (entities.get("FEE_TYPE") or [""])[0]
    
    # Puan türü normalizasyonu
    normalized_type = normalize_puan_turu(entities.get("TUR"))
    # Eğer puan türü belirtilmemişse hepsini tara
    scan_types = [normalized_type] if normalized_type else ["say", "ea", "söz", "dil"]
    print(f"[RETRIEVER] 🔍 Taranacak Puan Türleri: {scan_types}")

    # Sıralama aralığı belirle (Smart Search için)
    if target_rank:
        api_ust_bs = int(target_rank * 0.95) # %5 daha iyi
        api_alt_bs = int(target_rank * 3.5)  # Epey altı (garanti olsun diye)
    else:
        api_ust_bs = ""
        api_alt_bs = ""

    all_results = []
    loop = asyncio.get_running_loop()

    # --- AŞAMA 1: SMART WRAPPER ARAMASI ---
    print("\n[RETRIEVER] --- Aşama 1: Smart Wrapper ---")
    
    for p_turu in scan_types:
        search_params = {
            "length": 1000, 
            "universite": uni_query, 
            "program": program_query, 
            "puan_turu": p_turu, 
            "sehir": city_query, 
            "universite_turu": uni_type_filter, 
            "ucret": burs_filter, 
            "ust_bs": str(api_ust_bs) if target_rank else "",
            "alt_bs": str(api_alt_bs) if target_rank else ""
        }
        
        try:
            # Boş parametreleri temizle (API'ye boş string gitmesin)
            clean_params = {k: v for k, v in search_params.items() if v}
            print(f"[RETRIEVER] ⚙️ Wrapper Parametreleri ({p_turu}): {clean_params}")
            
            if HAS_WRAPPER:
                # Bloklamayı önlemek için run_in_executor kullan
                results = await loop.run_in_executor(None, lambda: search_lisans_programs(clean_params, smart_search=True))
                
                if results: 
                    print(f"[RETRIEVER] ✅ Wrapper {len(results)} sonuç buldu.")
                    all_results.extend(results)
                else:
                    print(f"[RETRIEVER] ⚠️ Wrapper bu puan türü ({p_turu}) için sonuç bulamadı.")
        except Exception as e: 
            print(f"[RETRIEVER] 💥 Wrapper Hatası: {e}")
            traceback.print_exc()

    # --- AŞAMA 2: MANUAL FALLBACK (DÜZELTME) ---
    # Eğer hiç sonuç çıkmadıysa ve bir bölüm adı varsa, yaygın yanlışları düzeltip tekrar dene
    if not all_results and program_query:
        print(f"\n[RETRIEVER] ⚠️ Wrapper sonuç bulamadı, MANUEL DÜZELTME (Fallback) devreye giriyor...")
        
        # Yaygın kısaltmalar haritası
        keyword_map = {
            "bilgisayar": "Bilgisayar Mühendisliği", 
            "yazılım": "Yazılım Mühendisliği",
            "elektrik": "Elektrik-Elektronik Mühendisliği", 
            "elektronik": "Elektrik-Elektronik Mühendisliği",
            "makine": "Makine Mühendisliği", 
            "tıp": "Tıp Fakültesi", 
            "hukuk": "Hukuk Fakültesi", 
            "diş": "Diş Hekimliği", 
            "psikoloji": "Psikoloji", 
            "ybs": "Yönetim Bilişim Sistemleri",
            "pdr": "Rehberlik ve Psikolojik Danışmanlık"
        }
        
        q_lower = program_query.lower()
        # Kullanıcının girdiği kelime map'te geçiyor mu?
        simple_query = next((v for k, v in keyword_map.items() if k in q_lower), None)
        
        if simple_query:
            print(f"[RETRIEVER] 🔄 İsim Düzeltildi: '{program_query}' -> '{simple_query}'")

            for p_turu in scan_types:
                search_params["program"] = simple_query
                search_params["puan_turu"] = p_turu
                # Fallback'te aralığı biraz genişletelim veya kaldıralım (isteğe bağlı)
                try:
                    clean_params = {k: v for k, v in search_params.items() if v}
                    print(f"[RETRIEVER] ⚙️ Fallback Çağrısı ({p_turu}): {clean_params}")

                    res = await loop.run_in_executor(None, lambda: search_lisans_programs(clean_params, smart_search=False)) # Smart search kapalı deniyoruz bazen daha iyi sonuç verir
                    if res:
                        print(f"[RETRIEVER] ✅ Fallback ile {len(res)} sonuç kurtarıldı.")
                        all_results.extend(res)
                except Exception as e:
                    print(f"[RETRIEVER] 💥 Fallback Hatası: {e}")

    # --- AŞAMA 3: GENİŞ ALTERNATİF ARAMA ---
    # Sadece kullanıcı SPESİFİK bir bölüm İSTEMEDİYSE devreye girer.
    # (örn: "45k için ne gelir" gibi açık uçlu sorularda)
    # Kullanıcı belirli bir bölüm istediyse (program_query dolu) bu adımı ATLA;
    # alternatifleri agent kendi karar versin, biz başka bölüm karıştırmayalım.
    if not all_results and target_rank and not program_query:
        print(f"\n[RETRIEVER] 🔁 Aşama 3: Bölüm belirtilmedi, hedef sıralama ({target_rank}) için POPÜLER BÖLÜMLER taranıyor...")
        popular_programs = [
            "Bilgisayar Mühendisliği",
            "Yazılım Mühendisliği",
            "Endüstri Mühendisliği",
            "Elektrik-Elektronik Mühendisliği",
            "Makine Mühendisliği",
            "İnşaat Mühendisliği",
        ]
        wide_ust = int(target_rank * 0.70)
        wide_alt = int(target_rank * 1.50)
        for pop_prog in popular_programs:
            for p_turu in (scan_types if scan_types else ["say"]):
                widened = {
                    "length": 1000,
                    "program": pop_prog,
                    "puan_turu": p_turu,
                    "ust_bs": str(wide_ust),
                    "alt_bs": str(wide_alt),
                }
                try:
                    res = await loop.run_in_executor(None, lambda p=widened: search_lisans_programs(p, smart_search=True))
                    if res:
                        print(f"[RETRIEVER] ✅ {pop_prog} ({p_turu}): {len(res)} sonuç eklendi.")
                        all_results.extend(res)
                except Exception as e:
                    print(f"[RETRIEVER] 💥 Alternatif arama hatası ({pop_prog}): {e}")
        if all_results:
            print(f"[RETRIEVER] 🎯 Otomatik alternatiflerle {len(all_results)} sonuç toplandı.")
    elif not all_results and target_rank and program_query:
        # Kullanıcı spesifik bölüm istedi ama sonuç yok → o bölümde sıralamayı genişletip tekrar dene
        print(f"\n[RETRIEVER] 🔁 Aşama 3 (spesifik): '{program_query}' için sıralama aralığı genişletiliyor...")
        wide_ust = int(target_rank * 0.60)
        wide_alt = int(target_rank * 1.80)
        for p_turu in (scan_types if scan_types else ["say"]):
            widened = {
                "length": 1000,
                "program": program_query,
                "puan_turu": p_turu,
                "ust_bs": str(wide_ust),
                "alt_bs": str(wide_alt),
            }
            try:
                res = await loop.run_in_executor(None, lambda p=widened: search_lisans_programs(p, smart_search=True))
                if res:
                    print(f"[RETRIEVER] ✅ '{program_query}' geniş aralık ({p_turu}): {len(res)} sonuç.")
                    all_results.extend(res)
            except Exception as e:
                print(f"[RETRIEVER] 💥 Geniş arama hatası: {e}")

    # --- AŞAMA 4: SON ÇARE — Filtre tamamen kaldır, sadece bölüm adıyla ara ---
    # Önceki tüm aşamalar boş döndüyse ve elimizde bir program adı varsa,
    # rank filtresi koymadan, smart_search kapalı, tüm puan türleriyle dene.
    # Sonra hedef sıralamaya en yakınları seçeriz.
    if not all_results and program_query:
        print(f"\n[RETRIEVER] 🆘 Aşama 4 (son çare): '{program_query}' — TÜM filtreler kapalı, varyasyonlar deneniyor...")

        # Bölüm adı varyantları (yazım farklılıkları için)
        program_variants = [program_query]
        pq_lower = program_query.lower()
        if "elektrik" in pq_lower:
            program_variants += ["Elektrik-Elektronik Mühendisliği", "Elektrik Elektronik Mühendisliği", "Elektrik ve Elektronik Mühendisliği", "Elektrik Mühendisliği", "Elektronik Mühendisliği"]
        elif "bilgisayar" in pq_lower:
            program_variants += ["Bilgisayar Mühendisliği", "Bilgisayar Bilimleri", "Bilgisayar Bilimi"]
        elif "yazılım" in pq_lower or "yazlim" in pq_lower:
            program_variants += ["Yazılım Mühendisliği", "Bilgisayar ve Yazılım Mühendisliği"]
        elif "endüstri" in pq_lower or "endustri" in pq_lower:
            program_variants += ["Endüstri Mühendisliği", "Endüstri Sistemleri Mühendisliği"]
        # Duplicates kaldır
        program_variants = list(dict.fromkeys(program_variants))

        for variant in program_variants:
            if all_results:
                break  # Bir varyant tuttu, yeter
            for p_turu in ["say", "ea"]:  # Mühendislik genelde SAY, ihtimal EA
                bare = {
                    "length": 1000,
                    "program": variant,
                    "puan_turu": p_turu,
                }
                try:
                    res = await loop.run_in_executor(None, lambda p=bare: search_lisans_programs(p, smart_search=False))
                    if res:
                        print(f"[RETRIEVER] 🎯 Son çare BAŞARILI: '{variant}' ({p_turu}) → {len(res)} sonuç")
                        all_results.extend(res)
                        break
                except Exception as e:
                    print(f"[RETRIEVER] 💥 Son çare hatası ({variant}, {p_turu}): {e}")

        # Hedef sıralamaya en yakınları seç (eğer target_rank verildiyse)
        if all_results and target_rank:
            def _rank_diff(item):
                _, r = _pick_best_year_rank(item.get('tbs') or {})
                if r is None:
                    return 9999999
                return abs(r - target_rank)
            all_results.sort(key=_rank_diff)
            # En yakın 30'u tut, gerisi gürültü
            all_results = all_results[:30]
            print(f"[RETRIEVER] 📍 Hedef sıralama {target_rank}'a en yakın {len(all_results)} sonuç seçildi.")

    # 3. Sonuç Kontrolü
    print(f"\n[RETRIEVER] Toplam Ham Sonuç: {len(all_results)}")

    if not all_results:
        print("[RETRIEVER] 😔 Hiçbir aşamada sonuç bulunamadı.")
        print("="*60 + "\n")
        return None

    # 4. Temizlik ve İşleme
    cleaned_data = clean_and_prioritize_years(all_results)
    final_results = []

    # 5. Etiketleme + relevancy scoring
    for item in cleaned_data:
        rank = item.get('current_rank', 9999999)
        if rank == 9999999:
            continue

        # Durum etiketi
        if target_rank:
            diff = rank - target_rank
            if diff < -10000:
                item['durum'] = "⛰️ Çok Zorla"
            elif -10000 <= diff <= 5000:
                item['durum'] = "🎯 Hedef"
            elif 5000 < diff <= 25000:
                item['durum'] = "✅ Garanti"
            else:
                item['durum'] = "🛡️ Çok Garanti"

        # Relevancy score: rank yakınlığı + şehir eşleşmesi + puan türü
        rel_score = 0
        if target_rank:
            rank_dist = abs(rank - target_rank)
            rel_score += max(0, 100 - int(rank_dist / 1000))
        if city_query:
            item_city = (item.get('sehir') or '').upper()
            if city_query.upper() in item_city or item_city in city_query.upper():
                rel_score += 50   # Şehir eşleşmesi büyük bonus
        if normalized_type:
            item_puan = (item.get('puan_turu') or '').lower()
            if normalized_type.lower() in item_puan:
                rel_score += 20
        item['_rel_score'] = rel_score

        final_results.append(item)

    # 6. Relevancy score'a göre sırala, rank yakınlığı ikincil kriter
    if target_rank:
        final_results.sort(
            key=lambda x: (-x.get('_rel_score', 0), abs(x.get('current_rank', 9999999) - target_rank))
        )
    else:
        final_results.sort(key=lambda x: x.get('current_rank', 9999999))

    # 7. Sonuç sayısını sınırla — 1000 satır tablo kullanıcıya yardımcı değil
    MAX_RESULTS = int(os.getenv("YOK_MAX_RESULTS", "40"))
    if len(final_results) > MAX_RESULTS:
        if city_query:
            # Şehir eşleşenler önce, geri kalan slotları diğerleriyle doldur
            city_matches = [r for r in final_results if city_query.upper() in (r.get('sehir') or '').upper()]
            others = [r for r in final_results if r not in city_matches]
            remaining = max(10, MAX_RESULTS - len(city_matches))
            final_results = city_matches + others[:remaining]
            print(f"[RETRIEVER] 🏙️ Şehir filtresi: {len(city_matches)} şehir eşleşmesi + {len(others[:remaining])} diğer")
        else:
            final_results = final_results[:MAX_RESULTS]

    print(f"[RETRIEVER] 📤 Son Kullanıcıya Gidecek Veri Sayısı: {len(final_results)} (max={MAX_RESULTS})")
    print("="*60 + "\n")

    markdown_table = generate_markdown_table_from_results(final_results)

    return {
        "url": "https://yokatlas.yok.gov.tr",
        "title": "YÖK Atlas Verileri",
        "structured_content": json.dumps(final_results, indent=2, ensure_ascii=False),
        "markdown_table": markdown_table,
        "source_type": "api"
    }

# --- 6. API SERVER AYARLARI ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Server açılış/kapanış işlemleri (Redis + Playwright browser lifecycle)"""
    global redis_client, pw_playwright, pw_browser
    try:
        if hasattr(settings, 'REDIS_URL'):
            redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True, ssl_cert_reqs=ssl.CERT_NONE)
            print("[SERVER] 🔌 Redis bağlantısı başlatıldı.")
    except Exception as e:
        print(f"[SERVER] ⚠️ Redis başlatılamadı: {e}")

    try:
        if HAS_PLAYWRIGHT and async_playwright is not None:
            pw_playwright = await async_playwright().start()
            pw_browser = await pw_playwright.chromium.launch(headless=True)
            print("[SERVER] 🌐 Playwright Chromium başlatıldı (global browser).")
        else:
            print("[SERVER] ⚠️ Playwright yok, JS scraping atlanıyor.")
            pw_browser = None
    except Exception as e:
        print(f"[SERVER] ⚠️ Playwright başlatılamadı: {e}")
        pw_browser = None

    yield

    if pw_browser:
        await pw_browser.close()
        print("[SERVER] 👋 Playwright browser kapatıldı.")
    if pw_playwright:
        await pw_playwright.stop()

    if redis_client:
        await redis_client.close()
        print("[SERVER] 👋 Redis bağlantısı kapatıldı.")

app = FastAPI(title="Retriever Service (Full Version)", lifespan=lifespan)

# --- 7. Pydantic Modelleri ---

class RetrieveRequest(BaseModel):
    original_query: str
    optimized_queries: list = []
    extracted_entities: dict = {}
    retrieval_strategy: str = "HYBRID" # HYBRID, API_ONLY, GOOGLE_ONLY
    include_reviews: bool = False

class FetchContentRequest(BaseModel): 
    urls: list

# --- 8. ENDPOINTLER ---

@app.post("/retrieve")
async def retrieve(req: RetrieveRequest):
    """
    Ana endpoint. YÖK Atlas ve/veya Web araması yapar, sonuçları birleştirir ve döndürür.
    """
    await push_log(f"Yeni retrieve isteği: {req.original_query}", log_type="info", extra={"endpoint": "retrieve"})
    print(f"\n[RETRIEVER-API] 📨 Yeni İstek: {req.original_query}")
    tasks = []
    
    # 1. YÖK Atlas Araması (Eğer strateji uygunsa)
    if req.retrieval_strategy != "GOOGLE_ONLY":
        tasks.append(search_yok_atlas(req.extracted_entities))
    
    # 2. Web Araması (Eğer strateji uygunsa)
    if req.retrieval_strategy != "API_ONLY":
        # Optimize edilmiş sorguları kullan, yoksa orijinali al
        queries = req.optimized_queries[:3] if req.optimized_queries else [req.original_query]
        for q in queries:
            # Yorum araması mı kontrolü
            is_rev = req.include_reviews or any(x in q.lower() for x in ["yorum", "ekşi", "şikayet", "nasıl"])
            tasks.append(search_ddg(q, is_review_search=is_rev))
    
    # Görevleri paralel çalıştır
    results = await asyncio.gather(*tasks)
    
    flat_results = []
    web_results_to_rerank = []
    
    # Sonuçları ayrıştır
    for r in results:
        if not r: continue
        if isinstance(r, dict): 
            # Bu bir YÖK Atlas sonucudur (Dictionary döner)
            flat_results.append(r)
        elif isinstance(r, list): 
            # Bu bir Web sonucudur (Liste döner)
            web_results_to_rerank.extend(r)
    
    # 3. Re-Ranking (Sadece web sonuçları için)
    if web_results_to_rerank:
        ranked_web_results = await call_external_reranker(req.original_query, web_results_to_rerank)
        # En iyi 3 sonucu al
        flat_results.extend(ranked_web_results[:3])
    
    print(f"[RETRIEVER-API] 📦 Toplam {len(flat_results)} veri paketi Gateway'e gönderiliyor.")
    await push_log(f"Toplam {len(flat_results)} veri paketi Gateway'e gönderiliyor.", log_type="success", extra={"endpoint": "retrieve", "result_count": len(flat_results)})
    return {"data": flat_results}


# ═══════════════════════════════════════════════════════════════════════════════
# AYRIŞTIRILMIŞ RETRIEVAL ENDPOINTLERİ
# graph_agent.py ve tools.py bu endpoint'lere HTTP ile erişir — doğrudan import yok.
# ═══════════════════════════════════════════════════════════════════════════════

class YokAtlasRequest(BaseModel):
    """YÖK Atlas sorgusu için entity parametreleri."""
    rank:       Optional[str] = None
    program:    Optional[str] = None
    uni:        Optional[str] = None
    city:       Optional[str] = None
    uni_type:   Optional[str] = None
    score_type: Optional[str] = None
    fee_type:   Optional[str] = None


class WebSearchRequest(BaseModel):
    """Web arama isteği."""
    query:            str
    queries:          List[str] = []   # Çoklu paralel sorgu
    query_type:       str = "specific" # general | academic | reviews | specific
    max_results:      int = 8
    is_review_search: bool = False
    rerank:           bool = True      # Sonuçları re-rank et


@app.post("/retrieve/yok_atlas")
async def retrieve_yok_atlas(req: YokAtlasRequest):
    """
    YÖK Atlas'tan program/sıralama verisi çeker.
    graph_agent.py ve tools.py bu endpoint'i kullanır — doğrudan import yok.

    Döndürür:
      markdown_table     : Hazır Markdown tablo string'i (LLM'e doğrudan verilebilir)
      structured_content : JSON array string (ileri işleme için)
      result_count       : Sonuç sayısı
    """
    entities: Dict[str, Any] = {}
    if req.rank:       entities["USER_RANK"] = [req.rank]
    if req.program:    entities["BOL"]       = [req.program]
    if req.uni:        entities["UNI"]       = [req.uni]
    if req.city:       entities["IL"]        = [req.city]
    if req.uni_type:   entities["UNI_TYPE"]  = [req.uni_type]
    if req.score_type: entities["TUR"]       = [req.score_type]
    if req.fee_type:   entities["FEE_TYPE"]  = [req.fee_type]

    if not entities:
        return {
            "markdown_table": None,
            "structured_content": "[]",
            "result_count": 0,
            "error": "En az bir parametre (rank, program, uni, city) gerekli",
        }

    try:
        result = await search_yok_atlas(entities)
        if not result:
            return {"markdown_table": None, "structured_content": "[]", "result_count": 0}
        structured = result.get("structured_content", "[]")
        try:
            count = len(json.loads(structured))
        except Exception:
            count = 0
        return {
            "markdown_table":     result.get("markdown_table"),
            "structured_content": structured,
            "result_count":       count,
            "source_url":         result.get("url", "https://yokatlas.yok.gov.tr"),
        }
    except Exception as e:
        print(f"[RETRIEVER-API] 💥 /retrieve/yok_atlas hatası: {e}")
        return {"markdown_table": None, "structured_content": "[]", "result_count": 0, "error": str(e)}


@app.post("/retrieve/web_search")
async def retrieve_web_search(req: WebSearchRequest):
    """
    DuckDuckGo web araması yapar ve isteğe bağlı olarak re-rank eder.
    graph_agent.py'deki _ddg_quick() / _fast_web_supplement() yerine kullanılır.

    Döndürür:
      results      : [{ url, title, snippet }] listesi
      result_count : Sonuç sayısı
    """
    try:
        all_results: List[Dict] = []
        seen_urls: set = set()

        queries_to_run = req.queries[:5] if req.queries else [req.query]

        async def _single(q: str) -> list:
            try:
                return await asyncio.wait_for(
                    search_ddg(q, is_review_search=req.is_review_search),
                    timeout=12.0,  # 15 → 12s: search_ddg içi 10s + küçük overhead
                )
            except Exception as ex:
                print(f"[RETRIEVER-API] ⚠️ web search error '{q[:40]}': {ex}")
                return []

        gathered = await asyncio.gather(*[_single(q) for q in queries_to_run], return_exceptions=True)

        for batch in gathered:
            if isinstance(batch, Exception) or not batch:
                continue
            for item in batch:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(item)

        if not all_results:
            return {"results": [], "result_count": 0}

        if req.rerank and len(all_results) > 1:
            ranked = await call_external_reranker(req.query, all_results)
            if ranked:
                all_results = ranked

        all_results = all_results[:req.max_results]

        return {
            "results": [
                {
                    "url":     r.get("url", ""),
                    "title":   r.get("title", ""),
                    "snippet": r.get("snippet", ""),
                }
                for r in all_results
            ],
            "result_count": len(all_results),
        }
    except Exception as e:
        print(f"[RETRIEVER-API] 💥 /retrieve/web_search hatası: {e}")
        return {"results": [], "result_count": 0, "error": str(e)}

# ═══════════════════════════════════════════════════════════════════════════════
# MODÜLER SCRAPER SİSTEMİ - Her kaynak tipi için ayrı handler
# ═══════════════════════════════════════════════════════════════════════════════

# Import edilecek özel kütüphaneler
try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False
    print("[SCRAPER] ⚠️ PyMuPDF yüklü değil, PDF desteği kapalı")

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    HAS_YOUTUBE = True
except ImportError:
    HAS_YOUTUBE = False
    print("[SCRAPER] ⚠️ youtube-transcript-api yüklü değil, YouTube desteği kapalı")

# --- URL TİPİ TESPİTİ ---

def get_url_type(url: str) -> str:
    """URL'nin tipini belirle."""
    url_lower = url.lower()
    
    # PDF
    if url_lower.endswith('.pdf') or '/pdf/' in url_lower:
        return 'pdf'
    
    # YouTube
    if 'youtube.com/watch' in url_lower or 'youtu.be/' in url_lower:
        return 'youtube'
    if 'youtube.com/channel' in url_lower or 'youtube.com/@' in url_lower:
        return 'youtube_channel'  # Bu atlanacak
    
    # Reddit
    if 'reddit.com' in url_lower:
        return 'reddit'
    
    # Ekşi Sözlük
    if 'eksisozluk.com' in url_lower:
        return 'eksisozluk'
    
    # Forum siteleri (JS heavy)
    if any(d in url_lower for d in ['forum.donanimhaber.com', 'unirehberi.com', 'sikayetvar.com']):
        return 'forum_js'
    
    # Atlanacak siteler
    skip_domains = ['twitter.com', 'x.com', 'instagram.com', 'facebook.com', 'tiktok.com', 'linkedin.com/posts']
    if any(d in url_lower for d in skip_domains):
        return 'skip'
    
    # Normal HTML
    return 'html'

# --- PDF SCRAPER ---

async def scrape_pdf(url: str, timeout: float = 15.0) -> Optional[str]:
    """PDF dosyasını indir ve metnini çıkar."""
    if not HAS_PYMUPDF:
        print(f"[SCRAPER-PDF] ⚠️ PyMuPDF yüklü değil: {url[:50]}")
        return None
    
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            
            if resp.status_code != 200:
                return None
            
            # PDF'i memory'de aç
            pdf_bytes = resp.content
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            
            text_parts = []
            max_pages = min(10, len(doc))  # İlk 10 sayfa yeterli
            
            for page_num in range(max_pages):
                page = doc[page_num]
                text = page.get_text()
                if text.strip():
                    text_parts.append(f"--- Sayfa {page_num + 1} ---\n{text}")
            
            doc.close()
            
            full_text = '\n\n'.join(text_parts)
            
            if len(full_text.strip()) < 50:
                return None
            
            print(f"[SCRAPER-PDF] ✅ {len(doc)} sayfalık PDF işlendi: {url[:50]}")
            return full_text.strip()
            
    except Exception as e:
        print(f"[SCRAPER-PDF] ⚠️ {url[:50]}: {e}")
        return None

# --- YOUTUBE TRANSCRIPT SCRAPER ---

def extract_youtube_id(url: str) -> Optional[str]:
    """YouTube URL'sinden video ID'sini çıkar."""
    patterns = [
        r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

async def scrape_youtube(url: str) -> Optional[str]:
    """YouTube videosunun transcript'ini çek."""
    if not HAS_YOUTUBE:
        print(f"[SCRAPER-YT] ⚠️ youtube-transcript-api yüklü değil: {url[:50]}")
        return None
    
    video_id = extract_youtube_id(url)
    if not video_id:
        print(f"[SCRAPER-YT] ⚠️ Video ID bulunamadı: {url[:50]}")
        return None
    
    try:
        # youtube-transcript-api v1.0+ instance tabanlı API kullanır
        # Eski: YouTubeTranscriptApi.get_transcript()  (class method - kaldırıldı)
        # Yeni: YouTubeTranscriptApi().fetch()         (instance method)
        ytt = YouTubeTranscriptApi()
        try:
            # Önce Türkçe dene
            transcript_data = ytt.fetch(video_id, languages=['tr'])
        except:
            try:
                # İngilizce dene
                transcript_data = ytt.fetch(video_id, languages=['en'])
            except:
                # Herhangi bir dil
                transcript_data = ytt.fetch(video_id)
        
        if not transcript_data:
            return None
        
        # Metni birleştir
        # v1.0+ FetchedTranscript: her item Snippet objesi (.text), eski versiyonlarda dict ['text']
        text_parts = []
        for entry in transcript_data:
            if hasattr(entry, 'text'):
                text_parts.append(entry.text)
            elif isinstance(entry, dict):
                text_parts.append(entry.get('text', ''))
        
        full_text = ' '.join(text_parts)
        
        if len(full_text.strip()) < 50:
            return None
        
        print(f"[SCRAPER-YT] ✅ Transcript çekildi ({len(transcript_data)} segment): {url[:50]}")
        return f"[YouTube Video Transcript]\n\n{full_text}"
        
    except Exception as e:
        print(f"[SCRAPER-YT] ⚠️ {url[:50]}: {e}")
        return None

# --- REDDIT SCRAPER (old.reddit.com) ---

async def scrape_reddit(url: str, timeout: float = 10.0) -> Optional[str]:
    """Reddit thread'ini old.reddit.com üzerinden çek."""
    try:
        # old.reddit.com'a çevir (daha basit HTML)
        old_url = url.replace('www.reddit.com', 'old.reddit.com')
        old_url = old_url.replace('reddit.com', 'old.reddit.com')
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        }
        
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(old_url, headers=headers)
            
            if resp.status_code != 200:
                return None
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            parts = []
            
            # Post başlığı
            title = soup.find('a', class_='title')
            if title:
                parts.append(f"# {title.get_text(strip=True)}")
            
            # Post içeriği
            post_content = soup.find('div', class_='usertext-body')
            if post_content:
                parts.append(post_content.get_text(strip=True))
            
            # Yorumlar
            comments = soup.find_all('div', class_='usertext-body')
            for i, comment in enumerate(comments[:20]):  # İlk 20 yorum
                text = comment.get_text(strip=True)
                if text and len(text) > 20:
                    parts.append(f"---\n{text}")
            
            full_text = '\n\n'.join(parts)
            
            if len(full_text.strip()) < 100:
                return None
            
            print(f"[SCRAPER-REDDIT] ✅ Thread çekildi: {url[:50]}")
            return full_text.strip()
            
    except Exception as e:
        print(f"[SCRAPER-REDDIT] ⚠️ {url[:50]}: {e}")
        return None

# --- EKŞİ SÖZLÜK SCRAPER (Playwright) ---

async def scrape_eksisozluk(url: str, timeout: int = 15000) -> Optional[str]:
    """Ekşi Sözlük entry'lerini Playwright ile çek (global browser)."""
    global pw_browser
    try:
        browser = pw_browser
        if not browser:
            # Fallback: browser başlatılamamışsa geçici başlat
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()
        try:
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            # Başlık
            title = await page.query_selector('h1#title')
            title_text = await title.inner_text() if title else ""

            # Entry'ler
            entries = await page.query_selector_all('div.content')

            parts = [f"# {title_text}"] if title_text else []

            for i, entry in enumerate(entries[:15]):  # İlk 15 entry
                try:
                    text = await entry.inner_text()
                    if text and len(text.strip()) > 20:
                        parts.append(f"---\n{text.strip()}")
                except:
                    continue

            full_text = '\n\n'.join(parts)
        finally:
            await page.close()
            await context.close()

        if len(full_text.strip()) < 100:
            return None

        print(f"[SCRAPER-EKSI] ✅ {len(entries)} entry çekildi: {url[:50]}")
        return full_text.strip()

    except Exception as e:
        print(f"[SCRAPER-EKSI] ⚠️ {url[:50]}: {e}")
        return None

# --- GENEL HTML SCRAPER (httpx + readability) ---

async def scrape_with_httpx(url: str, timeout: float = 8.0) -> Optional[str]:
    """Hafif scraper: httpx + readability-lxml + html2text (3x retry + exponential backoff)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    }
    
    for attempt in range(3):  # 3 deneme
        try:
            if attempt > 0:
                wait_time = (2 ** attempt)  # Exponential backoff: 2, 4 saniye
                print(f"[SCRAPER-HTTPX] 🔄 Retry #{attempt+1} (wait {wait_time}s): {url[:50]}")
                await asyncio.sleep(wait_time)
        except:
            pass
        
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                
                if resp.status_code != 200:
                    return None
                
                html = resp.text
                if not html or len(html) < 100:
                    return None
                
                doc = Document(html)
                content_html = doc.summary()
                title = doc.title()
                
                h2t = html2text.HTML2Text()
                h2t.ignore_links = False
                h2t.ignore_images = True
                h2t.body_width = 0
                
                text = h2t.handle(content_html)
                
                if title and title not in text[:100]:
                    text = f"# {title}\n\n{text}"
                
                if len(text.strip()) < 50:
                    return None
                
                return text.strip()
            
        except Exception as e:
            if attempt == 2:  # Son deneme
                print(f"[SCRAPER-HTTPX] ❌ Final attempt failed {url[:50]}: {e}")
                return None
            # Devam et, bir sonraki denemeye geç
    
    return None  # Tüm denemeler başarısız

# --- PLAYWRIGHT FALLBACK (JS siteler) ---

async def scrape_with_playwright(url: str, timeout: int = 12000) -> Optional[str]:
    """JS gerektiren siteler için Playwright scraper (global browser, 3x retry)."""
    global pw_browser
    for attempt in range(3):
        if attempt > 0:
            wait_time = (2 ** attempt)
            print(f"[SCRAPER-PW] 🔄 Retry #{attempt+1} (wait {wait_time}s): {url[:50]}")
            await asyncio.sleep(wait_time)

        try:
            browser = pw_browser
            use_temp = browser is None
            if use_temp:
                _pw = await async_playwright().start()
                browser = await _pw.chromium.launch(headless=True)

            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = await context.new_page()
            raw_html = None
            try:
                await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
                await page.wait_for_timeout(1500)
                raw_html = await page.content()
            finally:
                await page.close()
                await context.close()
                if use_temp:
                    await browser.close()
                    await _pw.stop()

            if not raw_html or len(raw_html) < 100:
                return None

            doc = Document(raw_html)
            content_html = doc.summary()

            h2t = html2text.HTML2Text()
            h2t.ignore_links = False
            h2t.ignore_images = True
            h2t.body_width = 0

            text = h2t.handle(content_html)

            if len(text.strip()) < 50:
                soup = BeautifulSoup(raw_html, "html.parser")
                parts = []
                for tag in soup.find_all(['h1', 'h2', 'h3', 'p']):
                    t = tag.get_text(strip=True)
                    if t and len(t) > 20:
                        parts.append(t)
                text = '\n'.join(parts[:30])

            return text.strip() if text else None

        except Exception as e:
            if attempt == 2:
                print(f"[SCRAPER-PW] ❌ Final attempt failed {url[:50]}: {e}")
                return None

    return None

# --- ANA SCRAPER FONKSİYONU ---

@app.post("/fetch_content")
async def fetch_content(req: FetchContentRequest):
    """
    Modüler Scraper: Her kaynak tipi için optimum yöntemi kullan.
    """
    if not req.urls: 
        await push_log("Boş URL listesi ile fetch_content çağrıldı.", log_type="warn")
        return {"data": []}
    
    await push_log(f"{len(req.urls)} adet link taranacak.", log_type="info", extra={"endpoint": "fetch_content"})
    print(f"[SCRAPER] 🕷️ {len(req.urls)} adet link taranacak...")

    # 1. URL'leri tipine göre grupla
    url_groups = {
        'fast': [],   # httpx, pdf, youtube, reddit
        'js': [],     # forum_js, eksisozluk, playwright fallback
        'skip': [],   # atlanacaklar
        'other': []   # bilinmeyen/garip
    }
    url_types = {}
    for url in req.urls:
        t = get_url_type(url)
        url_types[url] = t
        if t in ('html', 'pdf', 'youtube', 'reddit'):
            url_groups['fast'].append(url)
        elif t in ('forum_js', 'eksisozluk'):
            url_groups['js'].append(url)
        elif t in ('skip', 'youtube_channel'):
            url_groups['skip'].append(url)
        else:
            url_groups['other'].append(url)

    # 2. Batch scraping fonksiyonu
    semaphore_limit_fast = 20
    semaphore_limit_js = 3
    semaphore_fast = asyncio.Semaphore(semaphore_limit_fast)
    semaphore_js = asyncio.Semaphore(semaphore_limit_js)
    print(f"[SCRAPER] ⏩ Paralel tarama limiti: fast={semaphore_limit_fast}, js={semaphore_limit_js}")

    async def smart_scrape(url: str) -> dict:
        url_type = url_types.get(url) or get_url_type(url)
        content = None
        timeout_map = {
            'pdf': 15.0,
            'youtube': 10.0,
            'reddit': 10.0,
            'eksisozluk': 15.0,
            'forum_js': 12.0,
            'html': 8.0
        }
        timeout = timeout_map.get(url_type, 8.0)
        try:
            if url_type == 'skip':
                print(f"[SCRAPER] ⏭️ Atlandı (desteklenmiyor): {url[:50]}")
                return {"url": url, "content": ""}
            elif url_type == 'youtube_channel':
                print(f"[SCRAPER] ⏭️ Atlandı (kanal sayfası): {url[:50]}")
                return {"url": url, "content": ""}
            elif url_type == 'pdf':
                try:
                    content = await asyncio.wait_for(scrape_pdf(url, timeout=timeout), timeout=timeout+2)
                    if content:
                        print(f"[SCRAPER] 📄 PDF başarılı: {url[:50]}")
                except Exception as e:
                    print(f"[SCRAPER] ⚠️ PDF timeout/hata: {url[:50]} | {e}")
            elif url_type == 'youtube':
                try:
                    content = await asyncio.wait_for(scrape_youtube(url), timeout=timeout)
                    if content:
                        print(f"[SCRAPER] 🎬 YouTube başarılı: {url[:50]}")
                except Exception as e:
                    print(f"[SCRAPER] ⚠️ YouTube timeout/hata: {url[:50]} | {e}")
            elif url_type == 'reddit':
                try:
                    content = await asyncio.wait_for(scrape_reddit(url, timeout=timeout), timeout=timeout+2)
                    if content:
                        print(f"[SCRAPER] 🔴 Reddit başarılı: {url[:50]}")
                except Exception as e:
                    print(f"[SCRAPER] ⚠️ Reddit timeout/hata: {url[:50]} | {e}")
            elif url_type == 'eksisozluk':
                try:
                    content = await asyncio.wait_for(scrape_eksisozluk(url, timeout=int(timeout*1000)), timeout=timeout+3)
                    if content:
                        print(f"[SCRAPER] 💚 Ekşi başarılı: {url[:50]}")
                except Exception as e:
                    print(f"[SCRAPER] ⚠️ Ekşi timeout/hata: {url[:50]} | {e}")
            elif url_type == 'forum_js':
                try:
                    content = await asyncio.wait_for(scrape_with_playwright(url, timeout=int(timeout*1000)), timeout=timeout+3)
                    if content:
                        print(f"[SCRAPER] 🎭 JS Forum başarılı: {url[:50]}")
                except Exception as e:
                    print(f"[SCRAPER] ⚠️ JS Forum timeout/hata: {url[:50]} | {e}")
            else:  # Normal HTML
                try:
                    content = await asyncio.wait_for(scrape_with_httpx(url, timeout=timeout), timeout=timeout+2)
                    if content and len(content) > 100:
                        print(f"[SCRAPER] ⚡ Hızlı mod başarılı: {url[:50]}")
                    else:
                        try:
                            content = await asyncio.wait_for(scrape_with_playwright(url, timeout=int(timeout*1000)), timeout=timeout+3)
                            if content:
                                print(f"[SCRAPER] 🔄 Playwright fallback başarılı: {url[:50]}")
                        except Exception as e2:
                            print(f"[SCRAPER] ⚠️ Playwright fallback timeout/hata: {url[:50]} | {e2}")
                except Exception as e:
                    print(f"[SCRAPER] ⚠️ HTML timeout/hata: {url[:50]} | {e}")
        except Exception as e:
            print(f"[SCRAPER] ❌ Genel scraper hatası: {url[:50]} | {e}")
        # İçerik filtreleme: minimum bilgi eşiği (en az 80 karakter)
        if content and len(content.strip()) < 80:
            print(f"[SCRAPER] ⚠️ İçerik çok kısa, atlandı: {url[:50]}")
            content = None
        return {"url": url, "content": content or ""}

    async def batch_scrape(urls, desc, semaphore, timeout):
        print(f"[SCRAPER] ▶️ Batch başlatılıyor: {desc} ({len(urls)} link)")
        results = []
        if not urls:
            return results
        async def limited_scrape(url):
            async with semaphore:
                return await smart_scrape(url)
        tasks = [limited_scrape(u) for u in urls]
        try:
            batch = await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
        except asyncio.TimeoutError:
            print(f"[SCRAPER] ⏰ Batch timeout ({desc})!")
            batch = []
        results.extend(batch)
        return results

    # 3. Gruplara göre batch scraping başlat
    all_results = []
    all_results.extend(await batch_scrape(url_groups['fast'], 'Hızlı handler', semaphore_fast, 60))
    all_results.extend(await batch_scrape(url_groups['js'], 'JS/Playwright handler', semaphore_js, 120))
    # Skip ve other grubu da loglansın
    for u in url_groups['skip']:
        all_results.append({"url": u, "content": ""})
    for u in url_groups['other']:
        all_results.append(await smart_scrape(u))

    # Bozuk/uzun markdown filtrelemesi
    for r in all_results:
        if r.get("content"):
            r["content"] = filter_broken_markdown(r["content"])

    success_count = sum(1 for r in all_results if r.get("content"))
    log_msg = f"Tarama tamamlandı: {success_count}/{len(req.urls)} başarılı"
    await push_log(log_msg, log_type="success", extra={"endpoint": "fetch_content", "success_count": success_count, "total": len(req.urls)})
    print(f"[SCRAPER] ✅ {log_msg}")
    return {"data": all_results}


# Note: Synthesis (LLM-based summarization) was intentionally removed from the retriever.
# fetch_content now only returns raw scraped pages. Synthesis should be performed by the
# graph agent (gateway) so the router/LLM can control final formatting and include NER/context.


def generate_synthesized_summary(pages_sorted: List[Dict[str, Any]]) -> str:
    """
    Deterministic fallback summary used when no external LLM is available.
    Produces a short Turkish markdown list of extracted lines with source URLs.
    """
    if not pages_sorted:
        return "İçerik bulunamadı."

    lines = []
    for p in pages_sorted[:15]:
        url = p.get('url', '')
        content = (p.get('content') or '').strip()
        first_line = ''
        if content:
            # take first non-empty line as a short summary
            for ln in content.splitlines():
                ln = ln.strip()
                if ln:
                    first_line = ln[:200]
                    break

        if first_line:
            lines.append(f"- {first_line} — {url}")
        else:
            lines.append(f"- (kaynakta kısa içerik yok) — {url}")

    header = "**Basit Özet (LLM yoksa kullanılan yedek)**\n\n"
    return header + "\n".join(lines)


async def synthesize_with_llm(pages: List[Dict[str, Any]], original_query: Optional[str] = None) -> str:
    """
    Try to synthesize a polished Markdown summary using an external LLM (OpenAI).
    Falls back to deterministic `generate_synthesized_summary` if API key is missing or call fails.
    """
    # Use up to top N pages by content length
    N = 15
    pages_sorted = sorted(pages, key=lambda p: len(p.get('content') or ''), reverse=True)[:N]

    # Build a compact prompt containing URLs + truncated contents
    chunks = []
    for p in pages_sorted:
        url = p.get('url') or ''
        content = (p.get('content') or '')[:4000]
        chunks.append(f"URL: {url}\nCONTENT:\n{content}\n---\n")

    system_msg = (
        "You are a helpful assistant that synthesizes scraped web pages into a concise, well-structured Markdown report. "
        "Produce sections: Overview, Found Clubs (as a short table with name and source), and Sources. Cite sources using the URLs provided. Keep the output in Turkish."
    )

    user_prompt = """
    Given the following scraped pages, create a polished Markdown summary in Turkish. Be concise, extract club names or related lines, and cite the source URL next to each club. If no clubs found, provide an action suggestion. Pages:
    \n
    """ + "\n".join(chunks)

    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY') or os.environ.get('OPENAI_KEY')
    OPENAI_MODEL = os.environ.get('LLM_MODEL') or os.environ.get('OPENAI_MODEL') or 'gpt-4o-mini'

    if not OPENAI_API_KEY:
        # No API key: fallback
        return generate_synthesized_summary(pages_sorted)

    # Call OpenAI Chat Completions via aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            url = 'https://api.openai.com/v1/chat/completions'
            headers = {'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'}
            payload = {
                'model': OPENAI_MODEL,
                'messages': [
                    {'role': 'system', 'content': system_msg},
                    {'role': 'user', 'content': user_prompt}
                ],
                'max_tokens': 1200,
                'temperature': 0.2
            }
            async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # response structure: choices[0].message.content
                    choices = data.get('choices') or []
                    if choices:
                        text = choices[0].get('message', {}).get('content') or choices[0].get('text') or ''
                        return text
                    else:
                        return generate_synthesized_summary(pages_sorted)
                else:
                    print(f"[LLM] ⚠️ OpenAI HTTP {resp.status}: {await resp.text()}")
                    return generate_synthesized_summary(pages_sorted)
    except Exception as e:
        print(f"[LLM] ❌ Hata: {e}")
        return generate_synthesized_summary(pages_sorted)

# Dosya sonu