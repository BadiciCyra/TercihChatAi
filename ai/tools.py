# tools.py
# Retrieval işlemleri artık HTTP üzerinden ai-retriever servisine yönlendirilir.
# Doğrudan retrieve.py import'u kaldırıldı — Retrieval / Reasoning ayrımı.
import json
import os
import re
import unicodedata
from typing import Optional, Type
import asyncio
import time
import aiohttp
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field
from utils.link_fetcher import fetch_url_content
from program_name import format_program_adi

# Retriever servis URL'i — Docker compose'da ai-retriever:8000
_RETRIEVER_URL = os.getenv("RETRIEVER_URL", "http://ai-retriever:8000")


async def _http_yok_atlas(entities: dict) -> Optional[dict]:
    """YÖK Atlas verisini retriever servisinden HTTP ile çeker."""
    payload = {
        "rank":       (entities.get("USER_RANK") or [None])[0],
        "program":    (entities.get("BOL")        or [None])[0],
        "uni":        (entities.get("UNI")        or [None])[0],
        "city":       (entities.get("IL")         or [None])[0],
        "uni_type":   (entities.get("UNI_TYPE")   or [None])[0],
        "score_type": (entities.get("TUR")        or [None])[0],
        "fee_type":   (entities.get("FEE_TYPE")   or [None])[0],
    }
    # None değerleri çıkar
    payload = {k: v for k, v in payload.items() if v}
    if not payload:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_RETRIEVER_URL}/retrieve/yok_atlas",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                print(f"[TOOLS] ⚠️ /retrieve/yok_atlas HTTP {resp.status}")
                return None
    except Exception as e:
        print(f"[TOOLS] 💥 /retrieve/yok_atlas hatası: {e}")
        return None


async def _http_web_search(
    query: str,
    queries: list = None,
    query_type: str = "specific",
    is_review_search: bool = False,
    max_results: int = 5,
    rerank: bool = True,
) -> list:
    """Web aramasını retriever servisinden HTTP ile çeker."""
    payload = {
        "query":            query,
        "queries":          queries or [],
        "query_type":       query_type,
        "is_review_search": is_review_search,
        "max_results":      max_results,
        "rerank":           rerank,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_RETRIEVER_URL}/retrieve/web_search",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=40),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("results", [])
                print(f"[TOOLS] ⚠️ /retrieve/web_search HTTP {resp.status}")
                return []
    except Exception as e:
        print(f"[TOOLS] 💥 /retrieve/web_search hatası: {e}")
        return []

