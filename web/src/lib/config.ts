// Foruma (kimlik otoritesi / IdP) dönüş linki — client tarafında da gerekli.
export const FORUM_URL =
  process.env.NEXT_PUBLIC_FORUM_URL ?? "https://tercihnoktam.com";

// Not: AI_GATEWAY_URL ve AI_SCHOOL_KEY sadece sunucu tarafında (app/api/ask)
// okunur; anahtar tarayıcıya sızmasın diye burada export edilmez.
