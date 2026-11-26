"""pipeline/sources/_doc_text.py: text out of .docx and simple .pdf files (standard library only)."""

import io
import unittest
import zipfile
import zlib

from pipeline.sources._doc_text import document_text, pdf_text, docx_text


def make_docx(paragraphs):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def make_pdf(text):
    """A one-page PDF whose Type0 font maps 2-byte codes to `text`'s characters through a /ToUnicode CMap."""
    codes = {ch: k + 1 for k, ch in enumerate(dict.fromkeys(text))}
    cmap = "begincodespacerange <0000> <FFFF> endcodespacerange\n" + f"{len(codes)} beginbfchar\n" + "".join(
        f"<{c:04X}> <{ord(ch):04X}>\n" for ch, c in codes.items()) + "endbfchar\n"
    content = "BT /F1 12 Tf <" + "".join(f"{codes[ch]:04X}" for ch in text) + "> Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type0 /BaseFont /X /Encoding /Identity-H /ToUnicode 6 0 R >>",
    ]
    out = [b"%PDF-1.4\n"]
    for k, o in enumerate(objs, 1):
        out.append(f"{k} 0 obj\n".encode() + o + b"\nendobj\n")
    body = zlib.compress(content.encode())
    out.append(b"5 0 obj\n<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(body) + body + b"\nendstream\nendobj\n")
    cm = cmap.encode()
    out.append(b"6 0 obj\n<< /Length %d >>\nstream\n" % len(cm) + cm + b"\nendstream\nendobj\n%%EOF\n")
    return b"".join(out)


class DocTextTest(unittest.TestCase):
    def test_docx(self):
        data = make_docx(["一、综合", "年末全市常住人口1037.15万人"])
        self.assertIn("常住人口1037.15万人", docx_text(data))
        self.assertIn("常住人口1037.15万人", document_text(data))

    def test_pdf_tounicode(self):
        data = make_pdf("地区生产总值15760.34亿元")
        self.assertIn("地区生产总值15760.34亿元", pdf_text(data))
        self.assertIn("生产总值", document_text(data))

    def test_unknown_gives_empty(self):
        self.assertEqual(document_text(b"<html>not a file</html>"), "")


if __name__ == "__main__":
    unittest.main()
