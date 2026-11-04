#!/usr/bin/env python3
"""
Build the branded Bogwater & Burr proposal (.docx) from a small Markdown dialect.

Why this exists: letting the coordinator (an Opus model) write ~30k characters of python-docx
code took ~160 s per run. With this builder it only writes the proposal TEXT; colours, cover,
tables, callouts and footer are applied here, identically every time.

Usage:
    python3 build_docx.py proposal.md out.docx

Markdown dialect (everything else is treated as plain paragraphs):

    ---                                  front matter (first lines of the file)
    customer: Acme Corp
    title: Enterprise Data Platform: RFP Response
    date: 2026-05-26
    ---
    ## The Short Version — Executive summary      section heading (swamp heading — plain subtitle)
    Plain paragraph with **bold** and *italic*.   consecutive lines form one paragraph
    ### Smaller heading                           sub-heading
    - bullet                                      bullet list
    1. numbered item                              numbered list
    > Swamp Wisdom: one gruff, funny line.        callout box (Reed background, Lantern Gold edge)
    | Item | Detail |                             pipe table, first row = header
    |------|--------|
    | Term | 3 years |
    ---pagebreak---                               hard page break

Brand (from skills/firm-voice/SKILL.md): Swamp Green #4A6B2A, Peat Brown #5C4033, Lantern Gold
#D4A017, Reed #EEF2E2, Charcoal #2B2B2B. Cover page, section rules, table styling and the footer
are generated automatically.
"""

import argparse
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

GREEN, BROWN, GOLD, REED, CHARCOAL = "4A6B2A", "5C4033", "D4A017", "EEF2E2", "2B2B2B"
COMPANY = "Bogwater & Burr"
COMPANY_LONG = "Bogwater & Burr Data Works"
TAGLINE = "No magic beans. Just a platform that works."
FOOTER = f"{COMPANY_LONG} — Confidential — No trolls were harmed in the making of this proposal."
FONT = "Calibri"

PPR_AFTER_PBDR = (
    "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap", "w:overflowPunct",
    "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd",
    "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents",
    "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment", "w:textboxTightWrap",
    "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
)


def rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color)


# ------------------------------------------------------------------ low-level helpers

def para_border(paragraph, edge: str, color: str, size: int = 8, space: int = 2) -> None:
    """Thin rule above ('top') or below ('bottom') a paragraph."""
    pPr = paragraph._p.get_or_add_pPr()
    pbdr = pPr.find(qn("w:pBdr"))
    if pbdr is None:
        pbdr = OxmlElement("w:pBdr")
        pPr.insert_element_before(pbdr, *PPR_AFTER_PBDR)
    el = OxmlElement(f"w:{edge}")
    for k, v in (("val", "single"), ("sz", size), ("space", space), ("color", color)):
        el.set(qn(f"w:{k}"), str(v))
    pbdr.append(el)


def cell_borders(cell, **edges) -> None:
    """edges: top/left/bottom/right = (color, size) or None for no border. Call BEFORE shading."""
    tcPr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        spec = edges.get(edge)
        if spec:
            el.set(qn("w:val"), "single")
            el.set(qn("w:color"), spec[0])
            el.set(qn("w:sz"), str(spec[1]))
        else:
            el.set(qn("w:val"), "nil")
        borders.append(el)
    tcPr.append(borders)


def cell_shade(cell, fill: str) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcPr.append(shd)


def cell_margins(cell, top=80, bottom=80, left=120, right=120) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for edge, val in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:w"), str(val))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tcPr.append(mar)


def no_split(row) -> None:
    trPr = row._tr.get_or_add_trPr()
    trPr.append(OxmlElement("w:cantSplit"))


def add_runs(paragraph, text: str, size=None, color=CHARCOAL, bold=False, italic=False) -> None:
    """Write text with **bold** and *italic* inline markup."""
    for token in re.split(r"(\*\*.+?\*\*|\*.+?\*)", text):
        if not token:
            continue
        b, i = bold, italic
        if token.startswith("**") and token.endswith("**") and len(token) > 4:
            token, b = token[2:-2], True
        elif token.startswith("*") and token.endswith("*") and len(token) > 2:
            token, i = token[1:-1], True
        run = paragraph.add_run(token)
        run.bold, run.italic = b, i
        run.font.color.rgb = rgb(color)
        if size:
            run.font.size = Pt(size)