# --- YARDIMCI: TABLO OLUŞTURUCU (PYTHON) ---
def generate_markdown_table_python(data_list):
    """Verileri alır ve şık, sade bir Markdown tablosu stringi döndürür."""
    if not data_list: return "Veri bulunamadı."
    # Build rows and headers to compute widths and output a beautified table
    headers = ["Üniversite", "Bölüm", "Yıl", "Kont.", "Puan", "Sıralama", "Detay"]
    rows = []

    count = 0
    for item in data_list:
        try:
            if not isinstance(item, dict): continue
            
            # --- Veri Temizliği ---
            uni_raw = str(item.get('uni_adi', '-'))
            uni_clean = uni_raw.replace("ÜNİVERSİTESİ", "Ü.").replace("YÜKSEK TEKNOLOJİ ENSTİTÜSÜ", "İYTE").replace("TEKNİK Ü.", "TÜ.")
            uni = f"**{uni_clean[:25]}**" # Çok uzunsa kes

            # Program adını kısalt ama UOLP/ortak program, kampüs, İÖ gibi
            # öğrenciyi yanıltacak kritik nitelemeleri KORU (bkz. program_name.py)
            bol = format_program_adi(item.get('program_adi', '-'))

            yil = str(item.get('current_year', '-'))
            kont = str(item.get('kontenjan', {}).get(yil, '-')) if isinstance(item.get('kontenjan'), dict) else '-'
            
            puan_val = str(item.get('taban', {}).get(yil, '-'))[:6] if isinstance(item.get('taban'), dict) else '-'
            puan = f"_{puan_val}_"
            
            sira_val = str(item.get('current_rank', '-'))
            # Eğer sıralama çok absürtse veya boşsa tire koy
            sira = f"**{sira_val}**" if sira_val not in ['-', '9999999', '0'] else '-'
            
            # --- Tür ve Ücret Analizi ---
            program_adi_tam = str(item.get('program_adi', '')).upper()
            api_tur = str(item.get("universite_turu", "")).upper()
            
            # Ücret Durumu
            if "BURSLU" in program_adi_tam: ucret_detay = "%100"
            elif "%50" in program_adi_tam: ucret_detay = "%50"
            elif "%25" in program_adi_tam: ucret_detay = "%25"
            elif "ÜCRETLİ" in program_adi_tam: ucret_detay = "Paralı"
            else: ucret_detay = "Ücretsiz"

            # Üniversite Türü (Vakıf/Devlet)
            if "KKTC" in api_tur: tur = "KKTC"
            elif "YURT DIŞI" in api_tur: tur = "YDışı"
            elif "VAKIF" in api_tur: tur = "Vakıf"
            # İsimden Vakıf Yakalama (Bazen API devlet diyor yanlışlıkla)
            elif any(k in uni_raw.upper() for k in ["KOÇ", "SABANCI", "BİLKENT", "BAHÇEŞEHİR", "YEDİTEPE", "ÖZYEĞİN", "MEDİPOL", "AYDIN", "GELİŞİM"]): 
                tur = "Vakıf"
            else: tur = "Devlet"
            
            if tur == "Vakıf" and ucret_detay == "Ücretsiz": ucret_detay = "Paralı"

            sehir = str(item.get("sehir","")).capitalize()
            detay = f"{sehir}, {tur}, {ucret_detay}"

            # Satırı ekle
            rows.append([uni, bol, yil, str(kont), puan, sira, detay])
            count += 1
        except: continue
    if count == 0: return "Veriler formatlanırken bir sorun oluştu."

    def strip_md(s: str) -> str:
        return s.replace("**", "").replace("_", "")

    cols = list(zip(*([headers] + rows)))
    widths = [max(len(strip_md(str(cell))) for cell in col) for col in cols]

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

# --- INPUT ŞEMALARI ---
class YokInput(BaseModel):
    """
    YÖK Atlas arama parametreleri.
    En az bir parametre belirtilmeli (program, uni, city veya rank).
    """
    rank: Optional[str] = Field(
        default=None, 
        description="Başarı sıralaması (String formatında). Örnek: '150000', '50000'. Kullanıcı '150k' derse '150000' olarak gönder."
    )
    city: Optional[str] = Field(
        default=None, 
        description="Şehir adı (Türkçe). Örnek: 'İstanbul', 'Ankara', 'İzmir'"
    )
    uni: Optional[str] = Field(
        default=None, 
        description="Üniversite adı (tam veya kısmi). Örnek: 'İstanbul Teknik Üniversitesi', 'Boğaziçi Üniversitesi'"
    )
    program: Optional[str] = Field(
        default=None, 
        description="Bölüm/program adı. Örnek: 'Bilgisayar Mühendisliği', 'Hukuk', 'Tıp'. Kısaltmaları açarak gönder."
    )
    uni_type: Optional[str] = Field(
        default=None, 
        description="Üniversite türü filtresi. Değerler: 'Devlet', 'Vakıf', 'KKTC'"
    )
    order_type: Optional[str] = Field(
        default=None, 
        description="Puan türü filtresi. Değerler: 'SAY' (Sayısal), 'EA' (Eşit Ağırlık), 'SOZ' (Sözel), 'DIL' (Dil)"
    )

