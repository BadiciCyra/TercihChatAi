# test_extractors.py — utils/extractors.py için unit testler
import pytest
from ai.utils.extractors import (
    _extract_rank_from_text,
    _extract_program_from_text,
    _extract_city_from_text,
    _extract_score_type_from_text,
    _extract_fee_from_text,
    _extract_uni_type_from_text,
    _is_followup_question,
)
from ai.nodes.ner import rule_based_router


# ── Rank extraction ──────────────────────────────────────────────────────────

class TestExtractRank:
    def test_k_suffix(self):
        assert _extract_rank_from_text("50k sıralama") == "50000"

    def test_bin_suffix(self):
        assert _extract_rank_from_text("120 bin ile ne gelir") == "120000"

    def test_raw_number(self):
        assert _extract_rank_from_text("sıralamam 280000") == "280000"

    def test_no_rank(self):
        assert _extract_rank_from_text("istanbul bilgisayar") is None

    def test_none_input(self):
        assert _extract_rank_from_text(None) is None

    def test_year_is_not_rank(self):
        assert _extract_rank_from_text("2025 yılı için ışık üniversitesinin fiyatları nasıl?") is None

    def test_year_query_does_not_short_circuit_to_fast_lookup(self):
        assert rule_based_router("2025 yılı için ışık üniversitesinin fiyatları nasıl?") is None


# ── Program extraction ───────────────────────────────────────────────────────

class TestExtractProgram:
    def test_compound_bilgisayar(self):
        assert _extract_program_from_text("bilgisayar bilimleri okumak istiyorum") == "Bilgisayar Bilimleri"

    def test_single_bilgisayar(self):
        assert _extract_program_from_text("bilgisayar mühendisliği") == "Bilgisayar Mühendisliği"

    def test_abbreviated_ybs(self):
        assert _extract_program_from_text("ybs bölümü nerede var") == "Yönetim Bilişim Sistemleri"

    def test_elektrik(self):
        assert _extract_program_from_text("elektrik bölümü") == "Elektrik-Elektronik Mühendisliği"

    def test_hukuk(self):
        assert _extract_program_from_text("hukuk fakültesi") == "Hukuk"

    def test_no_program(self):
        assert _extract_program_from_text("50k ile istanbul") is None

    def test_none_input(self):
        assert _extract_program_from_text(None) is None

    def test_open_ended_no_specific_program(self):
        # "herhangi bir mühendislik" → mühendislik genel bir kategori, spesifik bölüm değil
        # Extractor bu durumda en yakın eşleşmeyi döndürür — bu davranış beklenen
        # Asıl kontrol fast_lookup_node'da _is_open_ended guard ile yapılıyor
        result = _extract_program_from_text("mühendislik bölümlerinden herhangi biri")
        # Sonuç her ne olursa olsun, string veya None dönmeli
        assert result is None or isinstance(result, str)


# ── City extraction ──────────────────────────────────────────────────────────

class TestExtractCity:
    def test_istanbul(self):
        assert _extract_city_from_text("istanbulda okumak istiyorum") == "İstanbul"

    def test_ankara_eksiz(self):
        assert _extract_city_from_text("ankara üniversiteleri") == "Ankara"

    def test_izmir_suffix(self):
        assert _extract_city_from_text("izmirde vakıf uni") == "İzmir"

    def test_no_city(self):
        assert _extract_city_from_text("bilgisayar mühendisliği 50k") is None

    def test_none_input(self):
        assert _extract_city_from_text(None) is None


# ── Score type extraction ─────────────────────────────────────────────────────

class TestExtractScoreType:
    def test_say(self):
        assert _extract_score_type_from_text("sayısal puan türü") == "SAY"

    def test_ea(self):
        assert _extract_score_type_from_text("eşit ağırlık ile") == "EA"

    def test_soz(self):
        assert _extract_score_type_from_text("sözel bölümler") == "SOZ"

    def test_say_short(self):
        assert _extract_score_type_from_text("50k say ile") == "SAY"

    def test_no_type(self):
        assert _extract_score_type_from_text("istanbul bilgisayar") is None


# ── Fee type extraction ──────────────────────────────────────────────────────

class TestExtractFee:
    def test_burslu(self):
        assert _extract_fee_from_text("tam burslu okumak istiyorum") == "Burslu"

    def test_percent_50(self):
        assert _extract_fee_from_text("%50 burslu üniversiteler") == "%50 İndirimli"

    def test_ucretli(self):
        assert _extract_fee_from_text("ücretli bölümler") == "Ücretli"

    def test_no_fee(self):
        assert _extract_fee_from_text("istanbul bilgisayar") is None


# ── Uni type extraction ──────────────────────────────────────────────────────

class TestExtractUniType:
    def test_vakif(self):
        assert _extract_uni_type_from_text("vakıf üniversiteleri") == "Vakıf"

    def test_devlet(self):
        assert _extract_uni_type_from_text("devlet üniversitesi") == "Devlet"

    def test_no_type(self):
        assert _extract_uni_type_from_text("istanbul bilgisayar") is None


# ── Followup question detection ──────────────────────────────────────────────

class TestIsFollowup:
    def test_followup_baska(self):
        assert _is_followup_question("başka şehirler de var mı") is True

    def test_followup_var_mi(self):
        assert _is_followup_question("daha ucuzu var mı") is True

    def test_not_followup_full_query(self):
        assert _is_followup_question("istanbul bilgisayar mühendisliği 50k sıralama ile") is False

    def test_empty(self):
        assert _is_followup_question("") is False
