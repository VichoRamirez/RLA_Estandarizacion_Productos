"""PASO 7 — Publicación: escribe las tablas derivadas en la BD (Supabase o SQLite) y exporta el Excel."""
from __future__ import annotations
import pandas as pd
from common import load_config, INTERIM, OUTPUT
import store

REPLACE = ["products", "product_legacy_map", "product_country", "duplicate_candidates"]


def run(out: dict | None = None, source_file: str = "") -> None:
    if out is None:
        out = {t: pd.read_parquet(INTERIM / f"04_{t}.parquet") for t in REPLACE}
    for name in REPLACE:
        store.replace_table(name, out[name])
    store.replace_table("taxonomy", load_config("taxonomy.csv"))
    sites_path = OUTPUT / "reports" / "02_sites.csv"
    if sites_path.exists():
        store.upsert("sites", pd.read_csv(sites_path, dtype={"siteId": str}))
    sites = store.read_table("sites")
    p = out["products"]
    metrics = {"legacyIds": int(out["product_legacy_map"].shape[0]), "globalCodes": int(p.shape[0]),
               "clasificados_pct": round(float((p.classificationSource != "sin_clasificar").mean() * 100), 1),
               "sin_clasificar": int((p.classificationSource == "sin_clasificar").sum()),
               "con_campos_IA": int((p.aiFields != "").sum()),
               "pares_duplicado": int(out["duplicate_candidates"].shape[0]),
               "sitios_sin_pais": int((sites.country == "SIN_ASIGNAR").sum()) if len(sites) else 0}
    store.log_run(source_file, "pipeline", metrics)

    xl = OUTPUT / "maestro_productos_estandarizado.xlsx"
    with pd.ExcelWriter(xl, engine="openpyxl") as w:
        p.to_excel(w, sheet_name="maestro_productos", index=False)
        out["product_country"].to_excel(w, sheet_name="disponibilidad_pais", index=False)
        out["product_legacy_map"].to_excel(w, sheet_name="equivalencias_legacy", index=False)
        out["duplicate_candidates"].to_excel(w, sheet_name="candidatos_duplicado", index=False)
        p[p.needsReview].to_excel(w, sheet_name="cola_revision", index=False)
        sites.to_excel(w, sheet_name="sitios_pais", index=False)
    print(f"[Paso 7] BD actualizada (backend: {store.backend()}) y Excel exportado ({xl.name}). Métricas: {metrics}")


if __name__ == "__main__":
    run()
