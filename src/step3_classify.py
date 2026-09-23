"""PASO 3 — Clasificación en taxonomía estándar (Familia > Categoría).

Orden de resolución por producto (legacyProductId):
  1) override manual guardado en la BD (tabla manual_overrides)   -> source = manual
  2) reglas regex sobre la descripción (config/category_rules.csv) -> source = regla_descripcion
  3) reglas sobre los grupos legacy (exchange/report/availability) -> source = regla_grupo
  4) departamento legacy (solo familia, categoría ACC/OTR)          -> source = regla_departamento
  5) sugerencia IA aceptada (tabla ai_suggestions, si existe)       -> source = ia
  6) sin clasificar                                                 -> cola de revisión
Salida: data/interim/03_classified.parquet + reports/03_classification_summary.csv
"""
from __future__ import annotations
import re
import pandas as pd
from common import load_config, norm_key, INTERIM, REPORTS
import store


def compile_rules():
    r = load_config("category_rules.csv")
    r["priority"] = r["priority"].astype(float)
    r = r.sort_values("priority")
    r["rx"] = r["pattern"].map(lambda p: re.compile(p, re.I))
    cab = load_config("cable_subtypes.csv")
    cab["rx"] = cab["pattern"].map(lambda p: re.compile(p, re.I))
    return r, cab


def classify_row(fields: dict, rules, cab):
    for r in rules.itertuples():
        text = fields.get(r.field, "")
        if text and r.rx.search(text):
            cat = r.categoryCode
            if cat == "_AUTO":  # subtipo de cable
                cat = next((c.categoryCode for c in cab.itertuples() if c.rx.search(fields["description"])), "ADP")
            src = {"description": "regla_descripcion", "groups": "regla_grupo", "department": "regla_departamento"}.get(r.field, "regla_tipo")
            return r.familyCode, cat, src, f"{r.priority:g}"
    return None, None, "sin_clasificar", None


def run(df: pd.DataFrame | None = None) -> pd.DataFrame:
    if df is None:
        df = pd.read_parquet(INTERIM / "02_country.parquet")
    rules, cab = compile_rules()
    tax = load_config("taxonomy.csv")
    prod = df.drop_duplicates("legacyProductId")[["legacyProductId", "description", "productType", "packageType",
                                                   "exchangeGroup", "reportGroup", "availabilityGroup", "department"]]
    res = []
    for p in prod.itertuples(index=False):
        f = {"description": norm_key(p.description),
             "productType": ("" if pd.isna(p.productType) else str(p.productType)), "packageType": ("" if pd.isna(p.packageType) else str(p.packageType)),
             "groups": norm_key(" | ".join(str(x) for x in (p.exchangeGroup, p.reportGroup, p.availabilityGroup) if pd.notna(x))),
             "department": norm_key(p.department)}
        fam, cat, src, rule = classify_row(f, rules, cab)
        res.append((p.legacyProductId, fam, cat, src, rule))
    cls = pd.DataFrame(res, columns=["legacyProductId", "familyCode", "categoryCode", "classificationSource", "classificationRule"])

    # IA (sugerencias persistidas) solo para lo no clasificado por reglas
    ai = store.read_table("ai_suggestions")
    if len(ai):
        ai = ai.set_index("legacyProductId")
        m = cls.classificationSource.eq("sin_clasificar") & cls.legacyProductId.isin(ai.index)
        idx = cls.loc[m, "legacyProductId"]
        cls.loc[m, "familyCode"] = idx.map(ai["familyCode"]).values
        cls.loc[m, "categoryCode"] = idx.map(ai["categoryCode"]).values
        cls.loc[m, "classificationSource"] = "ia"
    # overrides manuales (siempre ganan)
    cls = store.apply_overrides(cls, ["familyCode", "categoryCode"], source_col="classificationSource")

    cls = cls.merge(tax[["familyCode", "familyName", "categoryCode", "categoryName", "keyAttribute"]],
                    on=["familyCode", "categoryCode"], how="left")
    df = df.drop(columns=[c for c in cls.columns if c != "legacyProductId" and c in df.columns]).merge(cls, on="legacyProductId", how="left")
    df.to_parquet(INTERIM / "03_classified.parquet", index=False)

    summ = cls.groupby(["classificationSource"]).size().rename("products").reset_index()
    summ.to_csv(REPORTS / "03_classification_summary.csv", index=False, encoding="utf-8-sig")
    cls.groupby(["familyName", "categoryName"], dropna=False).size().rename("products").reset_index() \
       .to_csv(REPORTS / "03_products_by_category.csv", index=False, encoding="utf-8-sig")
    n = len(cls)
    print(f"[Paso 3] {n} productos: " + ", ".join(f"{r.classificationSource}={r.products} ({r.products/n:.0%})" for r in summ.itertuples()))
    return df


if __name__ == "__main__":
    run()
