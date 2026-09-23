"""PASO 2 — Asignación de país y tipo de sitio.

Prioridad: 1) config/sites_manual.csv (decisión humana)
           2) config/country_rules.csv (regex sobre nombre de sitio)
           3) inferencia por prefijo de código (CO/PE/PA/CL/MX) de los productos del sitio
           4) SIN_ASIGNAR -> queda en reporte para revisión
También deriva el país de origen del código (prefijo) de cada producto.
Salida: data/interim/02_country.parquet + reports/02_sites.csv
"""
from __future__ import annotations
import re
import pandas as pd
from common import load_config, norm_key, INTERIM, REPORTS

PREFIX_COUNTRY = {"CO": "COLOMBIA", "PE": "PERU", "PA": "PANAMA", "CL": "CHILE", "MX": "MEXICO"}
MIN_SHARE, MIN_ROWS, MIN_COVERAGE = 0.6, 10, 0.15


def code_country(code: pd.Series) -> pd.Series:
    pref = code.astype("string").str.extract(r"^(CO|PE|PA|CL|MX)(?=[ _-]?[A-Z]{2,})", flags=re.I)[0].str.upper()
    return pref.map(PREFIX_COUNTRY)


def first_match(name: str, rules: pd.DataFrame, col: str):
    n = norm_key(name).replace(" h c ", " h.c. ")
    raw = str(name).lower()
    for r in rules.itertuples():
        if re.search(r.pattern, raw) or re.search(r.pattern, n):
            return getattr(r, col)
    return None


def run(df: pd.DataFrame | None = None) -> pd.DataFrame:
    if df is None:
        df = pd.read_parquet(INTERIM / "01_clean.parquet")
    crules = load_config("country_rules.csv").assign(priority=lambda x: x.priority.astype(int)).sort_values("priority")
    trules = load_config("site_type_rules.csv").assign(priority=lambda x: x.priority.astype(int)).sort_values("priority")
    manual = load_config("sites_manual.csv").set_index("siteId")

    df["codeCountry"] = code_country(df["legacyProductId"])
    sites = df.groupby(["siteId", "siteName"], dropna=True).size().rename("rows").reset_index()

    # distribución de prefijos por sitio (evidencia para inferencia y validación)
    pref = df.dropna(subset=["codeCountry"]).groupby(["siteId", "codeCountry"]).size().unstack(fill_value=0)
    out = []
    for s in sites.itertuples():
        country, source = None, None
        stype = first_match(s.siteName, trules, "siteType")
        if s.siteId in manual.index and manual.loc[s.siteId, "country"]:
            country, source = manual.loc[s.siteId, "country"], "manual"
            stype = manual.loc[s.siteId, "siteType"] or stype
        if country is None:
            country = first_match(s.siteName, crules, "country")
            source = "regla_nombre" if country else None
        dist = pref.loc[s.siteId] if s.siteId in pref.index else pd.Series(dtype=int)
        top, share, n = (dist.idxmax(), dist.max() / dist.sum(), int(dist.sum())) if len(dist) and dist.sum() else (None, 0, 0)
        if country is None and n >= MIN_ROWS and share >= MIN_SHARE and n / s.rows >= MIN_COVERAGE:
            country, source = top, "inferido_prefijo"
        out.append({"siteId": s.siteId, "siteName": s.siteName, "rows": s.rows,
                    "country": country or "SIN_ASIGNAR", "countrySource": source or "sin_evidencia",
                    "siteType": stype, "prefixTopCountry": top, "prefixShare": round(share, 2), "prefixedRows": n,
                    "checkPrefixConflict": bool(country and top and n >= MIN_ROWS and share >= MIN_SHARE and top != country)})
    sites = pd.DataFrame(out)
    df = df.merge(sites[["siteId", "country", "countrySource", "siteType"]], on="siteId", how="left")
    df["country"] = df["country"].fillna("SIN_ASIGNAR")
    df["countrySource"] = df["countrySource"].fillna("sin_sitio")
    df["siteType"] = df["siteType"].fillna("SIN_SITIO")

    df.to_parquet(INTERIM / "02_country.parquet", index=False)
    sites.sort_values("rows", ascending=False).to_csv(REPORTS / "02_sites.csv", index=False, encoding="utf-8-sig")
    print(f"[Paso 2] {len(sites)} sitios: {sites.country.value_counts().to_dict()}")
    print(f"         fuente: {sites.countrySource.value_counts().to_dict()}")
    print(f"         conflictos nombre vs prefijo: {int(sites.checkPrefixConflict.sum())}")
    return df


if __name__ == "__main__":
    run()
