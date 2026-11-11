"""Turn a valuation workbook into display-ready sheet data.

The「計算」sheet stores live Excel formulas. openpyxl does not evaluate them,
and these workbooks have no cached calculated values. We evaluate the same
formulas from input cells so the page is not blank. Other sheets already
store computed values. If a formula cannot be evaluated, we leave a clear
placeholder rather than inventing a number.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from pathlib import Path
from typing import Any, Optional

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

TOKEN_RE = re.compile(
    r"\s+|"
    r'("(?:[^"]|"")*")|'
    r"(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)|"
    r"(\$?[A-Z]{1,3}\$?\d+)|"
    r"([A-Za-z_][A-Za-z0-9_.]*)|"
    r"(<>|<=|>=|[<>=+\-*/^&(),:])"
)

EXPECTED_SHEETS = ["摘要", "計算", "情境假設", "增長假設對照", "投行估值比較", "來源", "資料缺口"]


def col_letters_to_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def a1_to_rc(addr: str) -> tuple[int, int]:
    m = re.fullmatch(r"\$?([A-Z]{1,3})\$?(\d+)", addr.upper())
    if not m:
        raise ValueError(f"不是儲存格位址：{addr}")
    return int(m.group(2)), col_letters_to_index(m.group(1))


class FormulaError(Exception):
    def __init__(self, code: str = "#VALUE!"):
        self.code = code
        super().__init__(code)


def _is_array(v: Any) -> bool:
    return isinstance(v, list)


def _as_2d(v: Any) -> list:
    if not _is_array(v):
        return [[v]]
    if v and _is_array(v[0]):
        return v
    return [v]


def _flatten(v: Any) -> list:
    if not _is_array(v):
        return [v]
    out = []
    for row in v:
        if _is_array(row):
            out.extend(row)
        else:
            out.append(row)
    return out


def _to_num(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            raise FormulaError("#NUM!")
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("#"):
            raise FormulaError(s)
        if s.endswith("%"):
            try:
                return float(s[:-1]) / 100.0
            except ValueError:
                raise FormulaError("#VALUE!")
        try:
            return float(s)
        except ValueError:
            raise FormulaError("#VALUE!")
    raise FormulaError("#VALUE!")


def _elementwise(op, a, b):
    a_arr, b_arr = _is_array(a), _is_array(b)
    if not a_arr and not b_arr:
        return op(a, b)
    A, B = _as_2d(a), _as_2d(b)
    if not a_arr:
        A = [[a] * len(B[0])] * len(B)
    if not b_arr:
        B = [[b] * len(A[0])] * len(A)
    if len(A) != len(B) or len(A[0]) != len(B[0]):
        # try broadcast one-row / one-col if sizes differ; otherwise error
        raise FormulaError("#VALUE!")
    return [[op(A[i][j], B[i][j]) for j in range(len(A[0]))] for i in range(len(A))]


def _apply_unary(op, a):
    if not _is_array(a):
        return op(a)
    A = _as_2d(a)
    return [[op(x) for x in row] for row in A]


class FormulaEngine:
    """Evaluate the subset of Excel formulas used by the calc sheet."""

    def __init__(self):
        self.values: dict[tuple[int, int], Any] = {}
        self.formulas: dict[tuple[int, int], str] = {}
        self.cache: dict[tuple[int, int], Any] = {}
        self.visiting: set[tuple[int, int]] = set()
        self.failed: dict[tuple[int, int], str] = {}

    def set_value(self, row: int, col: int, value: Any):
        self.values[(row, col)] = value

    def set_formula(self, row: int, col: int, formula: str):
        self.formulas[(row, col)] = formula

    def get(self, row: int, col: int) -> Any:
        key = (row, col)
        if key in self.cache:
            return self.cache[key]
        if key in self.formulas:
            if key in self.visiting:
                self.failed[key] = "#CYCLE!"
                return FormulaError("#CYCLE!").code
            self.visiting.add(key)
            try:
                val = self.evaluate(self.formulas[key])
            except FormulaError as e:
                val = e.code
                self.failed[key] = e.code
            except Exception as e:
                val = "#VALUE!"
                self.failed[key] = f"{type(e).__name__}: {e}"
            finally:
                self.visiting.discard(key)
            self.cache[key] = val
            return val
        return self.values.get(key)

    def evaluate_all(self) -> dict:
        for key in list(self.formulas):
            self.get(*key)
        return {
            "formulas": len(self.formulas),
            "evaluated": len(self.formulas) - len(self.failed),
            "failed": len(self.failed),
            "failures": {f"{get_column_letter(c)}{r}": msg for (r, c), msg in list(self.failed.items())[:20]},
        }

    def evaluate(self, formula: str) -> Any:
        src = formula[1:] if formula.startswith("=") else formula
        tokens = self._tokenize(src)
        self._toks = tokens
        self._i = 0
        if not tokens:
            return None
        val = self._parse_cmp()
        if self._i < len(self._toks):
            raise FormulaError("#VALUE!")
        return val

    def _tokenize(self, src: str) -> list[tuple[str, str]]:
        out = []
        pos = 0
        for m in TOKEN_RE.finditer(src):
            if m.start() != pos:
                raise FormulaError("#VALUE!")
            pos = m.end()
            if m.group(0).strip() == "" and not any(m.groups()):
                continue
            if m.group(1) is not None:
                raw = m.group(1)
                out.append(("STR", raw[1:-1].replace('""', '"')))
            elif m.group(2) is not None:
                out.append(("NUM", m.group(2)))
            elif m.group(3) is not None:
                out.append(("CELL", m.group(3).upper()))
            elif m.group(4) is not None:
                out.append(("NAME", m.group(4).upper()))
            elif m.group(5) is not None:
                out.append(("OP", m.group(5)))
        if pos != len(src):
            raise FormulaError("#VALUE!")
        return out

    def _peek(self) -> Optional[tuple[str, str]]:
        if self._i < len(self._toks):
            return self._toks[self._i]
        return None

    def _accept(self, typ: str, val: Optional[str] = None) -> Optional[tuple[str, str]]:
        t = self._peek()
        if t and t[0] == typ and (val is None or t[1] == val):
            self._i += 1
            return t
        return None

    def _expect(self, typ: str, val: Optional[str] = None) -> tuple[str, str]:
        t = self._accept(typ, val)
        if not t:
            raise FormulaError("#VALUE!")
        return t

    def _parse_cmp(self):
        left = self._parse_add()
        while True:
            t = self._peek()
            if not t or t[0] != "OP" or t[1] not in ("=", "<>", "<", ">", "<=", ">="):
                return left
            op = self._expect("OP")[1]
            right = self._parse_add()
            left = self._cmp(op, left, right)

    def _parse_add(self):
        left = self._parse_mul()
        while True:
            t = self._peek()
            if not t or t[0] != "OP" or t[1] not in ("+", "-"):
                return left
            op = self._expect("OP")[1]
            right = self._parse_mul()
            if op == "+":
                left = _elementwise(self._add, left, right)
            else:
                left = _elementwise(self._sub, left, right)

    def _parse_mul(self):
        left = self._parse_pow()
        while True:
            t = self._peek()
            if not t or t[0] != "OP" or t[1] not in ("*", "/"):
                return left
            op = self._expect("OP")[1]
            right = self._parse_pow()
            if op == "*":
                left = _elementwise(self._mul, left, right)
            else:
                left = _elementwise(self._div, left, right)

    def _parse_pow(self):
        left = self._parse_unary()
        t = self._peek()
        if t and t[0] == "OP" and t[1] == "^":
            self._expect("OP", "^")
            right = self._parse_pow()  # right-associative
            return _elementwise(self._pow, left, right)
        return left

    def _parse_unary(self):
        t = self._peek()
        if t and t[0] == "OP" and t[1] == "-":
            self._expect("OP", "-")
            return _apply_unary(lambda x: -_to_num(x), self._parse_unary())
        if t and t[0] == "OP" and t[1] == "+":
            self._expect("OP", "+")
            return self._parse_unary()
        return self._parse_primary()

    def _parse_primary(self):
        t = self._peek()
        if not t:
            raise FormulaError("#VALUE!")
        if t[0] == "NUM":
            self._i += 1
            return float(t[1])
        if t[0] == "STR":
            self._i += 1
            return t[1]
        if t[0] == "CELL":
            return self._parse_cell_or_range()
        if t[0] == "NAME":
            name = self._expect("NAME")[1]
            if self._accept("OP", "("):
                args = []
                if not (self._peek() and self._peek() == ("OP", ")")):
                    args.append(self._parse_cmp())
                    while self._accept("OP", ","):
                        args.append(self._parse_cmp())
                self._expect("OP", ")")
                return self._call(name, args)
            raise FormulaError("#NAME?")
        if t[0] == "OP" and t[1] == "(":
            self._expect("OP", "(")
            val = self._parse_cmp()
            self._expect("OP", ")")
            return val
        raise FormulaError("#VALUE!")

    def _parse_cell_or_range(self):
        a = self._expect("CELL")[1]
        if self._accept("OP", ":"):
            b = self._expect("CELL")[1]
            return self._range(a, b)
        r, c = a1_to_rc(a)
        return self.get(r, c)

    def _range(self, a: str, b: str) -> list:
        r1, c1 = a1_to_rc(a)
        r2, c2 = a1_to_rc(b)
        if r2 < r1:
            r1, r2 = r2, r1
        if c2 < c1:
            c1, c2 = c2, c1
        return [[self.get(r, c) for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)]

    def _call(self, name: str, args: list):
        if name == "IF":
            if len(args) < 2:
                raise FormulaError("#VALUE!")
            cond = args[0]
            if _is_array(cond):
                raise FormulaError("#VALUE!")
            truthy = self._truthy(cond)
            if truthy:
                return args[1]
            return args[2] if len(args) > 2 else False
        if name == "ABS":
            if len(args) != 1:
                raise FormulaError("#VALUE!")
            return _apply_unary(lambda x: abs(_to_num(x)), args[0])
        if name == "SUM":
            total = 0.0
            for a in args:
                for x in _flatten(a):
                    if x is None or x == "":
                        continue
                    total += _to_num(x)
            return total
        if name == "SUMPRODUCT":
            if not args:
                return 0.0
            if len(args) == 1:
                return sum(_to_num(x) for x in _flatten(args[0]) if x not in (None, ""))
            arrays = [_flatten(a) for a in args]
            n = min(len(a) for a in arrays)
            total = 0.0
            for i in range(n):
                p = 1.0
                for arr in arrays:
                    p *= _to_num(arr[i])
                total += p
            return total
        raise FormulaError("#NAME?")

    def _truthy(self, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v != 0
        if v is None or v == "":
            return False
        return bool(v)

    def _cmp(self, op: str, a, b):
        # Excel comparison: try numeric, else string
        def one(x, y):
            try:
                xn, yn = _to_num(x), _to_num(y)
                if op == "=":
                    return xn == yn
                if op == "<>":
                    return xn != yn
                if op == "<":
                    return xn < yn
                if op == ">":
                    return xn > yn
                if op == "<=":
                    return xn <= yn
                if op == ">=":
                    return xn >= yn
            except (FormulaError, ValueError, TypeError):
                xs, ys = str(x if x is not None else ""), str(y if y is not None else "")
                if op == "=":
                    return xs == ys
                if op == "<>":
                    return xs != ys
                if op == "<":
                    return xs < ys
                if op == ">":
                    return xs > ys
                if op == "<=":
                    return xs <= ys
                if op == ">=":
                    return xs >= ys
            return False

        if _is_array(a) or _is_array(b):
            return _elementwise(one, a, b)
        return one(a, b)

    def _add(self, a, b):
        return _to_num(a) + _to_num(b)

    def _sub(self, a, b):
        return _to_num(a) - _to_num(b)

    def _mul(self, a, b):
        return _to_num(a) * _to_num(b)

    def _div(self, a, b):
        den = _to_num(b)
        if den == 0:
            raise FormulaError("#DIV/0!")
        return _to_num(a) / den

    def _pow(self, a, b):
        try:
            return _to_num(a) ** _to_num(b)
        except Exception:
            raise FormulaError("#NUM!")


def format_excel_value(value: Any, num_fmt: Optional[str] = None) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, dt.datetime):
        if value.time() == dt.time(0, 0):
            return value.date().isoformat()
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, dt.date):
        return value.isoformat()
    if not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return str(value)
    fmt = (num_fmt or "").strip()
    if fmt and "x" in fmt.lower() and "%" not in fmt:
        return f"{value:.2f}×"
    if "%" in fmt:
        decimals = 2
        if "0.0000%" in fmt:
            decimals = 4
        elif "0.000%" in fmt:
            decimals = 3
        return f"{value * 100:.{decimals}f}%"
    # decimal places from the positive part of the format
    pos = fmt.split(";")[0] if fmt else ""
    if re.search(r"0\.0+", pos):
        decimals = len(re.search(r"0\.(0+)", pos).group(1))
        if "#" in pos or "," in pos:
            return f"{value:,.{decimals}f}"
        return f"{value:.{decimals}f}"
    if re.search(r"#,##0|#0|0$", pos) or "#,##0" in pos:
        if abs(value - round(value)) < 1e-9:
            return f"{int(round(value)):,}"
        return f"{value:,.2f}"
    if isinstance(value, int) or (isinstance(value, float) and abs(value - round(value)) < 1e-12 and abs(value) >= 1):
        # large integers like shares; keep decimals for small magnitudes that are clearly not ints
        if abs(value) >= 1 and abs(value - round(value)) < 1e-12:
            return f"{int(round(value)):,}"
    if abs(value) >= 1e6 or (abs(value) >= 1000 and abs(value - round(value)) < 1e-9):
        return f"{value:,.2f}" if abs(value - round(value)) >= 1e-9 else f"{int(round(value)):,}"
    if abs(value) < 1e-4 and value != 0:
        return f"{value:.6g}"
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text


def _color_hex(color) -> Optional[str]:
    if color is None:
        return None
    rgb = getattr(color, "rgb", None)
    if rgb is None:
        return None
    s = str(rgb)
    if s in ("00000000", "None", "Values must be of type"):
        return None
    if len(s) == 8 and re.fullmatch(r"[0-9A-Fa-f]{8}", s):
        if s[2:] == "000000" and s[:2] == "00":
            return None
        return "#" + s[2:]
    if len(s) == 6 and re.fullmatch(r"[0-9A-Fa-f]{6}", s):
        return "#" + s
    return None


def _fill_hex(cell) -> Optional[str]:
    fill = getattr(cell, "fill", None)
    if not fill or fill.fill_type not in ("solid", "fgColor"):
        # still try fgColor
        pass
    if not fill:
        return None
    color = getattr(fill, "fgColor", None) or getattr(fill, "start_color", None)
    hx = _color_hex(color)
    if hx and hx.upper() in ("#000000", "#FFFFFF") and (fill.fill_type in (None, "none")):
        return None
    if fill.fill_type in (None, "none") and not hx:
        return None
    if fill.fill_type in (None, "none"):
        return None
    return hx


def _json_safe(v: Any):
    if v is None or isinstance(v, (str, int, bool)):
        return v
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return str(v)
        return v
    if isinstance(v, (dt.datetime, dt.date)):
        return format_excel_value(v)
    return str(v)


def render_workbook(xlsx_path: str | Path, json_path: Optional[str | Path] = None) -> dict:
    """Load a workbook and return a JSON-serialisable representation of every sheet."""
    xlsx_path = Path(xlsx_path)
    if not xlsx_path.is_file():
        raise FileNotFoundError(f"找不到 Excel：{xlsx_path}")

    wb = load_workbook(xlsx_path, data_only=False)
    # also try cached values (usually empty for these files)
    try:
        wb_cached = load_workbook(xlsx_path, data_only=True)
    except Exception:
        wb_cached = None

    sheets = []
    eval_stats_all = {}
    for name in wb.sheetnames:
        ws = wb[name]
        cached = wb_cached[name] if wb_cached and name in wb_cached.sheetnames else None
        engine = FormulaEngine()
        max_row = ws.max_row or 1
        max_col = ws.max_column or 1
        raw = {}
        formulas = {}
        for row in ws.iter_rows(min_row=1, max_row=max_row, min_col=1, max_col=max_col):
            for cell in row:
                v = cell.value
                if v is None:
                    continue
                if isinstance(v, str) and v.startswith("="):
                    formulas[(cell.row, cell.column)] = v
                    engine.set_formula(cell.row, cell.column, v)
                    if cached is not None:
                        cv = cached.cell(cell.row, cell.column).value
                        if cv is not None and not (isinstance(cv, str) and cv.startswith("=")):
                            engine.cache[(cell.row, cell.column)] = cv
                else:
                    raw[(cell.row, cell.column)] = v
                    engine.set_value(cell.row, cell.column, v)
        stats = engine.evaluate_all() if formulas else {"formulas": 0, "evaluated": 0, "failed": 0, "failures": {}}
        eval_stats_all[name] = stats

        merges = {}
        skip = set()
        for mr in ws.merged_cells.ranges:
            rs, cs = mr.max_row - mr.min_row + 1, mr.max_col - mr.min_col + 1
            merges[(mr.min_row, mr.min_col)] = (rs, cs)
            for r in range(mr.min_row, mr.max_row + 1):
                for c in range(mr.min_col, mr.max_col + 1):
                    if (r, c) != (mr.min_row, mr.min_col):
                        skip.add((r, c))

        col_widths = []
        for c in range(1, max_col + 1):
            letter = get_column_letter(c)
            dim = ws.column_dimensions.get(letter)
            w = dim.width if dim and dim.width else 12
            col_widths.append(round(float(w), 1))

        rows_out = []
        for r in range(1, max_row + 1):
            row_out = []
            for c in range(1, max_col + 1):
                if (r, c) in skip:
                    continue
                cell = ws.cell(r, c)
                formula = formulas.get((r, c))
                if formula:
                    value = engine.get(r, c)
                else:
                    value = raw.get((r, c))
                    if value is None and cached is not None:
                        value = cached.cell(r, c).value
                display = format_excel_value(value, cell.number_format)
                # error codes stay visible
                typ = "s"
                if value is None or value == "":
                    typ = "e" if display == "" else "s"
                    if value is None:
                        typ = "b"
                elif isinstance(value, str) and value.startswith("#"):
                    typ = "err"
                elif isinstance(value, bool):
                    typ = "b"
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    typ = "n"
                item = {
                    "d": display,
                    "t": typ,
                    "v": _json_safe(value) if typ != "err" else value,
                }
                if formula:
                    item["f"] = formula
                rs_cs = merges.get((r, c))
                if rs_cs:
                    if rs_cs[0] > 1:
                        item["rs"] = rs_cs[0]
                    if rs_cs[1] > 1:
                        item["cs"] = rs_cs[1]
                bg = _fill_hex(cell)
                if bg:
                    item["bg"] = bg
                fg = _color_hex(getattr(cell.font, "color", None)) if cell.font else None
                if fg:
                    item["fg"] = fg
                if cell.font and cell.font.bold:
                    item["b"] = True
                if cell.font and cell.font.italic:
                    item["i"] = True
                align = (cell.alignment.horizontal if cell.alignment else None) or (
                    "right" if typ == "n" else "left"
                )
                if align and align != "general":
                    item["a"] = "right" if typ == "n" and align == "general" else align
                elif typ == "n":
                    item["a"] = "right"
                if cell.alignment and cell.alignment.wrap_text:
                    item["w"] = True
                href = None
                if cell.hyperlink and getattr(cell.hyperlink, "target", None):
                    href = cell.hyperlink.target
                elif isinstance(value, str) and value.startswith(("http://", "https://")):
                    href = value
                if href:
                    item["h"] = href
                row_out.append(item)
            rows_out.append(row_out)
        sheets.append({
            "name": name,
            "rows": rows_out,
            "max_row": max_row,
            "max_col": max_col,
            "col_widths": col_widths,
            "eval": stats,
        })

    # If the calc sheet looks empty of numbers (shouldn't happen), note it.
    warnings = []
    names = [s["name"] for s in sheets]
    for expected in EXPECTED_SHEETS:
        if expected not in names:
            warnings.append(f"工作簿缺少分頁：{expected}")
    for s in sheets:
        nonempty = 0
        for row in s["rows"]:
            for cell in row:
                if cell.get("d"):
                    nonempty += 1
        s["nonempty"] = nonempty
        if nonempty == 0:
            warnings.append(f"分頁「{s['name']}」沒有可顯示內容")
        if s["name"] == "計算" and s["eval"].get("formulas") and s["eval"].get("failed"):
            warnings.append(
                f"分頁「計算」有 {s['eval']['failed']} 個公式未能求出數值（已標示錯誤碼，未捏造數字）"
            )

    return {
        "path": str(xlsx_path),
        "sheets": sheets,
        "eval": eval_stats_all,
        "warnings": warnings,
    }


def sheet_text_blob(sheet: dict) -> str:
    parts = []
    for row in sheet.get("rows") or []:
        for cell in row:
            d = cell.get("d")
            if d:
                parts.append(str(d))
    return "\n".join(parts)
