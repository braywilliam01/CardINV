"""Pure-function unit tests -- no DB, no HTTP client, no network.
Covers Card Search's exact-printing query parser and the fuzzy name
matcher used throughout inventory/checkout."""
from app.card_lookup import _parse_search_query
from app.fuzzy import find_best_match
from app.pokemon_common import normalize_collector_number


class TestParseSearchQuery:
    def test_plain_card_name(self):
        assert _parse_search_query("Lightning Bolt") == ("Lightning Bolt", "", "")

    def test_set_and_number_only(self):
        assert _parse_search_query("CLB 304") == ("", "CLB", "304")

    def test_name_comma_set_and_number(self):
        assert _parse_search_query("Lightning Bolt, CLB 304") == ("Lightning Bolt", "CLB", "304")

    def test_lowercase_set_code_normalized_to_upper(self):
        assert _parse_search_query("clb 304") == ("", "CLB", "304")

    def test_hash_before_number_tolerated(self):
        """The UI's own tip text shows the format as 'SET #', so a
        literal '#' the user typed needs to still parse correctly."""
        assert _parse_search_query("CLB #304") == ("", "CLB", "304")
        assert _parse_search_query("Lightning Bolt, CLB #304") == ("Lightning Bolt", "CLB", "304")

    def test_plain_two_word_name_not_misparsed_as_printing(self):
        """'Sol Ring' must never match SET=SOL/NUM=Ring -- the number
        token has to start with a digit for a reason."""
        assert _parse_search_query("Sol Ring") == ("Sol Ring", "", "")

    def test_name_with_comma_and_no_printing_suffix_kept_whole(self):
        """'Urza, Lord High Artificer' has a comma that's part of the
        name itself, not a printing separator -- what follows the
        comma doesn't parse as SET NUMBER, so the whole original query
        is kept as the name rather than truncating at the comma."""
        assert _parse_search_query("Urza, Lord High Artificer") == ("Urza, Lord High Artificer", "", "")

    def test_name_with_its_own_comma_plus_printing_suffix(self):
        """Splits on the *last* comma, so a comma-containing name can
        still have a printing reference appended after it."""
        assert _parse_search_query("Jhoira, Weatherlight Captain, CLB 5") == (
            "Jhoira, Weatherlight Captain",
            "CLB",
            "5",
        )

    def test_trailing_comma_no_printing_part(self):
        assert _parse_search_query("Sol Ring,") == ("Sol Ring,", "", "")

    def test_whitespace_stripped(self):
        assert _parse_search_query("  Sol Ring  ") == ("Sol Ring", "", "")

    def test_alphanumeric_set_code(self):
        # Some real Scryfall set codes are alphanumeric (e.g. "40k").
        assert _parse_search_query("40k 15") == ("", "40K", "15")

    def test_collector_number_with_trailing_letter(self):
        # Promo/variant collector numbers like "123a" are real.
        assert _parse_search_query("CLB 304a") == ("", "CLB", "304a")


class TestNormalizeCollectorNumber:
    def test_leading_zeros_stripped(self):
        assert normalize_collector_number("010") == "10"
        assert normalize_collector_number("001") == "1"

    def test_no_leading_zero_unchanged(self):
        assert normalize_collector_number("136") == "136"

    def test_all_zero_collapses_to_single_zero(self):
        assert normalize_collector_number("000") == "0"

    def test_alphanumeric_id_left_alone(self):
        # Promo sets (e.g. SWSH Black Star Promos) use ids like
        # "SWSH001" where the padding is part of the id itself, not a
        # plain zero-padded number -- must not be touched.
        assert normalize_collector_number("SWSH001") == "SWSH001"

    def test_trailing_letter_suffix_left_alone(self):
        assert normalize_collector_number("304a") == "304a"

    def test_blank_stays_blank(self):
        assert normalize_collector_number("") == ""
        assert normalize_collector_number(None) == ""


class TestFindBestMatch:
    def test_exact_match(self):
        assert find_best_match("Sol Ring", ["Sol Ring", "Lightning Bolt"]) == "Sol Ring"

    def test_case_difference_at_lower_threshold_still_matches(self):
        # WRatio scores a pure case difference ~75, below the default
        # 85 threshold -- so find_best_match is NOT strictly case-
        # insensitive out of the box; it needs a lower threshold or an
        # additional typo/difference budget alongside the case change.
        assert find_best_match("sol ring", ["Sol Ring", "Lightning Bolt"], threshold=70) == "Sol Ring"

    def test_typo_within_threshold_matches(self):
        assert find_best_match("Sol Rng", ["Sol Ring", "Lightning Bolt"]) == "Sol Ring"

    def test_no_candidates_returns_none(self):
        assert find_best_match("Sol Ring", []) is None

    def test_no_match_above_threshold_returns_none(self):
        assert find_best_match("Completely Unrelated Words", ["Sol Ring", "Lightning Bolt"]) is None

    def test_custom_threshold_is_respected(self):
        # A weak/partial match that clears a very low threshold...
        assert find_best_match("Sol", ["Sol Ring"], threshold=10) == "Sol Ring"
        # ...but not a very high (near-exact) one.
        assert find_best_match("Sol", ["Sol Ring"], threshold=95) is None
