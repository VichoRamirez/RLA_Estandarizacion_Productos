"""Capa de datos con dos backends intercambiables (variable DB_BACKEND en .env):
  - supabase : Postgres en Supabase vía API REST (PostgREST). Requiere SUPABASE_URL y SUPABASE_KEY.
  - sqlite   : archivo local db/rla_products.db (respaldo / modo offline).
El esquema está en schema.py (misma estructura en ambos).
API: read_table, replace_table, upsert, update, insert, delete, ping, save_override, apply_overrides, log_run, now.
"""
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd
from common import DB_PATH  # noqa: F401  (importar common también carga .env)
from schema import TABLES, columns

BATCH = 500


def backend() -> str:
    return os.environ.get("DB_BACKEND", "sqlite").strip().lower()


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _conform(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Deja solo las columnas del esquema, en orden, con tipos compatibles."""
    cols, _ = TABLES[name]
    df = df.copy()
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[list(cols)]
    for c, t in cols.items():
        if t == "boolean":
            df[c] = df[c].astype("boolean")
        elif t == "bigint":
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
        elif t == "double precision":
            df[c] = pd.to_numeric(df[c], errors="coerce")
        else:
            df[c] = df[c].astype("string")
    return df


def _typed(name: str, df: pd.DataFrame) -> pd.DataFrame:
    if name not in TABLES or df.empty:
        return df
    for c, t in TABLES[name][0].items():
        if c not in df.columns:
            continue
        if t == "boolean":
            df[c] = df[c].map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else bool(v))
        elif t in ("bigint", "double precision"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# =====================================================================  SQLITE
class _SQLite:
    def con(self):
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.execute("PRAGMA journal_mode=MEMORY")
        for name, (cols, pk) in TABLES.items():
            body = ", ".join(f'"{k}"' for k in cols)
            c.execute(f'CREATE TABLE IF NOT EXISTS {name} ({body}, PRIMARY KEY ({", ".join(pk)}))')
        return c

    def read(self, name, filters=None, cols=None):
        where, params = [], []
        for k, v in (filters or {}).items():
            if isinstance(v, (list, tuple, set)):
                v = list(v)
                if not v:
                    return pd.DataFrame(columns=cols or columns(name))
                where.append(f'"{k}" IN ({",".join("?" * len(v))})'); params += v
            elif v is None:
                where.append(f'"{k}" IS NULL')
            else:
                where.append(f'"{k}" = ?'); params.append(v)
        sel = ", ".join(f'"{c}"' for c in cols) if cols else "*"
        sql = f"SELECT {sel} FROM {name}" + (" WHERE " + " AND ".join(where) if where else "")
        with self.con() as c:
            return pd.read_sql(sql, c, params=params)

    def _rows(self, df):
        return [tuple(None if pd.isna(v) else (bool(v) if isinstance(v, (bool, np.bool_)) else v) for v in r)
                for r in df.astype(object).itertuples(index=False)]

    def replace(self, name, df):
        with self.con() as c:
            c.execute(f"DELETE FROM {name}")
            self._insert(c, name, df, "INSERT")

    def _insert(self, c, name, df, verb):
        cols = list(df.columns)
        sql = f'{verb} INTO {name} ({", ".join(chr(34)+x+chr(34) for x in cols)}) VALUES ({",".join("?" * len(cols))})'
        c.executemany(sql, self._rows(df))

    def upsert(self, name, df, keys=None):
        with self.con() as c:
            self._insert(c, name, df, "INSERT OR REPLACE")

    def insert(self, name, df):
        self.upsert(name, df)

    def update(self, name, values, filters):
        sets = ", ".join(f'"{k}" = ?' for k in values)
        where = " AND ".join(f'"{k}" = ?' for k in filters)
        with self.con() as c:
            c.execute(f"UPDATE {name} SET {sets} WHERE {where}", list(values.values()) + list(filters.values()))

    def delete(self, name, filters):
        where = " AND ".join(f'"{k}" = ?' for k in filters)
        with self.con() as c:
            c.execute(f"DELETE FROM {name} WHERE {where}", list(filters.values()))

    def ping(self):
        with self.con() as c:
            c.execute("SELECT 1")
        return f"SQLite local ({DB_PATH.name})"


# =====================================================================  SUPABASE (REST)
class _Supabase:
    def __init__(self):
        import requests
        self.url = os.environ.get("SUPABASE_URL", "").rstrip("/") + "/rest/v1"
        key = os.environ.get("SUPABASE_KEY", "").strip()
        if not key or "SUPABASE_URL" not in os.environ:
            raise RuntimeError("Faltan SUPABASE_URL / SUPABASE_KEY en .env")
        self.s = requests.Session()
        self.s.headers.update({"apikey": key, "Content-Type": "application/json"})
        if key.startswith("eyJ"):  # clave legacy JWT
            self.s.headers["Authorization"] = f"Bearer {key}"
        self.timeout = float(os.environ.get("SUPABASE_TIMEOUT_SECONDS", "60"))

    def _check(self, r):
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase {r.status_code}: {r.text[:300]}")
        return r

    @staticmethod
    def _fmt(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        s = str(v)
        return f'"{s}"' if any(ch in s for ch in ',()"') else s

    def _params(self, filters):
        p = {}
        for k, v in (filters or {}).items():
            if isinstance(v, (list, tuple, set)):
                p[k] = "in.(" + ",".join(self._fmt(x) for x in v) + ")"
            elif v is None:
                p[k] = "is.null"
            else:
                p[k] = "eq." + (self._fmt(v) if isinstance(v, bool) else str(v))
        return p

    def read(self, name, filters=None, cols=None):
        if filters:  # listas largas -> varias consultas (límite de largo de URL)
            for k, v in filters.items():
                if isinstance(v, (list, tuple, set)) and len(v) > 150:
                    v = list(v)
                    parts = [self.read(name, {**filters, k: v[i:i + 150]}, cols) for i in range(0, len(v), 150)]
                    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
                if isinstance(v, (list, tuple, set)) and not v:
                    return pd.DataFrame(columns=cols or columns(name))
        params = self._params(filters)
        params["select"] = ",".join(cols) if cols else "*"
        _, pk = TABLES[name]
        params["order"] = ",".join(pk)
        out, start, page = [], 0, 1000
        while True:
            r = self._check(self.s.get(f"{self.url}/{name}", params=params, timeout=self.timeout,
                                       headers={"Range-Unit": "items", "Range": f"{start}-{start + page - 1}"}))
            data = r.json()
            out += data
            if len(data) < page:
                break
            start += page
        return pd.DataFrame(out, columns=cols or columns(name)) if not out else pd.DataFrame(out)

    def _body(self, df):
        return df.to_json(orient="records", date_format="iso", force_ascii=False, double_precision=15)

    def _post(self, name, df, prefer, on_conflict=None):
        params = {"on_conflict": ",".join(on_conflict)} if on_conflict else None
        for i in range(0, len(df), BATCH):
            self._check(self.s.post(f"{self.url}/{name}", params=params, data=self._body(df.iloc[i:i + BATCH]).encode("utf-8"),
                                    headers={"Prefer": prefer}, timeout=self.timeout))

    def replace(self, name, df):
        first = TABLES[name][1][0]
        self._check(self.s.delete(f"{self.url}/{name}", params={first: "not.is.null"}, timeout=self.timeout))
        self._post(name, df, "return=minimal")

    def upsert(self, name, df, keys=None):
        self._post(name, df, "resolution=merge-duplicates,return=minimal", keys or TABLES[name][1])

    def insert(self, name, df):
        self._post(name, df, "return=minimal")

    def update(self, name, values, filters):
        body = json.dumps({k: (None if (isinstance(v, float) and np.isnan(v)) else v) for k, v in values.items()}, default=str)
        self._check(self.s.patch(f"{self.url}/{name}", params=self._params(filters), data=body.encode("utf-8"),
                                 headers={"Prefer": "return=minimal"}, timeout=self.timeout))

    def delete(self, name, filters):
        self._check(self.s.delete(f"{self.url}/{name}", params=self._params(filters), timeout=self.timeout))

    def ping(self):
        missing = []
        for t in TABLES:
            r = self.s.get(f"{self.url}/{t}", params={"select": "*", "limit": "1"}, timeout=self.timeout)
            if r.status_code >= 300:
                missing.append(f"{t} ({r.status_code})")
        if missing:
            raise RuntimeError("Conectado, pero faltan tablas o permisos: " + ", ".join(missing) +
                               ". Ejecuta db/schema_supabase.sql en el SQL Editor de Supabase.")
        return f"Supabase ({os.environ.get('SUPABASE_URL')})"


_impl = None


def _db():
    global _impl
    if _impl is None:
        _impl = _Supabase() if backend() == "supabase" else _SQLite()
    return _impl


def reset_backend():
    global _impl
    _impl = None


# =====================================================================  API pública
def read_table(name: str, filters: dict | None = None, cols: list | None = None) -> pd.DataFrame:
    try:
        return _typed(name, _db().read(name, filters, cols))
    except RuntimeError:
        raise
    except Exception as e:  # tabla inexistente en sqlite nuevo, etc.
        if backend() == "supabase":
            raise
        print(f"[store] {name}: {e}")
        return pd.DataFrame(columns=cols or (columns(name) if name in TABLES else []))


def replace_table(name: str, df: pd.DataFrame) -> None:
    _db().replace(name, _conform(name, df))


def upsert(name: str, df: pd.DataFrame, keys: list | None = None) -> None:
    if len(df):
        _db().upsert(name, _conform(name, df), keys)


def insert(name: str, df: pd.DataFrame) -> None:
    if len(df):
        _db().insert(name, _conform(name, df))


def update(name: str, values: dict, filters: dict) -> None:
    _db().update(name, values, filters)


def delete(name: str, filters: dict) -> None:
    _db().delete(name, filters)


def ping() -> str:
    return _db().ping()


# compatibilidad con el código anterior
def write_table(df: pd.DataFrame, name: str, if_exists: str = "replace") -> None:
    (insert if if_exists == "append" else replace_table)(name, df)


def apply_overrides(df: pd.DataFrame, fields: list[str], source_col: str | None = None,
                    source_map: dict | None = None) -> pd.DataFrame:
    """Aplica correcciones manuales por legacyProductId. Marca la fuente como 'manual'."""
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
    rows = pd.DataFrame([{"legacyProductId": lid, "field": field, "value": None if value is None else str(value),
                          "previousValue": None if previous is None or (isinstance(previous, float) and pd.isna(previous)) else str(previous),
                          "changedBy": user, "changedAt": now(), "comment": comment} for lid in legacy_ids])
    upsert("manual_overrides", rows)


def log_run(source_file: str, step: str, metrics: dict) -> None:
    ts = now()
    insert("run_log", pd.DataFrame([{"runAt": ts, "sourceFile": source_file, "step": step, "metric": k, "value": str(v)}
                                    for k, v in metrics.items()]))
