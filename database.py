"""SQLite database setup and operations."""
import secrets
import sqlite3
from typing import Any, Optional
from datetime import datetime, timezone
from contextlib import contextmanager

import config

_ATTR_COLS = tuple(getattr(config, "PRODUCT_ATTRIBUTE_COLUMNS", ()))
_REAL_COLS = frozenset({"quantity", "price", "pretuv", "tva", "discol"})


def _col_type(col: str) -> str:
    return "REAL" if col in _REAL_COLS else "TEXT"


def _create_table_sql() -> str:
    parts = [
        "barcode TEXT PRIMARY KEY",
        "name TEXT",
        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    ]
    for c in _ATTR_COLS:
        parts.append(f"{c} {_col_type(c)}")
    return "CREATE TABLE products (\n    " + ",\n    ".join(parts) + "\n)"


@contextmanager
def get_db():
    """Get database connection with context manager."""
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _table_column_names(conn: sqlite3.Connection) -> list[str]:
    return [row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()]


def init_db():
    """Initialize the database schema. Legacy JSON `data` table is dropped and recreated empty."""
    with get_db() as conn:
        info = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='products'"
        ).fetchone()
        if not info:
            conn.execute(_create_table_sql())
        else:
            names = _table_column_names(conn)
            if "data" in names:
                conn.execute("DROP TABLE products")
                conn.execute(_create_table_sql())
            else:
                for c in _ATTR_COLS:
                    if c not in names:
                        conn.execute(f"ALTER TABLE products ADD COLUMN {c} {_col_type(c)}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_name ON products(name)")
        # layout is NULL until the screen is configured
        conn.execute(
            "CREATE TABLE IF NOT EXISTS screens ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, layout TEXT, last_seen TEXT, ip TEXT)"
        )
        if "ip" not in [r[1] for r in conn.execute("PRAGMA table_info(screens)")]:
            conn.execute("ALTER TABLE screens ADD COLUMN ip TEXT")  # databases created before the IP was stored
        conn.execute(
            "CREATE TABLE IF NOT EXISTS screen_blocks ("
            "screen_id INTEGER NOT NULL, position INTEGER NOT NULL, "
            "gama TEXT NOT NULL, title TEXT NOT NULL, PRIMARY KEY (screen_id, position))"
        )
        # App-defined gamas: the identifier is generated and never changes; dbf_gama optionally
        # pulls in a whole gama coming from the DBF
        conn.execute(
            "CREATE TABLE IF NOT EXISTS gamas ("
            "identifier TEXT PRIMARY KEY, title TEXT NOT NULL, dbf_gama TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS gama_products ("
            "identifier TEXT NOT NULL, barcode TEXT NOT NULL, PRIMARY KEY (identifier, barcode))"
        )


def _get_ci(data: dict, key: str) -> Any:
    """Get value from dict with case-insensitive key match."""
    kl = key.lower()
    for k, v in data.items():
        if str(k).lower() == kl:
            return v
    return None


def _values_from_data_dict(data: dict) -> dict[str, Any]:
    """Map incoming / merged data dict to SQLite attribute columns."""
    out: dict[str, Any] = {}
    for c in _ATTR_COLS:
        v = data.get(c)
        if v is None:
            v = _get_ci(data, c)
        if v is None or v == "":
            out[c] = None
        elif c in _REAL_COLS:
            try:
                out[c] = float(v)
            except (TypeError, ValueError):
                out[c] = None
        else:
            out[c] = str(v).strip() if isinstance(v, str) else v
    return out


def _parse_date(val):
    """Parse date/datetime value to comparable form."""
    if val is None:
        return None
    if hasattr(val, "year"):
        return val
    s = str(val).strip()[:19]
    if not s:
        return None
    try:
        from datetime import datetime as dt
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y", "%d/%m/%Y", "%Y%m%d"):
            try:
                return dt.strptime(s, fmt)
            except (ValueError, TypeError):
                continue
        if "T" in s or "-" in s:
            return dt.fromisoformat(s.replace("Z", ""))
        return None
    except Exception:
        return None


