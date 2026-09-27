"""Tests for services.coordinates — coordinate text parsing/normalisation."""
import pytest

from services.coordinates import is_unsigned_dms, parse_coordinate, to_decimal_string


class TestParseDecimal:
    def test_plain_decimal(self):
        assert parse_coordinate("31.3303162") == pytest.approx(31.3303162)

    def test_signed_decimal_longitude(self):
        assert parse_coordinate("-100.4570705", is_longitude=True) == pytest.approx(-100.4570705)

    def test_integer_degrees(self):
        assert parse_coordinate("52") == pytest.approx(52.0)

    def test_partial_minus_is_unparseable(self):
        assert parse_coordinate("-") is None


class TestParseDMS:
    def test_space_separated_dms(self):
        # 31 32 51 -> 31 + 32/60 + 51/3600
        assert parse_coordinate("31 32 51") == pytest.approx(31.5475, abs=1e-4)

    def test_colon_separated_dms(self):
        assert parse_coordinate("31:32:51") == pytest.approx(31.5475, abs=1e-4)

    def test_symbol_dms(self):
        assert parse_coordinate("31° 32' 51\"") == pytest.approx(31.5475, abs=1e-4)

    def test_degrees_minutes_only(self):
        assert parse_coordinate("31 30") == pytest.approx(31.5)

    def test_hemisphere_west_is_negative(self):
        assert parse_coordinate("100 27 25 W", is_longitude=True) == pytest.approx(-100.4569, abs=1e-3)

    def test_hemisphere_south_is_negative(self):
        assert parse_coordinate("33 51 36 S") == pytest.approx(-33.86, abs=1e-2)

    def test_negative_dms_degrees(self):
        assert parse_coordinate("-100 27 25", is_longitude=True) == pytest.approx(-100.4569, abs=1e-3)


class TestRejects:
    def test_blank(self):
        assert parse_coordinate("") is None
        assert parse_coordinate("   ") is None
        assert parse_coordinate(None) is None

    def test_non_numeric(self):
        assert parse_coordinate("abc") is None

    def test_too_many_tokens(self):
        assert parse_coordinate("1 2 3 4") is None

    def test_minutes_out_of_range(self):
        assert parse_coordinate("31 75 00") is None

    def test_latitude_out_of_range(self):
        assert parse_coordinate("123.4", is_longitude=False) is None

    def test_longitude_out_of_range(self):
        assert parse_coordinate("200", is_longitude=True) is None


class TestRoundTrip:
    def test_dms_normalises_to_decimal_string(self):
        val = parse_coordinate("31 32 51")
        assert to_decimal_string(val) == "31.5475"

    def test_decimal_passthrough_is_stable(self):
        # A canonical decimal string parses back to itself.
        val = parse_coordinate("31.3303162")
        s = to_decimal_string(val)
        assert parse_coordinate(s) == pytest.approx(val)

    def test_trims_trailing_zeros(self):
        assert to_decimal_string(31.5) == "31.5"
        assert to_decimal_string(52.0) == "52"

    def test_negative_zero(self):
        assert to_decimal_string(-0.0) == "0"


class TestIsUnsignedDms:
    """The hemisphere of these values is an assumption, so the config
    migration warns on them and on nothing else."""

    @pytest.mark.parametrize("text", ["31 32 51", "31:32:51", "31° 32' 51\"", "100 27", " 31 32 51 "])
    def test_bare_dms_is_unsigned(self, text):
        assert is_unsigned_dms(text) is True

    @pytest.mark.parametrize("text", ["31.33", "-100.457", "100.457", "-100 27 25", "100 27 25 W",
                                      "N 31 32 51", "31 32 51 s", "", "   ", None])
    def test_decimal_signed_lettered_and_blank_are_not(self, text):
        assert is_unsigned_dms(text) is False
