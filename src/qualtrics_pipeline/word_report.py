"""Render a run's frequency tables into an editable Word document (report.docx).

This is the presentation counterpart to report.html. It reads the same data
contract -- the frequency CSVs plus frequency_manifest.json, via report.load_run
-- and lays the tables out with the same shared logic (report.flat_layout /
report.grouped_layout), so every config option the HTML report honors (stats,
show_code, hide_codes, response_total, overall, orientation, pct_decimals,
include_missing) means the same thing here. What this module adds is only how
the document looks, controlled by a separate style config.

Table styling (header shading, banding, borders, cell padding, rows that never
split, headers that repeat on a new page, rotated crosstab headers, keeping a
question with its table) follows the conventions of the Qualtrics document
generator (adichiara/Qualtrics-document-generator).
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .report import (
    _BASE_NAME,
    _MISSING_CODE,
    _MISSING_LABEL,
    _N_FIELD,
    _aggregate_rows,
    _numeric_sort_key,
    _stat_field,
    _stat_label,
    flat_layout,
    grouped_layout,
    load_run,
    writein_counts,
)

# ---------------------------------------------------------------------------
# Style config
# ---------------------------------------------------------------------------

# null means "work it out from the page and the table".
DEFAULT_STYLE: dict[str, Any] = {
    "document": {
        "title": None,             # null -> the survey name, else "Survey frequency tables"
        "subtitle": True,          # respondent count and generation date under the title
        "notes": True,             # a short paragraph explaining the three bases
    },
    "fonts": {
        "family": "Arial",
        "title_size": 17,
        "question_size": 11,
        "table_size": 9,
        "caption_size": 8.5,
    },
    "page": {
        "paper_size": "letter",
        "orientation": "portrait",
        "margin_top_inches": 0.75, "margin_bottom_inches": 0.75,
        "margin_left_inches": 0.8, "margin_right_inches": 0.8,
    },
    "questions": {
        "numbering": "survey",     # survey (Q1.5) | sequential (1, 2, ...) | off
        "bold": True,
        "page_break_before": False,
        "show_base": True,         # "Base: Eligible n = 101" under each table
        "space_before_pt": 14,
    },
    "tables": {
        "theme": "corporate",      # minimal | boxed | corporate | grid (see _THEMES)
        "spacing": "tight",        # tight | normal | spacious cell padding
        "cell_padding_inches": None,  # overrides spacing's left/right padding
        "value_align": "center",   # center | right, for the numbers
        "header_color": "#F2F2F2",
        "banded_rows": True,
        "band_color": "#F9F9F9",
        "outline": {"weight_pt": 1.25, "color": "#000000"},   # table edge, header rule
        "divider": {"weight_pt": 0.5, "color": "#A6A6A6"},    # lines between rows/columns
        "stat_column_width_inches": 0.8,
        "code_column_width_inches": 0.5,
        "attribute_column_width_inches": None,
        "min_group_column_width_inches": 0.55,
        "keep_together_max_rows": 30,  # longer tables may break across pages
    },
    "crosstabs": {
        "header_rotation": "auto",     # auto | always | never
        "rotate_at_columns": 8,
        "header_max_height_inches": 1.5,  # a taller sideways header is clipped to this
    },
    "matrices": {
        # grid: one row per item, one column per scale point (a rating matrix as it
        #       appears in the survey); list: one row per item x answer.
        "layout": "grid",
        "max_columns": 12,         # more distinct answers than this -> list
        # Scale-point headers: never = horizontal, wrapped between words (the item
        # column narrows, then the header font shrinks toward header_min_size,
        # before anything else gives); always = sideways; auto = horizontal when
        # it fits, else sideways.
        "header_rotation": "never",
        "header_min_size": 7,
        "item_min_width_inches": 0.9,    # the item column narrows no further
        "max_column_width_inches": 1.1,  # scale columns stop widening here
        # Where an include_missing count goes: column; note = a line under the
        # table; auto = a column unless that makes the grid squeeze its headers
        # or split.
        "missing": "auto",
    },
    "write_ins": {
        "show": True,
        "max_rows": None,              # null = every distinct write-in
    },
}

# Which lines each theme draws: the table's outline, the rule under the header,
# and the dividers between rows and between columns. "heavy" lines use
# tables.outline, "light" ones tables.divider.
_THEMES = {
    "minimal":   {"outline": None,    "header": "heavy", "rows": "light", "cols": None},
    "boxed":     {"outline": "light", "header": "light", "rows": "light", "cols": None},
    "corporate": {"outline": "heavy", "header": "heavy", "rows": "light", "cols": None},
    "grid":      {"outline": "light", "header": "light", "rows": "light", "cols": "light"},
}
# Cell padding in inches: (top/bottom, left/right).
_SPACING = {"tight": (0.02, 0.05), "normal": (0.05, 0.08), "spacious": (0.12, 0.15)}

_ENUMS = {
    "theme": set(_THEMES),
    "spacing": set(_SPACING),
    "value_align": {"center", "right"},
    "layout": {"grid", "list"},
    "missing": {"auto", "column", "note"},
    "paper_size": {"letter", "a4", "legal"},
    "orientation": {"portrait", "landscape"},
    "numbering": {"survey", "sequential", "off"},
    "header_rotation": {"auto", "always", "never"},
}
_PAGE_SIZES = {"letter": (8.5, 11.0), "a4": (210 / 25.4, 297 / 25.4), "legal": (8.5, 14.0)}
# Settings that may be null; every other default fixes the setting's type.
_NULLABLE = {"title", "attribute_column_width_inches", "max_rows", "cell_padding_inches"}
_INTEGER = {"rotate_at_columns", "keep_together_max_rows", "max_rows", "max_columns"}
_ZERO_OK = {"cell_padding_inches", "space_before_pt"}


def merge_style(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_style(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def validate_style(patch: Any, schema: dict = DEFAULT_STYLE, path: str = "word") -> list[str]:
    """Problems with a style patch, as messages. Underscore keys are comments."""
    if not isinstance(patch, dict):
        return [f"{path}: must be a JSON object"]
    errors: list[str] = []
    for key, value in patch.items():
        name = f"{path}.{key}"
        if key.startswith("_"):
            continue
        if key not in schema:
            errors.append(f"{name}: unknown setting")
            continue
        default = schema[key]
        if isinstance(default, dict):
            errors.extend(validate_style(value, default, name))
        elif value is None:
            if key not in _NULLABLE:
                errors.append(f"{name}: cannot be null")
        elif key in _ENUMS:
            if value not in _ENUMS[key]:
                errors.append(f"{name}: must be one of {', '.join(sorted(_ENUMS[key]))}")
        elif key.endswith("color"):
            if not isinstance(value, str) or not re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
                errors.append(f"{name}: must be a six-digit hex color, e.g. #EAEAEA")
        elif isinstance(default, bool):
            if type(value) is not bool:
                errors.append(f"{name}: must be true or false")
        elif key == "title":
            if not isinstance(value, str):
                errors.append(f"{name}: must be a string or null")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                errors.append(f"{name}: must be a number")
            elif key in _INTEGER and not isinstance(value, int):
                errors.append(f"{name}: must be a whole number")
            elif value < 0 or (value == 0 and key not in _ZERO_OK):
                errors.append(f"{name}: must be {'zero or more' if key in _ZERO_OK else 'positive'}")
    return errors


def resolve_style(*patches: dict | None) -> dict:
    style = DEFAULT_STYLE
    for patch in patches:
        if not patch:
            continue
        errors = validate_style(patch)
        if errors:
            raise SystemExit("Invalid Word style config:\n  " + "\n  ".join(errors))
        style = merge_style(style, patch)
    return style


# ---------------------------------------------------------------------------
# Low-level docx helpers
# ---------------------------------------------------------------------------

def _hex(color: str) -> str:
    return color.lstrip("#").upper()


def _shade(cell, color: str) -> None:
    tcpr = cell._tc.get_or_add_tcPr()
    for old in tcpr.findall(qn("w:shd")):
        tcpr.remove(old)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), _hex(color))
    tcpr.append(shd)


def _cell_border(cell, edge: str, weight_pt: float, color: str) -> None:
    tcpr = cell._tc.get_or_add_tcPr()
    borders = tcpr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tcpr.append(borders)
    for old in borders.findall(qn(f"w:{edge}")):
        borders.remove(old)
    el = OxmlElement(f"w:{edge}")
    el.set(qn("w:val"), "single")
    el.set(qn("w:sz"), str(round(weight_pt * 8)))
    el.set(qn("w:color"), _hex(color))
    borders.append(el)


def _no_split(row) -> None:
    row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))


def _repeat_header(row) -> None:
    row._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))


def _rotate(cell) -> None:
    tcpr = cell._tc.get_or_add_tcPr()
    direction = OxmlElement("w:textDirection")
    direction.set(qn("w:val"), "btLr")
    tcpr.append(direction)


def _zero_spacing(p, line_spacing: float = 1.0) -> None:
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = line_spacing


# Arial advance widths (em) for the characters a number cell holds; anything
# else is taken as an average letter.
# Arial advance widths (em). Bold is wider by roughly 6%, applied as a factor.
_ARIAL_EM = {
    **dict.fromkeys("0123456789", 0.556),
    **dict(zip("abcdefghijklmnopqrstuvwxyz",
               (.556, .556, .5, .556, .556, .278, .556, .556, .222, .222, .5, .222, .833,
                .556, .556, .556, .556, .333, .5, .278, .556, .5, .722, .5, .5, .5), strict=True)),
    **dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
               (.667, .667, .722, .722, .667, .611, .778, .722, .278, .5, .667, .556, .833,
                .722, .778, .667, .778, .722, .667, .611, .722, .667, .944, .667, .667, .611),
               strict=True)),
    ".": 0.278, ",": 0.278, "%": 0.889, " ": 0.278, "-": 0.333, "(": 0.333, ")": 0.333,
    "/": 0.278, ":": 0.278, "'": 0.191, "&": 0.667,
}
def _text_width_in(text: str, size: float, bold: bool = False) -> float:
    """Approximate Arial width in inches -- enough to size columns and headers."""
    em = sum(_ARIAL_EM.get(ch, 0.6) for ch in text)
    return em * size * (1.06 if bold else 1.0) / 72


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def _plain(text: Any) -> str:
    """Question text as plain prose. Older runs carry Qualtrics' HTML through."""
    text = re.sub(r"<br\s*/?>", " ", str(text or ""), flags=re.IGNORECASE)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\s+", " ", text).strip()


