from ai.utils.context_assembly import build_grouped_context, build_link_supplement


def test_grouped_context_deduplicates_and_builds_headings():
    buckets = [
        [
            {"title": "A", "url": "https://example.com/a", "snippet": "Alpha"},
            {"title": "A2", "url": "https://example.com/a", "snippet": "Duplicate"},
        ],
        [
            {"title": "B", "url": "https://example.com/b", "snippet": "Beta"},
        ],
    ]

    result = build_grouped_context(buckets, ["Genel", "Yorum"], heading="## Başlık")

    assert result.context_blocks
    assert "[Genel]" in result.context_blocks[0]
    assert "https://example.com/a" in result.context_blocks[0]
    assert result.seen_urls == ["https://example.com/a", "https://example.com/b"]
    assert result.fallback_parts[0] == "## Başlık"


def test_link_supplement_groups_sources():
    links = [
        {"title": "Forum", "url": "https://forum.example.com/post", "snippet": "Forum text"},
        {"title": "Review", "url": "https://eksisozluk.com/x", "snippet": "Review text"},
        {"title": "Other", "url": "https://uni.example.edu.tr", "snippet": "Other text"},
    ]

    supplement = build_link_supplement(links)

    assert supplement is not None
    assert "### 💬 Öğrenci Yorumları" in supplement
    assert "### 🗣️ Forum Tartışmaları" in supplement
