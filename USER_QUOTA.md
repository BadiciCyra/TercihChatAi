# Günlük Soru Kotası (kullanıcı başına 25 soru)

Chat asistanında kullanıcı başına **günlük soru limiti**. Limit, forumun ürettiği
imzalı token üzerinden uygulanır — tarayıcıdaki `session_id` ile değil, çünkü o
"Yeni sohbet"te ve sayfa yenilemede sıfırlanıyor (kullanıcı kotasını istediği
kadar tazeleyebilirdi).

## Akış

```
Forum (giriş yapmış kullanıcı)
  └─ HS256 JWT üretir:  {"sub": "<kullanici_id>", "exp": <15 dk sonrası>}
       └─ Asistana yönlendirir:  https://tercih-chat-ai.vercel.app/#t=<jwt>
            └─ Chat token'ı okur, adres çubuğundan siler, localStorage'a yazar
                 └─ Her istekte  X-User-Token: <jwt>  header'ı ile gönderir
                      └─ Gateway imzayı doğrular → günlük kotayı uygular
```

Token neden `#` (fragment) ile taşınıyor: fragment sunucuya gönderilmez, bu yüzden
sunucu log'larına ve `Referer` başlığına sızmaz. Query string (`?t=`) bu güvenceyi
vermez.

## Ortam değişkenleri (gateway / `ai` servisi)

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `AI_USER_TOKEN_SECRET` | *(boş)* | Forum ile **paylaşılan** imza sırrı. Boşsa hiçbir token doğrulanmaz. Production'da mutlaka set edilmeli. |
| `USER_DAILY_LIMIT` | `25` | Kullanıcı başına günlük soru hakkı. |
| `REQUIRE_USER_TOKEN` | `false` | `true` → token yoksa istek **401** ile reddedilir. Forum entegrasyonu bitene kadar `false` bırakın. |

### Devreye alma sırası (kesintisiz)

1. Bu kod deploy edilir — `REQUIRE_USER_TOKEN=false` olduğu için **davranış değişmez**,
   token göndermeyen istekler eskisi gibi çalışır.
2. `AI_USER_TOKEN_SECRET` hem gateway'e hem foruma aynı değerle girilir.
3. Forum token üretmeye başlar; kotalı kullanıcılar sayılmaya başlar.
4. Her şey doğrulanınca `REQUIRE_USER_TOKEN=true` yapılır — artık giriş zorunlu.

## Forum tarafında yapılacak (bu repoda değil)

Giriş yapmış kullanıcı asistana giderken token üretilip linke eklenmeli.

PHP örneği (`firebase/php-jwt` ile):

```php
use Firebase\JWT\JWT;

$token = JWT::encode([
    'sub' => (string) $user->id,   // forum kullanıcı id'si
    'exp' => time() + 900,         // 15 dakika
], getenv('AI_USER_TOKEN_SECRET'), 'HS256');

$url = 'https://tercih-chat-ai.vercel.app/#t=' . rawurlencode($token);
```

Node/JS örneği:

```js
const jwt = require("jsonwebtoken");
const token = jwt.sign({ sub: String(user.id) }, process.env.AI_USER_TOKEN_SECRET, {
  algorithm: "HS256",
  expiresIn: "15m",
});
const url = `https://tercih-chat-ai.vercel.app/#t=${encodeURIComponent(token)}`;
```

Notlar:
- `sub` **kalıcı ve benzersiz** olmalı (forum user id). Kota bu değere bağlanır.
- Kısa `exp` (15 dk) önerilir; token linkten kopyalanıp paylaşılsa bile çabuk ölür.
- Kullanıcı sekmede uzun kalırsa token'ın süresi dolar → kota uygulanamaz hale gelir.
  `REQUIRE_USER_TOKEN=true` iken bu 401 döner; forumun sayfayı yenileyip taze token
  vermesi gerekir. (İleride: sessiz yenileme endpoint'i.)

## Sayaç nerede tutuluyor

Redis: `ai:quota:<user_id>:<YYYY-MM-DD>` — TTL gece yarısına (TR saati) ayarlı,
yani sayaç her gece otomatik sıfırlanır. Redis yoksa in-process fallback devreye
girer (tek process dev ortamı için; çok replikalı production'da Redis şart).

Limit aşıldığında gateway **429** döner:

```
Günlük 25 soru hakkını doldurdun. Yarın tekrar bekleriz!
```

Bu mesaj `/api/ask` üzerinden aynen kullanıcıya gösterilir.

## Sınırlar

- Aynı kişi **iki farklı forum hesabıyla** girerse iki ayrı kota alır — hesap başına
  limit, kişi başına değil.
- `AI_USER_TOKEN_SECRET` sızarsa isteyen kendine token üretebilir. Sır sadece sunucu
  tarafında tutulmalı, istemciye asla gönderilmemeli.
