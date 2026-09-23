"""PASO 1 — Limpieza.

- Renombra columnas a camelCase en inglés según config/column_map.csv
  (columnas nuevas no mapeadas se convierten automáticamente y se reportan).
- Tipifica: números con formato latino, booleanos (RENTABLE/NOTRENTABLE...).
- Normaliza texto: espacios, comillas dobles, tildes invertidas (ò -> ó).
- Corrige faltas ortográficas por mapeo (palabras en descripciones, fabricantes,
  valores de grupos) y anula placeholders (S/N, S/M, XXXXXX...).
- Marca candidatos a eliminación (descripciones 'eliminar', vacías, etc.).
Salida: data/interim/01_clean.parquet + reports/01_*.csv
"""
from __future__ import annotations
import re
import sys
from pathlib import Path
import pandas as pd
from common import (load_config, parse_latin_number, to_camel, compact_key,
                    strip_accents, latest_input, INTERIM, REPORTS)

GROUP_COLS = ["availabilityGroup", "reportGroup", "department", "revenueGroup",
              "exchangeGroup", "inventoryGroup", "taxGroup"]
log: list[dict] = []


def record(column, before, after, rule, n):
    if n:
        log.append({"column": column, "before": before, "after": after, "rule": rule, "rows": int(n)})


# ---------------------------------------------------------------- columnas
def rename_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    cmap = load_config("column_map.csv")
    by_key = {compact_key(r.sourceColumn): r for r in cmap.itertuples()}
    rows, rename, drop = [], {}, []
    for col in df.columns:
        r = by_key.get(compact_key(col))
        if r is None:
            tgt = to_camel(col)
            rows.append({"sourceColumn": col, "targetColumn": tgt, "status": "NUEVA (no mapeada)", "kept": True})
            rename[col] = tgt
        elif r.keep == "n":
            rows.append({"sourceColumn": col, "targetColumn": r.targetColumn, "status": "descartada", "kept": False})
            drop.append(col)
        else:
            rows.append({"sourceColumn": col, "targetColumn": r.targetColumn, "status": "mapeada", "kept": True})
            rename[col] = r.targetColumn
    present = {compact_key(c) for c in df.columns}
    for r in cmap.itertuples():
        if compact_key(r.sourceColumn) not in present and r.keep == "y":
            rows.append({"sourceColumn": r.sourceColumn, "targetColumn": r.targetColumn,
                         "status": "FALTANTE en archivo (se crea vacía)", "kept": True})
            df[r.sourceColumn] = pd.NA
            rename[r.sourceColumn] = r.targetColumn
    df = df.drop(columns=drop).rename(columns=rename)
    return df, pd.DataFrame(rows)


# ---------------------------------------------------------------- tipos
def cast_types(df: pd.DataFrame) -> pd.DataFrame:
    cmap = load_config("column_map.csv")
    bmap = {r.value: r.bool == "true" for r in load_config("bool_map.csv").itertuples()}
    for r in cmap.itertuples():
        c = r.targetColumn
        if c not in df.columns:
            continue
        if r.dataType == "number":
            before = df[c].notna().sum()
            df[c] = parse_latin_number(df[c])
            record(c, "texto formato latino", "número", "tipado", before)
        elif r.dataType == "bool":
            s = df[c].astype("string").str.strip().str.upper()
            df[c] = s.map(bmap).astype("boolean").fillna(False).astype(bool)
            record(c, "etiqueta texto", "booleano", "tipado", len(s))
    return df


# ---------------------------------------------------------------- texto
def clean_text(s: pd.Series) -> pd.Series:
    s = s.astype("string")
    s = s.str.replace('""', '"', regex=False)
    s = s.str.replace("ò", "ó").str.replace("à", "á").str.replace("è", "é").str.replace("ì", "í").str.replace("ù", "ú")
    s = s.str.replace(r"\s+", " ", regex=True).str.strip()
    return s.replace("", pd.NA)


def fix_words(desc: pd.Series) -> pd.Series:
    wc = load_config("word_corrections.csv")
    out = desc.copy()
    for r in wc.itertuples():
        pat = re.compile(rf"\b{re.escape(r.wrong)}\b", re.IGNORECASE)

        def repl(m, good=r.correct):
            w = m.group(0)
            if w.isupper():
                return good.upper()
            if w[:1].isupper():
                return good[:1].upper() + good[1:]
            return good
        mask = out.fillna("").str.contains(pat)
        if mask.any():
            out.loc[mask] = out.loc[mask].str.replace(pat, repl, regex=True)
            record("description", r.wrong, r.correct, "ortografía (word_corrections.csv)", mask.sum())
    return out


