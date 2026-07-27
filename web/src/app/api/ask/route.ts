import { NextResponse } from "next/server";

// Vercel: fonksiyon süre limiti — agentic RAG akışı uzun sürebilir.
// (Northflank/self-host ortamlarında etkisi yoktur.)
export const maxDuration = 60;

// Gateway ayarları — yalnızca sunucu tarafında okunur (X-School-Key sızmaz).
const GATEWAY_URL =
  process.env.AI_GATEWAY_URL ??
  "https://p01--ai-gateway--z9ktjbpxmd48.code.run";
const SCHOOL_KEY = process.env.AI_SCHOOL_KEY ?? "test_key";

const VALID_MODES = ["wizard", "research", "career", "guidance"] as const;
type Mode = (typeof VALID_MODES)[number];

interface AskBody {
  query?: unknown;
  session_id?: unknown;
  mode?: unknown;
}

export async function POST(request: Request) {
  let body: AskBody;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { error: "Geçersiz istek gövdesi." },
      { status: 400 },
    );
  }

  const query = typeof body.query === "string" ? body.query.trim() : "";
  if (!query) {
    return NextResponse.json({ error: "Soru boş olamaz." }, { status: 400 });
  }

  const sessionId =
    typeof body.session_id === "string" && body.session_id
      ? body.session_id
      : "web-session";

  // mode: sadece geçerli değerler gönderilir; null/geçersiz → otomatik (gönderilmez).
  const mode =
    typeof body.mode === "string" && VALID_MODES.includes(body.mode as Mode)
      ? (body.mode as Mode)
      : undefined;

  const payload: Record<string, unknown> = { query, session_id: sessionId };
  if (mode) payload.mode = mode;

  // Forum'un ürettiği imzalı kullanıcı token'ı — günlük kota bunun üzerinden
  // uygulanır. Yoksa istek eskisi gibi (kotasız) geçer; zorunluluk gateway
  // tarafında REQUIRE_USER_TOKEN ile açılır.
  const userToken = request.headers.get("X-User-Token") ?? "";

  // Gateway agentic RAG akışı uzun sürebilir — geniş timeout.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 90_000);

  try {
    const resp = await fetch(`${GATEWAY_URL}/b2b/ask_intelligent`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-School-Key": SCHOOL_KEY,
        ...(userToken ? { "X-User-Token": userToken } : {}),
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });

    const text = await resp.text();
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(text);
    } catch {
      data = { answer: text };
    }

    if (!resp.ok) {
      const detail =
        (typeof data.detail === "string" && data.detail) ||
        `Gateway hatası (HTTP ${resp.status}).`;
      return NextResponse.json({ error: detail }, { status: resp.status });
    }

    return NextResponse.json({
      answer:
        (typeof data.answer === "string" && data.answer) ||
        (typeof data.message === "string" && data.message) ||
        "",
      cached: Boolean(data.cached),
      tokens:
        typeof data.usage === "object" && data.usage !== null
          ? (data.usage as { total_tokens?: number }).total_tokens ?? null
          : null,
    });
  } catch (err) {
    const aborted = err instanceof Error && err.name === "AbortError";
    return NextResponse.json(
      {
        error: aborted
          ? "Yanıt zaman aşımına uğradı. Lütfen tekrar dene."
          : "Asistana ulaşılamadı. Bağlantını kontrol edip tekrar dene.",
      },
      { status: aborted ? 504 : 502 },
    );
  } finally {
    clearTimeout(timeout);
  }
}
