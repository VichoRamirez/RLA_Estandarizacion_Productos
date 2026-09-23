-- Esquema RLA Maestro de Productos para Supabase (Postgres).
-- Ejecutar completo en Supabase > SQL Editor. Es idempotente (se puede correr más de una vez).

create table if not exists public.source_files (
  "fileHash" text,
  "fileName" text,
  "loadId" bigint,
  "uploadedAt" text,
  "uploadedBy" text,
  "rows" bigint,
  primary key ("fileHash")
);
create table if not exists public.loads (
  "loadId" bigint,
  "fileHash" text,
  "fileName" text,
  "startedAt" text,
  "finishedAt" text,
  "status" text,
  "uploadedBy" text,
  "rowsTotal" bigint,
  "rowsNew" bigint,
  "rowsChanged" bigint,
  "rowsUnchanged" bigint,
  "rowsAbsent" bigint,
  "rowsReappeared" bigint,
  "sitesInFile" bigint,
  "message" text,
  primary key ("loadId")
);
create table if not exists public.changes (
  "loadId" bigint,
  "legacyProductId" text,
  "siteId" text,
  "field" text,
  "oldValue" text,
  "newValue" text,
  "changeType" text,
  primary key ("loadId", "legacyProductId", "siteId", "field")
);
create table if not exists public.stock_rows (
  "legacyProductId" text,
  "siteId" text,
  "description" text,
  "descriptionOriginal" text,
  "productType" text,
  "itemCategory" text,
  "packageType" text,
  "manufacturer" text,
  "model" text,
  "availabilityGroup" text,
  "reportGroup" text,
  "exchangeGroup" text,
  "department" text,
  "revenueGroup" text,
  "siteName" text,
  "siteType" text,
  "country" text,
  "countrySource" text,
  "codeCountry" text,
  "stockQty" double precision,
  "unitCost" double precision,
  "replacementCost" double precision,
  "retailPrice" double precision,
  "totalCost" double precision,
  "canRent" boolean,
  "canSell" boolean,
  "canSubrent" boolean,
  "isDeletionCandidate" boolean,
  "deletionFlagReason" text,
  "sourceFile" text,
  "rowHash" text,
  "firstLoadId" bigint,
  "lastLoadId" bigint,
  "isPresent" boolean,
  primary key ("legacyProductId", "siteId")
);
create table if not exists public.manual_overrides (
  "legacyProductId" text,
  "field" text,
  "value" text,
  "previousValue" text,
  "changedBy" text,
  "changedAt" text,
  "comment" text,
  primary key ("legacyProductId", "field")
);
create table if not exists public.ai_suggestions (
  "legacyProductId" text,
  "familyCode" text,
  "categoryCode" text,
  "manufacturer" text,
  "model" text,
  "keyAttributeValue" text,
  "confidence" double precision,
  "reasoning" text,
  "aiModel" text,
  "createdAt" text,
  primary key ("legacyProductId")
);
create table if not exists public.duplicate_decisions (
  "pairKey" text,
  "decision" text,
  "decidedBy" text,
  "decidedAt" text,
  "comment" text,
  primary key ("pairKey")
);
create table if not exists public.code_registry (
  "legacyProductId" text,
  "globalCode" text,
  "familyCode" text,
  "categoryCode" text,
  "firstSeen" text,
  "lastSeen" text,
  primary key ("legacyProductId")
);
create table if not exists public.new_products (
  "globalCode" text,
  "standardName" text,
  "description" text,
  "familyCode" text,
  "categoryCode" text,
  "manufacturer" text,
  "model" text,
  "countries" text,
  "createdBy" text,
  "createdAt" text,
  "status" text,
  primary key ("globalCode")
);
create table if not exists public.run_log (
  "runAt" text,
  "sourceFile" text,
  "step" text,
  "metric" text,
  "value" text,
  primary key ("runAt", "step", "metric")
);
create table if not exists public.products (
  "globalCode" text,
  "standardName" text,
  "familyCode" text,
  "familyName" text,
  "categoryCode" text,
  "categoryName" text,
  "manufacturer" text,
  "model" text,
  "keyAttribute" text,
  "keyAttributeValue" text,
  "description" text,
  "itemCategory" text,
  "productType" text,
  "packageType" text,
  "canRent" boolean,
  "canSell" boolean,
  "canSubrent" boolean,
  "classificationSource" text,
  "manufacturerSource" text,
  "modelSource" text,
  "keyAttributeSource" text,
  "nameSource" text,
  "legacyIds" text,
  "nLegacyIds" bigint,
  "codeCountries" text,
  "totalStock" double precision,
  "countriesEnabled" text,
  "countriesWithStock" text,
  "status" text,
  "aiFields" text,
  "needsReview" boolean,
  primary key ("globalCode")
);
create table if not exists public.product_legacy_map (
  "legacyProductId" text,
  "globalCode" text,
  "matchType" text,
  "description" text,
  "descriptionOriginal" text,
  "codeCountry" text,
  "totalStock" double precision,
  "nSites" bigint,
  "isDeletionCandidate" boolean,
  "deletionFlagReason" text,
  primary key ("legacyProductId")
);
create table if not exists public.product_country (
  "globalCode" text,
  "country" text,
  "nSitesEnabled" bigint,
  "sitesEnabled" text,
  "totalStock" double precision,
  "nSitesWithStock" bigint,
  "sitesWithStock" text,
  "isAvailable" boolean,
  primary key ("globalCode", "country")
);
create table if not exists public.duplicate_candidates (
  "pairKey" text,
  "codeA" text,
  "nameA" text,
  "legacyA" text,
  "codeB" text,
  "nameB" text,
  "legacyB" text,
  "familyCode" text,
  "categoryCode" text,
  "score" double precision,
  "reasons" text,
  "decision" text,
  primary key ("pairKey")
);
create table if not exists public.sites (
  "siteId" text,
  "siteName" text,
  "rows" bigint,
  "country" text,
  "countrySource" text,
  "siteType" text,
  "prefixTopCountry" text,
  "prefixShare" double precision,
  "prefixedRows" bigint,
  "checkPrefixConflict" boolean,
  primary key ("siteId")
);
create table if not exists public.taxonomy (
  "familyCode" text,
  "familyName" text,
  "categoryCode" text,
  "categoryName" text,
  "keyAttribute" text,
  primary key ("familyCode", "categoryCode")
);