class WebSearchInput(BaseModel):
    """
    Web arama parametreleri.
    Güncel bilgiler, yorumlar ve YÖK Atlas'ta olmayan veriler için kullanılır.
    """
    query: str = Field(
        ..., 
        description="Aranacak sorgu. Türkçe ve spesifik olsun. Örnek: 'İTÜ Bilgisayar Mühendisliği öğrenci yorumları'"
    )
    query_type: str = Field(
        default="specific",
        description="""Sorgu tipi. Değerler:
        - 'general': Üniversite/bölüm hakkında GENEL TANITIM isteniyorsa (tarihçe, akademik yapı, kampüs, olanaklar)
        - 'academic': RESMİ AKADEMİK VERİLER isteniyorsa (ücretler, burs oranları, kontenjanlar, akademisyen sayıları, iletişim bilgileri). Resmi web sitelerini ve YÖK verilerini hedefler.
        - 'reviews': Sadece öğrenci YORUMLARI ve DENEYİMLERİ isteniyorsa
        - 'specific': Spesifik bir konu (kulüpler, yurt, staj vb.) soruluyorsa
        Örnek: 'X üniversitesi nasıl?' → 'general', 'X ücretleri 2025' → 'academic', 'X yorumları' → 'reviews'"""
    )
    search_reviews: bool = Field(
        default=False, 
        description="DEPRECATED - query_type kullan. True yapılırsa sonuçlar yorum/deneyim içeriklerine öncelik verir."
    )

# --- TOOL SINIFLARI ---

class YokAtlasTool(BaseTool):
    name: str = "yok_atlas_search"
    description: str = """YÖK Atlas veritabanından üniversite/bölüm verilerini çeker.

**NE ZAMAN KULLAN:**
- Taban puanı, başarı sıralaması, kontenjan gibi NET SAYISAL VERİ istendiğinde
- "150k ile hangi bölümler gelir?", "Bilgisayar mühendisliği kaç puan?" sorularında
- Bölüm veya üniversite listesi/karşılaştırması istendiğinde
- "En düşük puanlı tıp fakülteleri", "İstanbul'daki hukuk bölümleri" gibi sorgularda

**NE ZAMAN KULLANMA:**
- Öğrenci yorumları, deneyimler sorulduğunda → web_search kullan
- Kampüs hayatı, kulüpler, sosyal aktiviteler sorulduğunda → web_search kullan
- "Nasıl bir yer?", "Memnun musunuz?" gibi subjektif sorularda → web_search kullan

**PARAMETRE İPUÇLARI:**
- rank: Kullanıcı "150k" derse "150000" olarak gönder
- program: Kısaltmaları aç ("Bilgisayar" → "Bilgisayar Mühendisliği")
- En az bir parametre (program, uni, city veya rank) belirtilmeli"""
    args_schema: Type[BaseModel] = YokInput 

    def _run(self, **kwargs): raise NotImplementedError("Async only.")

    async def _arun(
        self,
        rank: str = None,
        city: str = None,
        uni: str = None,
        program: str = None,
        uni_type: str = None,
        order_type: str = None,
        score_type: str = None,
        fee_type: str = None,
        **kwargs,
    ) -> str:
        entities = {}
        if rank:       entities["USER_RANK"] = [str(rank)]
        if city:       entities["IL"]        = [city]
        if uni:        entities["UNI"]       = [uni]
        if program:    entities["BOL"]       = [program]
        if uni_type:   entities["UNI_TYPE"]  = [uni_type]
        if score_type: entities["TUR"]       = [score_type]
        elif order_type: entities["TUR"]     = [order_type]
        if fee_type:   entities["FEE_TYPE"]  = [fee_type]

        meaningful = (entities.get("BOL") or entities.get("UNI") or entities.get("IL")
                      or entities.get("FEE_TYPE") or entities.get("UNI_TYPE") or entities.get("USER_RANK"))
        if not meaningful:
            return "Lütfen bölüm, şehir, üniversite veya sıralama belirtin."

        try:
            # HTTP üzerinden retriever servisine gönder
            res = await _http_yok_atlas(entities)
            if not res:
                return "Kriterlere uygun sonuç bulunamadı."
            if res.get("markdown_table"):
                return res["markdown_table"]
            sc = res.get("structured_content", "[]")
            data_list = json.loads(sc) if sc else []
            if not data_list:
                return "Kriterlere uygun sonuç bulunamadı."
            return generate_markdown_table_python(data_list)
        except Exception as e:
            return f"Veri işlenirken hata: {str(e)}"

