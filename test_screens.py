"""Self-check for the screens and gamas API. Run: python test_screens.py

Uses a temporary database and settings file, so real data is never touched.
"""
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import config
import settings_store

_tmp = tempfile.TemporaryDirectory()
config.DATABASE_PATH = os.path.join(_tmp.name, "test.db")
settings_store._ensure_settings_path = lambda: Path(_tmp.name) / "settings.json"

import app as appmod  # noqa: E402  (must come after the paths above are patched)
from database import get_screen, init_db, upsert_product  # noqa: E402

init_db()
upsert_product("111", "Salam", {"gama": "MEZELURI", "price": 10.5, "quantity": 3, "um": "BUC"})
upsert_product("222", "Sunca", {"gama": "mezeluri", "price": 7, "quantity": 1, "um": "KG"})
upsert_product("333", "Mere", {"gama": "FRUCTE", "price": 1, "quantity": 5, "um": "BUC"})
upsert_product("444", "Fara gama", {"price": 2, "quantity": 1, "um": "BUC"})
upsert_product("555", "Zero stoc", {"price": 2, "quantity": 0, "um": "BUC"})
upsert_product("666", "Stoc negativ", {"price": 2, "quantity": -2, "um": "BUC"})  # negative is not zero
upsert_product("777", "Zero in dbf", {"gama": "ZERO", "price": 2, "quantity": 0, "um": "BUC"})
upsert_product("778", "Cu stoc in dbf", {"gama": "ZERO", "price": 2, "quantity": 4, "um": "BUC"})

c = appmod.app.test_client()
MEZ = {"gama": "MEZELURI", "title": "Mezeluri"}
FRU = {"gama": "FRUCTE", "title": "Fructe"}


def put(payload, screen_id=1):
    return c.put(f"/api/screens/{screen_id}", json=payload)


# --- registration and an unconfigured screen ---
r = c.post("/api/screens/register")
assert r.status_code == 201 and r.get_json() == {"id": 1}
assert c.post("/api/screens/register").get_json() == {"id": 2}

j = c.get("/json_screen/1").get_json()
assert j["configured"] is False and j["layout"] is None and j["blocks"] == []
assert j["name"] == "Ecran 1" and j["refresh_seconds"] == 60
assert c.get("/json_screen/99").status_code == 404

# --- validation ---
invalid = [
    {"layout": "5+1", "blocks": [MEZ]},                  # unknown layout
    {"layout": ["6"], "blocks": [MEZ]},                  # layout of the wrong type
    {"layout": "6", "blocks": []},                       # too few blocks
    {"layout": "6", "blocks": [MEZ, FRU]},               # too many blocks
    {"layout": "3+3", "blocks": [MEZ]},                  # two-block layout with one block
    {"layout": "6", "blocks": [{"gama": "", "title": "x"}]},
    {"layout": "6", "blocks": [{"gama": "FRUCTE", "title": "  "}]},
    {"layout": "6", "blocks": ["not a dict"]},
    {"layout": "6"},                                     # no blocks key
]
for payload in invalid:
    assert put(payload).status_code == 400, payload
assert put({"layout": "6", "blocks": [MEZ]}, screen_id=99).status_code == 404
assert c.get("/json_screen/1").get_json()["configured"] is False  # failed saves changed nothing

# --- configuring: widths come from the layout, gama match ignores case ---
r = put({"name": "  Raft  ", "layout": "2+4", "blocks": [MEZ, FRU]})
assert r.status_code == 200 and r.get_json()["name"] == "Raft"
j = c.get("/json_screen/1").get_json()
assert j["configured"] and j["layout"] == "2+4"
assert [(b["title"], b["width"], len(b["items"])) for b in j["blocks"]] == [("Mezeluri", 2, 2), ("Fructe", 4, 1)]
assert j["blocks"][1]["items"][0] == {"barcode": "333", "name": "Mere", "um": "BUC", "price": 1.0, "quantity": 5.0}

# swapping the order keeps the layout rule: 4+2 puts the first block on the wide side
assert put({"layout": "4+2", "blocks": [FRU, MEZ]}).status_code == 200
j = c.get("/json_screen/1").get_json()
assert [(b["title"], b["width"]) for b in j["blocks"]] == [("Fructe", 4), ("Mezeluri", 2)]

