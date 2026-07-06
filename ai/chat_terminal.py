#!/usr/bin/env python3
"""
chat_terminal.py — Tercih Noktam AI · Terminal Arayüzü
────────────────────────────────────────────────────────
Tek terminalde scroll eden çıktı. Ekran temizlenmez, yeni terminal açılmaz.

Çalıştırma:
    python chat_terminal.py                   # gateway modu (varsayılan)
    python chat_terminal.py --mode local      # graph_agent'ı doğrudan çalıştır
    python chat_terminal.py --url http://...  # özel gateway URL
"""

import argparse
import asyncio
import os
import sys
import time
import textwrap
from io import StringIO

# ─── ANSI renk sabitleri ─────────────────────────────────────────────────────
R      = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

C_HEADER  = "\033[38;5;39m"    # mavi
C_USER    = "\033[38;5;220m"   # sarı
C_AI      = "\033[38;5;120m"   # yeşil
C_NER     = "\033[38;5;213m"   # pembe
C_ROUTER  = "\033[38;5;117m"   # açık mavi
C_PLANNER = "\033[38;5;208m"   # turuncu
C_AGENT   = "\033[38;5;159m"   # cyan
C_TOOL    = "\033[38;5;190m"   # sarı-yeşil
C_CACHE   = "\033[38;5;82m"    # yeşil
C_EVAL    = "\033[38;5;183m"   # mor
C_ERR     = "\033[38;5;196m"   # kırmızı
C_DIM     = "\033[38;5;240m"   # koyu gri
C_TIME    = "\033[38;5;247m"   # açık gri
C_SEP     = "\033[38;5;236m"   # ayırıcı çizgi rengi

# ─── Terminal genişliği ───────────────────────────────────────────────────────
try:
    import shutil
    COLS = shutil.get_terminal_size().columns
except Exception:
    COLS = 100
COLS = max(60, COLS)


# ─── Yardımcılar ─────────────────────────────────────────────────────────────
def sep(char="─", color=C_SEP):
    print(f"{color}{char * COLS}{R}")


def _colorize_log(line: str) -> str:
    """Log satırına kaynak node'a göre renk ver."""
    tags = {
        "[NER]":       C_NER,
        "[ROUTER]":    C_ROUTER,
        "[PLANNER]":   C_PLANNER,
        "[AGENT]":     C_AGENT,
        "[TOOL":       C_TOOL,
        "[FAST_LOOKUP]": C_TOOL,
        "[UNI_INFO]":  C_TOOL,
        "[CACHE]":     C_CACHE,
        "[EVAL]":      C_EVAL,
        "[LLM]":       C_AGENT,
        "[GRAPH]":     C_HEADER,
        "[YOK_CLIENT]": C_TOOL,
        "[RETRIEVER]": C_TOOL,
        "[DONE]":      C_AI,
        "[ERROR]":     C_ERR,
    }
    for tag, color in tags.items():
        if tag in line:
            return f"{color}{line}{R}"
    # hata emojileri
    if any(x in line for x in ("💥", "❌", "ERROR", "Traceback")):
        return f"{C_ERR}{line}{R}"
    if any(x in line for x in ("✅", "⚡", "HIT")):
        return f"{C_AI}{line}{R}"
    return f"{C_DIM}{line}{R}"


def print_log(line: str):
    """Log satırını renkli olarak yaz (scroll eder)."""
    print(_colorize_log(line))


def print_ai_answer(text: str):
    """AI yanıtını renkli ve wrap'li yaz."""
    sep("═")
    print(f"{C_AI}{BOLD} 🤖 AI YANITI{R}")
    sep("─")
    for raw_line in text.splitlines():
        if not raw_line.strip():
            print()
            continue
        if raw_line.startswith("## "):
            print(f"{C_HEADER}{BOLD}{raw_line}{R}")
        elif raw_line.startswith("# "):
            print(f"{C_HEADER}{BOLD}{raw_line}{R}")
        elif raw_line.startswith("### "):
            print(f"{C_ROUTER}{BOLD}{raw_line}{R}")
        elif raw_line.startswith("| ") or raw_line.startswith("|---"):
            print(f"{C_TOOL}{raw_line}{R}")
        elif raw_line.startswith("- ") or raw_line.startswith("* "):
            print(f"{C_AI}{raw_line}{R}")
        else:
            # Uzun satırları wrap et
            wrapped = textwrap.wrap(raw_line, width=COLS - 2)
            for w in wrapped:
                print(w)
    sep("═")


# ─── stdout yakalayıcı (local mod) ───────────────────────────────────────────
class LogCapture(StringIO):
    """print() çıktısını log olarak bastır."""
    def __init__(self, orig):
        super().__init__()
        self._orig = orig

    def write(self, data: str) -> int:
        if data and data.strip():
            for line in data.splitlines():
                if line.strip():
                    # log ekrana yaz
                    self._orig.write(_colorize_log(line) + "\n")
                    self._orig.flush()
        return len(data)

    def flush(self):
        try:
            self._orig.flush()
        except Exception:
            pass


# ─── Gateway modu ────────────────────────────────────────────────────────────
async def call_gateway(url: str, query: str, session_id: str) -> dict:
    import aiohttp
    school_key = os.getenv("AI_GATEWAY_SCHOOL_KEY", "test_key")
    headers = {"X-School-Key": school_key, "Content-Type": "application/json"}
    payload = {"query": query, "session_id": session_id}
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=120)
        ) as resp:
            return await resp.json()


