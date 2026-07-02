import numpy as np
from PIL import Image
from core.visualize import (
    add_timestamp_label,
    add_location_marker,
    compose_frame,
    create_atmosphere_chart,
    create_sounding_chart,
    create_hodograph_chart,
    create_timelapse_gif,
    create_timelapse_mp4,
)


class TestTimestampLabel:
    def test_adds_label_to_image(self):
        img = Image.new("RGBA", (500, 500), (0, 0, 0, 255))
        labeled = add_timestamp_label(img, "2026-05-31 09:00 UTC", "Himawari-9", "45.75N, 126.65E")
        assert labeled.size == img.size
        assert labeled.mode == "RGBA"


class TestLocationMarker:
    def test_adds_crosshair(self):
        img = Image.new("RGBA", (500, 500), (0, 0, 0, 255))
        marked = add_location_marker(img, 250, 250)
        assert marked.size == img.size


class TestComposeFrame:
    def test_composes_all_layers(self):
        base = Image.new("RGBA", (400, 400), (50, 50, 50, 255))
        frame = compose_frame(
            satellite_image=base,
            timestamp="2026-05-31 09:00 UTC",
            satellite_name="Himawari-9",
            location_str="45.75N, 126.65E",
            center_x=200,
            center_y=200,
        )
        assert frame.size == base.size
        assert frame.mode == "RGBA"

    def test_compose_without_optional_layers(self):
        base = Image.new("RGBA", (400, 400), (50, 50, 50, 255))
        frame = compose_frame(
            satellite_image=base,
            timestamp="2026-05-31 09:00 UTC",
            satellite_name="Himawari-9",
            location_str="45.75N, 126.65E",
        )
        assert frame is not None


class TestAtmosphereChart:
    def test_cape_cin_chart(self):
        data = {
            "timestamps": ["2026-05-31T07:00", "2026-05-31T08:00", "2026-05-31T09:00"],
            "variables": {
                "cape": {"data": [500, 1200, 1800], "unit": "J/kg"},
                "cin": {"data": [20, 15, 5], "unit": "J/kg"},
            },
        }
        fig = create_atmosphere_chart(data, chart_type="cape_cin")
        assert fig is not None

    def test_temp_dewpoint_chart(self):
        data = {
            "timestamps": ["2026-05-31T07:00", "2026-05-31T08:00"],
            "variables": {
                "temperature": {"data": [25.0, 28.0], "unit": "C"},
                "dewpoint": {"data": [18.0, 19.0], "unit": "C"},
            },
        }
        fig = create_atmosphere_chart(data, chart_type="temp_dewpoint")
        assert fig is not None

    def test_wind_chart(self):
        data = {
            "timestamps": ["2026-05-31T07:00", "2026-05-31T08:00"],
            "variables": {
                "wind_speed": {"data": [5.0, 8.0], "unit": "m/s"},
                "wind_gusts": {"data": [12.0, 18.0], "unit": "m/s"},
                "wind_direction": {"data": [225, 230], "unit": "deg"},
            },
        }
        fig = create_atmosphere_chart(data, chart_type="wind")
        assert fig is not None

    def test_cape_cin_with_helicity(self):
        data = {
            "timestamps": ["2026-07-02T07:00", "2026-07-02T08:00", "2026-07-02T09:00"],
            "variables": {
                "cape": {"data": [500, 1200, 1800], "unit": "J/kg"},
                "cin": {"data": [20, 15, 5], "unit": "J/kg"},
            },
        }
        levels = [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500,
                  400, 300, 250, 200, 150, 100, 70, 50, 30]
        n = len(levels)
        profile_hourly = {
            "source": "open-meteo-pressure",
            "pressure_levels": levels,
            "timestamps": ["2026-07-02T07:00", "2026-07-02T08:00", "2026-07-02T09:00"],
            "wind_speed": [[5 + j for j in range(n)] for _ in range(3)],
            "wind_direction": [[180 + j * 10 for j in range(n)] for _ in range(3)],
            "geopotential_height": [[100, 320, 500, 800, 1000, 1500, 1900,
                                     3000, 4200, 5600, 7200, 9200, 10400,
                                     11800, 13500, 15800, 17700, 19300, 22000]] * 3,
            "temperature": [[20 - j for j in range(n)] for _ in range(3)],
            "dewpoint": [[15 - j for j in range(n)] for _ in range(3)],
        }
        fig = create_atmosphere_chart(data, "cape_cin", profile_hourly=profile_hourly)
        assert fig is not None
        axes = fig.axes
        assert len(axes) >= 3, "Expected at least 3 axes (CAPE, CIN, SRH)"


