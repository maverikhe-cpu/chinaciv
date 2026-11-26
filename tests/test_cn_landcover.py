"""sources.cn_landcover: PNG decoding (all row filters), colour → class lookup, tiling and process()."""

import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from pipeline.sources import cn_landcover as lc


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def _filter_row(ftype: int, line: bytes, prev: bytes, bpp: int) -> bytes:
    out = bytearray()
    for x, v in enumerate(line):
        a = line[x - bpp] if x >= bpp else 0
        b = prev[x]
        c = prev[x - bpp] if x >= bpp else 0
        if ftype == 0:
            pred = 0
        elif ftype == 1:
            pred = a
        elif ftype == 2:
            pred = b
        elif ftype == 3:
            pred = (a + b) >> 1
        else:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
        out.append((v - pred) & 255)
    return bytes([ftype]) + bytes(out)


def make_png(rows_rgba: list[list[tuple]], filters: list[int]) -> bytes:
    """Encode an 8-bit RGBA PNG, row y filtered with filters[y % len(filters)]."""
    h, w = len(rows_rgba), len(rows_rgba[0])
    raw, prev = b"", bytes(w * 4)
    for y, row in enumerate(rows_rgba):
        line = bytes(v for px in row for v in px)
        raw += _filter_row(filters[y % len(filters)], line, prev, 4)
        prev = line
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")


COLOR_OF = {code: rgb for rgb, code in lc.COLORS.items() if code}


class DecodeTest(unittest.TestCase):
    def setUp(self):
        codes = [[(x * 3 + y * 5) % 17 + 1 for x in range(7)] for y in range(6)]  # 1..17, varied
        self.codes = codes
        self.rows = [[(*COLOR_OF[c], 255) for c in row] for row in codes]
        self.rows[0][0] = (0, 0, 0, 0)  # transparent → no data
        self.codes[0][0] = 0

    def test_all_filters_roundtrip(self):
        png = make_png(self.rows, [0, 1, 2, 3, 4, 4])
        w, h, pixels = lc.decode_png(png)
        self.assertEqual((w, h), (7, 6))
        self.assertEqual(pixels, [px for row in self.rows for px in row])

    def test_classify(self):
        _, _, pixels = lc.decode_png(make_png(self.rows, [4]))
        unknown = {}
        self.assertEqual(lc.classify(pixels, unknown), [c for row in self.codes for c in row])
        self.assertEqual(unknown, {})
        unknown = {}
        self.assertEqual(lc.classify([(1, 2, 3, 255), (100, 100, 100, 255)], unknown), [0, 0])
        self.assertEqual(unknown, {(1, 2, 3): 1})

    def test_palette_png(self):
        palette = bytes(COLOR_OF[13]) + bytes(COLOR_OF[17])
        raw = b"\x00\x00\x01\x01" + b"\x01\x01\x00\x00"  # filter byte + 3 indices per row (filter 1 on row 2)
        png = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", 3, 2, 8, 3, 0, 0, 0))
               + _chunk(b"PLTE", palette) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b""))
        _, _, pixels = lc.decode_png(png)
        self.assertEqual(lc.classify(pixels), [13, 17, 17, 17, 17, 17])

    def test_rejects_non_png(self):
        with self.assertRaises(ValueError):
            lc.decode_png(b"<?xml version='1.0'?><ExceptionReport/>")


class TableTest(unittest.TestCase):
    def test_tables_consistent(self):
        self.assertEqual(set(lc.LEGEND), set(lc.CLASSES))
        self.assertEqual(set(lc.LEGEND), set(lc.GROUPS))
        self.assertEqual(len(set(lc.LEGEND.values())), len(lc.LEGEND))
        self.assertLessEqual(set(lc.GROUPS.values()), lc.ALLOWED_GROUPS)
        self.assertEqual(set(lc.COLORS.values()), set(lc.LEGEND))

    def test_tiles_cover_grid_exactly(self):
        t = lc.tiles()
        self.assertEqual(len(t), lc.TILES_LON * lc.TILES_LAT)
        self.assertEqual(sum(w * h for *_, w, h in t), 4100 * 2700)
        r, c, s, w, n, e, wpx, hpx = t[0]
        self.assertEqual((s, w, n, e, wpx, hpx), (39.0, 63.0, 57.0, 83.5, 1025, 900))
        self.assertIn("BBOX=39,63,57,83.5&WIDTH=1025&HEIGHT=900", lc.tile_url(s, w, n, e, wpx, hpx))


class ProcessTest(unittest.TestCase):
    def test_process_stitches_tiles(self):
        # Shrink the grid to 4 × 3 tiles of 2 × 2 px each (8 × 6 px) and check placement and georeferencing.
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.multiple(lc, RES=1.0, LON_W=0.0, LON_E=8.0, LAT_S=0.0, LAT_N=6.0), \
                mock.patch.object(lc.CnLandcover, "raw_dir", Path(tmp)):
            for r, c, *_ in lc.tiles():
                code = 1 + r * 4 + c  # 1..12, one class per tile
                (Path(tmp) / lc.tile_file(r, c)).write_bytes(make_png([[(*COLOR_OF[code], 255)] * 2] * 2, [1, 2]))
            out = lc.CnLandcover().process()
        self.assertEqual((out["cols"], out["rows_n"]), (8, 6))
        self.assertEqual((out["lon0"], out["lat0"], out["dlat"]), (0.5, 5.5, -1.0))
        self.assertEqual(out["rows"][0], "aabbccdd")
        self.assertEqual(out["rows"][5], "iijjkkll")
        self.assertEqual(out["legend"]["w"], 17)
        self.assertEqual(out["groups"]["13"], "developed")


if __name__ == "__main__":
    unittest.main()