def spacing(paragraph, before=0, after=6, line=None) -> None:
    pf = paragraph.paragraph_format
    pf.space_before, pf.space_after = Pt(before), Pt(after)
    if line:
        pf.line_spacing = line


def page_number_field(paragraph, color=BROWN, size=8) -> None:
    run = paragraph.add_run()
    run.font.size, run.font.color.rgb = Pt(size), rgb(color)
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)


# ------------------------------------------------------------------ document parts

def setup(doc: Document) -> None:
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin, sec.bottom_margin = Cm(2.2), Cm(2.0)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = FONT, Pt(10.5)
    normal.font.color.rgb = rgb(CHARCOAL)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    normal.paragraph_format.space_after = Pt(6)

    footer = sec.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para_border(p, "top", GREEN, size=6, space=4)
    add_runs(p, FOOTER, size=8, color=BROWN, italic=True)
    p2 = footer.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    page_number_field(p2)


def cover(doc: Document, meta: dict) -> None:
    p = doc.add_paragraph()
    spacing(p, before=140, after=0)
    add_runs(p, COMPANY, size=40, color=GREEN, bold=True)
    p = doc.add_paragraph()
    add_runs(p, "DATA WORKS", size=14, color=BROWN, bold=True)
    para_border(p, "bottom", GREEN, size=18, space=8)
    spacing(p, after=14)
    p = doc.add_paragraph()
    add_runs(p, TAGLINE, size=15, color=GOLD, bold=True, italic=True)
    spacing(p, after=70)
    p = doc.add_paragraph()
    add_runs(p, meta.get("title", "Proposal Response"), size=24, color=CHARCOAL, bold=True)
    spacing(p, after=8)
    p = doc.add_paragraph()
    add_runs(p, f"Prepared for {meta.get('customer', 'our customer')}", size=14, color=BROWN)
    spacing(p, after=2)
    if meta.get("date"):
        p = doc.add_paragraph()
        add_runs(p, meta["date"], size=11, color=BROWN, italic=True)
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def heading(doc: Document, text: str) -> None:
    title, _, subtitle = text.partition(" — ")
    p = doc.add_paragraph()
    spacing(p, before=16, after=2)
    p.paragraph_format.keep_with_next = True
    add_runs(p, title.strip(), size=18, color=GREEN, bold=True)
    if not subtitle:
        para_border(p, "bottom", GREEN, size=8, space=3)
        spacing(p, before=16, after=8)
        return
    p2 = doc.add_paragraph()
    p2.paragraph_format.keep_with_next = True
    add_runs(p2, subtitle.strip(), size=11, color=BROWN, italic=True)
    para_border(p2, "bottom", GREEN, size=8, space=3)
    spacing(p2, after=8)