def _code_shown(row: dict) -> str:
    """The code cell. A tabulated text answer's "code" is the answer itself, which
    the Response column already shows, so it is left blank rather than repeated."""
    code = str(row.get("response_code", ""))
    if code != str(row.get("response_label", "")):
        return code
    # A number labelled with itself (the 1-9 of a 0-10 scale) is a scale point.
    return code if _numeric_sort_key(code)[0] == 0 else ""


def _pct_text(value: Any, decimals: int) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.{decimals}f}%"
    except (TypeError, ValueError):
        return str(value)


def _stat_text(row: dict | None, stat: str, report_base: str, decimals: int) -> str:
    if row is None:
        return "—"
    field = _stat_field(stat, report_base)
    value = row.get(field, "")
    if field.endswith("_pct"):
        return _pct_text(value, decimals)
    if isinstance(value, float) and value == int(value):
        value = int(value)
    return "" if value is None else str(value)


class _Writer:
    def __init__(self, style: dict, title: str, subtitle: str | None):
        self.style = style
        self.doc = Document()
        self.number = 0
        self._last_qkey: str | None = None
        page = style["page"]
        width, height = _PAGE_SIZES[page["paper_size"]]
        if page["orientation"] == "landscape":
            width, height = height, width
        sec = self.doc.sections[0]
        sec.orientation = WD_ORIENT.LANDSCAPE if page["orientation"] == "landscape" else WD_ORIENT.PORTRAIT
        sec.page_width, sec.page_height = Inches(width), Inches(height)
        sec.top_margin = Inches(page["margin_top_inches"])
        sec.bottom_margin = Inches(page["margin_bottom_inches"])
        sec.left_margin = Inches(page["margin_left_inches"])
        sec.right_margin = Inches(page["margin_right_inches"])
        self.width = width - page["margin_left_inches"] - page["margin_right_inches"]
        if self.width < 2:
            raise SystemExit("Word style: page margins leave less than 2 inches of width")

        fonts = style["fonts"]
        normal = self.doc.styles["Normal"]
        normal.font.name = fonts["family"]
        normal.font.size = Pt(fonts["table_size"] + 1)
        normal.font.color.rgb = RGBColor(0, 0, 0)
        # East Asian font slot, or Word substitutes its own for some glyphs.
        normal.element.rPr.rFonts.set(qn("w:eastAsia"), fonts["family"])
        title_style = self.doc.styles["Title"]
        title_style.font.name = fonts["family"]
        title_style.font.size = Pt(fonts["title_size"])
        title_style.font.bold = True
        title_style.font.color.rgb = RGBColor(0, 0, 0)
        # Word's built-in Title style carries a colored bottom rule.
        ppr = title_style.element.get_or_add_pPr()
        rule = ppr.find(qn("w:pBdr"))
        if rule is not None:
            ppr.remove(rule)

        self.doc.add_paragraph(title, style="Title")
        if subtitle:
            self._caption(subtitle)

    # -- paragraphs ----------------------------------------------------------

    def _run(self, p, text: str, size: float, bold=False, italic=False, color: str | None = None):
        run = p.add_run(text)
        run.font.name = self.style["fonts"]["family"]
        run.font.size = Pt(size)
        run.bold = bold
        run.italic = italic
        if color:
            run.font.color.rgb = RGBColor.from_string(_hex(color))
        return run

    def _caption(self, text: str, keep_with_next: bool = False, space_before: float = 2):
        p = self.doc.add_paragraph()
        _zero_spacing(p)
        p.paragraph_format.space_before = Pt(space_before)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.keep_with_next = keep_with_next
        self._run(p, text, self.style["fonts"]["caption_size"], color="#555555")
        return p

    def notes(self) -> None:
        self._caption(
            "Count is the number of respondents who chose each response. Percentages use "
            "one of three bases, named under each table: Valid (respondents who answered "
            "the question), Eligible (respondents shown it, per the survey's display "
            "logic), or Total (all respondents). A Missing row, where shown, counts "
            "eligible respondents who left the question blank; it is not part of the Total.",
            space_before=6,
        )

    def question_heading(self, table, by_text: str | None) -> None:
        """The question's heading, once per question, then a sub-heading per breakout."""
        q = self.style["questions"]
        first = table.rows[0]
        if table.qkey != self._last_qkey:
            self._last_qkey = table.qkey
            self.number += 1
            label = {
                "survey": f"{first.get('question_id') or table.qkey}.",
                "sequential": f"{self.number}.",
                "off": "",
            }[q["numbering"]]
            p = self.doc.add_paragraph()
            _zero_spacing(p, 1.05)
            p.paragraph_format.space_before = Pt(q["space_before_pt"])
            p.paragraph_format.space_after = Pt(3)
            p.paragraph_format.keep_with_next = True
            p.paragraph_format.page_break_before = q["page_break_before"] and self.number > 1
            size = self.style["fonts"]["question_size"]
            if label:
                self._run(p, label + " ", size, bold=True)
            self._run(p, _plain(first.get("question_text", "")), size, bold=q["bold"])
        if by_text:
            p = self.doc.add_paragraph()
            _zero_spacing(p)
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.keep_with_next = True
            self._run(p, by_text, self.style["fonts"]["question_size"] - 1, bold=True, color="#333333")

    # -- tables --------------------------------------------------------------

    def _new_table(self, n_rows: int, widths: list[float]):
        t = self.style["tables"]
        table = self.doc.add_table(rows=n_rows, cols=len(widths))
        table.autofit = False
        tblpr = table._tbl.tblPr
        tblw = tblpr.find(qn("w:tblW"))
        tblw.set(qn("w:w"), str(round(sum(widths) * 1440)))
        tblw.set(qn("w:type"), "dxa")
        borders = OxmlElement("w:tblBorders")
        theme = _THEMES[t["theme"]]
        for edge, weight in (("top", theme["outline"]), ("left", theme["outline"]),
                             ("bottom", theme["outline"]), ("right", theme["outline"]),
                             ("insideH", theme["rows"]), ("insideV", theme["cols"])):
            el = OxmlElement(f"w:{edge}")
            line = self._line(weight)
            el.set(qn("w:val"), "single" if line else "nil")
            if line:
                el.set(qn("w:sz"), str(round(line[0] * 8)))
                el.set(qn("w:color"), _hex(line[1]))
            borders.append(el)
        tblpr.append(borders)
        margins = OxmlElement("w:tblCellMar")
        pad_v, pad_h = self._padding()
        for edge in ("top", "bottom", "left", "right"):
            el = OxmlElement(f"w:{edge}")
            pad = pad_h if edge in ("left", "right") else pad_v
            el.set(qn("w:w"), str(round(pad * 1440)))
            el.set(qn("w:type"), "dxa")
            margins.append(el)
        tblpr.append(margins)
        for col, width in zip(table.columns, widths, strict=True):
            col.width = Inches(width)
        for row in table.rows:
            for cell, width in zip(row.cells, widths, strict=True):
                cell.width = Inches(width)
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                _zero_spacing(cell.paragraphs[0])
        return table

    def _line(self, weight: str | None) -> tuple[float, str] | None:
        """(points, color) for a "heavy"/"light" line, or None for no line."""
        if weight is None:
            return None
        spec = self.style["tables"]["outline" if weight == "heavy" else "divider"]
        return spec["weight_pt"], spec["color"]

    def _padding(self) -> tuple[float, float]:
        t = self.style["tables"]
        pad_v, pad_h = _SPACING[t["spacing"]]
        if t["cell_padding_inches"] is not None:
            pad_h = t["cell_padding_inches"]
        return pad_v, pad_h

    @property
    def _value_align(self):
        return (WD_ALIGN_PARAGRAPH.CENTER if self.style["tables"]["value_align"] == "center"
                else WD_ALIGN_PARAGRAPH.RIGHT)

    def _set(self, cell, text: str, *, bold=False, italic=False, align=None, size=None, color=None):
        p = cell.paragraphs[0]
        for run in list(p.runs):
            run._r.getparent().remove(run._r)
        if align is not None:
            p.alignment = align
        lines = text.split("\n")
        for i, line in enumerate(lines):
            run = self._run(p, line, size or self.style["fonts"]["table_size"], bold, italic, color)
            if i < len(lines) - 1:
                run.add_break()

    def _finish_table(self, table, kinds: list[str], header_rows: int = 1) -> None:
        """Header shading/repeat, banding, Total/Missing emphasis, pagination."""
        t = self.style["tables"]
        rows = table.rows
        band = 0
        for i, row in enumerate(rows):
            _no_split(row)
            if i < header_rows:
                _repeat_header(row)
                rule = self._line(_THEMES[t["theme"]]["header"])
                for cell in row.cells:
                    _shade(cell, t["header_color"])
                    # Header text sits on the rule under it.
                    if cell._tc.get_or_add_tcPr().find(qn("w:textDirection")) is None:
                        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.BOTTOM
                    if rule and i == header_rows - 1:
                        _cell_border(cell, "bottom", rule[0], rule[1])
                continue
            kind = kinds[i - header_rows]
            if kind == "total":
                heavy = self._line("heavy") or (1.0, "#808080")
                for cell in row.cells:
                    _cell_border(cell, "top", heavy[0], heavy[1])
            elif kind == "missing" and (i == header_rows or kinds[i - header_rows - 1] != "missing"):
                for cell in row.cells:
                    _cell_border(cell, "top", t["divider"]["weight_pt"] * 2, t["divider"]["color"])
            if kind == "response":
                if t["banded_rows"] and band % 2 == 1:
                    for cell in row.cells:
                        _shade(cell, t["band_color"])
                band += 1
        # Keep the heading, header and table together when it is short enough to
        # fit; a longer table only keeps its header with the first body row.
        together = len(rows) <= t["keep_together_max_rows"]
        last = len(rows) - 1
        for i, row in enumerate(rows):
            keep = (i < last) if together else (i < header_rows)
            for cell in row.cells:
                for p in cell.paragraphs:
                    p.paragraph_format.keep_with_next = keep
                    p.paragraph_format.keep_together = True

    def _base_line(self, row: dict | None, report_base: str, hidden: int, extra: str = "") -> None:
        parts = []
        if self.style["questions"]["show_base"] and row:
            value = row.get(_N_FIELD.get(report_base, "eligible_n"), "")
            if value != "":
                note = f" ({hidden} response{'s' if hidden != 1 else ''} hidden)" if hidden else ""
                parts.append(f"Base: {_BASE_NAME.get(report_base, report_base)} n = {value}{note}")
        if extra:
            parts.append(extra)
        if parts:
            self._caption(" · ".join(parts), space_before=2)

    def _min_value_width(self, decimals: int) -> float:
        """Narrowest column that fits "100.00%" (at this precision) unwrapped."""
        sample = "100." + "0" * decimals + "%" if decimals else "100%"
        pad = self._padding()[1] * 2
        return max(self.style["tables"]["min_group_column_width_inches"],
                   _text_width_in(sample, self.style["fonts"]["table_size"]) + pad + 0.04)

    def flat_table(self, table) -> None:
        lay = flat_layout(table.rows, table.presentation, table.missing)
        stats, base, dec = lay["stats"], lay["report_base"], lay["decimals"]
        t = self.style["tables"]
        has_attr = lay["has_attribute"]
        responses = [r for k, r in lay["entries"] if k == "response"]
        # A select-all question's rows are its options (the attribute), each with
        # the same "1 / Selected" response -- so the options become the Response
        # column instead of repeating "Selected" on every row.
        # Tabulated write-ins (a verbatim "code") can sit among them.
        coded = [r for r in responses if _code_shown(r)]
        options_only = has_attr and bool(coded) and all(
            str(r.get("response_label", "")) == "Selected" for r in coded
        )
        if (self.style["matrices"]["layout"] == "grid" and has_attr and not options_only
                and coded and len(coded) == len(responses)
                and all((r.get("attribute") or "").strip() for r in responses)
                and len({r.get("response_code") for r in responses}) <= self.style["matrices"]["max_columns"]):
            self.matrix_table(table, lay)
            return
        # No column of nothing but blanks (see _code_shown).
        show_code = (lay["show_code"] and not options_only
                     and any(_code_shown(r) for r in lay["shown"]))
        stat_w = max(t["stat_column_width_inches"], self._min_value_width(dec))
        widths: list[float] = []
        attr_w = t["attribute_column_width_inches"] or max(1.2, min(2.2, self.width * 0.3))
        if has_attr and not options_only:
            widths.append(attr_w)
        if show_code:
            widths.append(t["code_column_width_inches"])
        label_w = max(1.2, self.width - sum(widths) - stat_w * len(stats))
        widths.append(label_w)
        widths.extend([stat_w] * len(stats))

        entries = lay["entries"]
        doc_table = self._new_table(len(entries) + 1, widths)
        head = doc_table.rows[0].cells
        lead_heads = (["Attribute"] if has_attr and not options_only else []) + \
            (["Code"] if show_code else []) + ["Response"]
        for c, text in enumerate(lead_heads):
            self._set(head[c], text, bold=True)
        for c, stat in enumerate(stats, start=len(lead_heads)):
            self._set(head[c], _stat_label(stat, base), bold=True, align=self._value_align)
        kinds = []
        for i, (kind, row) in enumerate(entries, start=1):
            kinds.append(kind)
            cells = doc_table.rows[i].cells
            c = 0
            if has_attr and not options_only:
                self._set(cells[c], "" if kind == "total" else row.get("attribute", ""))
                c += 1
            if show_code:
                self._set(cells[c], _code_shown(row) if kind == "response" else "")
                c += 1
            if kind == "total":
                label = "Total"
            elif kind == "missing":
                attr = row.get("attribute", "") if options_only else ""
                label = f"{attr}: {_MISSING_LABEL}" if attr else _MISSING_LABEL
            else:
                label = (row.get("attribute") or row.get("response_label", "")) if options_only \
                    else row.get("response_label", "")
            self._set(cells[c], label, bold=kind == "total", italic=kind == "missing")
            c += 1
            for stat in stats:
                self._set(cells[c], _stat_text(row, stat, base, dec), bold=kind == "total",
                          italic=kind == "missing", align=self._value_align)
                c += 1
        self._finish_table(doc_table, kinds)
        self._base_line(lay["shown"][0] if lay["shown"] else None, base, lay["hidden"])

    def _stacked(self, row: dict | None, stats: list[str], base: str, dec: int) -> str:
        """A cell holding several statistics, one per line. A blank one (Valid %
        on a Missing row) is dropped rather than leaving an empty line."""
        if row is None:
            return "\u2014"
        values = [_stat_text(row, s, base, dec) for s in stats]
        return "\n".join(v for i, v in enumerate(values) if v != "" or i == 0)

    def matrix_table(self, table, lay: dict) -> None:
        """A rating matrix as a grid: items down the side, scale points across.

        Reads the same rows the list layout would show (hidden codes removed,
        Valid n rebased), so only the arrangement differs. Total and Missing
        become columns, since each item has its own.
        """
        stats, base, dec = lay["stats"], lay["report_base"], lay["decimals"]
        pres = table.presentation
        responses = [r for k, r in lay["entries"] if k == "response"]
        missing = {r.get("attribute", ""): r for k, r in lay["entries"] if k == "missing"}

        items: list[str] = []
        by_item: dict[str, list[dict]] = {}
        idx: dict[tuple[str, str], dict] = {}
        labels: dict[str, str] = {}
        for r in responses:
            item, code = r.get("attribute", ""), str(r.get("response_code", ""))
            if item not in by_item:
                items.append(item)
                by_item[item] = []
            by_item[item].append(r)
            idx[(item, code)] = r
            labels.setdefault(code, r.get("response_label", code))
        # Scale points in survey order where the question defines one, which is
        # what a rating scale needs; anything else after, by code.
        # Every defined scale point gets a column, chosen or not -- a scale with a
        # gap in it would misread -- except the ones hidden with hide_codes.
        hidden = {str(c) for c in (pres.get("hide_codes") or [])}
        order: list[str] = []
        for u in table.universe or []:
            for code, label in u.get("codes") or []:
                code = str(code)
                if code not in hidden and code not in order:
                    order.append(code)
                    labels.setdefault(code, label)
        order += sorted((c for c in labels if c not in order), key=_numeric_sort_key)

        # Value columns: (header, kind, key)
        columns: list[tuple[str, str, str]] = []
        for code in order:
            head = labels[code]
            if lay["show_code"] and code != head:
                head = f"{head}\n({code})"
            columns.append((head, "response", code))
        total_pos = pres.get("response_total")
        if total_pos:
            total = ("Total", "total", "")
            columns = [total, *columns] if total_pos == "before" else [*columns, total]
        if base == "valid":
            # Valid n differs item by item, so it gets a column of its own.
            columns.append(("Valid n", "base", ""))
        totals = {item: _aggregate_rows(rs, base) for item, rs in by_item.items()}

        def text_for(item: str, kind: str, key: str) -> str:
            if kind == "response":
                return self._stacked(idx.get((item, key)), stats, base, dec)
            if kind == "total":
                return self._stacked(totals[item], stats, base, dec)
            if kind == "missing":
                return self._stacked(missing.get(item), stats, base, dec)
            return str(by_item[item][0].get("valid_n", ""))

        def geometry(cols_: list[tuple[str, str, str]]):
            grid_ = {item: [text_for(item, k, key) for _h, k, key in cols_] for item in items}
            # Each column as wide as its widest value, so only the Total column
            # pays for "100.0%".
            needs = [self._needed_width([grid_[item][j] for item in items], kind == "total")
                     for j, (_h, kind, _k) in enumerate(cols_)]
            return grid_, self._matrix_geometry(cols_, needs)

        # Missing goes last (before Valid n) as a column -- or, if that is what
        # makes the grid squeeze its headers or split, into a note under it.
        policy = self.style["matrices"]["missing"]
        missing_col = (_MISSING_LABEL, "missing", "")
        with_missing = [*columns[:-1], missing_col, columns[-1]] if base == "valid" \
            else [*columns, missing_col]
        missing_note = False
        if missing and policy == "note":
            missing_note = True
        elif missing and policy == "auto":
            grid, geo = geometry(with_missing)
            _g, alt = geometry(columns)
            # Better = fewer parts, then a larger header font, then horizontal.
            if (len(alt[2]), -alt[4], alt[3]) < (len(geo[2]), -geo[4], geo[3]):
                missing_note = True
            else:
                columns = with_missing
        elif missing:
            columns = with_missing
        grid, (item_w, widths, panels, rotate, head_size) = geometry(columns)
        size = self.style["fonts"]["table_size"]
        for p_i, cols in enumerate(panels):
            sub = [columns[j] for j in cols]
            doc_table = self._new_table(len(items) + 1, [item_w] + [widths[j] for j in cols])
            self._set(doc_table.rows[0].cells[0], "Item", bold=True)
            for j, col in enumerate(cols, start=1):
                self._header_cell(doc_table.rows[0].cells[j], columns[col][0], rotate, head_size)
            for i, item in enumerate(items, start=1):
                cells = doc_table.rows[i].cells
                self._set(cells[0], item)
                for j, col in enumerate(cols, start=1):
                    kind = columns[col][1]
                    self._set(cells[j], grid[item][col], bold=kind == "total",
                              italic=kind == "missing", align=self._value_align, size=size)
            if rotate:
                self._header_height(doc_table.rows[0], [h for h, _k, _c in sub])
            self._finish_table(doc_table, ["response"] * len(items))
            self._panel_gap(p_i, len(panels))

        stat_names = ", ".join(_stat_label(s_, base) for s_ in stats)
        parts = [f"Cells show {stat_names}"]
        if self.style["questions"]["show_base"] and base != "valid" and responses:
            value = responses[0].get(_N_FIELD.get(base, "eligible_n"), "")
            parts.append(f"Base: {_BASE_NAME.get(base, base)} n = {value}")
        if lay["hidden"]:
            parts.append(f"{lay['hidden']} response{'s' if lay['hidden'] != 1 else ''} hidden")
        self._caption(" \u00b7 ".join(parts), space_before=2)
        if missing_note:
            def one(item: str) -> str:
                row = missing.get(item)
                if row is None:
                    return f"{item} \u2014"
                pct = _stat_text(row, "pct", base, dec)
                return f"{item} {row.get('n', '')}" + (f" ({pct})" if pct else "")
            self._caption("Missing (shown the question, no answer): " +
                          "; ".join(one(item) for item in items), space_before=0)

    def _longest_word(self, headers: list[str], size: float) -> float:
        """Narrowest column (text only) the headers fit in without breaking a word."""
        return max((_text_width_in(w, size, True) for h in headers for w in h.split()), default=0.0)

    def _matrix_geometry(self, columns: list[tuple[str, str, str]], needs: list[float]):
        """Column widths for a matrix grid: (item_w, {col: width}, panels, rotate, header size).

        Scale-point columns all get one width, so a rating scale reads evenly;
        Total / Missing / Valid n are sized to what they hold. Horizontal
        headers wrap only between words, so a column must fit its longest word.
        In order, until it fits: the item column narrows (to
        item_min_width_inches), the header font shrinks (to header_min_size) --
        and only then does the grid turn its headers sideways ("auto") or split
        into parts ("never").
        """
        mx, t = self.style["matrices"], self.style["tables"]
        table_size = self.style["fonts"]["table_size"]
        min_size = min(mx["header_min_size"], table_size)
        pad = self._padding()[1] * 2 + 0.04
        cap = mx["max_column_width_inches"]
        scale = [j for j, (_h, kind, _k) in enumerate(columns) if kind == "response"]
        others = [j for j in range(len(columns)) if j not in scale]
        heads = [columns[j][0] for j in scale]
        scale_need = max((needs[j] for j in scale), default=0.0)
        fixed_item = t["attribute_column_width_inches"]
        item_min = fixed_item or min(mx["item_min_width_inches"], self.width / 3)
        item_pref = fixed_item or max(item_min, min(3.0, self.width * 0.35))

        def other_widths(horizontal: bool, header_size: float) -> dict[int, float]:
            return {j: max(needs[j], (self._longest_word([columns[j][0]], header_size) + pad)
                           if horizontal else 0.0) for j in others}

        def plan(horizontal: bool, header_size: float):
            """Widths when every scale column fits its longest word; None if not."""
            other_w = other_widths(horizontal, header_size)
            w_req = max(scale_need, self._longest_word(heads, header_size) + pad) if horizontal \
                else scale_need
            fixed = sum(other_w.values())
            n = max(len(scale), 1)
            item_w = max(item_min, min(item_pref, self.width - fixed - n * w_req))
            w = (self.width - item_w - fixed) / n
            if w + 1e-6 < w_req:
                return None
            w = min(w, max(cap, w_req))
            # Room left once the scale columns stop widening goes to the item column.
            if not fixed_item:
                item_w = max(item_w, min(3.0, self.width - fixed - n * w))
            return item_w, {**dict.fromkeys(scale, w), **other_w}

        mode = mx["header_rotation"]
        whole = [list(range(len(columns)))]
        if mode != "always":
            size = table_size
            while size >= min_size - 1e-6:
                fitted = plan(True, size)
                if fitted:
                    return fitted[0], fitted[1], whole, False, size
                size -= 0.5
        if mode != "never":
            fitted = plan(False, table_size)
            if fitted:
                return fitted[0], fitted[1], whole, True, table_size
        # Too many columns for one table even at the smallest setting: split into
        # parts, keeping one width for every scale column.
        horizontal = mode == "never"
        size = min_size if horizontal else table_size
        w_req = max(scale_need, self._longest_word(heads, size) + pad) if horizontal else scale_need
        other_w = other_widths(horizontal, size)
        col_need = [w_req if j in scale else other_w[j] for j in range(len(columns))]
        room = self.width - item_min
        panels = self._fit(col_need, room)
        widths = {j: w for cols in panels
                  for j, w in zip(cols, self._spread([col_need[j] for j in cols], room, cap), strict=True)}
        return item_min, widths, panels, not horizontal, size

    def _needed_width(self, texts: list[str], bold: bool = False) -> float:
        """Width a value column needs to show every one of ``texts`` unwrapped."""
        size = self.style["fonts"]["table_size"]
        widest = max((_text_width_in(line, size, bold) for t in texts for line in t.split("\n")),
                     default=0.0)
        return max(self.style["tables"]["min_group_column_width_inches"],
                   widest + self._padding()[1] * 2 + 0.04)

    @staticmethod
    def _fit(needs: list[float], room: float) -> list[list[int]]:
        """Group columns, in order, into parts whose widths each fit ``room``."""
        parts: list[list[int]] = [[]]
        used = 0.0
        for j, need in enumerate(needs):
            if parts[-1] and used + need > room + 1e-6:
                parts.append([])
                used = 0.0
            parts[-1].append(j)
            used += need
        return parts

    @staticmethod
    def _spread(needs: list[float], room: float, cap: float = 1.1) -> list[float]:
        """Share spare room equally among columns, none wider than ``cap``."""
        extra = max(0.0, room - sum(needs)) / max(len(needs), 1)
        return [max(n, min(cap, n + extra)) for n in needs]

    def _panels(self, n: int, room: float, min_w: float) -> list[range]:
        """Split n value columns into balanced panels that each fit ``room``."""
        per = max(1, int(room / min_w + 1e-6))  # tolerance: an exact fit is a fit
        if n <= per:
            return [range(n)]
        count = math.ceil(n / per)
        size = math.ceil(n / count)
        return [range(i, min(n, i + size)) for i in range(0, n, size)]

    def grouped_table(self, table) -> None:
        lay = grouped_layout(table.rows, table.presentation, table.overall_rows,
                             table.missing, table.overall_missing)
        stats, base, dec = lay["stats"], lay["report_base"], lay["decimals"]
        groups, axis, data = lay["groups"], lay["resp_axis"], lay["data"]
        t = self.style["tables"]
        size = self.style["fonts"]["table_size"]
        min_w = self._min_value_width(dec)

        def kind_of(attr: str, code: str) -> str:
            return "total" if attr == "__total__" else "missing" if code == _MISSING_CODE else "response"

        def resp_text(attr: str, code: str, label: str, as_header: bool) -> str:
            # A column header has no Code column beside it, so it carries the code.
            kind = kind_of(attr, code)
            text = {"total": "Total", "missing": _MISSING_LABEL}.get(kind, label)
            if as_header and kind == "response" and lay["show_code"] and code and code != label:
                text = f"{code} \u2014 {label}"
            if as_header and lay["has_attr"] and attr and kind != "total":
                text = f"{attr}: {text}"
            return text

        def cell_text(row: dict | None) -> str:
            # Stacked values; a blank one (Valid % on a Missing row) is dropped.
            if row is None:
                return "\u2014"
            values = [_stat_text(row, s, base, dec) for s in stats]
            return "\n".join(v for i, v in enumerate(values) if v != "" or i == 0)

        def value_cell(cell, row, kind):
            self._set(cell, cell_text(row), bold=kind == "total", italic=kind == "missing",
                      align=self._value_align, size=size)

        if lay["orientation"] == "rows":
            # Rows are groups; columns are response options.
            lead_w = max(1.3, min(2.2, self.width * 0.28))
            panels = self._panels(len(axis), self.width - lead_w, min_w)
            # One width and one header style for every part of a split table.
            col_w = max(min_w, (self.width - lead_w) / max(len(c) for c in panels))
            rotate = self._rotate(max(len(c) for c in panels), col_w,
                                  [resp_text(a, c, lbl, True) for a, c, lbl in axis])
            for p_i, cols in enumerate(panels):
                sub = [axis[j] for j in cols]
                headers = [resp_text(a, c, lbl, True) for a, c, lbl in sub]
                doc_table = self._new_table(len(groups) + 1, [lead_w] + [col_w] * len(sub))
                self._set(doc_table.rows[0].cells[0], "Group", bold=True)
                for j, text in enumerate(headers, start=1):
                    self._header_cell(doc_table.rows[0].cells[j], text, rotate)
                kinds = []
                for i, (gcode, glabel, gbase) in enumerate(groups, start=1):
                    kinds.append("total" if gcode == "__overall__" else "response")
                    cells = doc_table.rows[i].cells
                    self._set(cells[0], f"{glabel}\nn = {gbase}", bold=gcode == "__overall__")
                    for j, (a, c, _lbl) in enumerate(sub, start=1):
                        value_cell(cells[j], data(gcode, a, c), kind_of(a, c))
                if rotate:
                    self._header_height(doc_table.rows[0], headers)
                self._finish_table(doc_table, kinds)
                self._panel_gap(p_i, len(panels))
        else:
            # Rows are response options; columns are group levels (default).
            lead: list[float] = []
            attr_w = t["attribute_column_width_inches"] or max(1.1, min(1.8, self.width * 0.22))
            if lay["has_attr"]:
                lead.append(attr_w)
            if lay["show_code"]:
                lead.append(t["code_column_width_inches"])
            resp_min = 1.3
            panels = self._panels(len(groups), self.width - sum(lead) - resp_min, min_w)
            # One width and one header style for every part of a split table.
            widest = max(len(c) for c in panels)
            resp_w = max(resp_min, min(2.4, self.width - sum(lead) - widest * min_w))
            col_w = max(min_w, (self.width - sum(lead) - resp_w) / widest)
            rotate = self._rotate(widest, col_w, [glabel for _gc, glabel, _gb in groups])
            for p_i, cols in enumerate(panels):
                sub = [groups[j] for j in cols]
                headers = [f"{glabel}\nn = {gbase}" for _gc, glabel, gbase in sub]
                doc_table = self._new_table(len(axis) + 1, [*lead, resp_w] + [col_w] * len(sub))
                head = doc_table.rows[0].cells
                lead_heads = (["Attribute"] if lay["has_attr"] else []) + \
                    (["Code"] if lay["show_code"] else []) + ["Response"]
                for c, text in enumerate(lead_heads):
                    self._set(head[c], text, bold=True)
                for j, text in enumerate(headers, start=len(lead_heads)):
                    self._header_cell(head[j], text, rotate)
                kinds = []
                for i, (a, code, label) in enumerate(axis, start=1):
                    k = kind_of(a, code)
                    kinds.append(k)
                    cells = doc_table.rows[i].cells
                    c = 0
                    if lay["has_attr"]:
                        self._set(cells[c], "" if k == "total" else a)
                        c += 1
                    if lay["show_code"]:
                        self._set(cells[c], code if k == "response" and code != label else "")
                        c += 1
                    self._set(cells[c], resp_text(a, code, label, False), bold=k == "total",
                              italic=k == "missing")
                    c += 1
                    for j, (gcode, _gl, _gb) in enumerate(sub):
                        value_cell(cells[c + j], data(gcode, a, code), k)
                if rotate:
                    self._header_height(doc_table.rows[0], headers)
                self._finish_table(doc_table, kinds)
                self._panel_gap(p_i, len(panels))

        stat_names = ", ".join(_stat_label(s, base) for s in stats)
        base_name = _BASE_NAME.get(base, base)
        parts = [f"Cells show {stat_names}, within each group"] if stat_names else []
        if self.style["questions"]["show_base"]:
            parts.append(f"Base: {base_name} n of each group (shown as n = under its name)")
        if len(panels) > 1:
            parts.append(f"Shown in {len(panels)} parts to fit the page")
        if parts:
            self._caption(" \u00b7 ".join(parts), space_before=2)

    def _panel_gap(self, index: int, count: int) -> None:
        """A small gap between the parts of a split crosstab, kept with the next part."""
        if index < count - 1:
            p = self.doc.add_paragraph()
            _zero_spacing(p)
            p.paragraph_format.space_after = Pt(4)
            p.paragraph_format.keep_with_next = True
            self._run(p, "(continued)", self.style["fonts"]["caption_size"], italic=True, color="#555555")

    def _rotate(self, n_cols: int, col_w: float, headers: list[str]) -> bool:
        cx = self.style["crosstabs"]
        if cx["header_rotation"] == "always":
            return True
        if cx["header_rotation"] == "never":
            return False
        size = self.style["fonts"]["table_size"]
        too_wide = any(_text_width_in(w, size, True) > col_w - 0.1
                       for h in headers for w in h.split())
        return n_cols >= cx["rotate_at_columns"] or (n_cols >= 4 and too_wide)

    def _header_cell(self, cell, text: str, rotate: bool, size: float | None = None) -> None:
        if rotate:
            # Rotated text reads bottom to top; the base count stays on one line.
            # Left-aligned rotated text starts at the bottom, next to the data;
            # centring the cell puts it in the middle of its column.
            self._set(cell, text.replace("\n", "  "), bold=True, align=WD_ALIGN_PARAGRAPH.LEFT)
            _rotate(cell)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        else:
            self._set(cell, text, bold=True, align=self._value_align, size=size)

    def _header_height(self, row, headers: list[str]) -> None:
        size = self.style["fonts"]["table_size"]
        longest = max((_text_width_in(h.replace("\n", "  "), size, True) for h in headers), default=1.0)
        cap = self.style["crosstabs"]["header_max_height_inches"]
        wanted = max(0.6, longest + 0.25)
        row.height = Inches(min(cap, wanted))
        # Past the cap the height is fixed, so a very long label is clipped rather
        # than pushing the table down the page.
        row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY if wanted > cap else WD_ROW_HEIGHT_RULE.AT_LEAST

    def writeins(self, rows: list[dict[str, str]]) -> None:
        counts = writein_counts(rows)
        if not counts:
            return
        cap = self.style["write_ins"]["max_rows"]
        shown = counts if cap is None else counts[:cap]
        total = sum(n for _t, n in counts)
        self._caption(f"Write-in responses ({total})", keep_with_next=True, space_before=8)
        count_w = self.style["tables"]["stat_column_width_inches"]
        text_w = max(1.5, min(5.0, self.width - count_w))
        t = self._new_table(len(shown) + 1, [text_w, count_w])
        self._set(t.rows[0].cells[0], "Response", bold=True)
        self._set(t.rows[0].cells[1], "Count", bold=True, align=self._value_align)
        for i, (text, n) in enumerate(shown, start=1):
            self._set(t.rows[i].cells[0], text)
            self._set(t.rows[i].cells[1], str(n), align=self._value_align)
        self._finish_table(t, ["response"] * len(shown))
        if len(shown) < len(counts):
            self._caption(f"{len(counts) - len(shown)} more distinct write-ins not shown.")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _survey_title(run_dir: Path, manifest: dict) -> str | None:
    """The survey's name from the export stage's metadata, when it can be found."""
    candidates = [run_dir / "survey_metadata.json"]
    data_path = manifest.get("data_path")
    if data_path:
        # Manifests written on Windows carry backslashed paths.
        candidates.append(Path(str(data_path).replace("\\", "/")).parent / "survey_metadata.json")
    for path in candidates:
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = meta.get("SurveyName") if isinstance(meta, dict) else None
        if name:
            return str(name)
    return None


