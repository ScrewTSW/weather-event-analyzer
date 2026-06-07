import math
from core.projection import (
    latlon_to_pixel,
    pixel_to_tile,
    latlon_to_tiles,
    get_crop_bounds,
    TILE_SIZE,
)


class TestLatLonToPixel:
    def test_subsatellite_point_is_center(self):
        zoom = 4
        image_size = TILE_SIZE * (2 ** zoom)  # 10848
        center = image_size / 2
        col, row = latlon_to_pixel(0, 140.7, sat_lon=140.7, zoom=zoom)
        assert abs(col - center) < 5
        assert abs(row - center) < 5

    def test_north_pole_is_above_center(self):
        zoom = 4
        image_size = TILE_SIZE * (2 ** zoom)
        center = image_size / 2
        col, row = latlon_to_pixel(80, 140.7, sat_lon=140.7, zoom=zoom)
        assert row < center  # north is above center (lower row number)

    def test_point_east_of_subsatellite_is_right_of_center(self):
        zoom = 4
        image_size = TILE_SIZE * (2 ** zoom)
        center = image_size / 2
        col, row = latlon_to_pixel(0, 160.7, sat_lon=140.7, zoom=zoom)
        assert col > center

    def test_out_of_disk_returns_none(self):
        result = latlon_to_pixel(0, -40, sat_lon=140.7, zoom=4)
        assert result is None


class TestPixelToTile:
    def test_origin(self):
        tx, ty = pixel_to_tile(0, 0)
        assert tx == 0
        assert ty == 0

    def test_second_tile(self):
        tx, ty = pixel_to_tile(TILE_SIZE, 0)
        assert tx == 1
        assert ty == 0

    def test_diagonal(self):
        tx, ty = pixel_to_tile(TILE_SIZE * 3 + 100, TILE_SIZE * 2 + 50)
        assert tx == 3
        assert ty == 2


class TestLatLonToTiles:
    def test_returns_tile_list(self):
        tiles = latlon_to_tiles(45.75, 126.65, bbox_deg=5.0, zoom=3, sat_lon=140.7)
        assert len(tiles) > 0
        for tx, ty in tiles:
            assert 0 <= tx < 2 ** 3
            assert 0 <= ty < 2 ** 3

    def test_small_bbox_fewer_tiles(self):
        tiles_small = latlon_to_tiles(45.75, 126.65, bbox_deg=2.0, zoom=3, sat_lon=140.7)
        tiles_large = latlon_to_tiles(45.75, 126.65, bbox_deg=10.0, zoom=3, sat_lon=140.7)
        assert len(tiles_small) <= len(tiles_large)


class TestGetCropBounds:
    def test_returns_four_values(self):
        bounds = get_crop_bounds(45.75, 126.65, bbox_deg=5.0, zoom=3, sat_lon=140.7)
        assert len(bounds) == 4
        left, upper, right, lower = bounds
        assert left < right
        assert upper < lower
