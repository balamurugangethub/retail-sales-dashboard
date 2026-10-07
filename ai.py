"""AI assistant: IBM watsonx.ai when credentials are set, built-in rules otherwise.

Set these environment variables to use watsonx.ai (IBM Granite models):
  WATSONX_API_KEY     IBM Cloud API key
  WATSONX_PROJECT_ID  watsonx.ai project id
  WATSONX_URL         region endpoint, default https://us-south.ml.cloud.ibm.com
  WATSONX_MODEL_ID    default ibm/granite-3-8b-instruct

The assistant never changes data itself. It returns a reply plus a list of
*actions* (add a chart, rename a store, ...) that the browser shows and the
user confirms; data-changing actions go through the normal write endpoints.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from queries import CHART_TYPES, DIMENSIONS, MEASURES

IAM_URL = "https://iam.cloud.ibm.com/identity/token"
DEFAULT_URL = "https://us-south.ml.cloud.ibm.com"
DEFAULT_MODEL = "ibm/granite-3-8b-instruct"
CHAT_VERSION = "2024-10-08"
TEXT_FIELDS = ["store", "category", "product"]
MAX_MESSAGE = 1000


# ---- watsonx.ai client -----------------------------------------------------------
class WatsonxError(RuntimeError):
    pass


class WatsonxClient:  # pylint: disable=too-many-instance-attributes
    """Minimal watsonx.ai chat client using only the standard library."""

    def __init__(self, api_key, project_id, url=DEFAULT_URL, model_id=DEFAULT_MODEL, timeout=25):
        self.api_key, self.project_id = api_key, project_id
        self.url, self.model_id, self.timeout = url.rstrip("/"), model_id, timeout
        self._token, self._expires = None, 0.0
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        key, project = env.get("WATSONX_API_KEY"), env.get("WATSONX_PROJECT_ID")
        if not (key and project):
            return None
        return cls(key, project, env.get("WATSONX_URL") or DEFAULT_URL,
                   env.get("WATSONX_MODEL_ID") or DEFAULT_MODEL)

    def _post(self, url, data, headers):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise WatsonxError(f"watsonx.ai returned HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise WatsonxError(f"Could not reach watsonx.ai: {e}") from e

    def token(self):
        with self._lock:
            if self._token and time.time() < self._expires - 60:
                return self._token
            body = urllib.parse.urlencode({"grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                                           "apikey": self.api_key}).encode()
            out = self._post(IAM_URL, body, {"Content-Type": "application/x-www-form-urlencoded",
                                             "Accept": "application/json"})
            self._token = out["access_token"]
            self._expires = time.time() + float(out.get("expires_in", 3600))
            return self._token

    def chat(self, system, user, max_tokens=700):
        payload = {
            "model_id": self.model_id, "project_id": self.project_id,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": 0,
        }
        out = self._post(f"{self.url}/ml/v1/text/chat?version={CHAT_VERSION}", json.dumps(payload).encode(),
                         {"Content-Type": "application/json", "Accept": "application/json",
                          "Authorization": "Bearer " + self.token()})
        try:
            return out["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise WatsonxError("Unexpected watsonx.ai response") from e


# ---- formatting helpers ------------------------------------------------------------
def fmt_value(measure, v):
    f = MEASURES[measure]["format"]
    if f == "money":
        return f"${v:,.2f}" if abs(v) < 100 else f"${v:,.0f}"
    if f == "pct":
        return f"{v:.1f}%"
    return f"{v:,.0f}"


def widget_title(w):
    m = " & ".join(MEASURES[x]["label"] for x in w["measures"])
    t = m
    if w.get("dims"):
        t = f"{m} by " + " and ".join(DIMENSIONS[d]["label"].lower() for d in w["dims"])
    if w.get("limit"):
        t = ("Bottom " if w.get("sort", "").startswith("+") else "Top ") + f"{w['limit']}: {t}"
    if w.get("filters"):
        t += " (" + ", ".join(w["filters"].values()) + ")"
    return t


def auto_chart(dims, measures):
    if not dims:
        return "kpi"
    if len(dims) == 2:
        return "stacked"
    d = DIMENSIONS[dims[0]]
    if d["time"]:
        return "line"
    if dims[0] == "product":
        return "hbar"
    if dims[0] == "category" and len(measures) == 1:
        return "pie"
    return "bar"


def make_widget(dims, measures, chart=None, limit=None, sort=None, filters=None, title=None):
    dims = [d for d in dims if d in DIMENSIONS][:2]
    measures = [m for m in measures if m in MEASURES][:4] or ["sales"]
    chart = chart if chart in CHART_TYPES else auto_chart(dims, measures)
    if chart == "pie" and (not dims or len(measures) > 1):
        chart = auto_chart(dims, measures) if dims else "kpi"
    w = {"chart": chart, "dims": dims, "measures": measures}
    if limit:
        w["limit"] = max(1, min(int(limit), 100))
        w["sort"] = sort or "-" + measures[0]
    elif sort:
        w["sort"] = sort
    clean_filters = {k: str(v) for k, v in (filters or {}).items() if k in TEXT_FIELDS and v}
    if clean_filters:
        w["filters"] = clean_filters
    w["title"] = (title or "").strip()[:80] or widget_title(w)
    return w


# ---- rule-based understanding ------------------------------------------------------
MEASURE_WORDS = [
    ("margin", ["profit margin", "margin %", "margin"]),
    ("basket", ["average basket", "avg basket", "basket size", "basket", "average order value", "aov",
                "average order", "avg order", "ticket size", "average sale"]),
    ("avg_price", ["average price", "avg price", "average unit price", "unit price"]),
    ("units", ["units sold", "items sold", "units", "quantity", "qty", "pieces", "volume"]),
    ("profit", ["gross profit", "profits", "profit", "earnings"]),
    ("cost", ["costs", "cost", "cogs", "expenses", "expense"]),
    ("orders", ["orders", "order count", "transactions", "bills", "customers", "footfall"]),
    ("sales", ["sales", "revenue", "turnover", "income", "selling", "sold", "sell"]),
]
DIM_WORDS = [
    ("weekday", ["day of the week", "day of week", "weekdays", "weekday", "which day"]),
    ("store", ["stores", "store", "shops", "shop", "branches", "branch", "outlets", "outlet",
               "locations", "location"]),
    ("category", ["categories", "category", "departments", "department", "dept"]),
    ("product", ["products", "product", "items", "item", "skus", "sku"]),
    ("day", ["daily", "per day", "by day", "each day", "days"]),
    ("week", ["weekly", "per week", "by week", "weeks", "week"]),
    ("month", ["monthly", "per month", "by month", "months", "month", "over time", "trend"]),
    ("quarter", ["quarterly", "quarters", "quarter"]),
    ("year", ["yearly", "annual", "years", "year"]),
]
CHART_WORDS = [
    ("stacked", ["stacked"]),
    ("hbar", ["horizontal bar", "horizontal"]),
    ("pie", ["pie", "donut", "doughnut", "share", "breakdown", "proportion", "mix", "split"]),
    ("area", ["area"]),
    ("line", ["line", "trend", "over time"]),
    ("table", ["table", "list", "grid", "spreadsheet"]),
    ("kpi", ["kpi", "card", "tile", "big number", "single number"]),
    ("bar", ["bar", "bars", "column", "columns"]),
]
QUESTION_START = re.compile(r"^(which|what|who|how|is|are|did|does|do|why|when|where|tell me|compare|any)\b")
BUILD_WORDS = re.compile(r"\b(show|add|plot|chart|graph|visuali[sz]e|draw|display|put|create|make|insert|"
                         r"widget|kpi|pie|bar|line|table)\b")


def _find_terms(text, groups):
    """Finds group ids whose phrases appear in text, longest phrases first; returns (ids, text_left)."""
    found = []
    phrases = sorted(((p, gid) for gid, ps in groups for p in ps), key=lambda x: -len(x[0]))
    for phrase, gid in phrases:
        pat = r"(?<![\w])" + re.escape(phrase) + r"(?![\w])"
        m = re.search(pat, text)
        if m:
            found.append((m.start(), gid))
            text = text[:m.start()] + " " * len(phrase) + text[m.end():]
    found.sort()
    seen, ids = set(), []
    for _pos, gid in found:
        if gid not in seen:
            seen.add(gid)
            ids.append(gid)
    return ids, text


def _match_values(text, values):
    """Known store/category/product values mentioned in text -> {field: value}."""
    out, low = {}, text.lower()
    cands = sorted(((v, f) for f, vs in values.items() for v in vs), key=lambda x: -len(x[0]))
    for v, f in cands:
        if f in out or len(v) < 2:
            continue
        if re.search(r"(?<![\w])" + re.escape(v.lower()) + r"(?![\w])", low):
            out[f] = v
            low = low.replace(v.lower(), " ")
    return out


def _strip_quotes(s):
    return s.strip().strip(".!?").strip().strip("'\"“”‘’").strip()


def _field_word(word):
    w = (word or "").lower()
    for f, words in (("store", ("store", "shop", "branch", "outlet")), ("category", ("category", "department")),
                     ("product", ("product", "item", "sku"))):
        if w in words:
            return f
    return None


def _resolve_named(word, rest, values):
    """'store', 'A' -> tries 'store A' as a full value first (stores are often called 'Store A'),
    then 'A' within the store field."""
    rest = _strip_quotes(rest)
    if word:
        f, v = _resolve_value(f"{word} {rest}", values)
        if v:
            return f, v
    return _resolve_value(rest, values, _field_word(word))


def _resolve_value(raw, values, field=None):
    """Case-insensitive lookup of a known value; returns (field, exact value) or (field, None)."""
    raw_l = raw.lower()
    for f in ([field] if field else TEXT_FIELDS):
        for v in values.get(f, []):
            if v.lower() == raw_l:
                return f, v
    return field, None


HELP_TEXT = ("I can build charts, answer questions about your sales and tidy your data. Try:\n"
             "• \"Show sales by store\" or \"profit by month as a line\"\n"
             "• \"Top 5 products by units\"\n"
             "• \"Build a dashboard\"\n"
             "• \"Which store is falling behind?\"\n"
             "• \"Rename Store A to Downtown\" or \"Clean my data\"")


class RulesEngine:
    """Deterministic fallback that understands common shop-owner requests."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.values = ctx.get("values", {})

    def respond(self, message):
        text = " ".join(message.strip().split())
        low = text.lower()
        if not low or re.fullmatch(r"(help|\?|what can you do\??|hi|hello|hey)", low):
            return {"reply": HELP_TEXT, "actions": []}
        for handler in (self._rename, self._merge, self._tidy, self._delete, self._clear, self._build):
            out = handler(text, low)
            if out:
                return out
        measures, rest = _find_terms(low, MEASURE_WORDS)
        dims, rest = _find_terms(rest, DIM_WORDS)
        is_question = (low.endswith("?") or QUESTION_START.match(low)) and not re.match(
            r"^(show|add|plot|chart|graph|display|create|make|give me a chart)", low)
        if is_question and not re.search(r"\b(chart|graph|plot|widget|pie|bar chart|line chart)\b", low):
            return self._answer(low, measures, dims)
        charts, _ = _find_terms(low, CHART_WORDS)
        if not measures and not dims and not charts:
            return {"reply": "Sorry, I didn't catch which data you want. " + HELP_TEXT, "actions": []}
        return self._widget(low, measures, dims, charts)

    # -- data changes (always confirmed in the browser) --
    def _rename(self, text, low):
        m = re.match(r"^(?:please\s+)?(?:rename|change|relabel)\s+(?:the\s+)?(?:(store|shop|branch|outlet|category|"
                     r"department|product|item|sku)\s+)?(.+?)\s+(?:to|as|into)\s+(.+)$", text, re.IGNORECASE)
        if not m or not low.startswith(("rename", "change", "relabel", "please")):
            return None
        field, src = _resolve_named(m.group(1), m.group(2), self.values)
        dst = _strip_quotes(m.group(3))
        if not src:
            return {"reply": f"I couldn't find \"{_strip_quotes(m.group(2))}\" in your stores, categories or "
                             "products. Check the spelling on the Data page.", "actions": []}
        if not dst or len(dst) > 80:
            return {"reply": "Tell me the new name, e.g. \"rename Store A to Downtown\".", "actions": []}
        return {"reply": f"I'll rename {field} \"{src}\" to \"{dst}\" everywhere it appears. Please confirm.",
                "actions": [{"type": "rename", "field": field, "from": src, "to": dst}]}

    def _merge(self, _text, low):
        m = re.match(r"^merge\s+(.+?)\s+(?:into|with)\s+(.+)$", low)
        if not m:
            return None
        f1, a = _resolve_value(_strip_quotes(m.group(1)), self.values)
        f2, b = _resolve_value(_strip_quotes(m.group(2)), self.values, f1 if a else None)
        if not (a and b) or f1 != f2:
            return {"reply": "I could only merge two existing names of the same kind, e.g. "
                             "\"merge Store E into Store D\".", "actions": []}
        return {"reply": f"I'll merge {f1} \"{a}\" into \"{b}\". Please confirm.",
                "actions": [{"type": "rename", "field": f1, "from": a, "to": b}]}

    def _delete(self, _text, low):
        m = re.match(r"^(?:please\s+)?(?:delete|remove|drop|erase)\s+(?:all\s+)?(?:the\s+)?"
                     r"(?:(?:sales\s+)?(?:rows|data|records|sales|entries)\s+)?(?:(?:for|from|of|where|in|with)\s+)?"
                     r"(?:the\s+)?(?:(store|shop|branch|category|department|product|item)\s+)?(.+)$", low)
        if not m:
            return None
        field, value = _resolve_named(m.group(1), m.group(2), self.values)
        if not value:
            return {"reply": f"I couldn't find \"{_strip_quotes(m.group(2))}\" to delete.", "actions": []}
        return {"reply": f"This permanently deletes every sales row for {field} \"{value}\". Please confirm.",
                "actions": [{"type": "delete_rows", "field": field, "value": value}]}

    def _tidy(self, _text, low):
        if re.search(r"\b(clean|cleanup|tidy|de-?dup\w*|duplicates?|normali[sz]e|fix (my|the) data|"
                     r"spelling)\b", low) and not BUILD_WORDS.search(low.replace("clean", "")):
            return {"reply": "I'll tidy the stored data: trim spaces, merge names that only differ in "
                             "capitals or spacing, and remove exact duplicate rows. Please confirm.",
                    "actions": [{"type": "tidy"}]}
        return None

    def _clear(self, _text, low):
        if re.search(r"\b(clear|reset|empty|wipe|start over)\b.*\b(dashboard|canvas|board|charts)\b", low):
            return {"reply": "Cleared the canvas.", "actions": [{"type": "clear_dashboard"}]}
        return None

    def _build(self, text, low):
        if not re.search(r"\b(build|create|make|generate|design|set up|give me)\b.*\bdashboard\b", low):
            return None
        filters = _match_values(text, self.values)
        widgets = [make_widget([], [m], "kpi", filters=filters) for m in ("sales", "profit", "orders", "basket")]
        widgets += [make_widget(["month"], ["sales", "profit"], "line", filters=filters)]
        if "store" not in filters:
            widgets.append(make_widget(["store"], ["sales"], "bar", filters=filters))
        if "category" not in filters:
            widgets.append(make_widget(["category"], ["sales"], "pie", filters=filters))
        widgets.append(make_widget(["product"], ["sales", "units"], "table", limit=10, filters=filters))
        scope = " for " + ", ".join(filters.values()) if filters else ""
        return {"reply": f"Here's a starter dashboard{scope}: four KPIs, the monthly trend, store and category "
                         "breakdowns and the top 10 products. Drag fields onto any chart to change it.",
                "actions": [{"type": "clear_dashboard"}] + [{"type": "add_widget", "widget": w} for w in widgets]}

    # -- charts --
    def _widget(self, low, measures, dims, charts):
        limit, sort = None, None
        m = re.search(r"\b(top|best|bottom|worst|lowest|least|highest)\s+(\d{1,3})\b", low)
        if m:
            limit = int(m.group(2))
            sort = ("+" if m.group(1) in ("bottom", "worst", "lowest", "least") else "-")
        elif re.search(r"\b(top|best)\b", low) and dims:
            limit, sort = 10, "-"
        if limit and not dims:
            dims = ["product"]
        chart = charts[0] if charts else None
        if chart == "line" and dims and not DIMENSIONS[dims[0]]["time"] and "trend" in low:
            dims = ["month"] + [d for d in dims if d != "month"][:1]
        filters = _match_values(low, self.values)
        for f in list(filters):
            if f in dims:
                del filters[f]
        measures = measures or ["sales"]
        w = make_widget(dims, measures, chart, limit=limit,
                        sort=(sort + measures[0]) if sort else None, filters=filters)
        return {"reply": f"Added \"{w['title']}\" as a {CHART_LABELS[w['chart']]}.",
                "actions": [{"type": "add_widget", "widget": w}]}

    # -- questions --
    def _answer(self, low, measures, dims):
        query, insights = self.ctx.get("query"), self.ctx.get("insights")
        if re.search(r"\b(behind|declin\w*|struggl\w*|weak\w*|problems?|issues?|worry|concern\w*|why|wrong|"
                     r"drop\w*|summar\w*|overview|doing|insights?|recommend\w*|advice|improve)\b", low) and insights:
            items = insights()
            if not items:
                return {"reply": "There is no data in the current selection yet.", "actions": []}
            warn = [i["text"] for i in items if i["level"] == "warn"]
            if re.search(r"\b(behind|declin\w*|struggl\w*|weak\w*|problems?|issues?|worry|wrong|drop\w*)\b", low):
                text = " ".join(warn) if warn else "Nothing looks worrying: no store is more than 15% below " \
                                                      "average and none is declining."
            else:
                text = " ".join(i["text"] for i in items)
            return {"reply": text, "actions": []}
        if not query:
            return {"reply": HELP_TEXT, "actions": []}
        measure = measures[0] if measures else "sales"
        filters = _match_values(low, self.values)
        if not dims:
            if re.search(r"\b(which|who|what)\b.*\b(store|shop|branch)", low):
                dims = ["store"]
            elif re.search(r"\bwhen\b|\bwhich month\b|\bbest month\b", low):
                dims = ["month"]
        if not dims:
            res = query([], [measure], filters)
            total = res["rows"][0][0] if res["rows"] else 0
            scope = " for " + ", ".join(filters.values()) if filters else ""
            return {"reply": f"{MEASURES[measure]['label']}{scope} in the selected period: "
                             f"{fmt_value(measure, total)}.",
                    "actions": [{"type": "add_widget", "optional": True,
                                 "widget": make_widget([], [measure], "kpi", filters=filters)}]}
        dim = dims[0]
        filters.pop(dim, None)
        res = query([dim], [measure], filters)
        rows = [r for r in res["rows"] if r[0] is not None]
        if not rows:
            return {"reply": "There is no data in the current selection.", "actions": []}
        rows.sort(key=lambda r: r[1], reverse=True)
        low_first = re.search(r"\b(lowest|worst|least|bottom|smallest|weakest|minimum|min)\b", low)
        top, bottom = rows[0], rows[-1]
        total = sum(r[1] for r in rows)
        label = MEASURES[measure]["label"].lower()

        def share(r):
            return f" ({r[1] / total * 100:.0f}% of total)" if total and MEASURES[measure]["format"] != "pct" else ""

        if low_first:
            reply = f"{bottom[0]} has the lowest {label}: {fmt_value(measure, bottom[1])}{share(bottom)}. "
            reply += f"The highest is {top[0]} with {fmt_value(measure, top[1])}."
        else:
            reply = f"{top[0]} has the highest {label}: {fmt_value(measure, top[1])}{share(top)}. "
            if len(rows) > 1:
                reply += f"The lowest is {bottom[0]} with {fmt_value(measure, bottom[1])}."
        return {"reply": reply, "actions": [{"type": "add_widget", "optional": True,
                                             "widget": make_widget([dim], [measure], filters=filters)}]}