# ─── Local modu ──────────────────────────────────────────────────────────────
async def call_local(query: str, session_id: str) -> dict:
    from graph_agent import app_graph
    inputs = {
        "messages": [("user", query)],
        "ner_context": {},
        "iteration_count": 0,
        "query_plan": None,
        "thinking_steps": [],
        "sub_question_results": {},
        "current_answer": None,
        "evaluation": None,
        "search_depth": 0,
    }
    config = {"configurable": {"thread_id": session_id}}
    result = await app_graph.ainvoke(inputs, config=config)
    last = result["messages"][-1]
    content = last.content if hasattr(last, "content") else str(last)
    return {"answer": content, "cached": False, "usage": {}}


# ─── Ana uygulama ─────────────────────────────────────────────────────────────
async def run(mode: str, gateway_url: str):
    session_id = f"terminal_{int(time.time())}"
    orig_stdout = sys.__stdout__

    # Başlık
    sep("═")
    print(f"{C_HEADER}{BOLD}{'  🎓 Tercih Noktam AI — Terminal Chat':^{COLS}}{R}")
    print(f"{C_DIM}{'  Mod: ' + mode.upper() + '  |  Session: ' + session_id:^{COLS}}{R}")
    sep("═")
    print(f"{C_DIM}  Komutlar: /yardim  /temizle  /session  Ctrl+C = çıkış{R}")
    sep()
    print()

    # Local modda stdout'u yakala (loglar inline görünsün)
    if mode == "local":
        sys.stdout = LogCapture(orig_stdout)

    msg_count = 0

    try:
        while True:
            # Kullanıcı girişi
            if mode == "local":
                sys.stdout = orig_stdout
            try:
                user_input = input(f"{C_USER}{BOLD}Sen › {R}").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if mode == "local":
                sys.stdout = LogCapture(orig_stdout)

            if not user_input:
                continue

            # Dahili komutlar
            if user_input.lower() in ("/yardim", "/help"):
                print(f"\n{C_HEADER}{BOLD}Komutlar:{R}")
                print(f"  {C_TOOL}/temizle{R}  — ekranı temizle (scroll sıfırlamaz, sadece boşluk bırakır)")
                print(f"  {C_TOOL}/session{R}  — session ID göster")
                print(f"  {C_TOOL}/yardim{R}   — bu ekran")
                print(f"  {C_TOOL}Ctrl+C{R}    — çıkış")
                print(f"\n{C_HEADER}{BOLD}Örnek sorular:{R}")
                print(f"  {C_DIM}50k say ile İstanbul bilgisayar mühendisliği{R}")
                print(f"  {C_DIM}İTÜ nasıl bir üniversite?{R}")
                print(f"  {C_DIM}Koç Üniversitesi burs oranları{R}")
                print(f"  {C_DIM}selam{R}")
                print()
                continue

            if user_input.lower() in ("/temizle", "/clear"):
                print("\n" * 5)
                sep()
                continue

            if user_input.lower() in ("/session",):
                print(f"{C_DIM}Session: {session_id}{R}")
                continue

            if user_input.lower() in ("/cikis", "/exit", "/quit"):
                break

            # ── Sorgu işle ────────────────────────────────────────────────
            msg_count += 1
            sep()
            print(f"{C_USER}{BOLD}[{msg_count}] {user_input}{R}")
            sep("─")
            print(f"{C_DIM}⏳ işleniyor...{R}")
            t_start = time.time()

            try:
                if mode == "local":
                    result = await call_local(user_input, session_id)
                else:
                    result = await call_gateway(gateway_url, user_input, session_id)

                elapsed = time.time() - t_start
                answer  = result.get("answer") or result.get("message") or ""
                # Boş yanıt — dict olarak gelmiş olabilir, str'e çevir
                if not answer:
                    # Tüm dict'i debug için göster ama kullanıcıya anlamlı mesaj ver
                    print(f"[WARN] Boş yanıt, ham sonuç: {result}")
                    answer = "Yanıt boş geldi. Lütfen tekrar dene."
                cached  = result.get("cached", False)
                tokens  = result.get("usage", {}).get("total_tokens", 0)

                cache_tag = f"{C_CACHE} ⚡ CACHE{R}" if cached else ""
                print(f"{C_TIME}✅ {elapsed:.1f}sn  |  tokens={tokens}{cache_tag}{R}")
                print_ai_answer(answer)

            except Exception as e:
                elapsed = time.time() - t_start
                print(f"{C_ERR}❌ Hata ({elapsed:.1f}sn): {type(e).__name__}: {e}{R}")
                sep()

    except KeyboardInterrupt:
        pass
    finally:
        if mode == "local":
            sys.stdout = orig_stdout
        print(f"\n{C_HEADER}{BOLD}Görüşürüz! 👋{R}\n")


# ─── Entry point ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Tercih Noktam AI — Terminal Chat")
    parser.add_argument(
        "--mode", choices=["local", "gateway"], default="gateway",
        help="local: graph_agent import | gateway: HTTP (varsayılan: gateway)"
    )
    parser.add_argument(
        "--url",
        default=os.getenv("GATEWAY_URL", "http://localhost:8003/b2b/ask_intelligent"),
        help="Gateway URL"
    )
    parser.add_argument(
        "--school-key",
        default=os.getenv("AI_GATEWAY_SCHOOL_KEY", "test_key"),
        help="API anahtarı"
    )
    args = parser.parse_args()
    os.environ["AI_GATEWAY_SCHOOL_KEY"] = args.school_key

    try:
        asyncio.run(run(args.mode, args.url))
    except KeyboardInterrupt:
        print(f"\n{C_HEADER}Çıkılıyor...{R}\n")


if __name__ == "__main__":
    main()
