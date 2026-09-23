"""PASO 7 — Publicación: escribe las tablas en la BD SQLite y exporta el Excel de salida."""
from __future__ import annotations
import pandas as pd
from common import load_config, INTERIM, OUTPUT
import store

TABLES = ["products", "product_legacy_map", "product_site_stock", "product_country", "duplicate_candidates"]


def run(out: dict | None = None, source_file: str = "") -> None:
    if out is None:
        out = {t: pd.read_parquet(INTERIM / f"04_{t}.parquet") for t in TABLES}
    sites = pd.read_csv(OUTPUT / "reports" / "02_sites.csv")
    out = dict(out, sites=sites, taxonomy=load_config("taxonomy.csv"))
    for name, df in out.items():
        store.write_table(df, name)
    with store.connect() as con:
        con.executescript("""
          CREATE INDEX IF NOT EXISTS ix_pss_code ON product_site_stock(globalCode);
          CREATE INDEX IF NOT EXISTS ix_pc_code ON product_country(globalCode);
          CREATE INDEX IF NOT EXISTS ix_map_code ON product_legacy_map(globalCode);""")
    p = out["products"]
    metrics = {"legacyIds": int(out["product_legacy_map"].shape[0]), "globalCodes": int(p.shape[0]),
               "clasificados_pct": round(float((p.classificationSource != "sin_clasificar").mean() * 100), 1),
               "sin_clasificar": int((p.classificationSource == "sin_clasificar").sum()),
               "con_campos_IA": int((p.aiFields != "").sum()),
               "pares_duplicado": int(out["duplicate_candidates"].shape[0]),
               "sitios_sin_pais": int((sites.country == "SIN_ASIGNAR").sum())}
    store.log_run(source_file, "pipeline", metrics)

    xl = OUTPUT / "maestro_productos_estandarizado.xlsx"
    with pd.ExcelWriter(xl, engine="openpyxl") as w:
        p.to_excel(w, "maestro_productos", index=False)
        out["product_country"].to_excel(w, "disponibilidad_pais", index=False)
        out["product_legacy_map"].to_excel(w, "equivalencias_legacy", index=False)
        out["duplicate_candidates"].to_excel(w, "candidatos_duplicado", index=False)
        p[p.needsReview].to_excel(w, "cola_revision", index=False)
        sites.to_excel(w, "sitios_pais", index=False)
        out["taxonomy"].to_excel(w, "taxonomia", index=False)
    print(f"[Paso 7] BD actualizada ({store.DB_PATH.name}) y Excel exportado ({xl.name}). Métricas: {metrics}")


if __name__ == "__main__":
    run()
