"""
lab_record_router.py
─────────────────────
Lab module completion tracking + on-demand PDF record generation.

Logic:
  - Module is COMPLETE when all active questions in it are solved by the student
  - Completion is checked after every Accepted submission (called from submission_router)
  - PDF is generated ON DEMAND when student claims the record
  - PDF is never stored — generated fresh every time (no bucket needed)
  - Only lab courses can hit these endpoints

Mount with:
    app.include_router(lab_record_router.router, prefix="/api/lab-records")
"""

import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.courses.dependencies import get_db, get_current_user_id

# ReportLab imports
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether, Image
)
from reportlab.lib import colors
from reportlab.platypus.flowables import Flowable
from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

router = APIRouter(tags=["Lab Records"])

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm

# ── Brand config ──────────────────────────────────────────────────
BRAND_NAME           = "Lumetrix Classrooms"
BRAND_LOGO_PATH      = "logo.jpg"   # path relative to server cwd; adjust as needed
BRAND_LOGO_SIZE      = 7 * mm       # logo square size in footer
BRAND_LOGO_SIZE_COVER = 14 * mm     # logo square size on cover page


# ══════════════════════════════════════════════════════════════════
#  CUSTOM FLOWABLES
# ══════════════════════════════════════════════════════════════════

class PageBorderCanvas:
    """Mixin — draws border + header + footer on every page."""

    def __init__(self, student_name: str, module_name: str):
        self.student_name = student_name
        self.module_name  = module_name

    def draw_page_chrome(self, canvas, doc):
        canvas.saveState()
        w, h = A4

        # ── Outer border ──────────────────────────────────────────
        canvas.setStrokeColor(colors.black)
        canvas.setLineWidth(2)
        canvas.rect(10 * mm, 10 * mm, w - 20 * mm, h - 20 * mm)

        # ── Inner border (thin) ───────────────────────────────────
        canvas.setLineWidth(0.5)
        canvas.rect(12 * mm, 12 * mm, w - 24 * mm, h - 24 * mm)

        # ── Header line ───────────────────────────────────────────
        canvas.setLineWidth(0.8)
        canvas.line(14 * mm, h - 28 * mm, w - 14 * mm, h - 28 * mm)

        # ── Header text ───────────────────────────────────────────
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.black)
        # Left: module name
        canvas.drawString(14 * mm, h - 25 * mm, self.module_name.upper())
        # Right: student name
        canvas.drawRightString(w - 14 * mm, h - 25 * mm, self.student_name)

        # ── Footer line ───────────────────────────────────────────
        canvas.line(14 * mm, 19 * mm, w - 14 * mm, 19 * mm)

        # ── Page number (centred below footer line) ────────────────
        canvas.setFont("Helvetica", 8)
        canvas.drawCentredString(w / 2, 14 * mm, f"— {doc.page} —")

        # ── Brand: logo + name — bottom right, below footer line ──
        import os
        logo_size   = BRAND_LOGO_SIZE                 # 7 mm
        logo_bottom = 12 * mm                         # well below the footer line
        logo_x      = w - 14 * mm - logo_size         # right-aligned to inner border

        if os.path.exists(BRAND_LOGO_PATH):
            canvas.drawImage(
                BRAND_LOGO_PATH,
                logo_x, logo_bottom,
                width=logo_size, height=logo_size,
                preserveAspectRatio=True,
                mask="auto",
            )

        # Brand name — bold, larger, vertically centred with logo
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.setFillColor(colors.black)
        brand_text_x = logo_x - 2 * mm
        brand_text_y = logo_bottom + (logo_size / 2) - 2.8
        canvas.drawRightString(brand_text_x, brand_text_y, BRAND_NAME)

        canvas.restoreState()


class DiamondDivider(Flowable):
    """Full-width dashed line with ◆ in center between questions."""

    def __init__(self, width):
        Flowable.__init__(self)
        self.width  = width
        self.height = 8 * mm

    def draw(self):
        mid_y = self.height / 2
        mid_x = self.width / 2

        self.canv.setStrokeColor(colors.black)
        self.canv.setLineWidth(0.5)
        self.canv.setDash(3, 4)
        self.canv.line(0, mid_y, mid_x - 8, mid_y)
        self.canv.line(mid_x + 8, mid_y, self.width, mid_y)
        self.canv.setDash()  # reset

        # Diamond
        self.canv.setFillColor(colors.black)
        self.canv.setFont("Helvetica", 10)
        self.canv.drawCentredString(mid_x, mid_y - 3, "◆")


