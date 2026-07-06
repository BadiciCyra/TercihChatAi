# chat_terminal.py — Kullanım Kılavuzu

Terminal üzerinden AI'ı canlı test et. Sol panel logları, sağ panel yanıtı gösterir.

---

## Kurulum (ilk defa)

```bash
cd ai
pip install aiohttp   # gateway modu için
# local mod için tüm requirements.txt zaten kurulu olmalı
```

---

## Çalıştırma

### 1. Docker container'ları çalışırken → Gateway modu (önerilen)
```bash
# Önce docker'ı başlat
docker-compose up -d ai-gateway ai-retriever ai-reranker redis

# Sonra terminalde:
cd ai
python chat_terminal.py --mode gateway
```

### 2. Docker olmadan, doğrudan Python → Local modu
```bash
cd ai
# .env dosyasında GEMINI_API_KEY olmalı
python chat_terminal.py --mode local
```

### 3. Özel gateway URL
```bash
python chat_terminal.py --mode gateway --url http://sunucu:8003/b2b/ask_intelligent
```

---

## Terminal Komutları (chat içinde)

| Komut | Açıklama |
|---|---|
| `/temizle` | Ekranı temizle |
| `/session` | Session ID'yi logla |
| `/mod` | Aktif modu göster |
| `/yardim` | Yardım ekranı |
| `Ctrl+C` | Çıkış |

---

## Ne Görürsün?

```
────────────────────────────────────────────────────────────────
  🎓 Tercih Noktam AI — Canlı Test Arayüzü
────────────────────────────────────────────────────────────────
 📋 CANLI LOGLAR                 │  🤖 AI YANITI
─────────────────────────────────┼──────────────────────────────
[NER] ⚡ LLM bypass: fast        │  ## 🎯 Bilgisayar Mühendisliği
[FAST_LOOKUP] 🔎 Tool args:...   │
[YOK_CLIENT] ✅ 23 sonuç         │  | Üniversite | Bölüm | ...
[FAST_LOOKUP] ⚡ YÖK Atlas +     │  | **İTÜ**    | ...   | ...
[DONE] 8.3sn | tokens=0          │
─────────────────────────────────────────────────────────────────
  Durum: ✅ tamamlandı (8.3sn)   Süre: 8.3sn
```

---

## Uygulanan Fix'ler (Bu Sürümde)

| Fix | Açıklama | Kazanım |
|---|---|---|
| A | fast_lookup: YÖK Atlas + web_supplement paralel | -20sn |
| B | agent_node async def + ainvoke | event loop bloğu kaldırıldı |
| E | --reload docker'dan kaldırıldı | CPU/bellek tasarrufu |
| F | tools.py çift gather → tek gather | tanımsız davranış giderildi |
