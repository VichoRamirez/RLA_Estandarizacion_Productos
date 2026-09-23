"""PASO 2b — Registro del archivo y consolidación en la base acumulada (stock_rows).

- Huella SHA-256 del contenido: si el archivo ya se cargó (aunque tenga otro nombre) se detiene.
- Cada fila (producto x sitio) se compara contra la base por (legacyProductId, siteId) y una huella de sus valores:
    nueva -> insert | cambió -> update + historial en `changes` | idéntica -> no se toca (no se duplica)
    ausente -> solo si su SITIO viene en el archivo y la fila no: se marca isPresent=false (no se borra)
  Filas de sitios que no vienen en el archivo no se tocan (permite cargar archivos parciales por país).
"""
from __future__ import annotations
import hashlib
from datetime import datetime
from pathlib import Path

import pandas as pd
import store
from schema import columns

NO_SITE = "(SIN_SITIO)"
KEY = ["legacyProductId", "siteId"]
TRACK = ["stockQty", "unitCost", "replacementCost", "retailPrice", "description", "manufacturer", "model",
         "canRent", "canSell", "canSubrent", "siteName", "productType", "itemCategory"]


class AlreadyLoaded(Exception):
    pass


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_file(path: Path) -> tuple[str, pd.DataFrame]:
    h = file_hash(path)
    prev = store.read_table("source_files", {"fileHash": h})
    return h, prev


def _value_cols():
    return [c for c in columns("stock_rows") if c not in KEY + ["rowHash", "firstLoadId", "lastLoadId", "isPresent", "sourceFile"]]


def _row_hash(df: pd.DataFrame) -> pd.Series:
    vals = df[_value_cols()].astype(str)
    return pd.util.hash_pandas_object(vals, index=False).astype(str)


def _fmt(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
        return None
    return str(v)


def run(df: pd.DataFrame, path: Path, user: str = "pipeline", force: bool = False) -> tuple[pd.DataFrame, dict]:
    fh, prev = check_file(path)
    if len(prev) and not force:
        p = prev.iloc[0]
        raise AlreadyLoaded(f"El archivo ya fue cargado el {p.uploadedAt} como '{p.fileName}' (carga {p.loadId}). "
                            "No se volvió a procesar.")
    load_id = int(datetime.now().strftime("%Y%m%d%H%M%S"))
    started = store.now()

    new = df.copy()
    new["siteId"] = new["siteId"].fillna(NO_SITE).astype(str)
    new = new.drop_duplicates(KEY, keep="last")
    for c in columns("stock_rows"):
        if c not in new.columns:
            new[c] = None
    new = store._conform("stock_rows", new)
    new["rowHash"] = _row_hash(new)

    old = store.read_table("stock_rows")
    old = store._conform("stock_rows", old) if len(old) else old
    sites_in_file = set(new.siteId)
    if len(old):
        m = new.merge(old[KEY + ["rowHash", "firstLoadId", "isPresent"]], on=KEY, how="left", suffixes=("", "_old"),
                      indicator=True)
        is_new = m["_merge"] == "left_only"
        same = ~is_new & (m["rowHash"] == m["rowHash_old"])
        reappeared = same & (m["isPresent_old"] == False)  # noqa: E712
        changed = ~is_new & ~same
        m["firstLoadId"] = m["firstLoadId_old"].where(~is_new, load_id)
    else:
        m = new.copy()
        is_new = pd.Series(True, index=m.index)
        changed = reappeared = same = pd.Series(False, index=m.index)
        m["firstLoadId"] = load_id
    m["lastLoadId"] = load_id
    m["isPresent"] = True
    to_write = m[is_new | changed | reappeared][columns("stock_rows")]

    # historial de cambios campo a campo
    chg = []
    if changed.any():
        oldi = old.set_index(KEY)
        for r in m[changed].itertuples(index=False):
            o = oldi.loc[(r.legacyProductId, r.siteId)]
            for f in TRACK:
                a, b = _fmt(o[f]), _fmt(getattr(r, f))
                if a != b:
                    chg.append({"loadId": load_id, "legacyProductId": r.legacyProductId, "siteId": r.siteId,
                                "field": f, "oldValue": a, "newValue": b, "changeType": "UPDATE"})
    for r in m[is_new].itertuples(index=False):
        if len(old):
            chg.append({"loadId": load_id, "legacyProductId": r.legacyProductId, "siteId": r.siteId,
                        "field": "*", "oldValue": None, "newValue": "fila nueva", "changeType": "INSERT"})

    # ausentes: filas de sitios presentes en el archivo que ya no vienen
    absent = pd.DataFrame()
    if len(old):
        key_new = set(zip(new.legacyProductId, new.siteId))
        cand = old[old.siteId.isin(sites_in_file) & (old.isPresent != False)]  # noqa: E712
        absent = cand[[k not in key_new for k in zip(cand.legacyProductId, cand.siteId)]].copy()
        if len(absent):
            absent["isPresent"] = False
            for r in absent.itertuples(index=False):
                chg.append({"loadId": load_id, "legacyProductId": r.legacyProductId, "siteId": r.siteId,
                            "field": "*", "oldValue": "presente", "newValue": "ausente en la carga", "changeType": "ABSENT"})

    stats = {"loadId": load_id, "fileHash": fh, "fileName": path.name, "startedAt": started, "uploadedBy": user,
             "rowsTotal": len(new), "rowsNew": int(is_new.sum()), "rowsChanged": int(changed.sum()),
             "rowsUnchanged": int((same & ~reappeared).sum()), "rowsAbsent": len(absent),
             "rowsReappeared": int(reappeared.sum()), "sitesInFile": len(sites_in_file)}

    store.upsert("stock_rows", pd.concat([to_write, absent[columns("stock_rows")]]) if len(absent) else to_write)
    if chg:
        store.upsert("changes", pd.DataFrame(chg))
    stats.update(finishedAt=store.now(), status="OK",
                 message=f"{stats['rowsNew']} nuevas, {stats['rowsChanged']} actualizadas, "
                         f"{stats['rowsUnchanged']} sin cambios (no se duplican), {stats['rowsAbsent']} ausentes, "
                         f"{stats['rowsReappeared']} reaparecen")
    store.upsert("loads", pd.DataFrame([stats]))
    store.upsert("source_files", pd.DataFrame([{"fileHash": fh, "fileName": path.name, "loadId": load_id,
                                                "uploadedAt": started, "uploadedBy": user, "rows": len(new)}]))
    print(f"[Paso 2b] Carga {load_id}: {stats['message']}")

    # base acumulada vigente
    if len(old):
        in_file = set(zip(m.legacyProductId, m.siteId))
        keep_old = old[[k not in in_file for k in zip(old.legacyProductId, old.siteId)]]
        if len(absent):
            ab = set(zip(absent.legacyProductId, absent.siteId))
            keep_old = keep_old[[k not in ab for k in zip(keep_old.legacyProductId, keep_old.siteId)]]
            keep_old = pd.concat([keep_old, absent[columns("stock_rows")]])
        acc = pd.concat([keep_old, m[columns("stock_rows")]], ignore_index=True)
    else:
        acc = m[columns("stock_rows")].reset_index(drop=True)
    return acc, stats


def current_base() -> pd.DataFrame:
    return store._conform("stock_rows", store.read_table("stock_rows"))
