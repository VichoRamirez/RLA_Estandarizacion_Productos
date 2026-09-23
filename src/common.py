"""Utilidades compartidas del pipeline de estandarización de productos RLA."""
from __future__ import annotations
import re
import unicodedata
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
INPUT = ROOT / "data" / "input"
INTERIM = ROOT / "data" / "interim"
OUTPUT = ROOT / "data" / "output"
REPORTS = OUTPUT / "reports"
DB_PATH = ROOT / "db" / "rla_products.db"

for p in (INTERIM, OUTPUT, REPORTS, DB_PATH.parent):
    p.mkdir(parents=True, exist_ok=True)


def load_env(path: Path = ROOT / ".env") -> None:
    """Carga variables de .env sin dependencias externas.
    No pisa variables ya definidas en el sistema (ej. secretos de Streamlit Cloud)."""
    if not path.exists():
        return
    import os
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


load_env()


def load_config(name: str) -> pd.DataFrame:
    """Lee un CSV de configuración como texto (sin inferir tipos)."""
    return pd.read_csv(CONFIG / name, dtype=str, keep_default_na=False)


def strip_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def norm_key(s) -> str:
    """Clave de comparación: minúsculas, sin tildes, solo alfanumérico y espacios."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = strip_accents(str(s)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def compact_key(s) -> str:
    """Clave sin espacios ni símbolos (DA - LITE == DALITE)."""
    return norm_key(s).replace(" ", "").upper()


def to_camel(name: str) -> str:
    """Convierte un nombre de columna arbitrario a camelCase ASCII."""
    parts = re.split(r"[^A-Za-z0-9]+", strip_accents(str(name)).strip())
    parts = [p for p in parts if p]
    if not parts:
        return "unnamed"
    first = parts[0].lower()
    return first + "".join(p[:1].upper() + p[1:].lower() for p in parts[1:])


def parse_latin_number(series: pd.Series) -> pd.Series:
    """'90.000,50' -> 90000.5 ; admite valores ya numéricos y mezclas."""
    def conv(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip().replace(" ", "")
        if s == "":
            return None
        if "," in s:                       # formato latino
            s = s.replace(".", "").replace(",", ".")
        elif s.count(".") > 1:             # 1.234.567 sin decimales
            s = s.replace(".", "")
        try:
            return float(s)
        except ValueError:
            return None
    return series.map(conv).astype("float64")


def latest_input() -> Path:
    files = sorted(INPUT.glob("*.xlsx"), key=lambda p: p.stat().st_mtime)
    files = [f for f in files if not f.name.startswith("~$")]
    if not files:
        raise FileNotFoundError(f"No hay archivos .xlsx en {INPUT}")
    return files[-1]