def upsert_product(barcode: str, name: str, data: dict) -> str:
    """Insert or update a product by barcode (normalized columns)."""
    data = dict(data)
    quantity_dbf = data.get("quantity")
    if quantity_dbf is None:
        quantity_dbf = _get_ci(data, "quantity")
    price_dbf = data.get("price")
    if price_dbf is None:
        price_dbf = _get_ci(data, "price")
    datei_dbf = data.get("datei")
    if datei_dbf is None:
        datei_dbf = _get_ci(data, "datei")

    existing = get_product_by_barcode(barcode)
    now = datetime.utcnow().isoformat()

    if existing is None:
        vals = _values_from_data_dict(data)
        cols = ["barcode", "name", "updated_at"] + list(_ATTR_COLS)
        placeholders = ", ".join(["?"] * len(cols))
        sql = f"INSERT INTO products ({', '.join(cols)}) VALUES ({placeholders})"
        params = [barcode, name, now] + [vals.get(c) for c in _ATTR_COLS]
        with get_db() as conn:
            conn.execute(sql, params)
        return "added"

    existing_attrs = existing.get("data") or {}
    merged: dict = {**existing_attrs}
    for k, v in data.items():
        merged[k] = v

    merged["quantity"] = round(float(quantity_dbf or 0), 2)

    datei_local = existing_attrs.get("datei")
    dt_local = _parse_date(datei_local)
    dt_dbf = _parse_date(datei_dbf)
    if dt_dbf is not None and (dt_local is None or dt_dbf > dt_local):
        if price_dbf is not None:
            merged["price"] = price_dbf
        if datei_dbf is not None:
            merged["datei"] = datei_dbf

    vals = _values_from_data_dict(merged)
    sets = ["name = ?", "updated_at = ?"] + [f"{c} = ?" for c in _ATTR_COLS]
    params = [name, now] + [vals.get(c) for c in _ATTR_COLS] + [barcode]
    with get_db() as conn:
        conn.execute(
            f"UPDATE products SET {', '.join(sets)} WHERE barcode = ?",
            params,
        )
    return "updated"


def _search_where_clause() -> str:
    parts = ["barcode LIKE ?", "name LIKE ?"]
    for c in _ATTR_COLS:
        if c in _REAL_COLS:
            parts.append(f"ifnull(cast({c} AS TEXT), '') LIKE ?")
        else:
            parts.append(f"ifnull({c}, '') LIKE ?")
    return "(" + " OR ".join(parts) + ")"


def _search_params(pattern: str) -> list:
    return [pattern] * (2 + len(_ATTR_COLS))


def _products_filter(search: Optional[str], hide_zero_stock: bool) -> tuple[str, list]:
    """WHERE clause + params for the product list: optional search text, optional 'stock is not 0'."""
    conds, params = [], []
    if search:
        conds.append(_search_where_clause())
        params += _search_params(f"%{search}%")
    if hide_zero_stock:
        conds.append(_NOT_ZERO_STOCK)
    return ("WHERE " + " AND ".join(conds) if conds else ""), params


