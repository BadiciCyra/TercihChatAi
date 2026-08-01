"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ALL_MODES, MODES, type Mode, type ModeId } from "@/lib/modes";
import { Markdown } from "@/components/Markdown";

type Role = "user" | "assistant";

interface ChatMsg {
  id: string;
  role: Role;
  content: string;
  pending?: boolean;
  error?: boolean;
  cached?: boolean;
  tokens?: number | null;
  elapsed?: number;
}

function uid() {
  try {
    return crypto.randomUUID();
  } catch {
    return Math.random().toString(36).slice(2) + Date.now().toString(36);
  }
}

// Forum'dan gelen imzalı kullanıcı token'ının saklandığı anahtar.
const TOKEN_STORAGE_KEY = "tn_ai_user_token";

const STAR = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
    <path d="M12 2 9.9 8.6 3 9.2l5.2 4.4L6.6 21 12 17.3 17.4 21l-1.6-7.4L21 9.2l-6.9-.6L12 2Z" />
  </svg>
);

export function ChatInterface() {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [activeMode, setActiveMode] = useState<ModeId | null>("wizard");
  const [busy, setBusy] = useState(false);

  const sessionId = useRef<string>("");
  const userToken = useRef<string>("");
  // "Yeni sohbet"e basıldığında true olur; SADECE sonraki ilk istekte
  // gönderilir ve hemen sıfırlanır. Backend bu istekte cevap cache'ini
  // okumaz (taze üretir) ama cache'e yazmaya devam eder.
  const freshChat = useRef<boolean>(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  // Sohbet bazlı session id — her sohbet kendi hafızasını taşır.
  // "Yeni sohbet" yeni bir id üretir; backend (resolve_thread_id) yeni
  // thread açar ve önceki konuşmanın bağlamı sıfırlanır.
  // (Rate-limit API key'e, cevap cache'i soru+mod'a bağlı — session id'nin
  // kalıcı olmasının onlara etkisi yok.)
  useEffect(() => {
    if (!sessionId.current) sessionId.current = "web-" + uid();
  }, []);

  // Forum kullanıcı token'ı — günlük soru kotası buna göre işler.
  // Forum, asistana yönlendirirken token'ı adres çubuğunun # kısmında taşır
  // (/#t=<jwt>). Fragment sunucuya gönderilmez; sunucu log'larına ve
  // referrer'a sızmaması için query string yerine bunu kullanıyoruz.
  // Okuduktan sonra adres çubuğundan temizlenir, localStorage'da saklanır.
  useEffect(() => {
    try {
      const m = window.location.hash.match(/[#&]t=([^&]+)/);
      if (m) {
        userToken.current = decodeURIComponent(m[1]);
        localStorage.setItem(TOKEN_STORAGE_KEY, userToken.current);
        window.history.replaceState(
          null,
          "",
          window.location.pathname + window.location.search,
        );
      } else {
        userToken.current = localStorage.getItem(TOKEN_STORAGE_KEY) ?? "";
      }
    } catch {
      // localStorage kapalıysa sessizce token'sız devam et.
    }
  }, []);

  const hasChat = messages.length > 0;

  // Yeni mesajda en alta kaydır.
  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  // Textarea otomatik büyüsün.
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
  }, [input]);

  const send = useCallback(
    async (raw: string) => {
      const query = raw.trim();
      if (!query || busy) return;

      const userMsg: ChatMsg = { id: uid(), role: "user", content: query };
      const aiId = uid();
      const aiMsg: ChatMsg = {
        id: aiId,
        role: "assistant",
        content: "",
        pending: true,
      };
      setMessages((m) => [...m, userMsg, aiMsg]);
      setInput("");
      setBusy(true);

      // fresh bayrağı YALNIZCA bu ilk istekte geçerli; hemen tüketiliyor ki
      // sohbetin geri kalanı normal cache davranışına dönsün.
      const useFresh = freshChat.current;
      freshChat.current = false;

      const started = Date.now();
      try {
        const resp = await fetch("/api/ask", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(userToken.current
              ? { "X-User-Token": userToken.current }
              : {}),
          },
          body: JSON.stringify({
            query,
            session_id: sessionId.current,
            mode: activeMode,
            ...(useFresh ? { fresh: true } : {}),
          }),
        });
        const data = await resp.json();
        const elapsed = (Date.now() - started) / 1000;

        setMessages((m) =>
          m.map((msg) =>
            msg.id === aiId
              ? resp.ok && data.answer
                ? {
                    ...msg,
                    pending: false,
                    content: data.answer as string,
                    cached: Boolean(data.cached),
                    tokens: data.tokens ?? null,
                    elapsed,
                  }
                : {
                    ...msg,
                    pending: false,
                    error: true,
                    content:
                      (data && (data.error as string)) ||
                      "Bir şeyler ters gitti. Lütfen tekrar dene.",
                    elapsed,
                  }
              : msg,
          ),
        );
      } catch {
        const elapsed = (Date.now() - started) / 1000;
        setMessages((m) =>
          m.map((msg) =>
            msg.id === aiId
              ? {
                  ...msg,
                  pending: false,
                  error: true,
                  content:
                    "Asistana ulaşılamadı. Bağlantını kontrol edip tekrar dene.",
                  elapsed,
                }
              : msg,
          ),
        );
      } finally {
        setBusy(false);
      }
    },
    [busy, activeMode],
  );

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  const currentMode: Mode =
    ALL_MODES.find((mo) => mo.id === activeMode) ?? MODES[0];

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 sm:px-6">
      {hasChat ? (
        <MessageList messages={messages} scrollRef={scrollRef} />
      ) : (
        <Hero mode={currentMode} onExample={(t) => send(t)} busy={busy} />
      )}

      <Composer
        input={input}
        setInput={setInput}
        taRef={taRef}
        onKeyDown={onKeyDown}
        onSend={() => send(input)}
        busy={busy}
        activeMode={activeMode}
        setActiveMode={setActiveMode}
        onReset={
          hasChat
            ? () => {
                setMessages([]);
                // Yeni sohbet = yeni session = backend'de taze hafıza.
                sessionId.current = "web-" + uid();
                // Bir sonraki soru cevap cache'inden değil, taze üretilsin.
                freshChat.current = true;
              }
            : undefined
        }
      />
    </div>
  );
}