create index if not exists ix_stock_country on public.stock_rows ("country");
create index if not exists ix_map_global on public.product_legacy_map ("globalCode");
create index if not exists ix_pc_country on public.product_country ("country");
create index if not exists ix_changes_prod on public.changes ("legacyProductId");

-- Seguridad (MVP): RLS activado. Con la clave SECRETA (sb_secret_...) el backend omite RLS y no se necesitan políticas.
-- Con la clave PUBLICABLE (sb_publishable_...) se requieren estas políticas de acceso total para el rol anon.
-- En producción: usar la clave secreta solo en el servidor y eliminar las políticas 'mvp_anon_all'.
alter table public.source_files enable row level security;
drop policy if exists mvp_anon_all on public.source_files;
create policy mvp_anon_all on public.source_files for all to anon, authenticated using (true) with check (true);
alter table public.loads enable row level security;
drop policy if exists mvp_anon_all on public.loads;
create policy mvp_anon_all on public.loads for all to anon, authenticated using (true) with check (true);
alter table public.changes enable row level security;
drop policy if exists mvp_anon_all on public.changes;
create policy mvp_anon_all on public.changes for all to anon, authenticated using (true) with check (true);
alter table public.stock_rows enable row level security;
drop policy if exists mvp_anon_all on public.stock_rows;
create policy mvp_anon_all on public.stock_rows for all to anon, authenticated using (true) with check (true);
alter table public.manual_overrides enable row level security;
drop policy if exists mvp_anon_all on public.manual_overrides;
create policy mvp_anon_all on public.manual_overrides for all to anon, authenticated using (true) with check (true);
alter table public.ai_suggestions enable row level security;
drop policy if exists mvp_anon_all on public.ai_suggestions;
create policy mvp_anon_all on public.ai_suggestions for all to anon, authenticated using (true) with check (true);
alter table public.duplicate_decisions enable row level security;
drop policy if exists mvp_anon_all on public.duplicate_decisions;
create policy mvp_anon_all on public.duplicate_decisions for all to anon, authenticated using (true) with check (true);
alter table public.code_registry enable row level security;
drop policy if exists mvp_anon_all on public.code_registry;
create policy mvp_anon_all on public.code_registry for all to anon, authenticated using (true) with check (true);
alter table public.new_products enable row level security;
drop policy if exists mvp_anon_all on public.new_products;
create policy mvp_anon_all on public.new_products for all to anon, authenticated using (true) with check (true);
alter table public.run_log enable row level security;
drop policy if exists mvp_anon_all on public.run_log;
create policy mvp_anon_all on public.run_log for all to anon, authenticated using (true) with check (true);
alter table public.products enable row level security;
drop policy if exists mvp_anon_all on public.products;
create policy mvp_anon_all on public.products for all to anon, authenticated using (true) with check (true);
alter table public.product_legacy_map enable row level security;
drop policy if exists mvp_anon_all on public.product_legacy_map;
create policy mvp_anon_all on public.product_legacy_map for all to anon, authenticated using (true) with check (true);
alter table public.product_country enable row level security;
drop policy if exists mvp_anon_all on public.product_country;
create policy mvp_anon_all on public.product_country for all to anon, authenticated using (true) with check (true);
alter table public.duplicate_candidates enable row level security;
drop policy if exists mvp_anon_all on public.duplicate_candidates;
create policy mvp_anon_all on public.duplicate_candidates for all to anon, authenticated using (true) with check (true);
alter table public.sites enable row level security;
drop policy if exists mvp_anon_all on public.sites;
create policy mvp_anon_all on public.sites for all to anon, authenticated using (true) with check (true);
alter table public.taxonomy enable row level security;
drop policy if exists mvp_anon_all on public.taxonomy;
create policy mvp_anon_all on public.taxonomy for all to anon, authenticated using (true) with check (true);
grant select, insert, update, delete on all tables in schema public to anon, authenticated;
notify pgrst, 'reload schema';
