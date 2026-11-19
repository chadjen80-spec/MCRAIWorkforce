"""Document Agent: business documents from reusable templates → real PDF (ReportLab) and DOCX (python-docx)."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from pydantic import BaseModel, Field

from app.agents import prompts
from app.agents.base import AgentContext, has_thai
from app.services.media import find_fonts
from app.services.router import route_llm
from app.services.thai_text import lift_marks_markup


class Section(BaseModel):
    heading: str
    paragraphs: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)


class DocumentContent(BaseModel):
    title: str
    subtitle: str = ""
    sections: list[Section] = Field(min_length=1)


# Reusable templates. Body sections are drafted by the LLM; the fixed sections below are
# never generated so no pricing or legal terms can be fabricated.
TEMPLATES: dict[str, dict[str, Any]] = {
    "proposal": {
        "label": {"en": "Business Proposal", "th": "ข้อเสนอทางธุรกิจ"},
        "body": {"en": ["Executive Summary", "Objectives", "Scope of Work", "Deliverables", "Timeline"],
                 "th": ["บทสรุปผู้บริหาร", "วัตถุประสงค์", "ขอบเขตงาน", "สิ่งที่ส่งมอบ", "กำหนดเวลา"]},
    },
    "quotation": {
        "label": {"en": "Quotation (Draft)", "th": "ใบเสนอราคา (ฉบับร่าง)"},
        "body": {"en": ["Summary", "Scope of Work", "Deliverables"], "th": ["สรุป", "ขอบเขตงาน", "สิ่งที่ส่งมอบ"]},
    },
    "campaign_brief": {
        "label": {"en": "Campaign Brief", "th": "บรีฟแคมเปญ"},
        "body": {"en": ["Background", "Objectives", "Target Audience", "Key Messages", "Deliverables", "Timeline"],
                 "th": ["ที่มา", "วัตถุประสงค์", "กลุ่มเป้าหมาย", "สารหลัก", "สิ่งที่ส่งมอบ", "กำหนดเวลา"]},
    },
    "marketing_report": {
        "label": {"en": "Marketing Report", "th": "รายงานการตลาด"},
        "body": {"en": ["Summary", "Activities", "Observations", "Recommendations"],
                 "th": ["สรุป", "กิจกรรม", "ข้อสังเกต", "ข้อเสนอแนะ"]},
    },
}


def fixed_sections(doc_type: str, language: str, company: str) -> list[Section]:
    if doc_type not in ("proposal", "quotation"):
        return []
    if language == "th":
        return [
            Section(heading="งบประมาณและราคา", paragraphs=[
                f"เอกสารฉบับร่างนี้ยังไม่ระบุราคา ราคาและเงื่อนไขการชำระเงินจะได้รับการยืนยันโดย {company} [รอยืนยัน]"]),
            Section(heading="ข้อกำหนดและเงื่อนไข", paragraphs=[
                f"เอกสารฉบับร่างนี้ไม่มีข้อกำหนดทางกฎหมาย ข้อกำหนดและเงื่อนไขต้องจัดทำและตรวจสอบโดย {company} ก่อนส่งให้ลูกค้า [รอยืนยัน]"]),
        ]
    return [
        Section(heading="Investment", paragraphs=[
            f"No commercial pricing is included in this draft. Pricing and payment terms are to be confirmed by {company}. [to be confirmed]"]),
        Section(heading="Terms and Conditions", paragraphs=[
            "No legal terms are included in this draft. Terms and conditions must be supplied and reviewed by "
            f"{company} before this document is sent to a client. [to be confirmed]"]),
    ]


def _demo_content(ctx: AgentContext, doc_type: str, language: str) -> DocumentContent:
    tpl = TEMPLATES[doc_type]
    brief = ctx.content_brief() or {}
    msgs = (brief.get("brief") or {}).get("key_messages") or []
    note = ("[DEMO] เนื้อหาส่วนนี้ยังไม่ได้สร้างโดย AI — เชื่อมต่อผู้ให้บริการ AI เพื่อสร้างเนื้อหาจริง" if language == "th"
            else "[DEMO] This section was not AI-generated — connect an AI provider to draft real content.")
    sections = [Section(heading=h, paragraphs=[note]) for h in tpl["body"][language]]
    if msgs:
        sections[0].bullets = [str(m) for m in msgs[:5]]
    return DocumentContent(title=f"{tpl['label'][language]} — {ctx.brand['product']}", subtitle="DEMO", sections=sections)


# --- PDF ------------------------------------------------------------------------
def render_pdf(content: DocumentContent, out: Path, brand: dict, *, label: str, demo: bool) -> int:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import ListFlowable, ListItem, PageBreak, Paragraph, SimpleDocTemplate, Spacer

    regular, bold = find_fonts()
    if "MCR" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("MCR", regular))
        pdfmetrics.registerFont(TTFont("MCR-Bold", bold))

    primary = colors.HexColor(brand["primary_color"])
    dark = colors.HexColor(brand["background_color"])
    accent = colors.HexColor(brand["accent_color"])

    def style(name: str, text: str = "", **kw) -> ParagraphStyle:
        # Thai has no spaces between words: allow breaking anywhere so lines never overflow.
        wrap = "CJK" if has_thai(text) else None
        return ParagraphStyle(name, fontName=kw.pop("font", "MCR"), alignment=TA_LEFT, wordWrap=wrap, **kw)

    def para(text: str, name: str, **kw) -> Paragraph:
        return Paragraph(lift_marks_markup(escape(text), kw.get("fontSize", 10)), style(name, text, **kw))

    pages = {"n": 0}

    def decorate(canvas, doc):
        pages["n"] = max(pages["n"], doc.page)
        w, h = A4
        canvas.saveState()
        if doc.page == 1:
            canvas.setFillColor(dark)
            canvas.rect(0, h - 95 * mm, w, 95 * mm, stroke=0, fill=1)
            canvas.setFillColor(primary)
            canvas.rect(0, h - 97 * mm, w, 2 * mm, stroke=0, fill=1)
            canvas.setFillColor(accent)
            canvas.rect(0, h - 97 * mm, w * 0.35, 2 * mm, stroke=0, fill=1)
        canvas.setFont("MCR", 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(20 * mm, 12 * mm, f"{brand['company']} · {brand['product']}")
        canvas.drawRightString(w - 20 * mm, 12 * mm, str(doc.page))
        if demo:
            canvas.setFont("MCR-Bold", 60)
            canvas.setFillColor(colors.Color(0.9, 0.2, 0.2, alpha=0.12))
            canvas.translate(w / 2, h / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, "DEMO — NOT FINAL")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=22 * mm,
                            bottomMargin=22 * mm, title=content.title, author=brand["company"])
    white = colors.white
    story: list = [
        Spacer(1, 8 * mm),
        para(label.upper(), "label", font="MCR-Bold", fontSize=10, leading=14, textColor=accent),
        Spacer(1, 4 * mm),
        para(content.title, "title", font="MCR-Bold", fontSize=24, leading=34, textColor=white),
        Spacer(1, 3 * mm),
        para(content.subtitle or brand["company"], "subtitle", fontSize=12, leading=18, textColor=colors.HexColor("#CBD5E1")),
        Spacer(1, 3 * mm),
        para(date.today().isoformat(), "date", fontSize=9, leading=12, textColor=colors.HexColor("#94A3B8")),
        PageBreak(),
    ]
    for section in content.sections:
        # keepWithNext: never leave a heading alone at the bottom of a page
        story.append(para(section.heading, "h", font="MCR-Bold", fontSize=15, leading=24, textColor=primary,
                          spaceBefore=10, spaceAfter=4, keepWithNext=1))
        for text in section.paragraphs:
            story.append(para(text, "p", fontSize=11, leading=19, textColor=colors.HexColor("#0F172A"), spaceAfter=6))
        if section.bullets:
            story.append(ListFlowable(
                [ListItem(para(b, "b", fontSize=11, leading=19, textColor=colors.HexColor("#0F172A"))) for b in section.bullets],
                bulletType="bullet", bulletColor=primary, leftIndent=14, bulletFontSize=8,
            ))
            story.append(Spacer(1, 3 * mm))
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return pages["n"]


# --- DOCX --------------------------------------------------------------------------
def render_docx(content: DocumentContent, out: Path, brand: dict, *, label: str, demo: bool) -> None:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    font_name = "Tahoma"  # ships with Windows/Office and covers Thai
    primary = RGBColor.from_string(brand["primary_color"].lstrip("#").upper())

    def set_font(run, size: float, bold: bool = False, color: RGBColor | None = None) -> None:
        run.font.name = font_name
        run.font.size = Pt(size)
        run.font.bold = bold
        if color is not None:
            run.font.color.rgb = color
        rfonts = run._element.get_or_add_rPr().get_or_add_rFonts()
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rfonts.set(qn(attr), font_name)

    doc = Document()
    doc.core_properties.title = content.title
    doc.core_properties.author = brand["company"]
    if demo:
        set_font(doc.add_paragraph().add_run("DEMO — NOT FINAL (not AI-generated)"), 11, True, RGBColor(0xDC, 0x26, 0x26))
    set_font(doc.add_paragraph().add_run(label.upper()), 10, True, primary)
    set_font(doc.add_paragraph().add_run(content.title), 22, True)
    set_font(doc.add_paragraph().add_run(content.subtitle or brand["company"]), 12)
    set_font(doc.add_paragraph().add_run(date.today().isoformat()), 9)
    for section in content.sections:
        heading = doc.add_paragraph()
        heading.paragraph_format.space_before = Pt(14)
        set_font(heading.add_run(section.heading), 14, True, primary)
        for text in section.paragraphs:
            set_font(doc.add_paragraph().add_run(text), 11)
        for bullet in section.bullets:
            set_font(doc.add_paragraph(style="List Bullet").add_run(bullet), 11)
    footer = doc.sections[0].footer.paragraphs[0]
    set_font(footer.add_run(f"{brand['company']} · {brand['product']}"), 8)
    doc.save(str(out))


def run(ctx: AgentContext) -> dict[str, Any]:
    p = ctx.task.params
    doc_type = p.get("doc_type", "proposal")
    language = p.get("language", "th")
    formats = p.get("formats") or ["pdf", "docx"]
    tpl = TEMPLATES[doc_type]
    brand = ctx.brand

    route = ctx.use_route(route_llm(ctx.session, ctx.job, ctx.org))  # type: ignore[arg-type]
    if route.demo or route.openai is None:
        content = _demo_content(ctx, doc_type, language)
    else:
        brief = ctx.content_brief()
        user = (
            f"{prompts.context_block(ctx.business_context, brand)}\n\n"
            f"Document type: {tpl['label']['en']}\nLanguage: {'Thai' if language == 'th' else 'English'}\n"
            f"Section headings to use, in order: {tpl['body'][language]}\n"
            f"Campaign brief from the Content agent: {(brief or {}).get('brief', 'not available')}\n\n"
            f"Request from the business owner:\n\"\"\"\n{ctx.job.command}\n\"\"\"\n"
            "Each section: 1-3 paragraphs and optional bullets. Title max 90 characters."
            + (f"\nAdditional instruction from the business owner for this version: {p['instruction']}"
               if p.get("instruction") else "")
        )
        content, usage = route.openai.complete_json(prompts.DOCUMENT, user, DocumentContent, temperature=0.4)
        ctx.record_usage("openai", "chat", {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
                         usage.cost_usd)
    content.sections.extend(fixed_sections(doc_type, language, brand["company"]))
    ctx.heartbeat()

    label = tpl["label"][language]
    meta = {"doc_type": doc_type, "language": language, "provider": route.provider, "license": "generated",
            "template": doc_type, "sections": [s.heading for s in content.sections]}
    produced: dict[str, Any] = {"doc_type": doc_type, "title": content.title, "formats": [], "pages": None}
    if "pdf" in formats:
        pdf = ctx.workdir / f"{doc_type}.pdf"
        pages = render_pdf(content, pdf, brand, label=label, demo=route.demo)
        ctx.save_artifact("document_pdf", f"{doc_type}.pdf", "application/pdf", path=pdf, is_demo=route.demo,
                          metadata={**meta, "pages": pages})
        produced["formats"].append("pdf")
        produced["pages"] = pages
    if "docx" in formats:
        docx = ctx.workdir / f"{doc_type}.docx"
        render_docx(content, docx, brand, label=label, demo=route.demo)
        ctx.save_artifact("document_docx", f"{doc_type}.docx",
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                          path=docx, is_demo=route.demo, metadata=meta)
        produced["formats"].append("docx")
    return produced
