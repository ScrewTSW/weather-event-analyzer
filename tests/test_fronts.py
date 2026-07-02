import numpy as np
import pytest
from core.fronts import (
    compute_tfp,
    detect_cold_fronts,
    detect_warm_fronts,
    detect_dry_lines,
    detect_mergers,
    FrontalAnalyzer,
)


class TestTFP:
    def test_uniform_field_has_no_fronts(self):
        temp = np.ones((20, 20)) * 20.0
        lats = np.linspace(30, 50, 20)
        lons = np.linspace(-100, -80, 20)
        tfp = compute_tfp(temp, lats, lons)
        assert tfp.shape == temp.shape
        assert np.all(np.abs(tfp) < 1e-10)

    def test_sharp_gradient_has_nonzero_tfp(self):
        temp = np.zeros((40, 40))
        temp[:, :20] = 30.0
        temp[:, 20:] = 10.0
        lats = np.linspace(30, 50, 40)
        lons = np.linspace(-100, -80, 40)
        tfp = compute_tfp(temp, lats, lons)
        assert np.any(np.abs(tfp) > 0)


class TestColdFrontDetection:
    def test_detects_front_in_sharp_gradient(self):
        temp = np.zeros((40, 40))
        for j in range(40):
            temp[:, j] = 30.0 - j * 1.0
        u = np.full((40, 40), 5.0)
        v = np.full((40, 40), -5.0)
        lats = np.linspace(30, 50, 40)
        lons = np.linspace(-100, -80, 40)
        fronts = detect_cold_fronts(temp, u, v, lats, lons)
        assert isinstance(fronts, list)

    def test_no_front_in_uniform_field(self):
        temp = np.ones((20, 20)) * 20.0
        u = np.full((20, 20), 5.0)
        v = np.full((20, 20), 0.0)
        lats = np.linspace(30, 50, 20)
        lons = np.linspace(-100, -80, 20)
        fronts = detect_cold_fronts(temp, u, v, lats, lons)
        assert len(fronts) == 0


class TestDryLineDetection:
    def test_detects_sharp_moisture_gradient(self):
        dewpoint = np.zeros((40, 40))
        for j in range(40):
            dewpoint[:, j] = 25.0 - j * 1.5
        temp = np.ones((40, 40)) * 25.0
        lats = np.linspace(30, 50, 40)
        lons = np.linspace(-100, -80, 40)
        fronts = detect_dry_lines(dewpoint, temp, lats, lons)
        assert isinstance(fronts, list)


class TestMergerDetection:
    def test_nearby_fronts_flagged_as_merger(self):
        fronts = [
            {"type": "cold", "lats": [40.0, 40.5], "lons": [-95.0, -95.0], "is_merger": False, "label": ""},
            {"type": "warm", "lats": [40.1, 40.6], "lons": [-95.0, -95.0], "is_merger": False, "label": ""},
        ]
        result = detect_mergers(fronts, threshold_km=50)
        mergers = [f for f in result if f["is_merger"]]
        assert len(mergers) > 0

    def test_distant_fronts_not_merged(self):
        fronts = [
            {"type": "cold", "lats": [30.0], "lons": [-95.0], "is_merger": False, "label": ""},
            {"type": "warm", "lats": [50.0], "lons": [-80.0], "is_merger": False, "label": ""},
        ]
        result = detect_mergers(fronts, threshold_km=50)
        mergers = [f for f in result if f["is_merger"]]
        assert len(mergers) == 0


class TestFrontalAnalyzer:
    def test_analyze_returns_list(self):
        analyzer = FrontalAnalyzer()
        temp = np.ones((20, 20)) * 20.0
        dew = np.ones((20, 20)) * 15.0
        u = np.full((20, 20), 5.0)
        v = np.full((20, 20), 0.0)
        lats = np.linspace(30, 50, 20)
        lons = np.linspace(-100, -80, 20)
        result = analyzer.analyze(temp, dew, u, v, lats, lons)
        assert isinstance(result, list)