CHART_LABELS = {"bar": "bar chart", "hbar": "horizontal bar chart", "line": "line chart", "area": "area chart",
                "pie": "pie chart", "kpi": "KPI card", "table": "table", "stacked": "stacked bar chart"}


# ---- validating model output ---------------------------------------------------------
def sanitize_actions(actions, values):
    """Keeps only well-formed actions that reference real fields and values."""
    out = []
    for a in actions if isinstance(actions, list) else []:
        if not isinstance(a, dict):
            continue
        t = a.get("type")
        if t == "add_widget" and isinstance(a.get("widget"), dict):
            w = a["widget"]
            dims = [d for d in w.get("dims") or [] if d in DIMENSIONS]
            measures = [m for m in w.get("measures") or [] if m in MEASURES]
            if not measures:
                continue
            filters = {}
            for f, v in (w.get("filters") or {}).items() if isinstance(w.get("filters"), dict) else []:
                _f, exact = _resolve_value(str(v), values, f if f in TEXT_FIELDS else None)
                if exact:
                    filters[f] = exact
            limit = w.get("limit") if isinstance(w.get("limit"), int) and w.get("limit") > 0 else None
            sort = w.get("sort") if isinstance(w.get("sort"), str) and \
                w.get("sort").lstrip("+-") in dims + measures else None
            out.append({"type": t, "optional": bool(a.get("optional")),
                        "widget": make_widget(dims, measures, w.get("chart"), limit=limit, sort=sort,
                                              filters=filters, title=str(w.get("title") or ""))})
        elif t in ("rename", "delete_rows"):
            field, exact = _resolve_value(str(a.get("from" if t == "rename" else "value") or ""), values,
                                          a.get("field") if a.get("field") in TEXT_FIELDS else None)
            if not exact:
                continue
            if t == "rename":
                to = str(a.get("to") or "").strip()[:80]
                if to:
                    out.append({"type": t, "field": field, "from": exact, "to": to})
            else:
                out.append({"type": t, "field": field, "value": exact})
        elif t in ("tidy", "clear_dashboard"):
            out.append({"type": t})
    return out