def _group_descriptions(run_dir: Path, manifest: dict) -> dict[str, str]:
    """{column: 'Q1.9 What uniform were you issued?'} for naming breakouts."""
    candidates = [run_dir / "column_map.json"]
    cmap_path = manifest.get("column_map_path")
    if cmap_path:
        candidates.append(Path(str(cmap_path).replace("\\", "/")))
    for path in candidates:
        try:
            cmap = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out = {}
        for m in cmap if isinstance(cmap, list) else []:
            text = _plain(m.get("question_text", ""))
            sub = _plain(m.get("sub_question_text", ""))
            tag = m.get("data_export_tag") or m.get("column", "")
            desc = f"{text} — {sub}" if sub else text
            out[m.get("column", "")] = f"{tag}: {desc}" if desc else tag
        return out
    return {}


def generate_word_report(
    run_dir: str | Path,
    out_path: str | Path | None = None,
    style: dict | None = None,
) -> Path:
    """Write report.docx for a run directory.

    The style is built-in defaults, then the frequency config's "word" block (as
    stamped into the manifest by the frequencies stage), then ``style``.
    """
    run = load_run(run_dir)
    resolved = resolve_style(run.manifest.get("word_style"), style)
    doc_cfg = resolved["document"]
    title = doc_cfg["title"] or _survey_title(run.run_dir, run.manifest) or "Survey frequency tables"
    subtitle = None
    if doc_cfg["subtitle"]:
        overall = next((t for t in run.tables if not t.is_grouped), None)
        total_n = overall.rows[0].get("total_n") if overall else None
        generated = datetime.now().astimezone().strftime("%d %B %Y").lstrip("0")
        bits = ["Frequency tables"]
        if total_n:
            bits.append(f"{total_n} respondents")
        bits.append(f"generated {generated}")
        subtitle = " · ".join(bits)

    writer = _Writer(resolved, title, subtitle)
    if doc_cfg["notes"]:
        writer.notes()
    groups = _group_descriptions(run.run_dir, run.manifest)
    show_writeins = resolved["write_ins"]["show"]
    for table in run.tables:
        if table.is_grouped:
            keys = [g for g in (table.rows[0].get("group_keys") or "").split(" | ") if g]
            by = " and ".join(groups.get(k, k) for k in keys)
            writer.question_heading(table, f"By {by}")
            writer.grouped_table(table)
        else:
            writer.question_heading(table, None)
            writer.flat_table(table)
            if show_writeins and table.qkey in run.writeins:
                writer.writeins(run.writeins[table.qkey])

    out = Path(out_path) if out_path else run.run_dir / "report.docx"
    writer.doc.save(out)
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render frequency tables to a Word document")
    p.add_argument("--run-dir", required=True, help="Directory containing frequency_tables/")
    p.add_argument("--out", help="Output .docx path (default: <run-dir>/report.docx)")
    p.add_argument("--style", help="JSON file of Word style settings (same shape as the config's \"word\" block)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    style = json.loads(Path(args.style).read_text(encoding="utf-8")) if args.style else None
    out = generate_word_report(args.run_dir, args.out, style)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
