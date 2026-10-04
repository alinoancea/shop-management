"""DBF file parser and scheduled sync."""
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from dbfread import DBF
from apscheduler.schedulers.background import BackgroundScheduler

import config
from database import init_db, upsert_product
from settings_store import get_dbf_files

logger = logging.getLogger(__name__)

# Common barcode column names (case-insensitive) to auto-detect
BARCODE_ALIASES = ("barcode", "ean", "ean13", "code", "article", "artnr", "sku", "plu")


def _find_barcode_column(field_names: list[str]) -> str:
    """Find barcode column from field names. Uses config or auto-detects."""
    if config.BARCODE_COLUMN:
        for name in field_names:
            if name.upper() == config.BARCODE_COLUMN.upper():
                return name
    for name in field_names:
        if name.lower() in BARCODE_ALIASES:
            return name
    return field_names[0] if field_names else None


def _find_name_column(field_names: list[str], barcode_col: str) -> str:
    """Find display name column."""
    if config.NAME_COLUMN:
        for name in field_names:
            if name.upper() == config.NAME_COLUMN.upper():
                return name
    # Check field mapping for "name" first
    mapping = getattr(config, "FIELD_MAPPING", {})
    for dbf_field, local_field in mapping.items():
        if local_field == "name" and dbf_field in field_names:
            return dbf_field
    for preferred in ("name", "produs", "description", "desc", "product", "article_name", "bezeichnung"):
        for name in field_names:
            if name.lower() == preferred and name != barcode_col:
                return name
    for name in field_names:
        if name != barcode_col:
            return name
    return barcode_col