/* ---------------- Hero (bos sohbet) ---------------- */

function Hero({
  mode,
  onExample,
  busy,
}: {
  mode: Mode;
  onExample: (t: string) => void;
  busy: boolean;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center py-8 text-center">
      <div className="mb-5 inline-flex items-center gap-1.5 rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700 dark:border-blue-900 dark:bg-blue-950/60 dark:text-blue-300">
        {STAR}
        YÖK Atlas destekli yapay zekâ
      </div>
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
        <span className="gradient-text">Tercih Asistanı</span>
      </h1>
      <p className="mt-3 max-w-lg text-[15px] leading-relaxed text-neutral-600 dark:text-neutral-300">
        Sıralamana, ilgi alanlarına ve hedeflerine göre sana en uygun üniversite
        ve bölümleri yapay zekâ ile keşfet.
      </p>

      <p className="mt-8 mb-2.5 text-xs font-medium text-neutral-400 dark:text-neutral-500">
        {mode.emoji} {mode.label} — örnek sorular
      </p>
      <div className="flex w-full max-w-xl flex-col gap-2">
        {mode.examples.map((ex) => (
          <button
            key={ex}
            type="button"
            disabled={busy}
            onClick={() => onExample(ex)}
            className="group flex items-center gap-2.5 rounded-xl border border-neutral-200 bg-white/70 px-3.5 py-2.5 text-left text-sm text-neutral-700 shadow-xs transition-all hover:-translate-y-0.5 hover:border-blue-300 hover:text-blue-700 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-60 dark:border-neutral-800 dark:bg-neutral-900/60 dark:text-neutral-200 dark:hover:border-blue-800 dark:hover:text-blue-300"
          >
            <span className="text-blue-500 transition-transform group-hover:translate-x-0.5 dark:text-blue-400">
              →
            </span>
            {ex}
          </button>
        ))}
      </div>
    </div>
  );
}

/* ---------------- Mesaj listesi ---------------- */

function MessageList({
  messages,
  scrollRef,
}: {
  messages: ChatMsg[];
  scrollRef: React.RefObject<HTMLDivElement | null>;
}) {
  return (
    <div
      ref={scrollRef}
      className="scroll-slim flex-1 space-y-5 overflow-y-auto py-6"
    >
      {messages.map((msg) =>
        msg.role === "user" ? (
          <div key={msg.id} className="msg-enter flex justify-end">
            <div className="max-w-[85%] rounded-2xl rounded-br-md bg-gradient-to-r from-blue-600 to-indigo-600 px-4 py-2.5 text-[15px] leading-relaxed text-white shadow-sm">
              {msg.content}
            </div>
          </div>
        ) : (
          <AssistantMessage key={msg.id} msg={msg} />
        ),
      )}
    </div>
  );
}