# ══════════════════════════════════════════════════════════════════
#  STYLES
# ══════════════════════════════════════════════════════════════════

def _build_styles():
    base = getSampleStyleSheet()

    cover_title = ParagraphStyle(
        "CoverTitle",
        fontName="Helvetica-Bold",
        fontSize=26,
        leading=32,
        alignment=TA_CENTER,
        spaceAfter=4 * mm,
    )
    cover_sub = ParagraphStyle(
        "CoverSub",
        fontName="Helvetica",
        fontSize=14,
        leading=18,
        alignment=TA_CENTER,
        spaceAfter=2 * mm,
    )
    cover_meta = ParagraphStyle(
        "CoverMeta",
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        alignment=TA_RIGHT,
        textColor=colors.black,
    )
    q_title = ParagraphStyle(
        "QTitle",
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        spaceBefore=4 * mm,
        spaceAfter=2 * mm,
    )
    q_desc = ParagraphStyle(
        "QDesc",
        fontName="Helvetica",
        fontSize=9,
        leading=14,
        leftIndent=4 * mm,
        spaceAfter=3 * mm,
        alignment=TA_JUSTIFY,
    )
    section_label = ParagraphStyle(
        "SectionLabel",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        spaceBefore=3 * mm,
        spaceAfter=1.5 * mm,
        textColor=colors.black,
        borderPad=0,
    )
    code_style = ParagraphStyle(
        "Code",
        fontName="Courier",
        fontSize=7.5,
        leading=11,
        leftIndent=3 * mm,
        spaceAfter=0,
    )
    result_pass = ParagraphStyle(
        "ResultPass",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        alignment=TA_RIGHT,
        spaceAfter=2 * mm,
    )
    result_fail = ParagraphStyle(
        "ResultFail",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        alignment=TA_RIGHT,
        spaceAfter=2 * mm,
    )

    return {
        "cover_title":   cover_title,
        "cover_sub":     cover_sub,
        "cover_meta":    cover_meta,
        "q_title":       q_title,
        "q_desc":        q_desc,
        "section_label": section_label,
        "code_style":    code_style,
        "result_pass":   result_pass,
        "result_fail":   result_fail,
    }


# ══════════════════════════════════════════════════════════════════
#  PDF BUILDER
# ══════════════════════════════════════════════════════════════════