class WebSearchTool(BaseTool):
    name: str = "web_search"
    description: str = """Web'den üniversite/bölüm hakkında bilgi, tanıtım, yorum ve deneyimler arar.

**NE ZAMAN KULLAN:**
- Üniversite/bölüm hakkında GENEL BİLGİ istendiğinde (tarihçe, akademik yapı, kampüs, olanaklar)
- Öğrenci yorumları, deneyimler, memnuniyet sorulduğunda
- "Nasıl bir üniversite?", "X üniversitesi hakkında bilgi ver" gibi sorularda
- Kampüs hayatı, öğrenci kulüpleri, sosyal aktiviteler sorulduğunda
- Güncel haberler, duyurular veya YÖK Atlas'ta olmayan bilgiler gerektiğinde

**NE ZAMAN KULLANMA:**
- Taban puanı, sıralama, kontenjan gibi net sayısal veri istendiğinde → yok_atlas_search kullan
- Bölüm listesi veya karşılaştırması istendiğinde → yok_atlas_search kullan

**PARAMETRE İPUÇLARI:**
- query: Spesifik ve Türkçe olsun
- query_type: 
  - 'general' → Genel tanıtım istendiğinde ("X üniversitesi nasıl?", "X hakkında bilgi")
  - 'reviews' → Sadece yorum/deneyim istendiğinde ("X yorumları", "memnun musunuz")
  - 'specific' → Spesifik konu (kulüpler, yurt, burs, staj)
- search_reviews: (deprecated) query_type kullan"""
    args_schema: Type[BaseModel] = WebSearchInput

    def _run(self, **kwargs): raise NotImplementedError("Async only.")

    async def _arun(self, query: str, query_type: str = "specific", search_reviews: bool = False, optimized_queries: list = None, **kwargs) -> str:
        """Web aramasını retriever servisine HTTP ile devreder."""
        queries = optimized_queries[:3] if optimized_queries else []
        is_review = search_reviews or query_type == "reviews"

        print(f"\n[TOOL-WEB] 🔍 Retriever'a HTTP web search: '{query[:60]}' (type={query_type})")

        results = await _http_web_search(
            query=query,
            queries=queries,
            query_type=query_type,
            is_review_search=is_review,
            max_results=8,
            rerank=True,
        )

        if not results:
            return "Aradığınız kriterlere uygun web sonucu bulunamadı."

        # Sonuçları markdown formatına çevir
        formatted = ""
        for i, item in enumerate(results[:6]):
            title   = item.get("title", "Başlıksız")
            snippet = item.get("snippet", "Özet yok")
            url     = item.get("url", "-")
            formatted += f"{i+1}. **{title}**\n{snippet}\nKaynak: {url}\n\n"

        print(f"[TOOL-WEB] ✅ {len(results)} sonuç retriever'dan alındı")
        return formatted


@tool
async def fetch_link_content(url: str, timeout_seconds: int = 10) -> str:
    """Fetches the full text content of a URL. Use when web search snippets are insufficient and you need the full page to answer the user's question. Only call for URLs returned by a prior web_search tool call."""
    if not url:
        return "Geçersiz URL: boş değer"

    result = await fetch_url_content(url, timeout_seconds=timeout_seconds)

    if result.success:
        return f"[Kaynak: {result.url}]\n{result.content}"
    else:
        return f"[FETCH_FAILED] {result.url}: {result.error}"
