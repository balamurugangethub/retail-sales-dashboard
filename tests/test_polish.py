import glob
import os

import pytest

from app import HERE, create_app


@pytest.fixture()
def client(tmp_path):
    return create_app(db_path=str(tmp_path / "t.db")).test_client()


def test_unknown_page_gets_a_friendly_404(client):
    r = client.get("/no-such-page")
    assert r.status_code == 404 and b"Page not found" in r.data


def test_unknown_api_path_stays_json(client):
    r = client.get("/api/no-such-thing")
    assert r.status_code == 404 and r.get_json() == {"error": "Not found"}


def test_favicon_is_served(client):
    r = client.get("/static/favicon.svg")
    assert r.status_code == 200 and b"<svg" in r.data


def test_scripts_are_rechecked_after_a_deploy(client):
    # an old cached theme.js or app.js would leave new pages without the Theme button and charts
    r = client.get("/static/theme.js")
    assert "no-cache" in r.headers["Cache-Control"] and r.headers.get("ETag")
    assert client.get("/static/theme.js", headers={"If-None-Match": r.headers["ETag"]}).status_code == 304


@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(HERE, "static", "*.html"))))
def test_every_page_has_icon_and_viewport(path):
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert 'rel="icon"' in html and 'name="viewport"' in html