function AssistantMessage({ msg }: { msg: ChatMsg }) {
  return (
    <div className="msg-enter flex gap-3">
      <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-blue-600 to-indigo-600 text-white shadow-sm">
        {STAR}
      </div>
      <div className="min-w-0 flex-1">
        {msg.pending ? (
          <div className="flex items-center gap-1.5 py-2 text-neutral-400 dark:text-neutral-500">
            <span className="typing-dot" />
            <span className="typing-dot" />
            <span className="typing-dot" />
            <span className="ml-1.5 text-xs">düşünüyor…</span>
          </div>
        ) : msg.error ? (
          <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300">
            {msg.content}
          </div>
        ) : (
          <div className="rounded-2xl rounded-tl-md border border-neutral-200 bg-white px-4 py-3 shadow-xs dark:border-neutral-800 dark:bg-neutral-900">
            <Markdown>{msg.content}</Markdown>
          </div>
        )}

        {!msg.pending && !msg.error && (
          <div className="mt-1.5 flex items-center gap-2 pl-1 text-[11px] text-neutral-400 dark:text-neutral-500">
            {typeof msg.elapsed === "number" && (
              <span>{msg.elapsed.toFixed(1)} sn</span>
            )}
            {typeof msg.tokens === "number" && msg.tokens > 0 && (
              <span>· {msg.tokens} token</span>
            )}
            {msg.cached ? (
              <span className="rounded-full bg-emerald-100 px-1.5 py-0.5 font-semibold text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                önbellek
              </span>
            ) : (
              <span className="rounded-full bg-blue-100 px-1.5 py-0.5 font-semibold text-blue-700 dark:bg-blue-950 dark:text-blue-300">
                taze
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------- Composer (mod + input) ---------------- */

function Composer({
  input,
  setInput,
  taRef,
  onKeyDown,
  onSend,
  busy,
  activeMode,
  setActiveMode,
  onReset,
}: {
  input: string;
  setInput: (v: string) => void;
  taRef: React.RefObject<HTMLTextAreaElement | null>;
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  onSend: () => void;
  busy: boolean;
  activeMode: ModeId | null;
  setActiveMode: (m: ModeId | null) => void;
  onReset?: () => void;
}) {
  return (
    <div className="sticky bottom-0 bg-gradient-to-t from-neutral-50 via-neutral-50 to-transparent pb-4 pt-3 dark:from-neutral-950 dark:via-neutral-950">
      {/* Mod hapları */}
      <div className="scroll-slim mb-2.5 flex items-center gap-1.5 overflow-x-auto pb-1">
        {ALL_MODES.map((mo) => {
          const active = mo.id === activeMode;
          return (
            <button
              key={mo.id ?? "auto"}
              type="button"
              onClick={() => setActiveMode(mo.id)}
              title={mo.desc}
              className={
                "flex shrink-0 items-center gap-1 rounded-full border px-3 py-1 text-xs font-medium transition-colors " +
                (active
                  ? "border-blue-400 bg-blue-50 text-blue-700 dark:border-blue-600 dark:bg-blue-950/60 dark:text-blue-300"
                  : "border-neutral-200 text-neutral-500 hover:border-neutral-300 hover:text-neutral-700 dark:border-neutral-800 dark:text-neutral-400 dark:hover:text-neutral-200")
              }
            >
              <span>{mo.emoji}</span>
              {mo.short}
            </button>
          );
        })}
        {onReset && (
          <button
            type="button"
            onClick={onReset}
            className="ml-auto flex shrink-0 items-center gap-1 rounded-full border border-neutral-200 px-3 py-1 text-xs font-medium text-neutral-500 transition-colors hover:border-red-300 hover:text-red-600 dark:border-neutral-800 dark:text-neutral-400 dark:hover:border-red-800 dark:hover:text-red-400"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
            </svg>
            Yeni sohbet
          </button>
        )}
      </div>

      {/* Input kutusu */}
      <div className="flex items-end gap-2 rounded-2xl border border-neutral-200 bg-white p-2 shadow-sm transition-colors focus-within:border-blue-400 dark:border-neutral-800 dark:bg-neutral-900 dark:focus-within:border-blue-600">
        <textarea
          ref={taRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder="Sıralamanı, ilgi alanını ya da merak ettiğin bölümü yaz…"
          className="max-h-[200px] flex-1 resize-none bg-transparent px-2 py-1.5 text-[15px] leading-relaxed outline-none placeholder:text-neutral-400 dark:placeholder:text-neutral-500"
        />
        <button
          type="button"
          onClick={onSend}
          disabled={busy || !input.trim()}
          aria-label="Gönder"
          className="shimmer flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-sm transition-all hover:from-blue-700 hover:to-indigo-700 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" className="animate-spin" aria-hidden>
              <path d="M21 12a9 9 0 1 1-6.2-8.6" />
            </svg>
          ) : (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M22 2 11 13M22 2l-7 20-4-9-9-4 20-7Z" />
            </svg>
          )}
        </button>
      </div>
      <p className="mt-1.5 text-center text-[11px] text-neutral-400 dark:text-neutral-600">
        Tercih Asistanı yapay zekâdır; önemli kararlarda bilgileri doğrula.
      </p>
    </div>
  );
}
