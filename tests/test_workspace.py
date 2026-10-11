"""Tests for the builder, import/cleaning, data editing and assistant features."""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cleaning  # noqa: E402
from ai import AIError, Assistant, ChatClient, sanitize_actions  # noqa: E402
from app import create_app  # noqa: E402

MESSY = """Weekly sales export
Bill Date,Outlet,Department,Item Name,Qty,Net Amount,Cost Price
05/01/2026, store a ,Grocery,Bread,2,"$1,200.50",900
05/01/2026,Store A,Grocery,Bread,2,"$1,200.50",900
13/01/2026,STORE B,grocery,Milk,,₹300,
14/01/2026,Store B,Grocery,Milk,3,abc,10
,Store B,Grocery,Milk,3,5,1
"""


@pytest.fixture()
def client(tmp_path):
    return create_app(db_path=str(tmp_path / "w.db")).test_client()


def ok(resp):
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def preview(c, text, name="x.csv", **form):
    data = {"file": (io.BytesIO(text.encode()), name), **form}
    return c.post("/api/import/preview", data=data, content_type="multipart/form-data")


# ---- cleaning ----------------------------------------------------------------------
def test_mapping_recognises_common_column_names():
    m = cleaning.suggest_mapping(["Bill Date", "Outlet", "Department", "Item Name", "Qty", "Net Amount", "Cost Price"])
    assert m == {"date": "Bill Date", "store": "Outlet", "category": "Department", "product": "Item Name",
                 "units": "Qty", "revenue": "Net Amount", "cost": "Cost Price", "orders": None, "price": None}


def test_value_parsing():
    assert cleaning.parse_number("$1,234.50") == cleaning.Decimal("1234.50")
    assert cleaning.parse_number("(12)") == -12
    assert cleaning.parse_number("abc") is None
    assert str(cleaning.parse_date("13/01/2026")) == "2026-01-13"
    assert str(cleaning.parse_date("01/13/2026", day_first=False)) == "2026-01-13"
    assert str(cleaning.parse_date("2026-01-13T10:30:00Z")) == "2026-01-13"
    assert str(cleaning.parse_date("46023")) == "2026-01-01"     # Excel serial date
    assert cleaning.detect_day_first(["01/02/2026", "25/02/2026"]) is True
    assert cleaning.detect_day_first(["02/25/2026"]) is False


def test_clean_rows_fixes_and_reports():
    header, rows = cleaning.read_table("x.csv", MESSY.encode())
    assert header[0] == "Bill Date"                  # title line above the header skipped
    r = cleaning.clean_rows(header, rows, cleaning.suggest_mapping(header))
    assert r["rows"] == [("2026-01-05", "Store A", "Grocery", "Bread", 1, 2, 120050, 90000),
                         ("2026-01-13", "Store B", "Grocery", "Milk", 1, 1, 30000, 0)]
    assert r["fixes"]["Exact duplicate rows removed"] == 1
    assert [p[0] for p in r["problems"]] == [5, 6]
    keep = cleaning.clean_rows(header, rows, cleaning.suggest_mapping(header), drop_duplicates=False)
    assert len(keep["rows"]) == 3


def test_price_times_units_and_defaults_for_a_single_shop():
    header, rows = cleaning.read_table("t.tsv", b"Date\tPrice\tQty\n2026-03-01\t2.50\t4\n")
    r = cleaning.clean_rows(header, rows, cleaning.suggest_mapping(header))
    assert r["rows"] == [("2026-03-01", "Main store", "Uncategorized", "All products", 1, 4, 1000, 0)]


def test_excel_upload_is_read():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.append(["Date", "Store", "Sales"])
    wb.active.append([cleaning.date(2026, 2, 3), "North", 99.5])
    buf = io.BytesIO()
    wb.save(buf)
    header, rows = cleaning.read_table("s.xlsx", buf.getvalue())
    assert header == ["Date", "Store", "Sales"] and rows == [["2026-02-03", "North", "99.5"]]


# ---- import flow -------------------------------------------------------------------
def test_import_preview_then_commit(client):
    p = ok(preview(client, MESSY))
    assert (p["total_rows"], p["clean_rows"], p["problem_count"]) == (5, 2, 2)
    assert p["import_id"]
    assert ok(client.get("/api/meta"))["rows"] > 1000               # nothing saved yet
    r = ok(client.post("/api/import/commit", json={"import_id": p["import_id"], "mode": "replace"}))
    assert r["imported"] == 2
    assert ok(client.get("/api/meta"))["stores"] == ["Store A", "Store B"]
    again = client.post("/api/import/commit", json={"import_id": p["import_id"], "mode": "replace"})
    assert again.status_code == 400                                   # a preview is used once


def test_import_preview_with_custom_mapping(client):
    mapping = json.dumps({"date": "Bill Date", "revenue": "Net Amount"})
    p = ok(preview(client, MESSY, mapping=mapping))
    assert p["mapping"]["store"] is None and p["clean_rows"] == 2
    bad = ok(preview(client, MESSY, mapping=json.dumps({"revenue": "Net Amount"})))
    assert bad["import_id"] is None and "date" in bad["error"]


