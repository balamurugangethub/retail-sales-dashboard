"""Turns a messy sales file into clean rows for the `sales` table.

The steps are deliberately plain so a shop owner can follow the report:
  1. read CSV / TSV / Excel,
  2. guess which column is which field (``suggest_mapping``),
  3. clean every row and count each fix (``clean_rows``).
Nothing here touches the database.
"""
import csv
import io
import re
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

# Target fields. Only date and revenue (or price x units) are truly required;
# the others get a sensible default so a single shop's till export still works.
FIELDS = ["date", "store", "category", "product", "orders", "units", "revenue", "cost", "price"]
FIELD_LABELS = {
    "date": "Date", "store": "Store / branch", "category": "Category", "product": "Product",
    "orders": "Orders", "units": "Units sold", "revenue": "Sales amount", "cost": "Cost",
    "price": "Unit price (used if there is no sales amount)",
}
DEFAULTS = {"store": "Main store", "category": "Uncategorized", "product": "All products"}

SYNONYMS = {
    "date": ["date", "day", "sale date", "order date", "transaction date", "invoice date", "txn date",
             "bill date", "posting date", "datetime", "timestamp", "period"],
    "store": ["store", "shop", "branch", "outlet", "location", "store name", "site", "showroom",
              "store id", "branch name", "region"],
    "category": ["category", "department", "dept", "product category", "group", "segment",
                 "class", "family", "type", "section"],
    "product": ["product", "item", "product name", "item name", "sku", "description", "article",
                "goods", "product description", "item description"],
    "orders": ["orders", "order count", "transactions", "txns", "bills", "invoices", "tickets",
               "receipts", "no of orders", "number of orders", "baskets"],
    "units": ["units", "qty", "quantity", "units sold", "pieces", "pcs", "volume", "count", "items sold"],
    "revenue": ["revenue", "sales", "amount", "total", "net sales", "gross sales", "sales amount",
                "turnover", "value", "total amount", "net amount", "line total", "sale value", "income"],
    "cost": ["cost", "cogs", "cost of goods", "purchase cost", "total cost", "cost amount",
             "cost price", "landed cost"],
    "price": ["price", "unit price", "selling price", "rate", "mrp", "price each"],
}

MAX_ROWS = 200_000
_CURRENCY = re.compile(r"[\s$€£₹¥,]|rs\.?|inr|usd|eur|gbp", re.IGNORECASE)
_DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%m-%d-%Y",
                 "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%d-%b-%Y",
                 "%d-%b-%y", "%d/%m/%y", "%m/%d/%y", "%Y%m%d"]


class CleaningError(ValueError):
    """The file cannot be read at all (wrong format, no header, too big)."""


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


