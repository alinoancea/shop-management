"""Application configuration."""
import os

# Column in DBF that contains the barcode (product unique identifier)
BARCODE_COLUMN = os.getenv("BARCODE_COLUMN", "BARCODE")

# Optional: column for product name/description (fallback: first text column or barcode)
NAME_COLUMN = os.getenv("NAME_COLUMN", "produs")

# DBF fields to exclude from local database (case-insensitive)
EXCLUDED_FIELDS = {"seria", "ums", "valuta", "nrs", "nri", "curs", "cont", "plu", "luna", "pretuj", "adaos", "datae", "pretu"}

# DBF field -> local field mapping. Maps DBF column names to local database names.
# Use a tuple (local_name, transform) for value transforms. "name" maps to the product name column.
# Matching is case-insensitive (CANTITP, cantitp, Cantitp all work).
FIELD_MAPPING = {
    "produs": "name",
    "cantitp": ("quantity", lambda x: round(float(x or 0), 2)),  # quantity comes from cantitp
    "pret": "price",  # fallback if pretuv/tva not present; else overwritten by calculated price
    "datei": "datei",
    "um": "um",  # unit of measure (UM from DBF)
    "gama": "gama",  # product category/range for /json_items filter
}

# One SQLite column per attribute (not a single JSON blob)
PRODUCT_ATTRIBUTE_COLUMNS = (
    "quantity",
    "price",
    "um",
    "datei",
    "gama",
    "pretuv",
    "tva",
    "fel",
    "discol",
)

# SQLite database path
DATABASE_PATH = os.getenv("DATABASE_PATH", "products.db")

# Parse interval in minutes
PARSE_INTERVAL_MINUTES = int(os.getenv("PARSE_INTERVAL_MINUTES", "5"))

# Where the web server listens. 0.0.0.0 = every network interface, so screens and other PCs can connect
# (there is no login: keep it on the local network). Set APP_HOST to 127.0.0.1 to allow only this machine.
HOST = os.getenv("APP_HOST", "0.0.0.0")
PORT = int(os.getenv("APP_PORT", "18766"))

# Shown next to the store name under the app title. Bump it when you release a change.
APP_VERSION = "1.0.0"

# Screen layouts: name -> block widths, left to right (columns out of 6)
SCREEN_LAYOUTS = {"6": [6], "3+3": [3, 3], "2+4": [2, 4], "4+2": [4, 2]}

# Allowed range (seconds) for how often screen devices ask for their configuration
SCREEN_REFRESH_RANGE = (10, 3600)
