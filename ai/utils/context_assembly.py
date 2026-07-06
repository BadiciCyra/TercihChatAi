from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


@dataclass
class ContextAssemblyResult:
    context_blocks: list[str]
    fallback_parts: list[str]
    seen_urls: list[str]


def build_grouped_context(
    grouped_results: Sequence[Sequence[Mapping[str, Any]]],
    labels: Sequence[str],
    *,
    heading: str | None = None,
    max_urls: int = 15,
    max_entries_per_group: int = 3,
    exclude_url_substrings: Sequence[str] = (),
) -> ContextAssemblyResult:
    """Search sonuçlarını düzenli markdown context'e dönüştür."""
    seen_urls: list[str] = []
    context_blocks: list[str] = []
    fallback_parts: list[str] = [heading] if heading else []

    for label, bucket in zip(labels, grouped_results):
        entries: list[str] = []
        for item in bucket:
            url = (item.get("url") or "").strip()
            if not url or url in seen_urls:
                continue

            lowered = url.lower()
            if any(skip in lowered for skip in exclude_url_substrings):
                continue

            seen_urls.append(url)
            title = (item.get("title") or "Başlıksız").strip()
            snippet = (item.get("snippet") or "")[:300].strip()
            entries.append(f"  Kaynak: {title} ({url})\n  İçerik: {snippet}")

            if len(seen_urls) >= max_urls:
                break

        if entries:
            context_blocks.append(f"[{label}]\n" + "\n\n".join(entries))
            fallback_parts.append(f"### {label}")
            for entry in entries[:max_entries_per_group]:
                fallback_parts.append(f"- {entry.splitlines()[0].replace('Kaynak: ', '')}")
            fallback_parts.append("")

        if len(seen_urls) >= max_urls:
            break

    return ContextAssemblyResult(context_blocks=context_blocks, fallback_parts=fallback_parts, seen_urls=seen_urls)


def build_link_supplement(
    all_links: Sequence[Mapping[str, Any]],
    *,
    max_total: int = 10,
) -> str | None:
    """Fast lookup için toplu link listesini kategori bazlı markdown'a çevir."""
    yorum_links: list[Mapping[str, Any]] = []
    forum_links: list[Mapping[str, Any]] = []
    diger_links: list[Mapping[str, Any]] = []

    for link in all_links:
        url = (link.get("url") or "").strip()
        if not url:
            continue
        u = url.lower()
        if any(d in u for d in ["eksisozluk", "ekşi", "reddit", "sikayetvar"]):
            yorum_links.append(link)
        elif any(d in u for d in ["forum", "donanimhaber", "unirehberi", "uludag", "kunduz", "tercihrobotu"]):
            forum_links.append(link)
        else:
            diger_links.append(link)

    parts: list[str] = []

    if yorum_links:
        parts.append("### 💬 Öğrenci Yorumları")
        for link in yorum_links[:3]:
            parts.append(f"- **[{link.get('title', 'Başlıksız')}]({link.get('url', '')})**\n  {(link.get('snippet') or '')[:220]}")

    if forum_links:
        parts.append("\n### 🗣️ Forum Tartışmaları")
        for link in forum_links[:3]:
            parts.append(f"- **[{link.get('title', 'Başlıksız')}]({link.get('url', '')})**\n  {(link.get('snippet') or '')[:220]}")

    if diger_links:
        parts.append("\n### 🔗 Tavsiyeler & Diğer Kaynaklar")
        for link in diger_links[:4]:
            parts.append(f"- **[{link.get('title', 'Başlıksız')}]({link.get('url', '')})**\n  {(link.get('snippet') or '')[:220]}")

    if not parts and all_links:
        parts.append("### 🔗 İlgili Kaynaklar")
        for link in all_links[:max_total]:
            parts.append(f"- **[{link.get('title', 'Başlıksız')}]({link.get('url', '')})**\n  {(link.get('snippet') or '')[:220]}")

    return "\n".join(parts) if parts else None