def apply_placeholders(df: pd.DataFrame) -> pd.DataFrame:
    ph = load_config("placeholder_values.csv")
    for r in ph.itertuples():
        cols = [c for c in df.select_dtypes(include=["object", "string"]).columns] if r.column == "*" else [r.column]
        for c in cols:
            if c not in df.columns:
                continue
            mask = df[c].astype("string").str.upper() == r.value.upper()
            n = mask.sum()
            if n:
                df.loc[mask, c] = pd.NA if r.action == "null" else r.action
                record(c, r.value, "(vacío)" if r.action == "null" else r.action, "placeholder", n)
    return df


def normalize_manufacturer(df: pd.DataFrame) -> pd.DataFrame:
    al = {compact_key(r.alias): r.canonical for r in load_config("manufacturer_aliases.csv").itertuples()}
    m = df["manufacturer"].astype("string").str.upper()
    key = m.map(lambda v: compact_key(v) if pd.notna(v) else None)
    new = m.copy()
    hit = key.isin(al.keys())
    new[hit] = key[hit].map(al)
    changed = (new != m) & new.notna()
    for (b, a), n in pd.DataFrame({"b": m[changed], "a": new[changed]}).value_counts().items():
        record("manufacturer", b, a, "alias fabricante (manufacturer_aliases.csv)", n)
    df["manufacturer"] = new.map(lambda v: strip_accents(v) if pd.notna(v) else v)
    df["model"] = df["model"].astype("string").str.upper()
    return df


def normalize_groups(df: pd.DataFrame) -> pd.DataFrame:
    vc = load_config("value_corrections.csv")
    for c in GROUP_COLS:
        if c not in df.columns:
            continue
        before = df[c].astype("string")
        s = before.str.upper().map(lambda v: strip_accents(v) if pd.notna(v) else v)
        s = s.str.replace(r"\s+", " ", regex=True).str.strip()
        n = ((s != before) & s.notna()).sum()
        record(c, "mayúsc./tildes variables", "MAYÚSCULAS sin tilde", "formato", n)
        for r in vc[vc.column == c].itertuples():
            mask = s == r.wrong
            if mask.any():
                s[mask] = r.correct
                record(c, r.wrong, r.correct, "ortografía (value_corrections.csv)", mask.sum())
        df[c] = s
    return df


def flag_deletions(df: pd.DataFrame) -> pd.DataFrame:
    pats = load_config("deletion_patterns.csv")
    reason = pd.Series(pd.NA, index=df.index, dtype="string")
    d = df["description"].fillna("")
    for r in pats.itertuples():
        mask = d.str.contains(r.pattern, flags=re.IGNORECASE, regex=True) & reason.isna()
        reason[mask] = r.reason
    df["deletionFlagReason"] = reason
    df["isDeletionCandidate"] = reason.notna()
    return df


# ---------------------------------------------------------------- main
def run(input_path: Path | None = None) -> pd.DataFrame:
    log.clear()
    input_path = Path(input_path) if input_path else latest_input()
    raw = pd.read_excel(input_path, dtype=object)
    n0 = len(raw)
    df, colrep = rename_columns(raw)
    df = df.dropna(how="all")
    df = cast_types(df)
    # el código legacy es clave en R2: no se modifica (solo espacios en los extremos)
    legacy = df["legacyProductId"].astype("string").str.strip()
    for c in df.columns:
        if df[c].dtype == object or str(df[c].dtype) == "string":
            df[c] = clean_text(df[c])
    df["legacyProductId"] = legacy
    df["descriptionOriginal"] = df["description"]
    df = apply_placeholders(df)
    df["description"] = fix_words(df["description"])
    df = normalize_manufacturer(df)
    df = normalize_groups(df)
    df = flag_deletions(df)
    df["sourceFile"] = input_path.name
    df["sourceRow"] = df.index + 2

    df.to_parquet(INTERIM / "01_clean.parquet", index=False)
    colrep.to_csv(REPORTS / "01_column_mapping.csv", index=False, encoding="utf-8-sig")
    lg = pd.DataFrame(log)
    lg.to_csv(REPORTS / "01_cleaning_log.csv", index=False, encoding="utf-8-sig")
    print(f"[Paso 1] {input_path.name}: {n0} filas leídas -> {len(df)} filas, {df.shape[1]} columnas")
    print(f"         columnas: {colrep.status.value_counts().to_dict()}")
    print(f"         correcciones registradas: {len(lg)} reglas, {int(lg.rows.sum()) if len(lg) else 0} celdas")
    print(f"         candidatos a eliminación: {int(df.isDeletionCandidate.sum())} filas")
    return df


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