# ---- reading -----------------------------------------------------------------
def read_table(filename, data):
    """Returns (header, rows) where rows are lists of strings."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        return _read_excel(data)
    if name.endswith(".xls"):
        raise CleaningError("Old .xls files are not supported. In Excel choose Save As > .xlsx or .csv.")
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = "\t" if name.endswith(".tsv") else ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    return _split_header(rows)


def _read_excel(data):
    try:
        import openpyxl  # pylint: disable=import-outside-toplevel
    except ImportError as e:
        raise CleaningError("Excel support needs the openpyxl package; save the sheet as CSV instead.") from e
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # openpyxl raises many types for corrupt files
        raise CleaningError("Could not open the Excel file: " + str(e)) from e
    ws = wb.worksheets[0]
    rows = []
    for raw in ws.iter_rows(values_only=True):
        cells = ["" if v is None else (v.isoformat()[:10] if isinstance(v, (date, datetime)) else str(v))
                 for v in raw]
        if any(c.strip() for c in cells):
            rows.append(cells)
        if len(rows) > MAX_ROWS + 1:
            break
    return _split_header(rows)


def _split_header(rows):
    if not rows:
        raise CleaningError("The file is empty.")
    # Skip title lines above the real header: take the first row that has 2+ non-empty text cells.
    for i, r in enumerate(rows[:10]):
        if sum(1 for c in r if c.strip() and not _looks_numeric(c)) >= 2:
            header, body = [c.strip() for c in r], rows[i + 1:]
            break
    else:
        header, body = [c.strip() for c in rows[0]], rows[1:]
    if len(body) > MAX_ROWS:
        raise CleaningError(f"The file has more than {MAX_ROWS:,} rows; split it into smaller files.")
    width = len(header)
    header = [h or f"Column {i + 1}" for i, h in enumerate(header)]
    body = [(r + [""] * width)[:width] for r in body]
    return header, body


def _looks_numeric(text):
    return parse_number(text) is not None


# ---- mapping -----------------------------------------------------------------
def suggest_mapping(header):
    """Returns {field: column name or None}, each column used at most once."""
    normed = [_norm(h) for h in header]
    scored = []
    for field, words in SYNONYMS.items():
        for col, h in enumerate(normed):
            best = 0
            for rank, w in enumerate(words):
                if h == w:
                    best = max(best, 100 - rank)
                elif re.search(r"\b" + re.escape(w) + r"\b", h):
                    best = max(best, 50 - rank)
            if best:
                scored.append((best, field, col))
    scored.sort(reverse=True)
    mapping, used = {f: None for f in FIELDS}, set()
    for _score, field, col in scored:
        if mapping[field] is None and col not in used:
            mapping[field] = header[col]
            used.add(col)
    return mapping


# ---- value parsing -------------------------------------------------------------
def parse_number(text):
    """'$1,234.50' -> Decimal('1234.50'); '(12)' -> -12; '' or junk -> None."""
    s = str(text or "").strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = _CURRENCY.sub("", s.strip("()"))
    if s.endswith("-"):
        neg, s = True, s[:-1]
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    if not v.is_finite():
        return None
    return -v if neg else v


def detect_day_first(values):
    """True if dates like 03/04/2026 mean 3 April (day first). Decided by the whole column."""
    day_first = month_first = 0
    for v in values[:5000]:
        m = re.match(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", str(v))
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > 12 >= b:
                day_first += 1
            elif b > 12 >= a:
                month_first += 1
    return day_first >= month_first


def parse_date(text, day_first=True):
    s = str(text or "").strip()
    if not s:
        return None
    s = re.sub(r"[T ]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", "", s).strip()
    s = s.replace(",", " ")
    s = re.sub(r"\s+", " ", s)
    formats = _DATE_FORMATS
    if not day_first:
        formats = sorted(formats, key=lambda f: 0 if f.startswith("%m") else 1)
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # Excel serial day number (e.g. 46023)
    n = parse_number(s)
    if n is not None and 20000 < n < 80000 and n == int(n):
        return date(1899, 12, 30) + timedelta(days=int(n))
    return None


def preferred_spelling(counter):
    """Most used spelling; ties go to Mixed Case over 'lower' or 'UPPER'."""
    return max(counter.items(), key=lambda kv: (kv[1], kv[0] != kv[0].lower() and kv[0] != kv[0].upper(),
                                                kv[0] != kv[0].lower()))[0]


def _canonical_text(values):
    """Groups spelling variants ('store a', ' Store  A ') and picks the preferred spelling."""
    groups = {}
    for v in values:
        key = _norm(v)
        groups.setdefault(key, Counter())[v] += 1
    return {key: preferred_spelling(c) for key, c in groups.items()}


# ---- cleaning ----------------------------------------------------------------
def clean_rows(header, rows, mapping, drop_duplicates=True):
    """Cleans rows using mapping {field: column}. Identical rows are dropped unless
    drop_duplicates is False (a till export can legitimately repeat a line). Returns:

    rows      list of (day, store, category, product, orders, units, revenue_cents, cost_cents)
    fixes     {description: count} of automatic corrections
    problems  [(line_number, reason)] rows that could not be used
    warnings  [str] file-level notes
    """
    col = {}
    for field, name in (mapping or {}).items():
        if field in FIELDS and name:
            if name not in header:
                raise CleaningError(f"Column '{name}' is not in the file.")
            col[field] = header.index(name)
    if "date" not in col:
        raise CleaningError("Pick which column holds the date.")
    if "revenue" not in col and not ("price" in col and "units" in col):
        raise CleaningError("Pick the sales amount column (or both unit price and units).")

    fixes, problems, warnings = Counter(), [], []
    for field in ("store", "category", "product"):
        if field not in col:
            warnings.append(f"No {field} column: every row is set to '{DEFAULTS[field]}'.")
    if "cost" not in col:
        warnings.append("No cost column: cost is 0, so profit will equal sales.")
    if "orders" not in col:
        warnings.append("No orders column: each row counts as one order.")

    def cell(r, field):
        return r[col[field]].strip() if field in col else ""

    day_first = detect_day_first([cell(r, "date") for r in rows])
    text_vals = {f: _canonical_text([re.sub(r"\s+", " ", cell(r, f)) for r in rows if cell(r, f)])
                 for f in ("store", "category", "product") if f in col}

    out, seen = [], set()
    for line, r in enumerate(rows, start=2):
        row_fixes = Counter()
        try:
            raw_day = cell(r, "date")
            d = parse_date(raw_day, day_first)
            if d is None:
                raise ValueError(f"unreadable date '{raw_day}'" if raw_day else "date is empty")
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", raw_day):
                row_fixes["Dates converted to YYYY-MM-DD"] += 1

            texts = []
            for f in ("store", "category", "product"):
                raw = cell(r, f)
                if not raw:
                    if f in col:
                        row_fixes[f"Empty {f} filled with '{DEFAULTS[f]}'"] += 1
                    texts.append(DEFAULTS[f])
                    continue
                spaced = re.sub(r"\s+", " ", raw)
                canon = text_vals[f][_norm(spaced)]
                if canon != raw:
                    row_fixes[f"{f.capitalize()} names tidied (spaces / spelling variants merged)"] += 1
                texts.append(canon)

            nums = {}
            for f in ("orders", "units", "revenue", "cost", "price"):
                raw = cell(r, f)
                v = parse_number(raw)
                if raw and v is None:
                    raise ValueError(f"{f} '{raw}' is not a number")
                if v is not None and str(v) != raw:
                    row_fixes["Numbers cleaned (currency symbols, commas)"] += 1
                if v is not None and v < 0:
                    raise ValueError(f"{f} is negative ({raw}); refunds are not supported yet")
                nums[f] = v

            units = nums["units"]
            orders = nums["orders"]
            if orders is None:
                orders = Decimal(1)
            if units is None:
                units = orders
                if "units" in col:
                    row_fixes["Empty units set to the order count"] += 1
            revenue = nums["revenue"]
            if revenue is None and nums["price"] is not None and nums["units"] is not None:
                revenue = nums["price"] * nums["units"]
                row_fixes["Sales amount calculated from price x units"] += 1
            if revenue is None:
                raise ValueError("sales amount is empty")
            cost = nums["cost"] if nums["cost"] is not None else Decimal(0)
            if "cost" in col and nums["cost"] is None:
                row_fixes["Empty cost set to 0"] += 1

            row = (d.isoformat(), *texts, int(orders), int(units),
                   int((revenue * 100).to_integral_value()), int((cost * 100).to_integral_value()))
            if drop_duplicates and row in seen:
                fixes["Exact duplicate rows removed"] += 1
                continue
            seen.add(row)
            out.append(row)
            fixes.update(row_fixes)
        except ValueError as e:
            problems.append((line, str(e)))
    return {"rows": out, "fixes": dict(fixes), "problems": problems, "warnings": warnings,
            "day_first": day_first}
