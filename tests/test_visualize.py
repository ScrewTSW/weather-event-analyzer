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
from core.models.pressure_levels.base import PressureLevelSnapshot, PressureLevelHourly


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
        from core.models.atmosphere.base import AtmosphereData, VariableTimeSeries
        data = AtmosphereData(
            source="test",
            timestamps=["2026-05-31T07:00", "2026-05-31T08:00", "2026-05-31T09:00"],
            variables={
                "cape": VariableTimeSeries(data=[500, 1200, 1800], unit="J/kg"),
                "cin": VariableTimeSeries(data=[20, 15, 5], unit="J/kg"),
            },
        )
        fig = create_atmosphere_chart(data, chart_type="cape_cin")
        assert fig is not None

    def test_temp_dewpoint_chart(self):
        from core.models.atmosphere.base import AtmosphereData, VariableTimeSeries
        data = AtmosphereData(
            source="test",
            timestamps=["2026-05-31T07:00", "2026-05-31T08:00"],
            variables={
                "temperature": VariableTimeSeries(data=[25.0, 28.0], unit="C"),
                "dewpoint": VariableTimeSeries(data=[18.0, 19.0], unit="C"),
            },
        )
        fig = create_atmosphere_chart(data, chart_type="temp_dewpoint")
        assert fig is not None

    def test_wind_chart(self):
        from core.models.atmosphere.base import AtmosphereData, VariableTimeSeries
        data = AtmosphereData(
            source="test",
            timestamps=["2026-05-31T07:00", "2026-05-31T08:00"],
            variables={
                "wind_speed": VariableTimeSeries(data=[5.0, 8.0], unit="m/s"),
                "wind_gusts": VariableTimeSeries(data=[12.0, 18.0], unit="m/s"),
                "wind_direction": VariableTimeSeries(data=[225, 230], unit="deg"),
            },
        )
        fig = create_atmosphere_chart(data, chart_type="wind")
        assert fig is not None

    def test_cape_cin_with_helicity(self):
        from core.models.atmosphere.base import AtmosphereData, VariableTimeSeries
        data = AtmosphereData(
            source="test",
            timestamps=["2026-07-02T07:00", "2026-07-02T08:00", "2026-07-02T09:00"],
            variables={
                "cape": VariableTimeSeries(data=[500, 1200, 1800], unit="J/kg"),
                "cin": VariableTimeSeries(data=[20, 15, 5], unit="J/kg"),
            },
        )
        levels = [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500,
                  400, 300, 250, 200, 150, 100, 70, 50, 30]
        n = len(levels)
        profile_hourly = PressureLevelHourly(
            source="open-meteo-pressure",
            pressure_levels=levels,
            timestamps=["2026-07-02T07:00", "2026-07-02T08:00", "2026-07-02T09:00"],
            wind_speed=[[5 + j for j in range(n)] for _ in range(3)],
            wind_direction=[[180 + j * 10 for j in range(n)] for _ in range(3)],
            geopotential_height=[[100, 320, 500, 800, 1000, 1500, 1900,
                                  3000, 4200, 5600, 7200, 9200, 10400,
                                  11800, 13500, 15800, 17700, 19300, 22000]] * 3,
            temperature=[[20 - j for j in range(n)] for _ in range(3)],
            dewpoint=[[15 - j for j in range(n)] for _ in range(3)],
        )
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
        return PressureLevelSnapshot(
            source="open-meteo-pressure",
            synoptic_time="2026-07-02T12:00",
            pressure_levels=levels,
            temperature=[30 - i * 2 for i in range(n)],
            dewpoint=[20 - i * 2.5 for i in range(n)],
            wind_speed=[5 + i * 1.5 for i in range(n)],
            wind_direction=[180 + i * 5 for i in range(n)],
            geopotential_height=[100 + i * 500 for i in range(n)],
            latitude=35.0,
            longitude=-97.0,
        )

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
        return PressureLevelSnapshot(
            source="open-meteo-pressure",
            synoptic_time="2026-07-02T12:00",
            pressure_levels=levels,
            temperature=[30 - i * 2 for i in range(n)],
            dewpoint=[20 - i * 2.5 for i in range(n)],
            wind_speed=[5 + i * 1.5 for i in range(n)],
            wind_direction=[180 + i * 10 for i in range(n)],
            geopotential_height=[100, 320, 500, 800, 1000, 1500, 1900,
                                 3000, 4200, 5600, 7200, 9200, 10400,
                                 11800, 13500, 15800, 17700, 19300, 22000],
            latitude=35.0,
            longitude=-97.0,
        )

    def test_returns_figure(self):
        fig = create_hodograph_chart(self._make_profile())
        assert fig is not None
        import matplotlib.figure
        assert isinstance(fig, matplotlib.figure.Figure)

    def test_has_height_colored_segments(self):
        fig = create_hodograph_chart(self._make_profile())
        ax = fig.axes[0]
        assert len(ax.collections) > 0 or len(ax.lines) > 0