# ---- query & dashboards ---------------------------------------------------------------
def test_query_matches_kpis(client):
    q = ok(client.get("/api/query?dims=&measures=sales,orders&start=2026-01-01&end=2026-06-30"))
    assert q["rows"] == [[482000.0, 12340]]
    s = ok(client.get("/api/query?dims=store&measures=sales&sort=-sales&limit=2&start=2026-01-01&end=2026-06-30"))
    assert s["rows"] == [["Store A", 120000.0], ["Store C", 105000.0]]
    w = ok(client.get("/api/query?dims=weekday&measures=orders"))
    assert [r[0] for r in w["rows"]] == ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def test_query_rejects_unknown_fields(client):
    assert client.get("/api/query?dims=day;DROP TABLE sales&measures=sales").status_code == 400
    assert client.get("/api/query?dims=store&measures=").status_code == 400
    assert client.get("/api/query?dims=store&measures=sales&sort=profit").status_code == 400


def test_dashboard_crud(client):
    spec = {"widgets": [{"chart": "bar", "dims": ["store"], "measures": ["sales"], "span": 2}],
            "filters": {"preset": "6m"}}
    d = ok(client.post("/api/dashboards", json={"name": "Weekly review", "spec": spec}))
    assert d["spec"]["widgets"][0]["dims"] == ["store"]
    spec["widgets"].append({"chart": "kpi", "dims": [], "measures": ["profit"]})
    ok(client.put(f"/api/dashboards/{d['id']}", json={"name": "Weekly", "spec": spec}))
    assert [x["name"] for x in ok(client.get("/api/dashboards"))] == ["Weekly"]
    assert len(ok(client.get(f"/api/dashboards/{d['id']}"))["spec"]["widgets"]) == 2
    bad = {"widgets": [{"chart": "bar", "dims": ["nope"], "measures": ["sales"]}]}
    assert client.post("/api/dashboards", json={"name": "x", "spec": bad}).status_code == 400
    ok(client.delete(f"/api/dashboards/{d['id']}"))
    assert client.get(f"/api/dashboards/{d['id']}").status_code == 404


# ---- editing --------------------------------------------------------------------------
def test_edit_rename_tidy_delete(client):
    ok(client.post("/api/import/commit", json={"import_id": ok(preview(
        client, "date,store,category,product,revenue\n2026-01-01,Shop 1,Food,Bread,5\n"
                "2026-01-02,Shop 1,Food,Milk,3\n2026-01-02,Shop 1,Food,Milk,3\n",
        drop_duplicates="0"))["import_id"], "mode": "replace"}))
    rows = ok(client.get("/api/rows?sort=date"))["rows"]
    assert len(rows) == 3
    ok(client.patch(f"/api/rows/{rows[0]['id']}", json={"store": " shop  1 ", "revenue": "7.25"}))
    assert client.patch(f"/api/rows/{rows[0]['id']}", json={"date": "nope"}).status_code == 400
    t = ok(client.post("/api/data/tidy"))
    assert t == {"names_fixed": {"store": 1}, "duplicates_removed": 1}
    assert ok(client.get("/api/meta"))["stores"] == ["Shop 1"]
    assert ok(client.post("/api/data/rename", json={"field": "store", "from": "Shop 1", "to": "Main St"}))["updated"] == 2
    assert ok(client.get("/api/query?measures=sales"))["rows"] == [[10.25]]
    assert ok(client.post("/api/data/delete", json={"field": "product", "value": "Milk"}))["deleted"] == 1
    assert client.post("/api/data/rename", json={"field": "day", "from": "x", "to": "y"}).status_code == 400


def test_read_only_allows_preview_and_assistant_but_not_changes(tmp_path):
    c = create_app(db_path=str(tmp_path / "ro.db"), read_only=True).test_client()
    p = ok(preview(c, MESSY))
    assert p["clean_rows"] == 2 and p["import_id"] is None
    assert ok(c.post("/api/assistant", json={"message": "sales by store"}))["actions"]
    for method, url in (("post", "/api/data/tidy"), ("post", "/api/dashboards"), ("patch", "/api/rows/1"),
                        ("delete", "/api/rows/1"), ("post", "/api/import/commit")):
        assert getattr(c, method)(url, json={}).status_code == 403


# ---- assistant ------------------------------------------------------------------------
def say(c, message):
    return ok(c.post("/api/assistant", json={"message": message,
                                             "filters": {"start": "2026-01-01", "end": "2026-06-30"}}))


def test_assistant_builds_charts(client):
    r = say(client, "top 5 products by units")
    w = r["actions"][0]["widget"]
    assert r["source"] == "rules"
    assert (w["chart"], w["dims"], w["measures"], w["limit"], w["sort"]) == ("hbar", ["product"], ["units"], 5, "-units")
    w = say(client, "profit by month as a line")["actions"][0]["widget"]
    assert (w["chart"], w["dims"], w["measures"]) == ("line", ["month"], ["profit"])
    acts = say(client, "build a dashboard for Store B")["actions"]
    assert acts[0]["type"] == "clear_dashboard" and len(acts) > 5
    assert all(a["widget"]["filters"] == {"store": "Store B"} for a in acts[1:])


