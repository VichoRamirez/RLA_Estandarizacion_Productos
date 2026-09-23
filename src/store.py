"""Capa de base de datos (SQLite).

Tablas que REGENERA el pipeline en cada corrida (derivadas del archivo de entrada):
  products, product_legacy_map, product_site_stock, product_country, duplicate_candidates, sites, taxonomy
Tablas PERSISTENTES (decisiones humanas / IA; el pipeline las lee y NO las borra):
  manual_overrides   -> correcciones hechas a mano en la app (ganan siempre)
  ai_suggestions     -> propuestas de la IA (marcadas como IA hasta que alguien las confirme o cambie)
  duplicate_decisions-> confirmación / rechazo de pares duplicados
  code_registry      -> códigos globales ya emitidos (estabilidad entre corridas)
  run_log            -> historial de corridas
"""
from __future__ import annotations
import sqlite3
from datetime import datetime
import pandas as pd
from common import DB_PATH

PERSISTENT_DDL = """
CREATE TABLE IF NOT EXISTS manual_overrides (
  legacyProductId TEXT NOT NULL, field TEXT NOT NULL, value TEXT, previousValue TEXT,
  changedBy TEXT, changedAt TEXT, comment TEXT, PRIMARY KEY (legacyProductId, field));
CREATE TABLE IF NOT EXISTS ai_suggestions (
  legacyProductId TEXT PRIMARY KEY, familyCode TEXT, categoryCode TEXT, manufacturer TEXT, model TEXT,
  keyAttributeValue TEXT, confidence REAL, reasoning TEXT, aiModel TEXT, createdAt TEXT);
CREATE TABLE IF NOT EXISTS duplicate_decisions (
  pairKey TEXT PRIMARY KEY, decision TEXT, decidedBy TEXT, decidedAt TEXT, comment TEXT);
CREATE TABLE IF NOT EXISTS code_registry (
  legacyProductId TEXT PRIMARY KEY, globalCode TEXT, familyCode TEXT, categoryCode TEXT, firstSeen TEXT, lastSeen TEXT);
CREATE TABLE IF NOT EXISTS run_log (
  runAt TEXT, sourceFile TEXT, step TEXT, metric TEXT, value TEXT);
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=MEMORY")  # evita archivos -journal (compatible con carpetas sincronizadas)
    con.executescript(PERSISTENT_DDL)
    return con


def read_table(name: str) -> pd.DataFrame:
    with connect() as con:
        try:
            return pd.read_sql(f"SELECT * FROM {name}", con)
        except Exception:
            return pd.DataFrame()


def write_table(df: pd.DataFrame, name: str, if_exists: str = "replace") -> None:
    with connect() as con:
        df.to_sql(name, con, if_exists=if_exists, index=False)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def apply_overrides(df: pd.DataFrame, fields: list[str], source_col: str | None = None,
                    source_map: dict | None = None) -> pd.DataFrame:
    """Aplica overrides manuales por legacyProductId. Marca la fuente como 'manual'."""
    ov = read_table("manual_overrides")
    if ov.empty:
        return df
    ov = ov[ov.field.isin(fields)]
    for f, grp in ov.groupby("field"):
        m = grp.set_index("legacyProductId")["value"]
        hit = df["legacyProductId"].isin(m.index)
        if not hit.any():
            continue
        df.loc[hit, f] = df.loc[hit, "legacyProductId"].map(m).values
        col = (source_map or {}).get(f, source_col)
        if col:
            df.loc[hit, col] = "manual"
    return df


def save_override(legacy_ids, field: str, value, previous=None, user: str = "app", comment: str = "") -> None:
    if isinstance(legacy_ids, str):
        legacy_ids = [legacy_ids]
    with connect() as con:
        for lid in legacy_ids:
            con.execute("INSERT OR REPLACE INTO manual_overrides VALUES (?,?,?,?,?,?,?)",
                        (lid, field, None if value is None else str(value),
                         None if previous is None else str(previous), user, now(), comment))


def log_run(source_file: str, step: str, metrics: dict) -> None:
    ts = now()
    with connect() as con:
        con.executemany("INSERT INTO run_log VALUES (?,?,?,?,?)",
                        [(ts, source_file, step, k, str(v)) for k, v in metrics.items()])