class TestHodographAboveGround:
    """Wichita, KS 2026-11-02T18:00 — Open-Meteo, surface elevation 399 m.
    1000/975 hPa are below ground (extrapolated, identical 17.5 kt @ 179°)."""

    LEVELS = [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500,
              400, 300, 250, 200, 150, 100, 70, 50, 30]
    HGT = [50, 273, 498, 727, 961, 1447, 1960, 3075, 4327, 5757,
           7438, 9510, 10760, 12242, 14063, 16479, 18634, 20683, 23804]
    SPD_KT = [17.5, 17.5, 28.6, 36.3, 40.8, 46.5, 39.6, 49.2, 60.8, 61.8,
              53.9, 58.2, 55.0, 46.1, 36.3, 21.6, 13.1, 14.2, 10.4]
    DIR = [179, 179, 180, 183, 187, 202, 218, 220, 216, 215,
           213, 224, 232, 232, 259, 311, 290, 300, 356]
    ELEVATION = 399.0

    def _profile(self):
        n = len(self.LEVELS)
        return PressureLevelSnapshot(
            source="open-meteo-pressure",
            synoptic_time="2026-11-02T18:00",
            pressure_levels=self.LEVELS,
            temperature=[20 - i * 4 for i in range(n)],
            dewpoint=[12 - i * 4 for i in range(n)],
            wind_speed=[s / 1.94384 for s in self.SPD_KT],
            wind_direction=self.DIR,
            geopotential_height=self.HGT,
            latitude=37.69,
            longitude=-97.34,
            elevation=self.ELEVATION,
        )

    def _raw_uv(self, idx):
        r = np.radians(np.array(self.DIR)[idx])
        s = np.array(self.SPD_KT)[idx]
        return -s * np.sin(r), -s * np.cos(r)

    def _band_lines(self, fig):
        return [l for l in fig.axes[0].lines if l.get_label().endswith(' km')]

    def test_below_ground_levels_not_plotted(self):
        fig = create_hodograph_chart(self._profile())
        first = min(self._band_lines(fig), key=lambda l: l.get_label())
        u0, v0 = first.get_xdata()[0], first.get_ydata()[0]
        u950, v950 = self._raw_uv(2)
        assert abs(u0 - u950) < 0.5 and abs(v0 - v950) < 0.5

    def test_curve_does_not_overshoot_raw_data(self):
        fig = create_hodograph_chart(self._profile())
        us = np.concatenate([l.get_xdata() for l in self._band_lines(fig)])
        vs = np.concatenate([l.get_ydata() for l in self._band_lines(fig)])
        ru, rv = self._raw_uv(slice(2, 14))  # above ground, ≤ 12 km AGL
        assert vs.min() >= rv.min() - 0.5 and vs.max() <= rv.max() + 0.5
        assert us.min() >= ru.min() - 0.5 and us.max() <= ru.max() + 0.5

    def test_height_labels_are_agl(self):
        fig = create_hodograph_chart(self._profile())
        labels = {t.get_text() for t in fig.axes[0].texts}
        assert "0km" not in labels
        assert {"0.5km", "1km", "3km", "6km", "9km"} <= labels

    def _barbs(self, fig):
        ax_barbs = fig.axes[1]
        b = ax_barbs.collections
        hgt = np.array([float(c.get_offsets()[0][1]) for c in b])
        u = np.array([float(c.u[0]) for c in b])
        v = np.array([float(c.v[0]) for c in b])
        return ax_barbs, hgt, u, v

    def test_barb_column_has_interpolated_resolution(self):
        fig = create_hodograph_chart(self._profile())
        _, hgt, _, _ = self._barbs(fig)
        assert len(hgt) >= 16
        assert (hgt < 1000).sum() >= 4  # lowest km gets real coverage

    def test_barbs_evenly_spaced_on_screen(self):
        fig = create_hodograph_chart(self._profile())
        fig.canvas.draw()
        ax_barbs, hgt, _, _ = self._barbs(fig)
        ys = np.sort(ax_barbs.transData.transform(np.c_[np.zeros_like(hgt), hgt])[:, 1])
        gaps = np.diff(ys)
        assert gaps.max() - gaps.min() < 0.05 * gaps.mean()

    def test_barbs_lie_on_hodograph_curve(self):
        fig = create_hodograph_chart(self._profile())
        _, _, bu, bv = self._barbs(fig)
        lines = self._band_lines(fig)
        cu = np.concatenate([l.get_xdata() for l in lines])
        cv = np.concatenate([l.get_ydata() for l in lines])
        for u, v in zip(bu, bv):
            assert np.hypot(cu - u, cv - v).min() < 1.0

    def test_axis_ticks_align_with_20kt_rings(self):
        fig = create_hodograph_chart(self._profile())
        ax = fig.axes[0]
        lo, hi = ax.get_xlim()
        for ticks in (ax.get_xticks(), ax.get_yticks()):
            visible = [t for t in ticks if lo <= t <= hi]
            assert visible and all(t % 20 == 0 for t in visible)
            assert np.allclose(np.diff(visible), 20)

    def test_barb_column_primary_scale_is_pressure(self):
        fig = create_hodograph_chart(self._profile())
        ax_barbs = fig.axes[1]
        labels = [t.get_text() for t in ax_barbs.get_yticklabels()]
        ticks = ax_barbs.get_yticks()
        assert {"850", "700", "500", "300"} <= set(labels)
        # Each hPa tick sits at that level's actual AGL height
        for lbl, y in zip(labels, ticks):
            i = self.LEVELS.index(int(lbl))
            assert abs(y - (self.HGT[i] - self.ELEVATION)) < 1

    def test_barb_column_secondary_scale_is_km_agl(self):
        fig = create_hodograph_chart(self._profile())
        fig.canvas.draw()
        ax_barbs = fig.axes[1]
        sec = next(a for a in fig.axes if a.get_ylabel() == "km AGL")
        labels = [t.get_text() for t in sec.get_yticklabels()]
        assert {"1", "3", "6", "9"} <= set(labels)
        # km tick must land on screen where the barb column puts that height
        for lbl, y in zip(labels, sec.get_yticks()):
            want = ax_barbs.transData.transform((0, float(lbl) * 1000))[1]
            got = sec.transData.transform((0, y))[1]
            assert abs(got - want) < 1, f"{lbl} km tick off by {got - want:.0f}px"

    def test_barbs_not_clipped(self):
        fig = create_hodograph_chart(self._profile())
        ax_barbs = fig.axes[1]
        assert all(not b.get_clip_on() for b in ax_barbs.collections)