def _extract_json(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object")
    return json.loads(text[start:end + 1])


SYSTEM_PROMPT = """You are the assistant inside a retail sales dashboard used by shop and chain owners.
Answer in plain, friendly English (max 3 short sentences) and use ONLY the numbers in DATA SUMMARY.
Respond with ONE JSON object and nothing else:
{"reply": "<text for the owner>", "actions": [<zero or more actions>]}
Allowed actions:
{"type":"add_widget","widget":{"chart":<chart>,"dims":[<0-2 dimension ids>],"measures":[<1-4 measure ids>],
  "limit":<optional int>,"sort":<optional "-measure_id" or "+measure_id">,"filters":<optional {"store"|"category"|"product": value}>,
  "title":<optional short title>}, "optional": <true if only a suggestion>}
{"type":"rename","field":"store"|"category"|"product","from":<existing value>,"to":<new value>}
{"type":"delete_rows","field":"store"|"category"|"product","value":<existing value>}
{"type":"tidy"}   (trim spaces, merge case variants, remove duplicate rows)
{"type":"clear_dashboard"}
Use add_widget when the owner asks to show, add or build charts ("build a dashboard" = clear_dashboard then
several add_widget). For questions, answer in reply and optionally suggest one add_widget with "optional": true.
Never invent field ids or values that are not listed."""


class Assistant:
    """Entry point used by the API. ctx keys: values, widgets, query, insights, summary."""

    def __init__(self, client=None):
        self.client = client

    @property
    def provider(self):
        return "watsonx" if self.client else "rules"

    def respond(self, message, ctx):
        message = (message or "").strip()[:MAX_MESSAGE]
        rules = RulesEngine(ctx).respond(message)
        if not self.client:
            return dict(rules, source="rules")
        try:
            raw = self.client.chat(SYSTEM_PROMPT, self._user_prompt(message, ctx))
            parsed = _extract_json(raw)
            reply = str(parsed.get("reply") or "").strip()
            actions = sanitize_actions(parsed.get("actions"), ctx.get("values", {}))
            if not reply and not actions:
                raise ValueError("empty answer")
            return {"reply": reply or rules["reply"], "actions": actions, "source": "watsonx"}
        except (WatsonxError, ValueError, AttributeError) as e:
            out = dict(rules, source="rules")
            out["note"] = f"watsonx.ai was unavailable ({str(e)[:120]}); answered with built-in rules."
            return out

    @staticmethod
    def _user_prompt(message, ctx):
        values = ctx.get("values", {})
        lines = [
            "DIMENSIONS: " + ", ".join(f"{k} ({v['label']})" for k, v in DIMENSIONS.items()),
            "MEASURES: " + ", ".join(f"{k} ({v['label']})" for k, v in MEASURES.items()),
            "CHARTS: " + ", ".join(CHART_TYPES),
            "STORES: " + ", ".join(values.get("store", [])[:50]),
            "CATEGORIES: " + ", ".join(values.get("category", [])[:50]),
            "PRODUCTS (sample): " + ", ".join(values.get("product", [])[:40]),
            "CURRENT DASHBOARD: " + (", ".join(w.get("title", "") for w in ctx.get("widgets", [])[:20]) or "empty"),
            "DATA SUMMARY: " + json.dumps(ctx.get("summary", {}), separators=(",", ":"))[:6000],
            "OWNER SAYS: " + message,
        ]
        return "\n".join(lines)
