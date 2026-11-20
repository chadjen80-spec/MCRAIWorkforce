"""Thai quotation documents (PDF and DOCX) rendered from a quotation snapshot.

Reuses the Document Agent's Thai font discovery and mark-lifting (ReportLab draws stacked Thai tone marks
on top of upper vowels without it). A document is always rendered from the frozen revision snapshot once
the quotation has been submitted; a draft is rendered with a clear "draft, not approved" watermark.
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape

from app.qtc.quotations import thai_baht_text
from app.services.media import find_fonts
from app.services.thai_text import lift_marks_markup

TH_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน",
             "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]


def thai_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {TH_MONTHS[d.month - 1]} {d.year + 543}"


def money(value: str | None) -> str:
    if value is None:
        return "-"
    return f"{Decimal(value):,.2f}"


def qty(value: str) -> str:
    d = Decimal(value).normalize()
    return f"{d:,f}" if d == d.to_integral() else f"{d:,}"


def _seller_lines(seller: dict[str, Any]) -> list[str]:
    lines = [seller.get("legal_name") or seller.get("name") or ""]
    if seller.get("address"):
        lines.append(seller["address"])
    if seller.get("tax_id"):
        branch = "สำนักงานใหญ่" if seller.get("branch", "00000") == "00000" else f"สาขา {seller['branch']}"
        lines.append(f"เลขประจำตัวผู้เสียภาษี {seller['tax_id']} ({branch})")
    contact = " · ".join(x for x in (seller.get("phone"), seller.get("email")) if x)
    if contact:
        lines.append(contact)
    return [x for x in lines if x]


def _customer_lines(c: dict[str, Any]) -> list[str]:
    lines = [c.get("legal_name") or c.get("name") or ""]
    if c.get("address"):
        lines.append(c["address"])
    if c.get("tax_id"):
        branch = "สำนักงานใหญ่" if (c.get("branch") or "00000") == "00000" else f"สาขา {c['branch']}"
        lines.append(f"เลขประจำตัวผู้เสียภาษี {c['tax_id']} ({branch})")
    return [x for x in lines if x]


QUOTATION_LABELS = {
    "title": "ใบเสนอราคา\nQUOTATION", "title_line": "ใบเสนอราคา / QUOTATION", "doc_name": "ใบเสนอราคา",
    "date2_label": "ยืนราคาถึง", "date2_key": "valid_until", "note": "",
    "sign_left": "ผู้เสนอราคา", "sign_right": "ผู้อนุมัติสั่งซื้อ (ลูกค้า)",
}
INVOICE_LABELS = {
    "title": "ใบแจ้งหนี้\nINVOICE", "title_line": "ใบแจ้งหนี้ / INVOICE", "doc_name": "ใบแจ้งหนี้",
    "date2_label": "ครบกำหนดชำระ", "date2_key": "due_date",
    "note": "เอกสารนี้เป็นใบแจ้งหนี้ ไม่ใช่ใบกำกับภาษีหรือใบเสร็จรับเงิน",
    "sign_left": "ผู้วางบิล", "sign_right": "ผู้รับวางบิล",
}


def render_pdf(snap: dict[str, Any], seller: dict[str, Any], *, draft: bool, labels: dict[str, str] | None = None) -> bytes:
    lab = labels or QUOTATION_LABELS
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    regular, bold = find_fonts()
    if "MCR" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("MCR", regular))
        pdfmetrics.registerFont(TTFont("MCR-Bold", bold))

    def p(text: str, size: float = 9.5, *, font: str = "MCR", align: int = TA_LEFT, color: Any = colors.black) -> Paragraph:
        style = ParagraphStyle("p", fontName=font, fontSize=size, leading=size * 1.55, alignment=align, textColor=color, wordWrap="CJK")
        return Paragraph(lift_marks_markup(escape(text).replace("\n", "<br/>"), size), style)

    buf = io.BytesIO()
    w, h = A4

    def decorate(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("MCR", 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(18 * mm, 10 * mm, f"{snap['number']} rev {snap['revision']}")
        canvas.drawRightString(w - 18 * mm, 10 * mm, f"หน้า {doc.page}")
        if draft:
            canvas.setFont("MCR-Bold", 46)
            canvas.setFillColor(colors.Color(0.86, 0.15, 0.15, alpha=0.16))
            canvas.translate(w / 2, h / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, "ฉบับร่าง ยังไม่อนุมัติ")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"{lab['doc_name']} {snap['number']}", author=seller.get("legal_name") or seller.get("name") or "")
    dark = colors.HexColor("#0F172A")
    story: list[Any] = []
    head = Table(
        [[p("\n".join(_seller_lines(seller)), 9), p(lab["title"], 16, font="MCR-Bold", align=TA_RIGHT, color=dark)]],
        colWidths=[110 * mm, 64 * mm],
    )
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [head, Spacer(1, 5 * mm)]
    number = snap["number"] + (f" (ฉบับแก้ไข {snap['revision']})" if snap["revision"] > 1 else "")
    meta = Table(
        [[p("ลูกค้า", 8.5, font="MCR-Bold"), p("เลขที่", 8.5, font="MCR-Bold"), p(number, 9)],
         [p("\n".join(_customer_lines(snap["customer"])), 9), p("วันที่", 8.5, font="MCR-Bold"), p(thai_date(snap["issue_date"]), 9)],
         ["", p(lab["date2_label"], 8.5, font="MCR-Bold"), p(thai_date(snap[lab["date2_key"]]), 9)]],
        colWidths=[100 * mm, 26 * mm, 48 * mm],
    )
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("SPAN", (0, 1), (0, 2)),
                              ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1"))]))
    story += [meta, Spacer(1, 4 * mm)]
    if snap.get("title"):
        story += [p(snap["title"], 11, font="MCR-Bold"), Spacer(1, 2 * mm)]

    rows = [[p("ลำดับ", 8.5, font="MCR-Bold"), p("รายการ", 8.5, font="MCR-Bold"), p("จำนวน", 8.5, font="MCR-Bold", align=TA_RIGHT),
             p("ราคาต่อหน่วย", 8.5, font="MCR-Bold", align=TA_RIGHT), p("ส่วนลด", 8.5, font="MCR-Bold", align=TA_RIGHT),
             p("จำนวนเงิน", 8.5, font="MCR-Bold", align=TA_RIGHT)]]
    for line in snap["lines"]:
        discount = Decimal(line["discount_amount"] or "0")
        pct = Decimal(line["discount_percent"] or "0")
        disc = " + ".join(x for x in ((f"{pct.normalize()}%" if pct else ""), (money(line["discount_amount"]) if discount else "")) if x) or "-"
        amount = line["line_total"] if snap["prices_include_vat"] else line["line_net"]
        rows.append([p(str(line["position"]), 9), p(line["description"] + (" (ยกเว้น VAT)" if line["vat_exempt"] else ""), 9),
                     p(f"{qty(line['quantity'])} {line['unit']}", 9, align=TA_RIGHT),
                     p(money(line["unit_price"]) if line["unit_price"] is not None else "ต้องกำหนดราคา", 9, align=TA_RIGHT),
                     p(disc, 9, align=TA_RIGHT), p(money(amount), 9, align=TA_RIGHT)])
    table = Table(rows, colWidths=[12 * mm, 74 * mm, 22 * mm, 24 * mm, 18 * mm, 24 * mm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1"))]))
    story += [table, Spacer(1, 3 * mm)]

    t = snap["totals"]
    vat_pct = (Decimal(snap["vat_rate"]) * 100).normalize()
    total_rows = [[p("รวมเป็นเงิน (ก่อน VAT)", 9.5, align=TA_RIGHT), p(money(t["subtotal"]), 9.5, align=TA_RIGHT)],
                  [p(f"ภาษีมูลค่าเพิ่ม {vat_pct}%", 9.5, align=TA_RIGHT), p(money(t["vat"]), 9.5, align=TA_RIGHT)],
                  [p("จำนวนเงินรวมทั้งสิ้น", 10.5, font="MCR-Bold", align=TA_RIGHT), p(money(t["total"]), 10.5, font="MCR-Bold", align=TA_RIGHT)]]
    totals = Table(total_rows, colWidths=[140 * mm, 34 * mm])
    totals.setStyle(TableStyle([("LINEABOVE", (0, 2), (-1, 2), 0.8, dark)]))
    story += [totals, p(f"({thai_baht_text(Decimal(t['total']))})", 9.5, align=TA_RIGHT), Spacer(1, 5 * mm)]
    if snap["prices_include_vat"]:
        story.append(p("ราคาในรายการรวมภาษีมูลค่าเพิ่มแล้ว", 8.5, color=colors.HexColor("#475569")))
    if lab["note"]:
        story.append(p(lab["note"], 8.5, font="MCR-Bold", color=colors.HexColor("#475569")))
    for label, text in (("หมายเหตุ", snap.get("notes")), ("เงื่อนไข", snap.get("terms"))):
        if text:
            story += [p(label, 9, font="MCR-Bold"), p(text, 9), Spacer(1, 2 * mm)]
    story.append(Spacer(1, 14 * mm))
    dots = "\n\n\n.................................................\n"
    sign = Table([[p(lab["sign_left"] + dots + "(ผู้มีอำนาจลงนาม)", 9, align=TA_RIGHT),
                   p(lab["sign_right"] + dots + "วันที่ ......../......../........", 9, align=TA_RIGHT)]],
                 colWidths=[87 * mm, 87 * mm])
    story.append(sign)
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buf.getvalue()


def render_docx(snap: dict[str, Any], seller: dict[str, Any], *, draft: bool, labels: dict[str, str] | None = None) -> bytes:
    lab = labels or QUOTATION_LABELS
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    font_name = "Tahoma"  # ships with Windows / Office and covers Thai

    def run(par, text: str, size: float = 10, bold: bool = False, color: RGBColor | None = None):
        r = par.add_run(text)
        r.font.name, r.font.size, r.font.bold = font_name, Pt(size), bold
        if color is not None:
            r.font.color.rgb = color
        rfonts = r._element.get_or_add_rPr().get_or_add_rFonts()
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rfonts.set(qn(attr), font_name)
        return r

    doc = Document()
    doc.core_properties.title = f"{lab['doc_name']} {snap['number']}"
    doc.core_properties.author = seller.get("legal_name") or seller.get("name") or ""
    if draft:
        run(doc.add_paragraph(), "ฉบับร่าง ยังไม่อนุมัติ (DRAFT, NOT APPROVED)", 12, True, RGBColor(0xDC, 0x26, 0x26))
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run(title, lab["title_line"], 16, True)
    for line in _seller_lines(seller):
        run(doc.add_paragraph(), line, 9)
    info = doc.add_table(rows=0, cols=2)
    for k, v in (("เลขที่", snap["number"] + (f" (ฉบับแก้ไข {snap['revision']})" if snap["revision"] > 1 else "")),
                 ("วันที่", thai_date(snap["issue_date"])), (lab["date2_label"], thai_date(snap[lab["date2_key"]])),
                 ("ลูกค้า", "\n".join(_customer_lines(snap["customer"])))):
        cells = info.add_row().cells
        run(cells[0].paragraphs[0], k, 9, True)
        run(cells[1].paragraphs[0], v, 9)
    if snap.get("title"):
        run(doc.add_paragraph(), snap["title"], 11, True)
    table = doc.add_table(rows=1, cols=6)
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, ("ลำดับ", "รายการ", "จำนวน", "ราคาต่อหน่วย", "ส่วนลด", "จำนวนเงิน"), strict=True):
        run(cell.paragraphs[0], text, 9, True)
    for line in snap["lines"]:
        cells = table.add_row().cells
        discount = " + ".join(x for x in ((f"{Decimal(line['discount_percent']).normalize()}%" if Decimal(line["discount_percent"] or "0") else ""),
                                          (money(line["discount_amount"]) if Decimal(line["discount_amount"] or "0") else "")) if x) or "-"
        amount = line["line_total"] if snap["prices_include_vat"] else line["line_net"]
        values = (str(line["position"]), line["description"], f"{qty(line['quantity'])} {line['unit']}",
                  money(line["unit_price"]) if line["unit_price"] is not None else "ต้องกำหนดราคา", discount, money(amount))
        for cell, text in zip(cells, values, strict=True):
            run(cell.paragraphs[0], text, 9)
    t = snap["totals"]
    vat_pct = (Decimal(snap["vat_rate"]) * 100).normalize()
    for label, value, strong in (("รวมเป็นเงิน (ก่อน VAT)", t["subtotal"], False), (f"ภาษีมูลค่าเพิ่ม {vat_pct}%", t["vat"], False),
                                 ("จำนวนเงินรวมทั้งสิ้น", t["total"], True)):
        par = doc.add_paragraph()
        par.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run(par, f"{label}  {money(value)}", 10.5 if strong else 10, strong)
    par = doc.add_paragraph()
    par.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run(par, f"({thai_baht_text(Decimal(t['total']))})", 10)
    if lab["note"]:
        run(doc.add_paragraph(), lab["note"], 9, True)
    for heading, body in (("หมายเหตุ", snap.get("notes")), ("เงื่อนไข", snap.get("terms"))):
        if body:
            run(doc.add_paragraph(), heading, 10, True)
            run(doc.add_paragraph(), str(body), 10)
    sign = doc.add_table(rows=1, cols=2)
    run(sign.rows[0].cells[0].paragraphs[0], lab["sign_left"] + "\n\n\n....................................\n(ผู้มีอำนาจลงนาม)", 9)
    run(sign.rows[0].cells[1].paragraphs[0], lab["sign_right"] + "\n\n\n....................................\nวันที่ ......../......../........", 9)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
