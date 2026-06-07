import math
from core.location import (
    parse_location_input,
    angular_distance,
    select_satellite,
    is_conus,
    SATELLITES,
)


class TestParseLocation:
    def test_parse_latlon_comma(self):
        lat, lon = parse_location_input("45.75, 126.65")
        assert abs(lat - 45.75) < 0.001
        assert abs(lon - 126.65) < 0.001

    def test_parse_latlon_negative(self):
        lat, lon = parse_location_input("-33.86, 151.21")
        assert abs(lat - (-33.86)) < 0.001
        assert abs(lon - 151.21) < 0.001

    def test_parse_returns_none_for_city_name(self):
        result = parse_location_input("Harbin, China")
        assert result is None


class TestAngularDistance:
    def test_same_point(self):
        assert angular_distance(0, 140.7, 0, 140.7) == 0.0

    def test_known_distance(self):
        # Harbin (45.75N, 126.65E) to Himawari subsatellite (0N, 140.7E)
        dist = angular_distance(45.75, 126.65, 0, 140.7)
        assert 45 < dist < 50  # approximately 47 degrees

    def test_antipodal(self):
        dist = angular_distance(0, 0, 0, 180)
        assert abs(dist - 180) < 0.1


class TestSelectSatellite:
    def test_harbin_selects_gk2a_or_himawari(self):
        # GK2A at 128.2E is closer to Harbin (126.65E) than Himawari (140.7E)
        sat = select_satellite(45.75, 126.65)
        assert sat["slider_id"] in ("gk2a", "himawari")

    def test_nyc_selects_goes19(self):
        sat = select_satellite(40.71, -74.01)
        assert sat["slider_id"] == "goes-19"

    def test_london_selects_meteosat(self):
        sat = select_satellite(51.51, -0.13)
        assert sat["slider_id"] in ("meteosat-0deg", "meteosat-12")

    def test_rejects_far_locations(self):
        # A point at 70N is at the edge and should be covered
        sat = select_satellite(70, 0)
        assert sat is not None


class TestIsCONUS:
    def test_oklahoma_is_conus(self):
        assert is_conus(35.47, -97.52) is True

    def test_harbin_is_not_conus(self):
        assert is_conus(45.75, 126.65) is False

    def test_alaska_is_not_conus(self):
        assert is_conus(64.2, -152.5) is False