# a single block is always full width, and an empty name falls back to "Ecran N"
assert put({"name": "", "layout": "6", "blocks": [MEZ]}).status_code == 200
j = c.get("/json_screen/1").get_json()
assert [b["width"] for b in j["blocks"]] == [6] and j["name"] == "Ecran 1"

# --- listing, gamas, last_seen ---
screens = c.get("/api/screens").get_json()
assert [s["id"] for s in screens] == [1, 2] and screens[1]["configured"] is False
assert c.get("/api/dbf-gamas").get_json() == [
    {"gama": "FRUCTE", "count": 1}, {"gama": "MEZELURI", "count": 2}, {"gama": "ZERO", "count": 2},
]
assert c.get("/api/gamas").get_json() == [  # nothing defined yet: only the DBF gamas
    {"identifier": "FRUCTE", "title": "FRUCTE", "source": "dbf", "count": 1},
    {"identifier": "MEZELURI", "title": "MEZELURI", "source": "dbf", "count": 2},
    {"identifier": "ZERO", "title": "ZERO", "source": "dbf", "count": 2},
]
before = c.get("/api/screens").get_json()[1]["last_seen"]
assert screens[1]["ip"] == "127.0.0.1"  # the address of whoever registered it
c.get("/json_screen/2", environ_overrides={"REMOTE_ADDR": "192.168.1.50"})
after = c.get("/api/screens").get_json()[1]
assert after["last_seen"] > before and after["ip"] == "192.168.1.50"  # every request refreshes time and address

# --- deleting ---
assert c.delete("/api/screens/1").status_code == 204
assert c.get("/json_screen/1").status_code == 404
assert c.delete("/api/screens/1").status_code == 404
with closing(sqlite3.connect(config.DATABASE_PATH)) as conn:  # `with conn` alone doesn't close it
    assert conn.execute("SELECT COUNT(*) FROM screen_blocks WHERE screen_id = 1").fetchone()[0] == 0

# --- refresh interval ---
r = c.post("/api/settings/screens", json={"refresh_seconds": 30})
assert r.status_code == 200 and r.get_json()["screen_refresh_seconds"] == 30
assert c.get("/json_screen/2").get_json()["refresh_seconds"] == 30
assert c.get("/api/settings").get_json()["dbf_files"] == []  # other settings untouched
for bad in (5, 4000, "abc", None, [1]):
    assert c.post("/api/settings/screens", json={"refresh_seconds": bad}).status_code == 400, bad
assert c.get("/json_screen/2").get_json()["refresh_seconds"] == 30

# --- /json_items keeps its old shapes ---
assert c.get("/json_items?gama=FRUCTE").get_json() == {
    "FRUCTE": [{"barcode": "333", "name": "Mere", "um": "BUC", "price": 1.0, "quantity": 5.0}]
}
assert len(c.get("/json_items").get_json()) == 8  # without a gama: the whole catalog, whatever the stock

# --- app-defined gamas: generated identifier, products added one by one ---
names = lambda items: [i["name"] for i in items]
assert c.get("/api/custom-gamas").get_json() == []


def post_gama(payload):
    return c.post("/api/custom-gamas", json=payload)


for payload in ({}, {"title": "  "}, {"title": "x", "dbf_gama": "NU_EXISTA"}):
    assert post_gama(payload).status_code == 400, payload
assert c.get("/api/custom-gamas").get_json() == []  # the failed attempts created nothing

# a new gama has no products of its own; a linked DBF gama brings its products
r = post_gama({"title": "Oferte saptamana asta", "dbf_gama": "mezeluri"})
g = r.get_json()
OFERTE = g["identifier"]
assert r.status_code == 201 and g["source"] == "app" and g["products"] == []
assert len(OFERTE) == 6 and OFERTE.isalnum() and OFERTE == OFERTE.upper()  # generated, not chosen
assert g["dbf_gama"] == "MEZELURI" and g["count"] == 2

# titles aren't unique, every gama gets its own identifier, and an identifier sent by the client is ignored
r = post_gama({"identifier": "CHOSEN", "title": "Oferte saptamana asta"})
ALTA = r.get_json()["identifier"]
assert r.status_code == 201 and ALTA not in ("CHOSEN", OFERTE) and r.get_json()["count"] == 0
assert c.get("/api/custom-gamas/nope").status_code == 404

# adding products by hand: once per gama (333 twice, 111 is already in the DBF gama), unknown ones refused
add = lambda barcode, gama=OFERTE: c.post(f"/api/custom-gamas/{gama}/products", json={"barcode": barcode})
for barcode in ("333", "444", "111", "333"):
    assert add(barcode).status_code == 200
g = c.get(f"/api/custom-gamas/{OFERTE}").get_json()
assert [p["barcode"] for p in g["products"]] == ["444", "333", "111"]  # by name: Fara gama, Mere, Salam
assert g["count"] == 4  # Salam and Sunca from the DBF gama + Fara gama and Mere
assert add("999999").status_code == 404 and add("", ).status_code == 404
assert add("333", "nope").status_code == 404
assert c.post(f"/api/custom-gamas/{OFERTE}/products", json={}).status_code == 404

# devices resolve the identifier in any case
low = OFERTE.lower()
assert names(c.get(f"/json_items?gama={low}").get_json()[low]) == ["Fara gama", "Mere", "Salam", "Sunca"]
assert put({"layout": "6", "blocks": [{"gama": OFERTE, "title": "Oferte"}]}, screen_id=2).status_code == 200
assert names(c.get("/json_screen/2").get_json()["blocks"][0]["items"]) == ["Fara gama", "Mere", "Salam", "Sunca"]

# a gama used by a screen can't be deleted
r = c.delete(f"/api/custom-gamas/{OFERTE}")
assert r.status_code == 409 and "Ecran 2" in r.get_json()["error"]
assert c.delete("/api/custom-gamas/nope").status_code == 404

# changing title and DBF link leaves the products added by hand alone; the identifier stays
r = c.put(f"/api/custom-gamas/{low}", json={"identifier": "IGNORED", "title": "Oferte", "dbf_gama": ""})
g = r.get_json()
assert r.status_code == 200 and g["identifier"] == OFERTE and g["title"] == "Oferte" and g["dbf_gama"] is None
assert [p["barcode"] for p in g["products"]] == ["444", "333", "111"] and g["count"] == 3
assert c.put("/api/custom-gamas/nope", json={"title": "x"}).status_code == 404
assert c.put(f"/api/custom-gamas/{OFERTE}", json={"title": ""}).status_code == 400

# removing products: idempotent; a product that comes from the DBF gama can only leave with the link
rm = lambda barcode, gama=OFERTE: c.delete(f"/api/custom-gamas/{gama}/products/{barcode}")
assert rm("444").status_code == 200 and rm("444").status_code == 200 and rm("111").status_code == 200
assert rm("333", "nope").status_code == 404
g = c.get(f"/api/custom-gamas/{OFERTE}").get_json()
assert [p["barcode"] for p in g["products"]] == ["333"] and g["count"] == 1
assert names(c.get(f"/json_items?gama={OFERTE}").get_json()[OFERTE]) == ["Mere"]
assert c.put(f"/api/custom-gamas/{OFERTE}", json={"title": "Oferte", "dbf_gama": "MEZELURI"}).get_json()["count"] == 3  # 2 DBF + Mere

# a gama made before identifiers were generated can have a hand-picked one: it wins over the DBF value
with closing(sqlite3.connect(config.DATABASE_PATH)) as conn, conn:  # `with conn` commits
    conn.execute("INSERT INTO gamas (identifier, title, dbf_gama) VALUES ('FRUCTE', 'Fructe proaspete', NULL)")
    conn.execute("INSERT INTO gama_products (identifier, barcode) VALUES ('FRUCTE', '111')")
assert names(c.get("/json_items?gama=FRUCTE").get_json()["FRUCTE"]) == ["Salam"]
assert names(c.get("/json_items?gama=MEZELURI").get_json()["MEZELURI"]) == ["Salam", "Sunca"]  # unshadowed DBF values still work
assert [(g["identifier"], g["title"], g["source"]) for g in c.get("/api/gamas").get_json()] == [
    ("FRUCTE", "Fructe proaspete", "app"),
    (OFERTE, "Oferte", "app"),
    (ALTA, "Oferte saptamana asta", "app"),
    ("MEZELURI", "MEZELURI", "dbf"),  # the DBF FRUCTE is shadowed
    ("ZERO", "ZERO", "dbf"),
]

# stock: products whose stock is exactly 0 never reach the screens (negative stock still does)
for barcode in ("333", "555", "666"):
    assert add(barcode, ALTA).status_code == 200
