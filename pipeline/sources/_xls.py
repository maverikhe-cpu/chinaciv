"""A tiny reader for legacy Excel .xls (BIFF8 in an OLE compound file), standard library only.

Not a source itself (no Source subclass), so the registry ignores it. Provincial statistical yearbooks (统计年鉴)
publish each table as an .xls next to its .jpg; this reads the first worksheet's cell values (strings and
numbers; formulas give their cached number) into a list of rows. Formatting, merged cells and dates are ignored.
"""

from __future__ import annotations

import struct

__all__ = ["xls_rows"]

_FREE, _END = 0xFFFFFFFF, 0xFFFFFFFE


def _cfb_stream(data: bytes, names: tuple[str, ...]) -> bytes:
    """Contents of the first stream called one of `names` in an OLE compound file."""
    if data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise ValueError("not an OLE compound file")
    ss = 1 << struct.unpack_from("<H", data, 0x1E)[0]
    mss = 1 << struct.unpack_from("<H", data, 0x20)[0]
    n_fat, dir_start = struct.unpack_from("<II", data, 0x2C)
    cutoff, minifat_start, n_minifat, difat_start, n_difat = struct.unpack_from("<IIIII", data, 0x38)

    def sector(i: int) -> bytes:
        return data[512 + i * ss:512 + (i + 1) * ss]

    difat = list(struct.unpack_from("<109I", data, 0x4C))
    d = difat_start
    for _ in range(n_difat):
        if d in (_FREE, _END):
            break
        vals = struct.unpack(f"<{ss // 4}I", sector(d))
        difat += vals[:-1]
        d = vals[-1]
    fat: list[int] = []
    for s in difat[:n_fat]:
        if s not in (_FREE, _END):
            fat += struct.unpack(f"<{ss // 4}I", sector(s))

    def chain(start: int) -> bytes:
        out, s, seen = [], start, set()
        while s not in (_FREE, _END) and s < len(fat) and s not in seen:
            seen.add(s)
            out.append(sector(s))
            s = fat[s]
        return b"".join(out)

    dirs = chain(dir_start)
    entries = []
    for k in range(0, len(dirs) - 127, 128):
        e = dirs[k:k + 128]
        nlen = struct.unpack_from("<H", e, 0x40)[0]
        name = e[:max(0, nlen - 2)].decode("utf-16-le", "replace")
        typ = e[0x42]
        start, size = struct.unpack_from("<II", e, 0x74)
        entries.append((name, typ, start, size))
    root = entries[0]
    minifat: list[int] = []
    if n_minifat:
        minifat = list(struct.unpack(f"<{len(chain(minifat_start)) // 4}I", chain(minifat_start)))
    ministream = chain(root[2]) if root[2] not in (_FREE, _END) else b""
    for name, typ, start, size in entries:
        if typ == 2 and name in names:
            if size >= cutoff:
                return chain(start)[:size]
            out, s, seen = [], start, set()
            while s not in (_FREE, _END) and s < len(minifat) and s not in seen:
                seen.add(s)
                out.append(ministream[s * mss:(s + 1) * mss])
                s = minifat[s]
            return b"".join(out)[:size]
    raise KeyError(f"no stream {names}")


def _rk(v: int) -> float:
    if v & 2:
        x = float(v >> 2 if v < 0x80000000 else (v >> 2) - (1 << 30))
    else:
        x = struct.unpack("<d", struct.pack("<Q", (v & 0xFFFFFFFC) << 32))[0]
    return x / 100 if v & 1 else x


