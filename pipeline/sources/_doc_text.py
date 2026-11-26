"""Plain text out of the official bulletin attachments (.pdf / .docx / .doc), standard library only.

Not a source itself (no Source subclass), so the registry ignores it. Used by cn_stats_city for the 2023 统计公报
that statistics bureaus publish only as an attached file. Accuracy target: the running text of a bulletin, good
enough that the same sentence regexes as for the HTML pages find "地区生产总值…亿元" / "常住人口…万人". Anything
that cannot be decoded (scanned images, fonts without a /ToUnicode map, encrypted files) gives "" or garbage, and
the regexes then find nothing, i.e. the value stays null.

* PDF: every object (also inside /ObjStm object streams) is read, FlateDecode streams are inflated, fonts are
  decoded through their /ToUnicode CMap (failing that: a GBK/UCS-2 predefined CMap, or for Identity-H the
  inverted 'cmap' table of the embedded TrueType font), and the text-showing operators (Tj, TJ, ', ") of each
  page's content streams are concatenated in stream order.
* DOCX: the w:t runs of word/document.xml.
* DOC (Word 97–2003): Chinese text is stored as UTF-16LE in the WordDocument stream; the file is decoded as
  UTF-16LE at both byte alignments and the longer run of CJK text wins.
"""

from __future__ import annotations

import re
import zipfile
import zlib
from io import BytesIO
from xml.etree import ElementTree as ET

__all__ = ["document_text", "pdf_text", "docx_text", "doc_text"]


# --- PDF ------------------------------------------------------------------------------------------------------


def _inflate(raw: bytes) -> bytes | None:
    try:
        return zlib.decompressobj().decompress(raw)
    except zlib.error:
        return None


def _read_objects(data: bytes) -> tuple[dict[int, bytes], dict[int, bytes]]:
    """({obj number: dictionary text}, {obj number: decoded stream}) for every object in the file."""
    dicts: dict[int, bytes] = {}
    streams: dict[int, bytes] = {}
    for m in re.finditer(rb"(\d+)\s+\d+\s+obj\b", data):
        n, start = int(m.group(1)), m.end()
        end = data.find(b"endobj", start)
        end = len(data) if end < 0 else end
        k = data.find(b"stream", start, end)
        if k < 0:
            dicts[n] = data[start:end]
            continue
        head = data[start:k]
        s = k + 6
        s += 2 if data[s:s + 2] == b"\r\n" else (1 if data[s:s + 1] in (b"\r", b"\n") else 0)
        e = data.find(b"endstream", s)
        body = data[s:e if e > 0 else end]
        dicts[n] = head
        if b"/Filter" not in head:
            streams[n] = body
        elif re.search(rb"/Filter\s*(?:\[\s*)?/FlateDecode\s*\]?", head):
            dec = _inflate(body)
            if dec is not None:
                streams[n] = dec
    for n, head in list(dicts.items()):  # compressed object streams (PDF 1.5+)
        if b"/ObjStm" in head and n in streams:
            body = streams[n]
            fm = re.search(rb"/First\s+(\d+)", head)
            if not fm:
                continue
            first = int(fm.group(1))
            nums = [int(x) for x in body[:first].split()]
            pairs = list(zip(nums[0::2], nums[1::2]))
            for i, (on, off) in enumerate(pairs):
                stop = pairs[i + 1][1] if i + 1 < len(pairs) else len(body) - first
                dicts.setdefault(on, body[first + off:first + stop])
    return dicts, streams


def _hex_utf16(h: bytes) -> str:
    b = bytes.fromhex(h.decode())
    return b.decode("utf-16-be", "replace") if len(b) % 2 == 0 else b.decode("latin-1")