def test_assistant_answers_questions(client):
    r = say(client, "which store has the highest sales?")
    assert r["reply"].startswith("Store A has the highest sales: $120,000")
    assert r["actions"][0]["optional"] is True
    assert "Store E" in say(client, "which store is falling behind?")["reply"]
    assert "$482,000" in say(client, "what are total sales?")["reply"]


def test_assistant_proposes_data_changes_without_doing_them(client):
    r = say(client, "rename Store A to Downtown")
    assert r["actions"] == [{"type": "rename", "field": "store", "from": "Store A", "to": "Downtown"}]
    assert "Store A" in ok(client.get("/api/meta"))["stores"]        # unchanged until confirmed
    assert say(client, "delete rows for store e")["actions"] == [{"type": "delete_rows", "field": "store", "value": "Store E"}]
    assert say(client, "clean my data")["actions"] == [{"type": "tidy"}]
    assert say(client, "rename Store Z to X")["actions"] == []


class FakeAI:
    provider, label = "gemini", "Google Gemini"

    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.prompts = answer, fail, []

    def chat(self, system, user, max_tokens=700):
        self.prompts.append((system, user, max_tokens))
        if self.fail:
            raise AIError("HTTP 401")
        return self.answer


def test_ai_answer_is_validated(tmp_path):
    answer = "Sure!\n" + json.dumps({"reply": "Store A leads.", "actions": [
        {"type": "add_widget", "widget": {"chart": "pie", "dims": ["store"], "measures": ["sales", "bogus"]}},
        {"type": "add_widget", "widget": {"dims": ["store"], "measures": ["nope"]}},
        {"type": "rename", "field": "store", "from": "store a", "to": "Downtown"},
        {"type": "drop_database"}]})
    fake = FakeAI(answer)
    c = create_app(db_path=str(tmp_path / "ai.db"), assistant=Assistant(fake)).test_client()
    fields = ok(c.get("/api/fields"))
    assert (fields["assistant"], fields["assistant_label"]) == ("gemini", "Google Gemini")
    r = say(c, "sales split by store")
    assert r["source"] == "gemini" and r["reply"] == "Store A leads."
    assert [a["type"] for a in r["actions"]] == ["add_widget", "rename"]
    assert r["actions"][0]["widget"]["measures"] == ["sales"]
    assert r["actions"][1]["from"] == "Store A"
    assert "DATA SUMMARY" in fake.prompts[0][1] and "Store A" in fake.prompts[0][1]


def test_ai_failure_falls_back_to_rules(tmp_path):
    c = create_app(db_path=str(tmp_path / "ai.db"), assistant=Assistant(FakeAI(fail=True))).test_client()
    r = say(c, "sales by store")
    assert r["source"] == "rules" and "unavailable" in r["note"] and r["actions"]
    c2 = create_app(db_path=str(tmp_path / "ai2.db"), assistant=Assistant(FakeAI("not json"))).test_client()
    assert say(c2, "sales by store")["source"] == "rules"


def test_ai_client_from_env():
    assert ChatClient.from_env({}) is None
    assert ChatClient.from_env({"AI_PROVIDER": "groq"}) is None                 # key missing
    g = ChatClient.from_env({"AI_API_KEY": "k"})                               # bare key = Gemini
    assert (g.provider, g.base_url, g.api_key) == ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "k")
    q = ChatClient.from_env({"AI_PROVIDER": "Groq", "AI_API_KEY": "k", "AI_MODEL": "m"})
    assert (q.base_url, q.model) == ("https://api.groq.com/openai/v1", "m")
    o = ChatClient.from_env({"AI_PROVIDER": "ollama"})                          # local, no key
    assert o.base_url == "http://localhost:11434/v1" and o.api_key is None


def test_ai_client_sends_openai_style_request(monkeypatch):
    sent = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode()

    def fake_urlopen(req, timeout):
        sent.update(url=req.full_url, auth=req.get_header("Authorization"), body=json.loads(req.data), timeout=timeout)
        return Resp()

    monkeypatch.setattr("ai.urllib.request.urlopen", fake_urlopen)
    c = ChatClient.from_env({"AI_PROVIDER": "groq", "AI_API_KEY": "secret"})
    assert c.chat("sys", "user") == "hi"
    assert sent["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert sent["auth"] == "Bearer secret"
    assert sent["body"]["messages"][0] == {"role": "system", "content": "sys"}


def test_sanitize_drops_unknown_values():
    values = {"store": ["Store A"], "category": [], "product": []}
    assert sanitize_actions([{"type": "delete_rows", "field": "store", "value": "Ghost"}], values) == []
    assert sanitize_actions("garbage", values) == []