class TestTimelapse:
    def test_creates_gif(self, tmp_path):
        frames = [Image.new("RGBA", (200, 200), color) for color in [(255,0,0,255), (0,255,0,255), (0,0,255,255)]]
        output = tmp_path / "test.gif"
        create_timelapse_gif(frames, str(output), fps=2)
        assert output.exists()
        assert output.stat().st_size > 100

    def test_creates_mp4(self, tmp_path):
        frames = [Image.new("RGBA", (200, 200), color) for color in [(255,0,0,255), (0,255,0,255), (0,0,255,255)]]
        output = tmp_path / "test.mp4"
        create_timelapse_mp4(frames, str(output), fps=2)
        assert output.exists()
        assert output.stat().st_size > 100


class TestSoundingChart:
    def _make_profile(self):
        levels = [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500,
                  400, 300, 250, 200, 150, 100, 70, 50, 30]
        n = len(levels)
        return {
            "source": "open-meteo-pressure",
            "synoptic_time": "2026-07-02T12:00",
            "pressure_levels": levels,
            "temperature": [30 - i * 2 for i in range(n)],
            "dewpoint": [20 - i * 2.5 for i in range(n)],
            "wind_speed": [5 + i * 1.5 for i in range(n)],
            "wind_direction": [180 + i * 5 for i in range(n)],
            "geopotential_height": [100 + i * 500 for i in range(n)],
            "latitude": 35.0,
            "longitude": -97.0,
            "timestamps": None,
        }

    def test_returns_figure(self):
        fig = create_sounding_chart(self._make_profile())
        assert fig is not None
        import matplotlib.figure
        assert isinstance(fig, matplotlib.figure.Figure)

    def test_has_cape_shading(self):
        fig = create_sounding_chart(self._make_profile())
        ax = fig.axes[0]
        collections = ax.collections
        assert len(collections) > 0, "Expected CAPE/CIN shading collections"


class TestHodographChart:
    def _make_profile(self):
        levels = [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500,
                  400, 300, 250, 200, 150, 100, 70, 50, 30]
        n = len(levels)
        return {
            "source": "open-meteo-pressure",
            "synoptic_time": "2026-07-02T12:00",
            "pressure_levels": levels,
            "temperature": [30 - i * 2 for i in range(n)],
            "dewpoint": [20 - i * 2.5 for i in range(n)],
            "wind_speed": [5 + i * 1.5 for i in range(n)],
            "wind_direction": [180 + i * 10 for i in range(n)],
            "geopotential_height": [100, 320, 500, 800, 1000, 1500, 1900,
                                    3000, 4200, 5600, 7200, 9200, 10400,
                                    11800, 13500, 15800, 17700, 19300, 22000],
            "latitude": 35.0,
            "longitude": -97.0,
            "timestamps": None,
        }

    def test_returns_figure(self):
        fig = create_hodograph_chart(self._make_profile())
        assert fig is not None
        import matplotlib.figure
        assert isinstance(fig, matplotlib.figure.Figure)

    def test_has_height_colored_segments(self):
        fig = create_hodograph_chart(self._make_profile())
        ax = fig.axes[0]
        assert len(ax.collections) > 0 or len(ax.lines) > 0
