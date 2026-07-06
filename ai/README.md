# TercihNoktam AI Servisleri

YKS/YÖK Atlas tabanlı üniversite tercih danışmanlığı için AI mikroservis mimarisi.

## Servisler

| Servis | Port | Açıklama |
|--------|------|----------|
| `ai-gateway` | 8003 | Ana API gateway — dışarıya açık tek servis |
| `ai-retriever` | 8000 | YÖK Atlas + DDG web araması — internal |
| `ai-reranker` | 8002 | Sonuç re-ranking (sentence-transformers) — internal |

## Hızlı Başlangıç (Local)

```bash
# .env dosyasını oluştur
cp .env.example .env
# GEMINI_API_KEY ve diğer değerleri doldur

# Servisleri başlat
docker-compose up -d
```

## Production (Northflank)

`Dockerfile.prod` kullan — Playwright olmadan daha hafif image.

Her servis için ayrı start command:
- reranker: `uvicorn re-rank:app --host 0.0.0.0 --port 8002`
- retriever: `uvicorn retrieve:app --host 0.0.0.0 --port 8000`  
- gateway: `uvicorn gate:app --host 0.0.0.0 --port 8003`

Gerekli env değişkenleri için `.env.northflank.example`'a bak.

## API Kullanımı

```bash
# Soru sor
curl -X POST "https://<gateway-url>/b2b/ask_intelligent" \
  -H "X-School-Key: <api-key>" \
  -H "Content-Type: application/json" \
  -d '{"query": "50k SAY ile bilgisayar mühendisliği nereler gelir?", "session_id": "test"}'
```
