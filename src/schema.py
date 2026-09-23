"""Esquema único de la base de datos (fuente de verdad para SQLite y Supabase/Postgres).
Tipos: T=texto, F=decimal, I=entero, B=booleano.  `python src/schema.py` regenera db/schema_supabase.sql
"""
T, F, I, B = "text", "double precision", "bigint", "boolean"

TABLES = {
    # ---------------- control de cargas / historial (persistentes)
    "source_files": ({"fileHash": T, "fileName": T, "loadId": I, "uploadedAt": T, "uploadedBy": T, "rows": I}, ["fileHash"]),
    "loads": ({"loadId": I, "fileHash": T, "fileName": T, "startedAt": T, "finishedAt": T, "status": T, "uploadedBy": T,
               "rowsTotal": I, "rowsNew": I, "rowsChanged": I, "rowsUnchanged": I, "rowsAbsent": I, "rowsReappeared": I,
               "sitesInFile": I, "message": T}, ["loadId"]),
    "changes": ({"loadId": I, "legacyProductId": T, "siteId": T, "field": T, "oldValue": T, "newValue": T, "changeType": T},
                ["loadId", "legacyProductId", "siteId", "field"]),
    # ---------------- base acumulada producto x sitio (persistente, se hace upsert)
    "stock_rows": ({"legacyProductId": T, "siteId": T, "description": T, "descriptionOriginal": T, "productType": T,
                    "itemCategory": T, "packageType": T, "manufacturer": T, "model": T, "availabilityGroup": T,
                    "reportGroup": T, "exchangeGroup": T, "department": T, "revenueGroup": T, "siteName": T, "siteType": T,
                    "country": T, "countrySource": T, "codeCountry": T, "stockQty": F, "unitCost": F, "replacementCost": F,
                    "retailPrice": F, "totalCost": F, "canRent": B, "canSell": B, "canSubrent": B,
                    "isDeletionCandidate": B, "deletionFlagReason": T, "sourceFile": T, "rowHash": T,
                    "firstLoadId": I, "lastLoadId": I, "isPresent": B}, ["legacyProductId", "siteId"]),
    # ---------------- decisiones humanas / IA (persistentes)
    "manual_overrides": ({"legacyProductId": T, "field": T, "value": T, "previousValue": T, "changedBy": T,
                          "changedAt": T, "comment": T}, ["legacyProductId", "field"]),
    "ai_suggestions": ({"legacyProductId": T, "familyCode": T, "categoryCode": T, "manufacturer": T, "model": T,
                        "keyAttributeValue": T, "confidence": F, "reasoning": T, "aiModel": T, "createdAt": T},
                       ["legacyProductId"]),
    "duplicate_decisions": ({"pairKey": T, "decision": T, "decidedBy": T, "decidedAt": T, "comment": T}, ["pairKey"]),
    "code_registry": ({"legacyProductId": T, "globalCode": T, "familyCode": T, "categoryCode": T, "firstSeen": T,
                       "lastSeen": T}, ["legacyProductId"]),
    "new_products": ({"globalCode": T, "standardName": T, "description": T, "familyCode": T, "categoryCode": T,
                      "manufacturer": T, "model": T, "countries": T, "createdBy": T, "createdAt": T, "status": T},
                     ["globalCode"]),
    "run_log": ({"runAt": T, "sourceFile": T, "step": T, "metric": T, "value": T}, ["runAt", "step", "metric"]),
    # ---------------- derivadas (se recalculan en cada carga sobre la base acumulada)
    "products": ({"globalCode": T, "standardName": T, "familyCode": T, "familyName": T, "categoryCode": T,
                  "categoryName": T, "manufacturer": T, "model": T, "keyAttribute": T, "keyAttributeValue": T,
                  "description": T, "itemCategory": T, "productType": T, "packageType": T, "canRent": B, "canSell": B,
                  "canSubrent": B, "classificationSource": T, "manufacturerSource": T, "modelSource": T,
                  "keyAttributeSource": T, "nameSource": T, "legacyIds": T, "nLegacyIds": I, "codeCountries": T,
                  "totalStock": F, "countriesEnabled": T, "countriesWithStock": T, "status": T, "aiFields": T,
                  "needsReview": B}, ["globalCode"]),
    "product_legacy_map": ({"legacyProductId": T, "globalCode": T, "matchType": T, "description": T,
                            "descriptionOriginal": T, "codeCountry": T, "totalStock": F, "nSites": I,
                            "isDeletionCandidate": B, "deletionFlagReason": T}, ["legacyProductId"]),
    "product_country": ({"globalCode": T, "country": T, "nSitesEnabled": I, "sitesEnabled": T, "totalStock": F,
                         "nSitesWithStock": I, "sitesWithStock": T, "isAvailable": B}, ["globalCode", "country"]),
    "duplicate_candidates": ({"pairKey": T, "codeA": T, "nameA": T, "legacyA": T, "codeB": T, "nameB": T, "legacyB": T,
                              "familyCode": T, "categoryCode": T, "score": F, "reasons": T, "decision": T}, ["pairKey"]),
    "sites": ({"siteId": T, "siteName": T, "rows": I, "country": T, "countrySource": T, "siteType": T,
               "prefixTopCountry": T, "prefixShare": F, "prefixedRows": I, "checkPrefixConflict": B}, ["siteId"]),
    "taxonomy": ({"familyCode": T, "familyName": T, "categoryCode": T, "categoryName": T, "keyAttribute": T},
                 ["familyCode", "categoryCode"]),
}
DERIVED = ["products", "product_legacy_map", "product_country", "duplicate_candidates", "sites", "taxonomy"]


def columns(table: str) -> list[str]:
    return list(TABLES[table][0])


def postgres_ddl() -> str:
    out = ["-- Esquema RLA Maestro de Productos para Supabase (Postgres).",
           "-- Ejecutar completo en Supabase > SQL Editor. Es idempotente (se puede correr más de una vez).", ""]
    for name, (cols, pk) in TABLES.items():
        body = ",\n".join(f'  "{c}" {t}' for c, t in cols.items())
        pkc = ", ".join(f'"{c}"' for c in pk)
        out.append(f'create table if not exists public.{name} (\n{body},\n  primary key ({pkc})\n);')
    out += ["", 'create index if not exists ix_stock_country on public.stock_rows ("country");',
            'create index if not exists ix_map_global on public.product_legacy_map ("globalCode");',
            'create index if not exists ix_pc_country on public.product_country ("country");',
            'create index if not exists ix_changes_prod on public.changes ("legacyProductId");', "",
            "-- Seguridad (MVP): RLS activado. Con la clave SECRETA (sb_secret_...) el backend omite RLS y no se necesitan políticas.",
            "-- Con la clave PUBLICABLE (sb_publishable_...) se requieren estas políticas de acceso total para el rol anon.",
            "-- En producción: usar la clave secreta solo en el servidor y eliminar las políticas 'mvp_anon_all'."]
    for name in TABLES:
        out.append(f"alter table public.{name} enable row level security;")
        out.append(f'drop policy if exists mvp_anon_all on public.{name};')
        out.append(f"create policy mvp_anon_all on public.{name} for all to anon, authenticated using (true) with check (true);")
    out.append("grant select, insert, update, delete on all tables in schema public to anon, authenticated;")
    out.append("notify pgrst, 'reload schema';")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "db" / "schema_supabase.sql"
    p.write_text(postgres_ddl(), encoding="utf-8")
    print(f"Escrito {p}")