def _to_json_safe(value):
    """Convert value to JSON-serializable form."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value.strip() if isinstance(value, str) else value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _apply_field_mapping(record_dict: dict) -> dict:
    """Apply config FIELD_MAPPING: translate DBF field names to local names, with optional transforms.
    Matching is case-insensitive. EXCLUDED_FIELDS are not added to the local DB.
    """
    mapping = getattr(config, "FIELD_MAPPING", {})
    mapping_lower = {k.lower(): (k, v) for k, v in mapping.items()}
    excluded = getattr(config, "EXCLUDED_FIELDS", set())
    excluded_lower = {f.lower() for f in excluded}
    result = {}
    for dbf_key, value in record_dict.items():
        key_lower = dbf_key.lower()
        if key_lower in excluded_lower:
            continue
        if key_lower not in mapping_lower:
            result[dbf_key] = _to_json_safe(value)
            continue
        _, mapped = mapping_lower[key_lower]
        if isinstance(mapped, tuple):
            local_name, transform = mapped
            try:
                result[local_name] = transform(value)
            except (TypeError, ValueError):
                result[local_name] = _to_json_safe(value)
        else:
            result[mapped] = _to_json_safe(value)
    return result


def _get_by_key(data: dict, key: str):
    """Get value from dict by case-insensitive key."""
    key_lower = key.lower()
    for k, v in data.items():
        if k.lower() == key_lower:
            return v
    return None


def _calculate_price(data: dict):
    """Calculate price as pretuv + tva% (pretuv * (1 + tva/100)), rounded to 2 decimals."""
    pretuv = _get_by_key(data, "pretuv")
    tva = _get_by_key(data, "tva")
    if pretuv is None and tva is None:
        return None
    try:
        p = float(pretuv or 0)
        t = float(tva or 0) / 100
        return round(p * (1 + t), 2)
    except (TypeError, ValueError):
        return None


def _record_to_dict(record, barcode_col: str, name_col: str) -> tuple:
    """Convert DBF record to (barcode, name, data) tuple."""
    record_dict = dict(record)
    barcode = str(record_dict.get(barcode_col, "") or "").strip()
    if not barcode:
        return None, None, None
    name = str(record_dict.get(name_col, "") or barcode).strip()
    data = _apply_field_mapping(record_dict)
    # Calculate price: pretuv + tva%, rounded to 2 decimals
    calc_price = _calculate_price(data)
    if calc_price is not None:
        data["price"] = calc_price
    return barcode, name, data


def parse_dbf_file_to_list(filepath: str) -> list[dict]:
    """Parse a DBF file and return a list of products. No DB writes.
    Each product has: {barcode, name, data}
    """
    products = []
    try:
        table = DBF(filepath)
        field_names = [f.name for f in table.fields]
        if not field_names:
            logger.warning(f"No fields in {filepath}")
            return []

        barcode_col = _find_barcode_column(field_names)
        name_col = _find_name_column(field_names, barcode_col)

        for record in table:
            result = _record_to_dict(record, barcode_col, name_col)
            if result[0] is None:
                continue
            barcode, name, data = result
            products.append({"barcode": barcode, "name": name, "data": data})

        logger.info(f"Parsed {filepath}: {len(products)} products")
    except Exception as e:
        logger.exception(f"Error parsing {filepath}: {e}")
    return products


def _aggregate_products_by_barcode(products: list[dict]) -> list[dict]:
    """Aggregate products by barcode: sum quantity (cantitp), take latest name/price/datei."""
    by_barcode = {}
    for p in products:
        barcode = p["barcode"]
        if barcode not in by_barcode:
            by_barcode[barcode] = {"barcode": barcode, "name": p["name"], "data": dict(p["data"])}
        else:
            agg = by_barcode[barcode]
            agg["name"] = p["name"]  # latest name
            q = float(agg["data"].get("quantity") or 0) + float(p["data"].get("quantity") or 0)
            agg["data"] = {**agg["data"], **p["data"]}
            agg["data"]["quantity"] = round(q, 2)
    return list(by_barcode.values())


def update_database_from_products(products: list[dict]) -> tuple[int, int]:
    """Update local SQLite database from parsed DBF products. Products are aggregated by barcode
    (quantity summed from cantitp), then each updates the local record by barcode. Returns (added, updated)."""
    aggregated = _aggregate_products_by_barcode(products)
    added, updated = 0, 0
    for p in aggregated:
        action = upsert_product(p["barcode"], p["name"], p["data"])
        if action == "added":
            added += 1
        else:
            updated += 1
    return added, updated


def sync_all_dbf() -> dict:
    """Parse all DBF files into a product list, then update local SQLite by barcode.
    Parsed products update/insert into the database based on barcode match. Returns {added, updated, total, finished_at}."""
    all_products = []
    dbf_files = get_dbf_files()
    if not dbf_files:
        logger.debug("No DBF files configured in settings")
        return {"added": 0, "updated": 0, "total": 0, "finished_at": None}

    for filepath in dbf_files:
        p = Path(filepath)
        if not p.exists():
            logger.warning("DBF file not found: %s", filepath)
            continue
        products = parse_dbf_file_to_list(str(p))
        all_products.extend(products)

    total_added, total_updated = update_database_from_products(all_products)

    from datetime import datetime
    return {
        "added": total_added,
        "updated": total_updated,
        "total": total_added + total_updated,
        "finished_at": datetime.utcnow().isoformat() + "Z",
    }


def _run_sync_with_status():
    """Run sync and update status for UI."""
    from sync_status import set_in_progress, set_result
    set_in_progress(True)
    try:
        result = sync_all_dbf()
        set_result(result)
    except Exception as e:
        logger.exception("Sync failed: %s", e)
        set_result({"added": 0, "updated": 0, "total": 0, "finished_at": None, "error": str(e)})


def start_scheduler():
    """Start the background scheduler for periodic DBF parsing."""
    import threading
    from sync_status import set_in_progress, set_result
    scheduler = BackgroundScheduler()
    scheduler.add_job(_run_sync_with_status, "interval", minutes=config.PARSE_INTERVAL_MINUTES, id="dbf_sync")
    scheduler.start()
    logger.info(f"DBF sync scheduled every {config.PARSE_INTERVAL_MINUTES} minutes")
    # Run initial sync in background so app can start immediately
    threading.Thread(target=_run_sync_with_status, daemon=True).start()