def _cmap(body: bytes) -> tuple[dict[int, str], int]:
    """(code → text, code width in bytes) of a /ToUnicode CMap."""
    m: dict[int, str] = {}
    width = 1
    for cs in re.findall(rb"begincodespacerange(.*?)endcodespacerange", body, re.S):
        for a in re.findall(rb"<([0-9A-Fa-f]+)>", cs):
            width = max(width, len(a) // 2)
    for blk in re.findall(rb"beginbfchar(.*?)endbfchar", body, re.S):
        for a, b in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", blk):
            m[int(a, 16)] = _hex_utf16(b)
            width = max(width, len(a) // 2)
    for blk in re.findall(rb"beginbfrange(.*?)endbfrange", body, re.S):
        for a, b, c in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])", blk):
            lo, hi = int(a, 16), int(b, 16)
            width = max(width, len(a) // 2)
            if hi - lo > 65535:
                continue
            if c.startswith(b"["):
                for k, h in enumerate(re.findall(rb"<([0-9A-Fa-f]+)>", c)):
                    m[lo + k] = _hex_utf16(h)
            else:
                hexs = c[1:-1]
                base, w = int(hexs, 16) if hexs else 0, max(1, len(hexs) // 2)
                for k in range(hi - lo + 1):
                    try:
                        m[lo + k] = (base + k).to_bytes(w, "big").decode("utf-16-be", "replace")
                    except OverflowError:
                        break
    return m, width


def _truetype_gid_to_unicode(font: bytes) -> dict[int, str]:
    """Glyph id → character from an embedded TrueType font's (3,1)/(3,10)/(0,x) 'cmap' subtable (formats 4, 12)."""
    import struct
    try:
        num = struct.unpack(">H", font[4:6])[0]
        tables = {font[12 + 16 * i:16 + 16 * i]: struct.unpack(">II", font[20 + 16 * i:28 + 16 * i])
                  for i in range(num)}
        off, _len = tables[b"cmap"]
        n_sub = struct.unpack(">H", font[off + 2:off + 4])[0]
        subs = []
        for i in range(n_sub):
            pid, eid, so = struct.unpack(">HHI", font[off + 4 + 8 * i:off + 12 + 8 * i])
            if (pid, eid) in ((3, 1), (3, 10)) or pid == 0:
                subs.append(off + so)
        out: dict[int, str] = {}
        for so in subs:
            fmt = struct.unpack(">H", font[so:so + 2])[0]
            if fmt == 4:
                segx2 = struct.unpack(">H", font[so + 6:so + 8])[0]
                seg = segx2 // 2
                ends = struct.unpack(f">{seg}H", font[so + 14:so + 14 + segx2])
                p0 = so + 16 + segx2
                starts = struct.unpack(f">{seg}H", font[p0:p0 + segx2])
                deltas = struct.unpack(f">{seg}h", font[p0 + segx2:p0 + 2 * segx2])
                ro_pos = p0 + 2 * segx2
                ranges = struct.unpack(f">{seg}H", font[ro_pos:ro_pos + segx2])
                for k in range(seg):
                    if starts[k] == 0xFFFF:
                        continue
                    for c in range(starts[k], ends[k] + 1):
                        if ranges[k] == 0:
                            g = (c + deltas[k]) & 0xFFFF
                        else:
                            a = ro_pos + 2 * k + ranges[k] + 2 * (c - starts[k])
                            g = struct.unpack(">H", font[a:a + 2])[0]
                            g = (g + deltas[k]) & 0xFFFF if g else 0
                        if g:
                            out.setdefault(g, chr(c))
            elif fmt == 12:
                n = struct.unpack(">I", font[so + 12:so + 16])[0]
                for k in range(n):
                    a, b, g = struct.unpack(">III", font[so + 16 + 12 * k:so + 28 + 12 * k])
                    for c in range(a, min(b, a + 70000) + 1):
                        out.setdefault(g + c - a, chr(c))
        return out
    except (KeyError, struct.error, ValueError, IndexError):
        return {}


_ESC = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}


def _literal(lit: bytes) -> bytes:
    def rep(mm):
        g = mm.group(1)
        if g[:1].isdigit():
            return bytes([int(g, 8) & 0xFF])
        return _ESC.get(g, g)

    return re.sub(rb"\\([nrtbf()\\]|[0-7]{1,3})", rep, lit)


_TEXT_OPS = re.compile(
    rb"/([^\s/\[\]()<>]+)\s+-?[\d.]+\s+Tf"            # 1: font selection
    rb"|\[((?:[^\]\\]|\\.)*)\]\s*TJ"                   # 2: array show
    rb"|(<[0-9A-Fa-f\s]*>|\((?:[^()\\]|\\.)*\))\s*(?:Tj|'|\")",  # 3: string show
    re.S)
_STRINGS = re.compile(rb"<([0-9A-Fa-f\s]*)>|\(((?:[^()\\]|\\.)*)\)", re.S)


def pdf_text(data: bytes) -> str:
    dicts, streams = _read_objects(data)
    if b"/Encrypt" in data[-4096:] or re.search(rb"/Encrypt\s+\d+\s+0\s+R", data):
        return ""
    # font object → decoder of its string bytes
    cmaps: dict[int, object] = {}
    for n, d in dicts.items():
        r = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", d)
        if r and int(r.group(1)) in streams:
            cmaps[n] = _cmap(streams[int(r.group(1))])
        elif re.search(rb"/Subtype\s*/Type0", d):
            enc = re.search(rb"/Encoding\s*/([\w-]+)", d)
            enc = enc.group(1) if enc else b""
            if b"GBK" in enc or b"GB-EUC" in enc or b"GBpc" in enc:
                cmaps[n] = "gb18030"
            elif b"UCS2" in enc or b"UTF16" in enc:
                cmaps[n] = "utf-16-be"
            elif enc == b"Identity-H":  # no ToUnicode: invert the embedded TrueType's own cmap (CID = GID)
                desc = re.search(rb"/DescendantFonts\s*\[?\s*(\d+)\s+0\s+R", d)
                cid = dicts.get(int(desc.group(1)), b"") if desc else b""
                fd = re.search(rb"/FontDescriptor\s+(\d+)\s+0\s+R", cid)
                ff = re.search(rb"/FontFile2\s+(\d+)\s+0\s+R", dicts.get(int(fd.group(1)), b"")) if fd else None
                if ff and int(ff.group(1)) in streams and not re.search(rb"/CIDToGIDMap\s+\d+\s+0\s+R", cid):
                    table = _truetype_gid_to_unicode(streams[int(ff.group(1))])
                    if table:
                        cmaps[n] = (table, 2)

    def font_names(d: bytes) -> dict[bytes, int]:
        fm = re.search(rb"/Font\s*<<(.*?)>>", d, re.S)
        if fm is None:
            r = re.search(rb"/Font\s+(\d+)\s+0\s+R", d)
            if r:
                fm = re.search(rb"<<(.*)>>", dicts.get(int(r.group(1)), b""), re.S)
        return {a: int(b) for a, b in re.findall(rb"/([^\s/\[\]()<>]+)\s+(\d+)\s+0\s+R", fm.group(1))} if fm else {}

    every_font: dict[bytes, int] = {}  # fallback for pages inheriting /Resources from the page tree
    for d in dicts.values():
        if b"/Font" in d:
            for k, v in font_names(d).items():
                every_font.setdefault(k, v)

    out: list[str] = []
    for n in sorted(dicts):
        d = dicts[n]
        if not re.search(rb"/Type\s*/Page(?![a-zA-Z])", d):
            continue
        rr = re.search(rb"/Resources\s+(\d+)\s+0\s+R", d)
        fonts = font_names(dicts.get(int(rr.group(1)), b"") if rr else d) or every_font
        cm = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", d)
        if not cm:
            continue
        refs = [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R", cm.group(1))]
        content = b"\n".join(streams.get(x, b"") for x in refs)
        cur: object = None
        for tok in _TEXT_OPS.finditer(content):
            if tok.group(1) is not None:
                cur = cmaps.get(fonts.get(tok.group(1), -1))
                continue
            body = tok.group(2) if tok.group(2) is not None else tok.group(3)
            for sm in _STRINGS.finditer(body):
                if sm.group(1) is not None:
                    hexs = re.sub(rb"\s", b"", sm.group(1)).decode()
                    bs = bytes.fromhex(hexs + "0" * (len(hexs) % 2))
                else:
                    bs = _literal(sm.group(2))
                if isinstance(cur, str):
                    out.append(bs.decode(cur, "replace"))
                elif cur:
                    table, w = cur
                    out.append("".join(table.get(int.from_bytes(bs[i:i + w], "big"), "")
                                       for i in range(0, len(bs) - w + 1, w)))
                else:
                    out.append(bs.decode("latin-1"))
        out.append("\n")
    return "".join(out)


# --- Word -----------------------------------------------------------------------------------------------------


def docx_text(data: bytes) -> str:
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            root = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        return ""
    paras = []
    for p in root.iter(w + "p"):
        paras.append("".join(t.text or "" for t in p.iter(w + "t")))
    return "\n".join(paras)


_KEEP = re.compile(r"[一-鿿　-〿＀-￯0-9A-Za-z.,%:;()\[\]\-+/ \n]+")


def doc_text(data: bytes) -> str:
    best = ""
    for off in (0, 1):
        s = data[off:len(data) - ((len(data) - off) % 2)].decode("utf-16-le", "ignore")
        runs = [r for r in _KEEP.findall(s) if re.search(r"[一-鿿]{4}", r)]
        txt = "\n".join(runs)
        if len(re.findall(r"[一-鿿]", txt)) > len(re.findall(r"[一-鿿]", best)):
            best = txt
    return best


def document_text(data: bytes) -> str:
    """Text of a bulletin attachment, dispatched on the file's magic bytes ("" if unknown)."""
    if data[:5] == b"%PDF-":
        return pdf_text(data)
    if data[:2] == b"PK":
        return docx_text(data)
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return doc_text(data)
    return ""