class TestSnapshotElevation:
    def test_parse_response_keeps_elevation(self):
        from core.pressure_levels import PressureLevelClient, extract_snapshot
        data = {
            "latitude": 37.69, "longitude": -97.34, "elevation": 399.0,
            "hourly": {"time": ["2026-11-02T18:00"]},
        }
        hourly = PressureLevelClient()._parse_response(data)
        assert hourly.elevation == 399.0
        assert extract_snapshot(hourly, 0).elevation == 399.0


class TestThermoAboveGround:
    """Same Wichita profile: underground 1000/975 hPa must not affect the
    parcel, CAPE or helicity. The test poisons them with absurd values."""

    LEVELS = TestHodographAboveGround.LEVELS
    HGT = TestHodographAboveGround.HGT
    SPD = [s / 1.94384 for s in TestHodographAboveGround.SPD_KT]
    DIR = TestHodographAboveGround.DIR
    T = [24, 22, 20, 18, 16, 13, 10, 3, -4, -12, -22, -36, -45, -55, -60, -62, -63, -60, -55]
    TD = [18, 17, 16, 15, 13, 10, 5, -5, -15, -25, -35, -48, -55, -65, -70, -75, -80, -80, -80]

    def _arrays(self, poison):
        t, td, spd, dr = list(self.T), list(self.TD), list(self.SPD), list(self.DIR)
        if poison:
            for i in (0, 1):
                t[i], td[i], spd[i], dr[i] = 45.0, 40.0, 60.0, 0.0
        return t, td, spd, dr

    def _snapshot(self, poison):
        t, td, spd, dr = self._arrays(poison)
        return PressureLevelSnapshot(
            source="open-meteo-pressure", synoptic_time="2026-11-02T18:00",
            pressure_levels=self.LEVELS, temperature=t, dewpoint=td,
            wind_speed=spd, wind_direction=dr, geopotential_height=self.HGT,
            latitude=37.69, longitude=-97.34, elevation=399.0,
        )

    def _hourly(self, poison):
        t, td, spd, dr = self._arrays(poison)
        return PressureLevelHourly(
            source="open-meteo-pressure", pressure_levels=self.LEVELS,
            timestamps=["2026-11-02T18:00"], temperature=[t], dewpoint=[td],
            wind_speed=[spd], wind_direction=[dr], geopotential_height=[self.HGT],
            latitude=37.69, longitude=-97.34, elevation=399.0,
        )

    @staticmethod
    def _texts(fig):
        return sorted(t.get_text() for t in fig.axes[0].texts)

    def test_sounding_starts_at_surface(self):
        fig = create_sounding_chart(self._snapshot(poison=False))
        temp_line = next(l for l in fig.axes[0].lines if l.get_label() == 'Temperature')
        y = temp_line.get_ydata()
        assert np.max(getattr(y, 'magnitude', y)) <= 950.5

    def test_sounding_ignores_underground_levels(self):
        clean = self._texts(create_sounding_chart(self._snapshot(poison=False)))
        poisoned = self._texts(create_sounding_chart(self._snapshot(poison=True)))
        assert clean == poisoned

    def test_sfc_label_is_station_elevation(self):
        texts = self._texts(create_sounding_chart(self._snapshot(poison=False)))
        assert any('SFC 399m' in t for t in texts)

    def _cape_chart(self, poison):
        from core.models.atmosphere.base import AtmosphereData
        data = AtmosphereData(source="test", timestamps=["2026-11-02T18:00"], variables={})
        return create_atmosphere_chart(data, "cape_cin", profile_hourly=self._hourly(poison))

    @staticmethod
    def _series(fig):
        return {l.get_label(): np.asarray(l.get_ydata(), dtype=float)
                for ax in fig.axes for l in ax.lines if l.get_label().startswith('SRH')}

    def test_srh_ignores_underground_levels(self):
        clean = self._series(self._cape_chart(poison=False))
        poisoned = self._series(self._cape_chart(poison=True))
        assert set(clean) >= {'SRH 0-3km', 'SRH 0-1km'}
        for k in clean:
            np.testing.assert_allclose(clean[k], poisoned[k])
            assert np.isfinite(clean[k]).all()

    def test_profile_cape_ignores_underground_levels(self):
        def cape(fig):
            return fig.axes[0].lines[0].get_ydata()
        np.testing.assert_allclose(cape(self._cape_chart(False)), cape(self._cape_chart(True)))

    def test_srh_is_storm_relative(self):
        import metpy.calc as mpcalc
        from metpy.units import units
        from core.visualize import _bunkers_srh
        p = np.array(self.LEVELS[2:], dtype=float) * units.hPa
        agl = (np.array(self.HGT[2:], dtype=float) - 399.0) * units.meter
        u, v = mpcalc.wind_components(np.array(self.SPD[2:]) * units('m/s'),
                                      np.array(self.DIR[2:], dtype=float) * units.degrees)
        _, _, ground_rel = mpcalc.storm_relative_helicity(agl, u, v, depth=3000 * units.meter)
        assert _bunkers_srh(p, u, v, agl, 3000) != float(ground_rel.magnitude)

    def test_srh_spans_same_hours_as_cape(self):
        from core.models.atmosphere.base import AtmosphereData, VariableTimeSeries
        week = [f"2026-11-0{d}T{h:02d}:00" for d in (1, 2, 3) for h in range(24)]
        hourly = self._hourly(poison=False)
        for f in ("temperature", "dewpoint", "wind_speed", "wind_direction", "geopotential_height"):
            setattr(hourly, f, getattr(hourly, f) * len(week))
        hourly.timestamps = week
        day = [ts for ts in week if ts.startswith("2026-11-02")]
        data = AtmosphereData(source="test", timestamps=day, variables={
            "cape": VariableTimeSeries(data=[100.0] * 24, unit="J/kg"),
            "cin": VariableTimeSeries(data=[-10.0] * 24, unit="J/kg"),
        })
        fig = create_atmosphere_chart(data, "cape_cin", profile_hourly=hourly)
        srh = [l for ax in fig.axes for l in ax.lines if l.get_label().startswith('SRH')]
        cape_x = fig.axes[0].lines[0].get_xdata()
        assert srh and all(len(l.get_xdata()) == 24 for l in srh)
        assert srh[0].get_xdata()[0] == cape_x[0] and srh[0].get_xdata()[-1] == cape_x[-1]