def subheading(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.keep_with_next = True
    add_runs(p, text, size=12.5, color=BROWN, bold=True)
    spacing(p, before=8, after=3)


def paragraph(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    add_runs(p, text)
    spacing(p, after=6, line=1.12)


def list_item(doc: Document, marker: str, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.9)
    p.paragraph_format.first_line_indent = Cm(-0.5)
    add_runs(p, marker + "\t", color=GREEN, bold=True)
    p.paragraph_format.tab_stops.add_tab_stop(Cm(0.9))
    add_runs(p, text)
    spacing(p, after=3, line=1.1)


def callout(doc: Document, text: str) -> None:
    text = re.sub(r"^\s*Swamp Wisdom\s*:?\s*", "", text, flags=re.I)
    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    cell.width = Cm(16.6)
    cell_borders(cell, left=(GOLD, 36))
    cell_shade(cell, REED)
    cell_margins(cell, top=110, bottom=110, left=200, right=160)
    p = cell.paragraphs[0]
    add_runs(p, "SWAMP WISDOM", size=8.5, color=GOLD, bold=True)
    spacing(p, after=2)
    p = cell.add_paragraph()
    add_runs(p, text, size=10.5, color=CHARCOAL, italic=True)
    spacing(p, after=0)
    gap = doc.add_paragraph()
    spacing(gap, after=4)


def table(doc: Document, rows: list) -> None:
    cols = max(len(r) for r in rows)
    t = doc.add_table(rows=len(rows), cols=cols)
    t.autofit = True
    light = ("C9D3B5", 4)
    for r, cells in enumerate(rows):
        cells = cells + [""] * (cols - len(cells))
        no_split(t.rows[r])
        for c, text in enumerate(cells):
            cell = t.rows[r].cells[c]
            cell_borders(cell, top=light, bottom=light, left=light, right=light)
            if r == 0:
                cell_shade(cell, GREEN)
            elif r % 2 == 0:
                cell_shade(cell, REED)
            cell_margins(cell, top=60, bottom=60, left=100, right=100)
            p = cell.paragraphs[0]
            spacing(p, after=0)
            add_runs(p, text, size=9.5, color="FFFFFF" if r == 0 else CHARCOAL, bold=(r == 0))
    gap = doc.add_paragraph()
    spacing(gap, after=4)


# ------------------------------------------------------------------ parsing

def parse_front_matter(lines: list) -> tuple:
    meta, i = {}, 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return meta, lines[i + 1:]
            key, _, value = lines[i].partition(":")
            if value:
                meta[key.strip().lower()] = value.strip()
    return {}, lines


def is_special(line: str) -> bool:
    s = line.lstrip()
    return bool(
        s.startswith(("#", ">", "|", "---pagebreak---"))
        or re.match(r"^[-*] ", s) or re.match(r"^\d+[.)] ", s)
    )


def build(md_text: str, out: Path) -> None:
    meta, lines = parse_front_matter(md_text.splitlines())
    doc = Document()
    setup(doc)
    cover(doc, meta)
    sections, i = 0, 0
    while i < len(lines):
        line = lines[i].rstrip()
        s = line.strip()
        if not s:
            i += 1
        elif s == "---pagebreak---":
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            i += 1
        elif s.startswith("### "):
            subheading(doc, s[4:])
            i += 1
        elif s.startswith("## "):
            heading(doc, s[3:])
            sections += 1
            i += 1
        elif s.startswith("# "):  # a stray H1: treat like a section heading
            heading(doc, s[2:])
            sections += 1
            i += 1
        elif s.startswith(">"):
            block = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                block.append(lines[i].strip().lstrip(">").strip())
                i += 1
            callout(doc, " ".join(b for b in block if b))
        elif s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            if rows:
                table(doc, rows)
        elif re.match(r"^[-*] ", s):
            list_item(doc, "•", s[2:].strip())
            i += 1
        elif re.match(r"^\d+[.)] ", s):
            num, _, rest = s.partition(" ")
            list_item(doc, num, rest.strip())
            i += 1
        else:
            block = [s]
            i += 1
            while i < len(lines) and lines[i].strip() and not is_special(lines[i]):
                block.append(lines[i].strip())
                i += 1
            paragraph(doc, " ".join(block))
    if sections == 0:
        raise SystemExit("ERROR: no '## ' section headings found. Check the Markdown format (see --help).")
    doc.core_properties.author = COMPANY_LONG
    doc.core_properties.title = meta.get("title", "Proposal Response")
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    print(f"Wrote {out}  ({sections} sections, {len(doc.tables)} tables)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("markdown", type=Path, help="proposal in the Markdown dialect above")
    ap.add_argument("output", type=Path, help="target .docx (use /mnt/session/outputs/ in the container)")
    args = ap.parse_args()
    if not args.markdown.is_file():
        sys.exit(f"ERROR: {args.markdown} not found")
    build(args.markdown.read_text(encoding="utf-8"), args.output)


if __name__ == "__main__":
    main()