def _build_pdf(
    course_title: str,
    module_title: str,
    student_name: str,
    student_id:   str,
    questions:    list,   # list of dicts with question + submission data
    generated_at: str,
) -> bytes:

    buf    = io.BytesIO()
    styles = _build_styles()

    # Chrome handler
    chrome = PageBorderCanvas(student_name=student_name, module_name=module_title)

    def on_page(canvas, doc):
        chrome.draw_page_chrome(canvas, doc)

    def on_first_page(canvas, doc):
        chrome.draw_page_chrome(canvas, doc)
        # ── Brand block pinned to bottom of cover ─────────────────
        import os
        w, h = A4
        brand_y      = 28 * mm          # above the inner border bottom
        logo_size    = BRAND_LOGO_SIZE_COVER
        left_x       = 14 * mm

        # thin rule above brand
        canvas.saveState()
        canvas.setStrokeColor(colors.black)
        canvas.setLineWidth(0.5)
        canvas.line(left_x, brand_y + logo_size + 3 * mm,
                    w - 14 * mm, brand_y + logo_size + 3 * mm)

        if os.path.exists(BRAND_LOGO_PATH):
            canvas.drawImage(
                BRAND_LOGO_PATH, left_x, brand_y,
                width=logo_size, height=logo_size,
                preserveAspectRatio=True, mask="auto",
            )
            text_x = left_x + logo_size + 3 * mm
        else:
            text_x = left_x

        canvas.setFont("Helvetica-Bold", 9)
        canvas.setFillColor(colors.black)
        canvas.drawString(text_x, brand_y + logo_size * 0.55, BRAND_NAME)

        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.Color(0.45, 0.45, 0.45))
        canvas.drawString(text_x, brand_y + logo_size * 0.2, "Lab Record \u00b7 Verified Completion")

        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=32 * mm,
        bottomMargin=25 * mm,
        title=f"{module_title} — Lab Record",
        author=student_name,
    )

    content_width = PAGE_W - 2 * MARGIN
    story = []

    # ══════════════════════════════════════════════════════════════
    #  COVER PAGE  (lab-record style — mirrors physical record book)
    # ══════════════════════════════════════════════════════════════

    # ── Styles for cover ─────────────────────────────────────────
    meta_label_style = ParagraphStyle(
        "MetaLabel", fontName="Helvetica-Bold", fontSize=9, leading=14,
    )
    exp_title_cover = ParagraphStyle(
        "ExpTitleCover", fontName="Helvetica-Bold", fontSize=13,
        leading=18, alignment=TA_CENTER,
    )
    section_head = ParagraphStyle(
        "SectionHead", fontName="Helvetica-Bold", fontSize=10,
        leading=14, spaceBefore=5 * mm, spaceAfter=2 * mm,
    )
    body_text = ParagraphStyle(
        "BodyText2", fontName="Helvetica", fontSize=9,
        leading=14, spaceAfter=1.5 * mm, leftIndent=4 * mm,
    )
    bullet_text = ParagraphStyle(
        "BulletText", fontName="Helvetica", fontSize=9,
        leading=14, leftIndent=8 * mm, spaceAfter=1 * mm,
    )

    # ── Ex. No / Date / Title header box ─────────────────────────
    left_col = [
        Paragraph("Ex. No: ___________", meta_label_style),
        Spacer(1, 3 * mm),
        Paragraph("Date: ___________", meta_label_style),
    ]
    right_col = Paragraph(module_title, exp_title_cover)

    header_table = Table(
        [[left_col, right_col]],
        colWidths=[content_width * 0.30, content_width * 0.70],
    )
    header_table.setStyle(TableStyle([
        ("BOX",          (0, 0), (-1, -1), 1.2, colors.black),
        ("LINEBEFORE",   (1, 0), (1, -1),  0.8, colors.black),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING",   (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 8),
    ]))

    story.append(Spacer(1, 8 * mm))
    story.append(header_table)
    story.append(Spacer(1, 5 * mm))

    # ── AIM ───────────────────────────────────────────────────────
    # Collect unique languages used across all questions
    lang_set = sorted({q.get("language","").strip() for q in questions if q.get("language","").strip()})
    lang_display = ", ".join(lang_set) if lang_set else "the given programming language"

    story.append(Paragraph("AIM:", section_head))
    story.append(Paragraph(
        f"The aim of this program is to understand and demonstrate the concepts covered "
        f"in <b>{module_title}</b> using <b>{lang_display}</b>.",
        body_text,
    ))

    # ── SOFTWARE REQUIRED ─────────────────────────────────────────
    story.append(Paragraph("SOFTWARE REQUIRED:", section_head))
    story.append(Paragraph(
        "&#8226; <b>Lumetrix Classrooms</b> — an advanced online lab environment "
        "with a built-in code editor, multi-language compiler, automated test-case "
        "runner, and instant submission feedback.",
        bullet_text,
    ))
    story.append(Paragraph(
        "&#8226; A modern <b>Web Browser</b> (Chrome / Firefox / Edge / Safari) "
        "to access Lumetrix Classrooms.",
        bullet_text,
    ))

    # ── HARDWARE REQUIRED ─────────────────────────────────────────
    story.append(Paragraph("HARDWARE REQUIRED:", section_head))
    story.append(Paragraph(
        "&#8226; A computer, laptop, or <b>mobile phone</b> capable of running "
        "a modern web browser with a stable internet connection.",
        bullet_text,
    ))

    # ── PROCEDURE ─────────────────────────────────────────────────
    story.append(Paragraph("PROCEDURE", section_head))

    procedure_steps = [
        ("<b>Open Lumetrix Classrooms:</b> Log in to your account and navigate "
         "to the assigned lab module under your enrolled course."),
        ("<b>Read the Problem Statement:</b> Carefully read the question, including "
         "the description, constraints, and sample test cases provided."),
        ("<b>Write the Program:</b> Use the built-in code editor to write your solution "
         "in the given programming language."),
        ("<b>Tap Run:</b> Click the <b>Run</b> button to compile and execute your program. "
         "Review the output and fix any errors before proceeding."),
        ("<b>Submit:</b> Once the output is correct, click <b>Submit</b>. The system will "
         "automatically validate your solution against all test cases."),
        ("<b>Claim the Record:</b> After successfully completing all questions in the module, "
         "navigate to the module page and claim your lab record. "
         "The PDF is generated instantly with your accepted solutions and verified results."),
    ]

    for i, step in enumerate(procedure_steps, start=1):
        story.append(Paragraph(
            f"{i}.&nbsp;&nbsp;{step}",
            ParagraphStyle(
                f"Step{i}", fontName="Helvetica", fontSize=9,
                leading=14, leftIndent=6 * mm, spaceAfter=3 * mm,
            )
        ))

    # Brand block is drawn via canvas in on_first_page (pinned to bottom of page)
    story.append(PageBreak())

    # ── QUESTIONS ─────────────────────────────────────────────────
    diff_order = {"hard": 0, "medium": 1, "easy": 2}
    questions_sorted = sorted(
        questions, key=lambda x: diff_order.get(x.get("difficulty", "easy"), 3)
    )

    for idx, q in enumerate(questions_sorted):
        q_blocks = []

        # ── Q Title ───────────────────────────────────────────────
        diff_label = q.get("difficulty", "").upper()
        q_blocks.append(Paragraph(
            f"Q{idx + 1}.  {q['title']}  "
            f"<font size='8' color='grey'>[{diff_label}]</font>",
            styles["q_title"]
        ))

        # Left-border description block via table trick
        desc_text = q.get("description", "").replace("\n", "<br/>")
        desc_table = Table(
            [[Paragraph(desc_text, styles["q_desc"])]],
            colWidths=[content_width - 6 * mm],
        )
        desc_table.setStyle(TableStyle([
            ("LEFTPADDING",  (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING",   (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
            ("LINEBEFORE",   (0, 0), (0, -1), 3, colors.black),
            ("BACKGROUND",   (0, 0), (-1, -1), colors.white),
        ]))
        q_blocks.append(desc_table)
        q_blocks.append(Spacer(1, 3 * mm))

        # ── SOLUTION ──────────────────────────────────────────────
        q_blocks.append(Paragraph("SOLUTION", styles["section_label"]))

        code_raw  = q.get("code", "# No accepted submission found")
        lang_label = q.get("language", "").upper()

        # Code lines — escape HTML chars
        code_lines = code_raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        code_para  = Paragraph(
            code_lines.replace("\n", "<br/>").replace(" ", "&nbsp;"),
            styles["code_style"]
        )

        lang_para = Paragraph(
            f"<font size='7' color='grey'>{lang_label}</font>",
            ParagraphStyle("LangLabel", fontName="Helvetica", fontSize=7,
                           alignment=TA_RIGHT)
        )

        code_table = Table(
            [[lang_para], [code_para]],
            colWidths=[content_width],
        )
        code_table.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), colors.Color(0.95, 0.95, 0.95)),
            ("BOX",          (0, 0), (-1, -1), 0.8, colors.black),
            ("LEFTPADDING",  (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ]))
        q_blocks.append(code_table)
        q_blocks.append(Spacer(1, 3 * mm))

        # ── TEST CASES ────────────────────────────────────────────
        q_blocks.append(Paragraph("TEST CASES", styles["section_label"]))

        public_tcs = q.get("public_test_cases", [])[:3]
        if public_tcs:
            tc_data = [
                [
                    Paragraph("<b>INPUT</b>", ParagraphStyle(
                        "TCH", fontName="Helvetica-Bold", fontSize=8,
                        alignment=TA_CENTER, textColor=colors.white)),
                    Paragraph("<b>EXPECTED OUTPUT</b>", ParagraphStyle(
                        "TCH2", fontName="Helvetica-Bold", fontSize=8,
                        alignment=TA_CENTER, textColor=colors.white)),
                ]
            ]
            for tc in public_tcs:
                inp = str(tc.get("input", "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                out = str(tc.get("output", "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                tc_data.append([
                    Paragraph(f"<font face='Courier' size='8'>{inp}</font>",
                               ParagraphStyle("TCI", fontName="Courier", fontSize=8)),
                    Paragraph(f"<font face='Courier' size='8'>{out}</font>",
                               ParagraphStyle("TCO", fontName="Courier", fontSize=8)),
                ])

            col_w = content_width / 2
            tc_table = Table(tc_data, colWidths=[col_w, col_w])
            tc_table.setStyle(TableStyle([
                ("BOX",          (0, 0), (-1, -1), 0.8, colors.black),
                ("INNERGRID",    (0, 0), (-1, -1), 0.4, colors.black),
                ("BACKGROUND",   (0, 0), (-1, 0),  colors.black),
                ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
                ("LEFTPADDING",  (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING",   (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.white, colors.Color(0.97, 0.97, 0.97)]),
            ]))
            q_blocks.append(tc_table)
        else:
            q_blocks.append(Paragraph(
                "No public test cases available.",
                styles["q_desc"]
            ))

        q_blocks.append(Spacer(1, 3 * mm))

        # ── RESULT ────────────────────────────────────────────────
        passed = q.get("passed", 0)
        total  = q.get("total",  0)

        if total > 0 and passed == total:
            result_text  = f"✓  ALL {total} TEST CASES PASSED"
            result_style = styles["result_pass"]
        else:
            result_text  = f"✓  {passed} / {total} TEST CASES PASSED"
            result_style = styles["result_pass"] if passed > 0 else styles["result_fail"]

        q_blocks.append(Paragraph(result_text, result_style))

        # Keep the whole question together if possible, else allow split
        story.append(KeepTogether(q_blocks))

        # Divider between questions
        if idx < len(questions_sorted) - 1:
            story.append(DiamondDivider(content_width))

    # ══════════════════════════════════════════════════════════════
    #  RESULT PAGE
    # ══════════════════════════════════════════════════════════════
    story.append(PageBreak())

    # Styles for result page
    result_page_title = ParagraphStyle(
        "ResultPageTitle", fontName="Helvetica-Bold", fontSize=15,
        leading=20, alignment=TA_CENTER, spaceAfter=2 * mm,
    )
    result_page_sub = ParagraphStyle(
        "ResultPageSub", fontName="Helvetica", fontSize=9,
        leading=13, alignment=TA_CENTER,
        textColor=colors.Color(0.35, 0.35, 0.35), spaceAfter=6 * mm,
    )
    result_row_label = ParagraphStyle(
        "ResultRowLabel", fontName="Helvetica-Bold", fontSize=9, leading=13,
    )
    result_row_value = ParagraphStyle(
        "ResultRowValue", fontName="Helvetica", fontSize=9, leading=13,
    )
    result_verdict = ParagraphStyle(
        "ResultVerdict", fontName="Helvetica-Bold", fontSize=11,
        leading=16, alignment=TA_CENTER, spaceAfter=3 * mm,
    )
    result_sign_label = ParagraphStyle(
        "ResultSignLabel", fontName="Helvetica", fontSize=8,
        leading=12, alignment=TA_CENTER,
        textColor=colors.Color(0.45, 0.45, 0.45),
    )

    # Push result block to lower portion of the page
    story.append(Spacer(1, 38 * mm))

    # ── Section heading ───────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.black, spaceAfter=5 * mm))
    story.append(Paragraph("RESULT", result_page_title))
    story.append(Paragraph(
        f"{module_title}  ·  {course_title}",
        result_page_sub,
    ))
    story.append(HRFlowable(width="40%", thickness=0.5, color=colors.black,
                            hAlign="CENTER", spaceAfter=6 * mm))

    # ── Per-question result summary table ─────────────────────────
    res_table_data = [[
        Paragraph("<b>No.</b>", ParagraphStyle("RH0", fontName="Helvetica-Bold", fontSize=8, alignment=TA_CENTER, textColor=colors.white)),
        Paragraph("<b>Question</b>", ParagraphStyle("RH1", fontName="Helvetica-Bold", fontSize=8, textColor=colors.white)),
        Paragraph("<b>Language</b>", ParagraphStyle("RH2", fontName="Helvetica-Bold", fontSize=8, alignment=TA_CENTER, textColor=colors.white)),
        Paragraph("<b>Test Cases</b>", ParagraphStyle("RH3", fontName="Helvetica-Bold", fontSize=8, alignment=TA_CENTER, textColor=colors.white)),
        Paragraph("<b>Status</b>", ParagraphStyle("RH4", fontName="Helvetica-Bold", fontSize=8, alignment=TA_CENTER, textColor=colors.white)),
    ]]

    for idx, q in enumerate(questions_sorted):
        passed = q.get("passed", 0)
        total  = q.get("total", 0)
        tc_str = f"{passed} / {total}" if total > 0 else "—"
        status = "✓  PASS" if (total > 0 and passed == total) else f"{passed}/{total}"
        res_table_data.append([
            Paragraph(str(idx + 1), ParagraphStyle(f"RN{idx}", fontName="Helvetica", fontSize=8, alignment=TA_CENTER)),
            Paragraph(q.get("title", ""), ParagraphStyle(f"RT{idx}", fontName="Helvetica", fontSize=8)),
            Paragraph(q.get("language", "").upper(), ParagraphStyle(f"RL{idx}", fontName="Helvetica", fontSize=8, alignment=TA_CENTER)),
            Paragraph(tc_str, ParagraphStyle(f"RTC{idx}", fontName="Courier", fontSize=8, alignment=TA_CENTER)),
            Paragraph(status, ParagraphStyle(f"RS{idx}", fontName="Helvetica-Bold", fontSize=8, alignment=TA_CENTER)),
        ])

    col_widths = [
        content_width * 0.07,
        content_width * 0.42,
        content_width * 0.16,
        content_width * 0.16,
        content_width * 0.19,
    ]
    res_table = Table(res_table_data, colWidths=col_widths)
    res_table.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 0.8, colors.black),
        ("INNERGRID",     (0, 0), (-1, -1), 0.3, colors.black),
        ("BACKGROUND",    (0, 0), (-1, 0),  colors.black),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.Color(0.96, 0.96, 0.96)]),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(res_table)
    story.append(Spacer(1, 6 * mm))

    # ── Verdict statement ─────────────────────────────────────────
    total_q   = len(questions_sorted)
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.black, spaceAfter=5 * mm))
    story.append(Paragraph(
        f"All {total_q} question{'s' if total_q != 1 else ''} and topics in the "
        f"<b>{module_title}</b> module have been successfully understood and demonstrated.",
        ParagraphStyle("VerdictBody", fontName="Helvetica", fontSize=9,
                       leading=15, alignment=TA_CENTER, spaceAfter=8 * mm),
    ))

    # ── Signature row ─────────────────────────────────────────────
    sign_data = [[
        Paragraph("____________________________", result_sign_label),
        Paragraph("____________________________", result_sign_label),
    ],[
        Paragraph("Student Signature", result_sign_label),
        Paragraph("Staff Signature", result_sign_label),
    ]]
    sign_table = Table(sign_data, colWidths=[content_width / 2, content_width / 2])
    sign_table.setStyle(TableStyle([
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(sign_table)

    doc.build(story, onFirstPage=on_first_page, onLaterPages=on_page)
    buf.seek(0)
    return buf.read()


# ══════════════════════════════════════════════════════════════════
#  BACKGROUND COMPLETION CHECKER
#  Called from submission_router after a first solve in a lab course
# ══════════════════════════════════════════════════════════════════

async def check_and_mark_module_completion(
    db,
    user_id:    str,
    course_id:  str,
    question_id: str,
):
    """
    Non-blocking background task.
    After a question is solved in a lab course:
      1. Find which module this question belongs to
      2. Check if ALL active questions in that module are now solved
      3. If yes and not already marked → add module_id to completed_modules
    """
    try:
        # Find the question's module
        question = await db.course_questions.find_one({"question_id": question_id})
        if not question:
            return

        module_id = question.get("module_id")
        if not module_id:
            return

        # Get enrollment
        enrollment = await db.course_enrollments.find_one({
            "course_id": course_id,
            "user_id":   user_id,
            "is_active": True
        })
        if not enrollment:
            return

        # Already marked complete?
        completed_modules = enrollment.get("completed_modules", [])
        if module_id in completed_modules:
            return

        # All active questions in this module
        module_questions = await db.course_questions.find(
            {"course_id": course_id, "module_id": module_id, "is_active": True}
        ).to_list(length=None)

        if not module_questions:
            return

        module_q_ids  = {q["question_id"] for q in module_questions}
        solved_q_ids  = set(enrollment.get("solved_questions", []))

        # Check if all solved
        if module_q_ids.issubset(solved_q_ids):
            await db.course_enrollments.update_one(
                {"course_id": course_id, "user_id": user_id},
                {
                    "$addToSet": {"completed_modules": module_id},
                    "$set":      {"updated_at": datetime.utcnow()}
                }
            )

    except Exception as e:
        # Never block submissions
        print(f"[LAB RECORD] Module completion check failed: {e}")


# ══════════════════════════════════════════════════════════════════
#  ENDPOINTS
# ══════════════════════════════════════════════════════════════════

@router.get("/course/{course_id}/module/{module_id}/status")
async def get_module_completion_status(
    course_id: str,
    module_id: str,
    db:      AsyncIOMotorDatabase = Depends(get_db),
    user_id: str                  = Depends(get_current_user_id),
):
    """
    Check if student has completed a lab module.
    Returns completion status + per-question solved status.
    """
    # Verify lab course
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.get("course_type") != "LAB":
        raise HTTPException(status_code=403, detail="This endpoint is for lab courses only")

    # Verify enrollment
    enrollment = await db.course_enrollments.find_one({
        "course_id": course_id,
        "user_id":   user_id,
        "is_active": True
    })
    if not enrollment:
        raise HTTPException(status_code=403, detail="Not enrolled in this lab")

    # Verify module
    module = await db.modules.find_one({"module_id": module_id, "course_id": course_id})
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")

    # Questions in module
    questions = await db.course_questions.find(
        {"course_id": course_id, "module_id": module_id, "is_active": True}
    ).to_list(length=None)

    solved_ids        = set(enrollment.get("solved_questions", []))
    completed_modules = enrollment.get("completed_modules", [])
    is_completed      = module_id in completed_modules

    q_status = [
        {
            "question_id": q["question_id"],
            "title":       q["title"],
            "difficulty":  q["difficulty"],
            "is_solved":   q["question_id"] in solved_ids,
        }
        for q in questions
    ]

    solved_count = sum(1 for q in q_status if q["is_solved"])

    return {
        "course_id":     course_id,
        "module_id":     module_id,
        "module_title":  module["title"],
        "is_completed":  is_completed,
        "total_questions": len(questions),
        "solved_questions": solved_count,
        "can_claim_record": is_completed,
        "questions": q_status,
    }


@router.get("/course/{course_id}/completed-modules")
async def get_completed_modules(
    course_id: str,
    db:      AsyncIOMotorDatabase = Depends(get_db),
    user_id: str                  = Depends(get_current_user_id),
):
    """
    List all modules the student has completed in a lab course.
    Used by frontend to show claimable records.
    """
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.get("course_type") != "LAB":
        raise HTTPException(status_code=403, detail="This endpoint is for lab courses only")

    enrollment = await db.course_enrollments.find_one({
        "course_id": course_id,
        "user_id":   user_id,
        "is_active": True
    })
    if not enrollment:
        raise HTTPException(status_code=403, detail="Not enrolled in this lab")

    completed_modules = enrollment.get("completed_modules", [])

    modules = await db.modules.find(
        {"course_id": course_id, "module_id": {"$in": completed_modules}}
    ).sort("order", 1).to_list(length=None)

    return {
        "course_id":          course_id,
        "completed_count":    len(completed_modules),
        "completed_modules":  [
            {
                "module_id":    m["module_id"],
                "title":        m["title"],
                "order":        m["order"],
                "can_claim_record": True,
            }
            for m in modules
        ]
    }


@router.get("/course/{course_id}/module/{module_id}/record")
async def download_module_record(
    course_id: str,
    module_id: str,
    db:      AsyncIOMotorDatabase = Depends(get_db),
    user_id: str                  = Depends(get_current_user_id),
):
    """
    Generate and stream the PDF lab record for a completed module.

    - Only lab courses allowed
    - Student must be enrolled and must have completed the module
    - PDF generated on the fly — nothing stored
    - Non-blocking: StreamingResponse returns immediately as bytes stream

    PDF contains per question:
      - Title + description
      - Accepted code
      - Up to 3 public test cases
      - Total passed / total test cases
    """
    # ── Guard: lab course only ────────────────────────────────────
    course = await db.courses.find_one({"course_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.get("course_type") != "LAB":
        raise HTTPException(status_code=403, detail="Records are only available for lab courses")

    # ── Guard: enrolled ───────────────────────────────────────────
    enrollment = await db.course_enrollments.find_one({
        "course_id": course_id,
        "user_id":   user_id,
        "is_active": True
    })
    if not enrollment:
        raise HTTPException(status_code=403, detail="Not enrolled in this lab")

    # ── Guard: module completed ───────────────────────────────────
    completed_modules = enrollment.get("completed_modules", [])
    if module_id not in completed_modules:
        raise HTTPException(
            status_code=403,
            detail="Complete all questions in this module to claim your record"
        )

    # ── Fetch module ──────────────────────────────────────────────
    module = await db.modules.find_one({"module_id": module_id, "course_id": course_id})
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")

    # ── Fetch student profile ─────────────────────────────────────
    profile = await db.users_profile.find_one({"user_id": user_id})
    student_name = profile.get("username", "Student") if profile else "Student"
    student_id   = profile.get("sidhi_id", user_id)   if profile else user_id

    # ── Fetch questions in module ─────────────────────────────────
    questions = await db.course_questions.find(
        {"course_id": course_id, "module_id": module_id, "is_active": True}
    ).to_list(length=None)

    # ── Build question data for PDF ───────────────────────────────
    pdf_questions = []
    for q in questions:
        question_id = q["question_id"]

        # Best accepted submission
        best_sub = await db.course_submissions.find_one(
            {
                "user_id":     user_id,
                "course_id":   course_id,
                "question_id": question_id,
                "verdict":     "Accepted",
            },
            sort=[("league_points_awarded", -1)]
        )

        code         = best_sub.get("code", "# No code found") if best_sub else "# No code found"
        passed       = best_sub.get("result", {}).get("passed", 0) if best_sub else 0
        total        = best_sub.get("result", {}).get("total", 0)  if best_sub else 0
        language     = best_sub.get("language", q.get("language", "")) if best_sub else q.get("language", "")

        # Public test cases only (is_sample=True)
        public_tcs = [
            {"input": tc.get("input", ""), "output": tc.get("output", "")}
            for tc in q.get("test_cases", [])
            if tc.get("is_sample", False) or not tc.get("is_hidden", True)
        ][:3]

        pdf_questions.append({
            "title":            q.get("title", ""),
            "description":      q.get("description", ""),
            "difficulty":       q.get("difficulty", "easy"),
            "language":         language,
            "code":             code,
            "public_test_cases": public_tcs,
            "passed":           passed,
            "total":            total,
        })

    # ── Generate PDF ──────────────────────────────────────────────
    generated_at = datetime.utcnow().strftime("%d %B %Y, %H:%M UTC")

    pdf_bytes = _build_pdf(
        course_title  = course.get("title", "Lab Course"),
        module_title  = module.get("title", "Module"),
        student_name  = student_name,
        student_id    = student_id,
        questions     = pdf_questions,
        generated_at  = generated_at,
    )

    safe_module = module.get("title", "record").replace(" ", "_").lower()
    filename    = f"lab_record_{safe_module}_{student_id}.pdf"

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )