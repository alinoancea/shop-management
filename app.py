"""Flask application with web UI and API."""
import errno
import logging
import os
import socket
import subprocess
import sys

from flask import Flask, render_template, request, jsonify, redirect, url_for

import config
from database import (
    init_db,
    get_products,
    get_product_count,
    get_product_by_barcode,
    get_products_by_gama,
    get_all_data_keys,
    get_dbf_gamas,
    get_gama_products,
    list_gamas,
    list_custom_gamas,
    get_custom_gama,
    create_custom_gama,
    update_custom_gama,
    add_gama_product,
    remove_gama_product,
    delete_custom_gama,
    screens_using_gama,
    create_screen,
    get_screen,
    list_screens,
    touch_screen,
    save_screen,
    delete_screen,
)
from dbf_parser import start_scheduler, sync_all_dbf, _run_sync_with_status
from sync_status import get_status as get_sync_status
from settings_store import (
    load_settings, save_settings, get_dbf_files, set_dbf_files,
    get_screen_refresh, set_screen_refresh,
    get_store_name, set_store_name,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = Flask(__name__)
_db_initialized = False


@app.before_request
def _ensure_db():
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True


@app.context_processor
def _branding():
    """Store name and app version, shown under the app title on every page."""
    return {"store_name": get_store_name(), "app_version": config.APP_VERSION}


@app.route("/")
def index():
    """Default section."""
    return redirect(url_for("produse"))


@app.route("/produse")
def produse():
    """Product list web page."""
    return render_template("produse.html")


@app.route("/api/products")
def api_products():
    """API: List products with optional search and pagination. hide_zero_stock=1 leaves out products whose stock is exactly 0."""
    search = request.args.get("q", "").strip() or None
    limit = min(int(request.args.get("limit", 100)), 500)
    offset = int(request.args.get("offset", 0))
    hide_zero = request.args.get("hide_zero_stock") in ("1", "true")
    products = get_products(search=search, limit=limit, offset=offset, hide_zero_stock=hide_zero)
    total = get_product_count(search=search, hide_zero_stock=hide_zero)
    return jsonify({
        "products": products,
        "total": total,
        "limit": limit,
        "offset": offset,
        "data_fields": get_all_data_keys(),
    })


def _item(p: dict) -> dict:
    """Product shape sent to display devices."""
    d = p.get("data") or {}
    return {
        "barcode": p["barcode"],
        "name": p["name"] or p["barcode"],
        "um": d.get("um", ""),
        "price": d.get("price"),
        "quantity": d.get("quantity"),
    }


@app.route("/ecrane")
def ecrane():
    """Screens configuration page."""
    return render_template("ecrane.html", layouts=list(config.SCREEN_LAYOUTS.items()))


@app.route("/json_items")
def json_items():
    """API: Get products, optionally filtered by gama. Query param: gama (an app-defined
    identifier or a DBF gama value).
    With gama: {"<gama>": [{"barcode", "name", "um", "price", "quantity"}, ...]}, without the
    products whose stock is exactly 0.
    Without gama: every product, whatever its stock: [{"barcode", "name", "um", "price", "quantity"}, ...]
    """
    gama = request.args.get("gama", "").strip()
    limit = int(request.args.get("limit", -1))  # SQLite: LIMIT -1 = fara limita
    offset = int(request.args.get("offset", 0))
    if gama:
        products = get_gama_products(gama, limit=limit, offset=offset, hide_zero_stock=True)  # devices skip products with stock 0
    else:
        products = get_products_by_gama(gama="", limit=limit, offset=offset)  # no gama: every product
    items = [_item(p) for p in products]
    return jsonify({gama: items} if gama else items)


@app.route("/api/screens/register", methods=["POST"])
def api_register_screen():
    """Device API: a device without an identifier asks for one."""
    return jsonify({"id": create_screen(request.remote_addr)}), 201


@app.route("/json_screen/<int:screen_id>")
def json_screen(screen_id):
    """Device API: configuration and products of one screen. 404 if the id is unknown."""
    screen = get_screen(screen_id)
    if screen is None:
        return jsonify({"error": "Ecran negăsit"}), 404
    touch_screen(screen_id, request.remote_addr)
    widths = config.SCREEN_LAYOUTS.get(screen["layout"], [])
    return jsonify({
        "id": screen["id"],
        "name": screen["name"],
        "configured": screen["configured"],
        "layout": screen["layout"],
        "refresh_seconds": get_screen_refresh(),
        "blocks": [
            {
                "title": b["title"],
                "gama": b["gama"],
                "width": width,
                "items": [_item(p) for p in get_gama_products(b["gama"], limit=-1, hide_zero_stock=True)],
            }
            for b, width in zip(screen["blocks"], widths)
        ],
    })


def _parse_screen(data: dict):
    """Validate a screen payload. Returns ((name, layout, blocks), None) or (None, error)."""
    layout = data.get("layout")
    widths = config.SCREEN_LAYOUTS.get(layout) if isinstance(layout, str) else None
    if widths is None:
        return None, "Aranjament invalid"
    raw = data.get("blocks")
    if not isinstance(raw, list) or len(raw) != len(widths):
        return None, f"Aranjamentul {layout} cere {len(widths)} bloc(uri)"
    blocks = []
    for b in raw:
        b = b if isinstance(b, dict) else {}
        gama, title = str(b.get("gama") or "").strip(), str(b.get("title") or "").strip()
        if not gama or not title:
            return None, "Fiecare bloc are nevoie de gamă și titlu"
        blocks.append({"gama": gama, "title": title})
    return (str(data.get("name") or "").strip() or None, layout, blocks), None


@app.route("/api/screens")
def api_screens():
    """API: List all screens with their blocks."""
    return jsonify(list_screens())


@app.route("/api/screens/<int:screen_id>", methods=["PUT"])
def api_save_screen(screen_id):
    """API: Configure a screen: {"name", "layout", "blocks": [{"gama", "title"}]}."""
    parsed, error = _parse_screen(request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400
    if not save_screen(screen_id, *parsed):
        return jsonify({"error": "Ecran negăsit"}), 404
    return jsonify(get_screen(screen_id))


@app.route("/api/screens/<int:screen_id>", methods=["DELETE"])
def api_delete_screen(screen_id):
    """API: Delete a screen. The device will get 404 on its next request."""
    if not delete_screen(screen_id):
        return jsonify({"error": "Ecran negăsit"}), 404
    return "", 204


@app.route("/api/gamas")
def api_gamas():
    """API: Gamas a screen block can use: app-defined first, then the DBF ones. With product counts."""
    return jsonify(list_gamas())


@app.route("/game")
def game():
    """Gamas configuration page."""
    return render_template("game.html")


@app.route("/api/dbf-gamas")
def api_dbf_gamas():
    """API: Gama values found in the DBF data, with product counts."""
    return jsonify(get_dbf_gamas())


def _parse_gama(data: dict):
    """Validate a gama payload: {title, dbf_gama}. Returns ((title, dbf_gama), None) or (None, error)."""
    title = str(data.get("title") or "").strip()
    if not title:
        return None, "Titlul este obligatoriu"
    dbf_gama = str(data.get("dbf_gama") or "").strip() or None
    if dbf_gama:
        canonical = {g["gama"].lower(): g["gama"] for g in get_dbf_gamas()}
        dbf_gama = canonical.get(dbf_gama.lower())
        if dbf_gama is None:
            return None, "Gama din DBF nu există"
    return (title, dbf_gama), None


@app.route("/api/custom-gamas")
def api_custom_gamas():
    """API: App-defined gamas with product counts."""
    return jsonify(list_custom_gamas())


@app.route("/api/custom-gamas", methods=["POST"])
def api_create_gama():
    """API: Create a gama. The server generates its identifier; it never changes afterwards."""
    parsed, error = _parse_gama(request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400
    return jsonify(get_custom_gama(create_custom_gama(*parsed))), 201


@app.route("/api/custom-gamas/<identifier>")
def api_get_gama(identifier):
    """API: One gama with the products added by hand."""
    gama = get_custom_gama(identifier)
    if gama is None:
        return jsonify({"error": "Gamă negăsită"}), 404
    return jsonify(gama)


@app.route("/api/custom-gamas/<identifier>", methods=["PUT"])
def api_update_gama(identifier):
    """API: Change title and DBF link of a gama. The products added by hand are not touched."""
    parsed, error = _parse_gama(request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400
    if not update_custom_gama(identifier.strip().upper(), *parsed):
        return jsonify({"error": "Gamă negăsită"}), 404
    return jsonify(get_custom_gama(identifier))


@app.route("/api/custom-gamas/<identifier>/products", methods=["POST"])
def api_add_gama_product(identifier):
    """API: Add a product to a gama by hand: {"barcode"}. Adding it again does nothing."""
    if get_custom_gama(identifier) is None:
        return jsonify({"error": "Gamă negăsită"}), 404
    barcode = str((request.get_json(silent=True) or {}).get("barcode") or "").strip()
    if get_product_by_barcode(barcode) is None:
        return jsonify({"error": "Produs negăsit"}), 404
    add_gama_product(identifier, barcode)
    return jsonify(get_custom_gama(identifier))


@app.route("/api/custom-gamas/<identifier>/products/<path:barcode>", methods=["DELETE"])
def api_remove_gama_product(identifier, barcode):
    """API: Remove a product added by hand (one that comes from the linked DBF gama stays)."""
    if get_custom_gama(identifier) is None:
        return jsonify({"error": "Gamă negăsită"}), 404
    remove_gama_product(identifier, barcode)
    return jsonify(get_custom_gama(identifier))


@app.route("/api/custom-gamas/<identifier>", methods=["DELETE"])
def api_delete_gama(identifier):
    """API: Delete a gama, unless a screen still uses it."""
    if get_custom_gama(identifier) is None:
        return jsonify({"error": "Gamă negăsită"}), 404
    used = screens_using_gama(identifier)
    if used:
        return jsonify({"error": "Folosită de: " + ", ".join(used)}), 409
    delete_custom_gama(identifier.strip().upper())
    return "", 204


@app.route("/api/products/<barcode>")
def api_product_detail(barcode):
    """API: Get single product by barcode."""
    product = get_product_by_barcode(barcode)
    if product is None:
        return jsonify({"error": "Produs negăsit"}), 404
    return jsonify(product)


@app.route("/api/sync/status")
def api_sync_status():
    """API: Get sync status (in progress, last result)."""
    return jsonify(get_sync_status())


@app.route("/api/sync", methods=["POST"])
def api_sync():
    """API: Trigger manual DBF sync."""
    status = get_sync_status()
    if status.get("in_progress"):
        return jsonify({
            "status": "in_progress",
            "message": "Sync already in progress",
            **status,
        }), 409
    _run_sync_with_status()
    return jsonify({"status": "ok", **get_sync_status()})


@app.route("/settings")
def settings_page():
    """Settings page."""
    return render_template("settings.html", refresh_range=config.SCREEN_REFRESH_RANGE)


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    """API: Get settings (DBF file list)."""
    return jsonify(load_settings())


@app.route("/api/settings", methods=["PUT", "POST"])
def api_save_settings():
    """API: Save settings (DBF file list)."""
    data = request.get_json(silent=True) or {}
    paths = data.get("dbf_files", [])
    if not isinstance(paths, list):
        return jsonify({"error": "dbf_files trebuie să fie o listă"}), 400
    set_dbf_files(paths)
    return jsonify(load_settings())


@app.route("/api/settings/store", methods=["POST"])
def api_save_store_settings():
    """API: Save the store name shown under the app title: {"name"}. An empty name clears it."""
    name = (request.get_json(silent=True) or {}).get("name", "")
    if not isinstance(name, str):
        return jsonify({"error": "Numele trebuie să fie text"}), 400
    name = name.strip()
    if len(name) > 60:
        return jsonify({"error": "Numele magazinului poate avea cel mult 60 de caractere"}), 400
    set_store_name(name)
    return jsonify(load_settings())


@app.route("/api/settings/screens", methods=["POST"])
def api_save_screen_settings():
    """API: Save screen settings: {"refresh_seconds": n}."""
    lo, hi = config.SCREEN_REFRESH_RANGE
    try:
        seconds = int((request.get_json(silent=True) or {}).get("refresh_seconds"))
    except (TypeError, ValueError):
        seconds = None
    if seconds is None or not lo <= seconds <= hi:
        return jsonify({"error": f"Intervalul trebuie să fie între {lo} și {hi} de secunde"}), 400
    set_screen_refresh(seconds)
    return jsonify(load_settings())


# Native file dialog; runs in a subprocess because tkinter must own its thread.
_PICK_DBF = (
    "import tkinter as tk\n"
    "from tkinter import filedialog\n"
    "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
    "print(filedialog.askopenfilename(title='DBF', filetypes=[('DBF', '*.dbf')]))\n"
)


@app.route("/api/settings/dbf/pick", methods=["POST"])
def api_pick_dbf():
    """API: Open a file dialog on the machine running the server. Returns {"path": ""} if cancelled."""
    out = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", _PICK_DBF],
        capture_output=True, text=True, encoding="utf-8",
    )
    if out.returncode != 0:
        logging.error("File dialog failed: %s", out.stderr)
        return jsonify({"error": "Nu se poate deschide dialogul de selectare"}), 500
    path = out.stdout.strip()
    return jsonify({"path": os.path.normpath(path) if path else ""})


@app.route("/api/settings/dbf", methods=["POST"])
def api_add_dbf():
    """API: Add a DBF file path."""
    data = request.get_json(silent=True) or {}
    path = data.get("path", "").strip()
    if not path:
        return jsonify({"error": "calea este obligatorie"}), 400
    from settings_store import add_dbf_file
    add_dbf_file(path)
    return jsonify(load_settings())


@app.route("/api/settings/dbf", methods=["DELETE"])
def api_remove_dbf():
    """API: Remove a DBF file path."""
    path = request.args.get("path", "").strip()
    if not path:
        return jsonify({"error": "calea este obligatorie"}), 400
    from settings_store import remove_dbf_file
    remove_dbf_file(path)
    return jsonify(load_settings())


def _port_in_use(host: str, port: int) -> bool:
    """True if another socket already holds host:port. Checked up front because the Flask dev server
    sets SO_REUSEADDR, which on Windows lets a second process bind the same port without any error."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name != "nt":  # on POSIX SO_REUSEADDR only skips TIME_WAIT leftovers, as it should
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError as e:
            return e.errno == errno.EADDRINUSE  # other failures (bad address, no permission) surface in app.run
    return False


if __name__ == "__main__":
    if _port_in_use(config.HOST, config.PORT):
        sys.exit(
            f"Port {config.PORT} on {config.HOST} is already in use (another program, or this app already running).\n"
            "Set APP_PORT to another port (e.g. APP_PORT=8080) and start again."
        )
    init_db()  # create the tables now: the first DBF sync must not wait for the first web request
    start_scheduler()
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host=config.HOST, port=config.PORT, debug=debug)