g = c.get(f"/api/custom-gamas/{ALTA}").get_json()
assert g["count"] == 3  # membership doesn't depend on stock
assert [(p["barcode"], p["price"], p["quantity"]) for p in g["products"]] == [
    ("333", 1.0, 5.0), ("666", 2.0, -2.0), ("555", 2.0, 0.0),  # the products dialog shows price and stock
]
assert names(c.get(f"/json_items?gama={ALTA}").get_json()[ALTA]) == ["Mere", "Stoc negativ"]
assert names(c.get("/json_items?gama=ZERO").get_json()["ZERO"]) == ["Cu stoc in dbf"]  # a raw DBF gama too
assert put({"layout": "3+3", "blocks": [{"gama": ALTA, "title": "A"}, {"gama": "ZERO", "title": "Z"}]}, screen_id=2).status_code == 200
j = c.get("/json_screen/2").get_json()
assert [names(b["items"]) for b in j["blocks"]] == [["Mere", "Stoc negativ"], ["Cu stoc in dbf"]]

# the product list can leave stock 0 out as well, and the total follows
assert c.get("/api/products?limit=100").get_json()["total"] == 8
r = c.get("/api/products?limit=100&hide_zero_stock=1").get_json()
assert r["total"] == 6 and "Zero stoc" not in names(r["products"]) and "Stoc negativ" in names(r["products"])
r = c.get("/api/products?q=stoc&hide_zero_stock=1").get_json()
assert r["total"] == 2 and names(r["products"]) == ["Cu stoc in dbf", "Stoc negativ"]
assert c.get("/api/products?q=stoc").get_json()["total"] == 3

# once the screen is gone the gama can be deleted, together with its products
assert c.delete("/api/screens/2").status_code == 204
assert c.delete(f"/api/custom-gamas/{OFERTE}").status_code == 204
assert c.get(f"/api/custom-gamas/{OFERTE}").status_code == 404
assert c.get(f"/json_items?gama={OFERTE}").get_json() == {OFERTE: []}
with closing(sqlite3.connect(config.DATABASE_PATH)) as conn:
    assert conn.execute("SELECT COUNT(*) FROM gama_products WHERE identifier = ?", (OFERTE,)).fetchone()[0] == 0

# --- store name and version under the app title ---
assert c.get("/api/settings").get_json()["store_name"] == ""
page = c.get("/produse").get_data(as_text=True)
assert f'<div class="side-foot">v{config.APP_VERSION}</div>' in page  # the version sits at the bottom of the menu
assert 'id="brandStore"></span>' in page  # no store name yet: nothing under the title
r = c.post("/api/settings/store", json={"name": "  Magazin <Centru> & Co  "})
assert r.status_code == 200 and r.get_json()["store_name"] == "Magazin <Centru> & Co"  # trimmed
page = c.get("/ecrane").get_data(as_text=True)
assert "Magazin &lt;Centru&gt; &amp; Co" in page and "<Centru>" not in page  # on every page, escaped
assert c.post("/api/settings/store", json={"name": "x" * 61}).status_code == 400
assert c.post("/api/settings/store", json={"name": 5}).status_code == 400
assert c.get("/api/settings").get_json()["store_name"] == "Magazin <Centru> & Co"  # failed saves changed nothing
assert c.post("/api/settings/store", json={"name": ""}).get_json()["store_name"] == ""  # an empty name clears it
assert c.get("/api/settings").get_json()["dbf_files"] == []  # other settings untouched

# a database created before the IP was stored gets the column on startup, without losing its screens
legacy = os.path.join(_tmp.name, "legacy.db")
with closing(sqlite3.connect(legacy)) as conn, conn:
    conn.execute("CREATE TABLE screens (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, layout TEXT, last_seen TEXT)")
    conn.execute("INSERT INTO screens (name, last_seen) VALUES ('Vechi', '2026-01-01T00:00:00+00:00')")
current_db, config.DATABASE_PATH = config.DATABASE_PATH, legacy
try:
    init_db()
    old = get_screen(1)
    assert old["name"] == "Vechi" and old["ip"] is None
finally:
    config.DATABASE_PATH = current_db

# --- pages: each section highlights itself in the menu, interval limits reach the form ---
for path in ("/produse", "/game", "/ecrane", "/settings"):
    html = c.get(path).get_data(as_text=True)
    assert f'class="side-link active" href="{path}"' in html, path
    assert html.count("side-link active") == 1, path
assert 'min="10" max="3600"' in c.get("/settings").get_data(as_text=True)
assert c.get("/").status_code == 302

print("test_screens: all checks passed")