def get_products(search: str = None, limit: int = 100, offset: int = 0, hide_zero_stock: bool = False) -> list[dict]:
    """Get products from database, optionally filtered by search term."""
    where, params = _products_filter(search, hide_zero_stock)
    with get_db() as conn:
        sel = "barcode, name, updated_at, " + ", ".join(_ATTR_COLS)
        rows = conn.execute(
            f"SELECT {sel} FROM products {where} ORDER BY name LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def get_product_by_barcode(barcode: str) -> Optional[dict]:
    """Get a single product by exact barcode match."""
    with get_db() as conn:
        sel = "barcode, name, updated_at, " + ", ".join(_ATTR_COLS)
        cursor = conn.execute(
            f"SELECT {sel} FROM products WHERE barcode = ?",
            (barcode,),
        )
        row = cursor.fetchone()
        return _row_to_dict(row) if row else None


# Products whose stock is exactly zero are left out where asked (screens, the product list).
# Negative or unknown stock does not count as zero.
_NOT_ZERO_STOCK = "ifnull(quantity, 1) != 0"


def get_products_by_gama(gama: str, limit: int = 500, offset: int = 0, hide_zero_stock: bool = False) -> list[dict]:
    """Get products filtered by gama column. Case-insensitive match."""
    if not gama or not gama.strip():
        return get_products(limit=limit, offset=offset)
    gama_val = gama.strip().lower()
    with get_db() as conn:
        sel = "barcode, name, updated_at, " + ", ".join(_ATTR_COLS)
        cursor = conn.execute(
            f"""
            SELECT {sel} FROM products
            WHERE lower(ifnull(gama, '')) = ?{' AND ' + _NOT_ZERO_STOCK if hide_zero_stock else ''}
            ORDER BY name
            LIMIT ? OFFSET ?
            """,
            (gama_val, limit, offset),
        )
        rows = cursor.fetchall()
        return [_row_to_dict(row) for row in rows]


def get_all_data_keys() -> list[str]:
    """Column names used for product attributes (for API / UI table headers)."""
    return list(_ATTR_COLS)


def get_product_count(search: str = None, hide_zero_stock: bool = False) -> int:
    """Get total count of products, optionally filtered."""
    where, params = _products_filter(search, hide_zero_stock)
    with get_db() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM products {where}", params).fetchone()[0]


def _row_to_dict(row: sqlite3.Row) -> dict:
    """API shape: `data` holds attribute columns (backwards compatible)."""
    data: dict[str, Any] = {}
    for c in _ATTR_COLS:
        v = row[c]
        if v is not None:
            data[c] = v
    return {
        "barcode": row["barcode"],
        "name": row["name"] or row["barcode"],
        "data": data,
        "updated_at": row["updated_at"],
    }


def get_dbf_gamas() -> list[dict]:
    """Distinct gama values coming from the DBF (case-insensitive) with their product counts."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT MIN(trim(gama)) AS gama, COUNT(*) AS count FROM products "
            "WHERE trim(ifnull(gama, '')) != '' GROUP BY lower(trim(gama)) ORDER BY gama"
        ).fetchall()
        return [{"gama": r["gama"], "count": r["count"]} for r in rows]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _screen_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    blocks = conn.execute(
        "SELECT gama, title FROM screen_blocks WHERE screen_id = ? ORDER BY position",
        (row["id"],),
    ).fetchall()
    return {
        "id": row["id"],
        "name": row["name"] or f"Ecran {row['id']}",
        "layout": row["layout"],
        "configured": row["layout"] is not None,
        "last_seen": row["last_seen"],
        "ip": row["ip"],
        "blocks": [{"gama": b["gama"], "title": b["title"]} for b in blocks],
    }


def create_screen(ip: Optional[str] = None) -> int:
    """Register a new, unconfigured screen. Returns its id."""
    with get_db() as conn:
        return conn.execute("INSERT INTO screens (last_seen, ip) VALUES (?, ?)", (_now(), ip)).lastrowid


def get_screen(screen_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM screens WHERE id = ?", (screen_id,)).fetchone()
        return _screen_dict(conn, row) if row else None


def list_screens() -> list[dict]:
    with get_db() as conn:
        return [_screen_dict(conn, r) for r in conn.execute("SELECT * FROM screens ORDER BY id")]


def touch_screen(screen_id: int, ip: Optional[str] = None) -> None:
    """Record that the device just asked for its configuration, and from which address."""
    with get_db() as conn:
        conn.execute("UPDATE screens SET last_seen = ?, ip = ? WHERE id = ?", (_now(), ip, screen_id))


def save_screen(screen_id: int, name: Optional[str], layout: str, blocks: list[dict]) -> bool:
    """Replace a screen's name, layout and blocks. Returns False if the screen doesn't exist."""
    with get_db() as conn:
        updated = conn.execute(
            "UPDATE screens SET name = ?, layout = ? WHERE id = ?", (name, layout, screen_id)
        ).rowcount
        if not updated:
            return False
        conn.execute("DELETE FROM screen_blocks WHERE screen_id = ?", (screen_id,))
        conn.executemany(
            "INSERT INTO screen_blocks (screen_id, position, gama, title) VALUES (?, ?, ?, ?)",
            [(screen_id, i, b["gama"], b["title"]) for i, b in enumerate(blocks)],
        )
        return True


def delete_screen(screen_id: int) -> bool:
    with get_db() as conn:
        conn.execute("DELETE FROM screen_blocks WHERE screen_id = ?", (screen_id,))
        return conn.execute("DELETE FROM screens WHERE id = ?", (screen_id,)).rowcount > 0


# ---- App-defined gamas ----

def _gama_row(conn: sqlite3.Connection, identifier: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM gamas WHERE identifier = ?", (identifier.strip().upper(),)
    ).fetchone()


def _gama_members(g: sqlite3.Row) -> tuple[str, list]:
    """SQL condition + params for the products of a gama: its manual products plus, optionally,
    every product of the linked DBF gama. One condition, so a product never shows up twice."""
    cond = "barcode IN (SELECT barcode FROM gama_products WHERE identifier = ?)"
    params = [g["identifier"]]
    if g["dbf_gama"]:
        cond += " OR lower(ifnull(gama, '')) = ?"
        params.append(g["dbf_gama"].lower())
    return f"({cond})", params


def _gama_dict(conn: sqlite3.Connection, g: sqlite3.Row) -> dict:
    cond, params = _gama_members(g)
    count = conn.execute(f"SELECT COUNT(*) FROM products WHERE {cond}", params).fetchone()[0]
    return {
        "identifier": g["identifier"],
        "title": g["title"],
        "dbf_gama": g["dbf_gama"],
        "source": "app",
        "count": count,
    }


def get_gama_products(identifier: str, limit: int = -1, offset: int = 0, hide_zero_stock: bool = False) -> list[dict]:
    """Products of a gama. An app-defined gama wins; otherwise `identifier` is a raw DBF gama value.
    With hide_zero_stock, products whose stock is exactly zero are left out (that's what screens get)."""
    with get_db() as conn:
        g = _gama_row(conn, identifier)
        if g is not None:
            cond, params = _gama_members(g)
            sel = "barcode, name, updated_at, " + ", ".join(_ATTR_COLS)
            rows = conn.execute(
                f"SELECT {sel} FROM products WHERE {cond}{' AND ' + _NOT_ZERO_STOCK if hide_zero_stock else ''} "
                "ORDER BY name LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
    return get_products_by_gama(identifier, limit=limit, offset=offset, hide_zero_stock=hide_zero_stock)


def list_custom_gamas() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM gamas ORDER BY lower(title), identifier").fetchall()
        return [_gama_dict(conn, g) for g in rows]


def list_gamas() -> list[dict]:
    """Gamas offered to screens: the app-defined ones, then the DBF ones not shadowed by an
    app-defined identifier."""
    custom = list_custom_gamas()
    taken = {g["identifier"].lower() for g in custom}
    dbf = [
        {"identifier": g["gama"], "title": g["gama"], "source": "dbf", "count": g["count"]}
        for g in get_dbf_gamas()
        if g["gama"].lower() not in taken
    ]
    return custom + dbf


def get_custom_gama(identifier: str) -> Optional[dict]:
    """An app-defined gama with the products added by hand (the DBF part is only counted)."""
    with get_db() as conn:
        g = _gama_row(conn, identifier)
        if g is None:
            return None
        manual = conn.execute(
            "SELECT p.barcode, p.name, p.price, p.quantity FROM gama_products gp "
            "JOIN products p ON p.barcode = gp.barcode WHERE gp.identifier = ? ORDER BY p.name",
            (g["identifier"],),
        ).fetchall()
        return {
            **_gama_dict(conn, g),
            "products": [
                {"barcode": r["barcode"], "name": r["name"] or r["barcode"],
                 "price": r["price"], "quantity": r["quantity"]}
                for r in manual
            ],
        }


_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no look-alikes (O/0, I/1)


def create_custom_gama(title: str, dbf_gama: Optional[str]) -> str:
    """Creates a gama with a generated identifier (the user never picks one). Returns the identifier."""
    with get_db() as conn:
        while True:
            identifier = "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))
            try:
                conn.execute(
                    "INSERT INTO gamas (identifier, title, dbf_gama) VALUES (?, ?, ?)",
                    (identifier, title, dbf_gama),
                )
                return identifier
            except sqlite3.IntegrityError:
                continue  # already taken: draw again


def update_custom_gama(identifier: str, title: str, dbf_gama: Optional[str]) -> bool:
    """Changes title and DBF link; the products added by hand stay. The identifier never changes."""
    with get_db() as conn:
        return conn.execute(
            "UPDATE gamas SET title = ?, dbf_gama = ? WHERE identifier = ?", (title, dbf_gama, identifier)
        ).rowcount > 0


def add_gama_product(identifier: str, barcode: str) -> None:
    """Adds a product by hand. Adding it again does nothing: it shows up once per gama."""
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO gama_products (identifier, barcode) VALUES (?, ?)",
            (identifier.strip().upper(), barcode),
        )


def remove_gama_product(identifier: str, barcode: str) -> None:
    with get_db() as conn:
        conn.execute(
            "DELETE FROM gama_products WHERE identifier = ? AND barcode = ?",
            (identifier.strip().upper(), barcode),
        )


def delete_custom_gama(identifier: str) -> bool:
    with get_db() as conn:
        conn.execute("DELETE FROM gama_products WHERE identifier = ?", (identifier,))
        return conn.execute("DELETE FROM gamas WHERE identifier = ?", (identifier,)).rowcount > 0


def screens_using_gama(identifier: str) -> list[str]:
    """Names of the screens that have a block on this gama."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name FROM screens WHERE id IN "
            "(SELECT screen_id FROM screen_blocks WHERE lower(gama) = lower(?)) ORDER BY id",
            (identifier,),
        ).fetchall()
        return [r["name"] or f"Ecran {r['id']}" for r in rows]