class _Chunks:
    """Byte reader over a record plus its CONTINUE records (for the SST)."""

    def __init__(self, chunks: list[bytes]):
        self.chunks, self.i, self.p = chunks, 0, 0

    def _ensure(self):
        while self.i < len(self.chunks) and self.p >= len(self.chunks[self.i]):
            self.i, self.p = self.i + 1, 0

    def read(self, n: int) -> bytes:
        out = b""
        while n > 0:
            self._ensure()
            if self.i >= len(self.chunks):
                break
            c = self.chunks[self.i]
            take = c[self.p:self.p + n]
            self.p += len(take)
            n -= len(take)
            out += take
        return out

    def chars(self, cch: int, high: bool) -> str:
        out = []
        while cch > 0:
            self._ensure()
            if self.i >= len(self.chunks):
                break
            avail = len(self.chunks[self.i]) - self.p
            w = 2 if high else 1
            n = min(cch, avail // w)
            raw = self.chunks[self.i][self.p:self.p + n * w]
            self.p += n * w
            out.append(raw.decode("utf-16-le" if high else "latin-1", "replace"))
            cch -= n
            if cch > 0:  # string continues in the next CONTINUE record, which starts with a fresh flags byte
                self.i, self.p = self.i + 1, 0
                if self.i >= len(self.chunks):
                    break
                high = bool(self.chunks[self.i][0] & 1)
                self.p = 1
        return "".join(out)


def _sst(chunks: list[bytes]) -> list[str]:
    r = _Chunks(chunks)
    _total, unique = struct.unpack("<II", r.read(8))
    out = []
    for _ in range(unique):
        head = r.read(3)
        if len(head) < 3:
            break
        cch, flags = struct.unpack("<HB", head)
        runs = struct.unpack("<H", r.read(2))[0] if flags & 8 else 0
        ext = struct.unpack("<I", r.read(4))[0] if flags & 4 else 0
        out.append(r.chars(cch, bool(flags & 1)))
        r.read(4 * runs + ext)
    return out


def xls_rows(data: bytes) -> list[list]:
    """Cell values of the first worksheet as a list of rows (str, float or None)."""
    wb = _cfb_stream(data, ("Workbook", "Book"))
    recs = []
    pos = 0
    while pos + 4 <= len(wb):
        typ, ln = struct.unpack_from("<HH", wb, pos)
        recs.append((typ, wb[pos + 4:pos + 4 + ln]))
        pos += 4 + ln
    sst: list[str] = []
    for k, (typ, body) in enumerate(recs):
        if typ == 0x00FC:
            chunks = [body]
            for t2, b2 in recs[k + 1:]:
                if t2 != 0x003C:
                    break
                chunks.append(b2)
            sst = _sst(chunks)
            break
    cells: dict[tuple[int, int], object] = {}
    bof = 0
    for typ, body in recs:
        if typ == 0x0809:  # BOF: the 2nd one starts the first worksheet
            bof += 1
            if bof > 2:
                break
            continue
        if bof < 2:
            continue
        if typ == 0x00FD and len(body) >= 10:  # LABELSST
            r, c, _xf, i = struct.unpack_from("<HHHI", body)
            cells[r, c] = sst[i] if i < len(sst) else None
        elif typ == 0x0203 and len(body) >= 14:  # NUMBER
            r, c, _xf, v = struct.unpack_from("<HHHd", body)
            cells[r, c] = v
        elif typ == 0x027E and len(body) >= 10:  # RK
            r, c, _xf, v = struct.unpack_from("<HHHI", body)
            cells[r, c] = _rk(v)
        elif typ == 0x00BD and len(body) >= 6:  # MULRK
            r, c0 = struct.unpack_from("<HH", body)
            n = (len(body) - 6) // 6
            for k in range(n):
                _xf, v = struct.unpack_from("<HI", body, 4 + 6 * k)
                cells[r, c0 + k] = _rk(v)
        elif typ == 0x0204 and len(body) >= 9:  # LABEL
            r, c, _xf, cch, fl = struct.unpack_from("<HHHHB", body)
            raw = body[9:]
            cells[r, c] = raw[:cch * 2].decode("utf-16-le", "replace") if fl & 1 else raw[:cch].decode("latin-1")
        elif typ == 0x0006 and len(body) >= 14:  # FORMULA: cached result
            r, c, _xf = struct.unpack_from("<HHH", body)
            res = body[6:14]
            if res[6:8] != b"\xff\xff":
                cells[r, c] = struct.unpack("<d", res)[0]
    if not cells:
        return []
    nr = max(r for r, _ in cells) + 1
    nc = max(c for _, c in cells) + 1
    return [[cells.get((r, c)) for c in range(nc)] for r in range(nr)]
