# Tercih Asistanı (tercihnoktam-ai)

TercihNoktam'ın yapay zekâ tercih danışmanı **web arayüzü**. YÖK Atlas + Gemini
tabanlı [`TercihChatAi`](https://github.com/BadiciCyra/TercihChatAi) gateway'ine
konuşur. Tasarım dili, renk paleti ve bileşenleri **tercihnoktam-forum** ile
birebir aynıdır (Next.js 16 + Tailwind v4 + Geist).

## Mimari

```
Kullanıcı → tercihnoktam-ai (bu repo, Next.js)
              └─ /api/ask  (sunucu proxy, X-School-Key'i saklar)
                   └─ POST {AI_GATEWAY_URL}/b2b/ask_intelligent
                        └─ ai-gateway (FastAPI + LangGraph + Gemini)
```

Anahtar tarayıcıya sızmasın diye gateway çağrısı **sunucu tarafındaki
`/api/ask`** route handler'ından yapılır.

## Kurulum

```bash
npm install
cp .env.example .env.local   # değerler zaten canlı gateway'e işaret eder
npm run dev                  # http://localhost:3000
```

## Ortam değişkenleri

Tüm ayarlar `.env.local` dosyasında tutulur ve **git'e gönderilmez**
(`.gitignore`, `.env.example` dışındaki tüm `.env*` dosyalarını yok sayar).
Örnekten kopyala:

```bash
cp .env.example .env.local
```

| Değişken | Tarafı | Açıklama |
|---|---|---|
| `AI_GATEWAY_URL` | sunucu | Gateway kök adresi. Canlı: `https://p01--ai-gateway--z9ktjbpxmd48.code.run` · Lokal: `http://localhost:8003` |
| `AI_SCHOOL_KEY` | sunucu | `X-School-Key` — gateway'e gönderilen kurum anahtarı. **Asla commit etme.** |
| `NEXT_PUBLIC_FORUM_URL` | tarayıcı | Navbar "Foruma dön" linki |

## API anahtarı (X-School-Key) nasıl eklenir

Gateway `/b2b/ask_intelligent` her isteği `X-School-Key` başlığıyla doğrular. Bu
site anahtarı **sunucu tarafında** ([`src/app/api/ask/route.ts`](src/app/api/ask/route.ts))
ekler; tarayıcıya hiç sızmaz. Anahtarı kaynak koda **yazma** — iki yerden birine koy:

### Lokal geliştirme
`.env.local` dosyasına yaz (git-ignore'lu), sonra dev server'ı yeniden başlat:

```bash
# .env.local
AI_SCHOOL_KEY=buraya-gercek-anahtar
```

```bash
npm run dev   # env değişikliği restart ister
```

### Production (Vercel / Northflank vb.)
Anahtarı repoya değil, **hosting platformunun "Environment Variables" ekranına**
gir: `AI_SCHOOL_KEY` ve `AI_GATEWAY_URL`. Böylece anahtar kaynak kodda görünmez.

### Anahtar nereden gelir?
Gateway bir anahtarı üç şekilde tanır (`ai/app/gate.py`):

1. **Redis** — `ai:apikey:<KEY>` hash'i (isim + plan). Admin ucu: `POST /admin/keys`.
2. **Gateway env** — `API_KEY_<KEY>=Kurum Adı:plan` (ör. `API_KEY_ABC123=Test Lisesi:gold`).
3. **Dev fallback** — yalnızca `ALLOW_DEV_FALLBACK=true` iken `test_key` geçerli (lokal).

> ⚠️ **Canlı deployment** `test_key`'i ve anonim erişimi kapatmıştır → geçerli,
> kayıtlı bir anahtar gerekir. Lokal gateway'de (`localhost:8003`) `test_key` çalışır.

## Modlar

🎯 Tercih Sihirbazı · 🔍 Araştırma Asistanı · 🧭 Kariyer Pusulası · 💬 Rehberlik ·
⚙️ Otomatik (gateway sorunu kendisi sınıflandırır).

## Sıradaki adımlar

- Forumun IdP login akışıyla entegrasyon (ortak oturum).
- Sohbet geçmişinin kalıcılığı.
