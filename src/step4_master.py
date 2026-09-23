"""PASOS 4-5-6 — Maestro de productos.

4) Código global FAM-CAT-NNNN (estable entre corridas vía code_registry) +
   tablas de disponibilidad por país y por sitio.
5) Nombre estándar: Tipo + Marca + Modelo + Atributo clave (metraje, pulgadas, lúmenes, GB...).
6) Duplicados: (a) exactos -> se consolidan bajo el mismo código global (NO se borran filas);
               (b) aproximados -> puntaje 0-100 y cola de revisión (duplicate_candidates).
"""
from __future__ import annotations
import re
import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process
from common import load_config, norm_key, compact_key, INTERIM, REPORTS
import store

WEAK_DESC = re.compile(r"^\W*$")
FUZZY_SKIP = {("PAQ", "SAL"), ("SRV", "TEC"), ("SRV", "LOG"), ("SRV", "OTR"), ("SRV", "SUB")}
FUZZY_MIN = 85


# ------------------------------------------------------------------ atributos (paso 5)
def _num(s):
    return float(str(s).replace(",", "."))


def extract_attribute(desc: str, attr: str, pats: dict) -> str | None:
    if not attr or pd.isna(attr) or attr not in pats or not isinstance(desc, str):
        return None
    text = desc.lower()
    rx = pats[attr]
    ms = list(rx.finditer(text))
    if not ms:
        return None
    try:
        if attr == "almacenamiento":
            vals = [(_num(m.group(1)) * (1024 if m.group(2) == "tb" else 1)) for m in ms]
            v = max(vals)
            return f"{v/1024:g} TB" if v >= 1024 else f"{v:g} GB"
        m = ms[0]
        if attr == "largo":
            return f"{_num(m.group(1)):g} m"
        if attr == "pulgadas":
            return f'{_num(m.group(1)):g}"'
        if attr == "lumenes":
            return f"{int(re.sub(r'[.,]', '', m.group(1)))} lm"
        if attr == "potencia":
            return f"{_num(m.group(1)):g} {m.group(2).upper().replace('WATTS','W').replace('WATT','W')}"
        if attr == "canales":
            return f"{m.group(1)} ch"
        if attr == "pitch":
            return f"P{_num(m.group(1) or m.group(2)):g}"
        if attr == "medida":
            return f"{m.group(1)}x{m.group(2)}{(' ' + m.group(3)) if m.group(3) else ''}"
        if attr == "puertos":
            return f"{m.group(1)} puertos"
    except (ValueError, TypeError):
        return None
    return None


_TYPE_RULES = None
_STOP = {"de", "del", "para", "con", "y", "a", "el", "la", "los", "las", "en"}


def type_rules():
    global _TYPE_RULES
    if _TYPE_RULES is None:
        t = load_config("type_keywords.csv")
        _TYPE_RULES = {}
        for r in t.itertuples():
            _TYPE_RULES.setdefault((r.familyCode, r.categoryCode), []).append((re.compile(r.pattern, re.I), r.typeLabel))
    return _TYPE_RULES


def product_type(r) -> str:
    """Tipo específico por palabra clave (config/type_keywords.csv); si no, primer segmento de la categoría."""
    nd = norm_key(r.description)
    for rx, label in type_rules().get((r.familyCode, r.categoryCode), []):
        if rx.search(nd):
            return label
    return str(r.categoryName).split(" / ")[0] if pd.notna(r.categoryName) else "Sin categoría"


def strip_type_words(desc: str, tipo: str) -> str:
    """Quita de la descripción las palabras que ya están en el tipo (primera aparición) y conectores sobrantes."""
    type_tokens = set(norm_key(tipo).split())
    out, removed = [], set()
    for w in str(desc).split():
        k = norm_key(w)
        if k in type_tokens and k not in removed:
            removed.add(k)
            continue
        out.append(w)
    while out and (norm_key(out[0]) in _STOP or norm_key(out[0]) == ""):
        out.pop(0)
    while out and norm_key(out[-1]) == "":
        out.pop()
    return " ".join(out)


def standard_name(r) -> str:
    tipo = product_type(r)
    parts = [tipo]
    has_brand = pd.notna(r.manufacturer) and r.manufacturer not in ("", "GENERICO")
    has_model = pd.notna(r.model) and bool(r.model)
    if has_brand:
        parts.append(r.manufacturer)
    if has_model:
        parts.append(r.model)
    if not has_brand and not has_model and pd.notna(r.description):
        rest = strip_type_words(r.description, tipo)
        if rest:
            parts.append(rest[:70])
    if pd.notna(r.keyAttributeValue) and r.keyAttributeValue and \
            compact_key(r.keyAttributeValue) not in compact_key(" ".join(parts)):
        parts.append(r.keyAttributeValue)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


# ------------------------------------------------------------------ main
def run(df: pd.DataFrame | None = None) -> dict:
    if df is None:
        df = pd.read_parquet(INTERIM / "03_classified.parquet")
    pats = {r.keyAttribute: re.compile(r.pattern, re.I) for r in load_config("attribute_patterns.csv").itertuples()}

    # ---------- nivel producto legacy
    stock = df.groupby("legacyProductId").agg(totalStock=("stockQty", "sum"),
                                               nSites=("siteId", "nunique"),
                                               nSitesWithStock=("stockQty", lambda s: int((s > 0).sum())))
    p = df.drop_duplicates("legacyProductId").set_index("legacyProductId").join(stock).reset_index()
    p["manufacturerSource"] = np.where(p.manufacturer.notna(), "origen", "vacio")
    p["modelSource"] = np.where(p.model.notna(), "origen", "vacio")

    # IA: completa marca/modelo/atributo solo donde falta (queda marcado como 'ia')
    ai = store.read_table("ai_suggestions")
    p["keyAttributeValue"] = [extract_attribute(d, a, pats) for d, a in zip(p.description, p.keyAttribute)]
    p["keyAttributeSource"] = np.where(p.keyAttributeValue.notna(), "regla_regex", "vacio")
    if len(ai):
        ai = ai.set_index("legacyProductId")
        for f, src in (("manufacturer", "manufacturerSource"), ("model", "modelSource"), ("keyAttributeValue", "keyAttributeSource")):
            if f not in ai.columns:
                continue
            m = p[f].isna() & p.legacyProductId.isin(ai.index)
            vals = p.loc[m, "legacyProductId"].map(ai[f])
            ok = vals.notna() & (vals.astype(str).str.strip() != "")
            p.loc[vals[ok].index, f] = vals[ok].values
            p.loc[vals[ok].index, src] = "ia"
    p = store.apply_overrides(p, ["manufacturer", "model", "keyAttributeValue", "standardName"],
                              source_map={"manufacturer": "manufacturerSource", "model": "modelSource",
                                          "keyAttributeValue": "keyAttributeSource", "standardName": "nameSource"})
    manual_names = p["standardName"].copy() if "standardName" in p.columns else pd.Series(index=p.index, dtype=object)
    p["standardName"] = [standard_name(r) for r in p.itertuples()]
    if "nameSource" in p.columns:
        keep = p["nameSource"].eq("manual")
        p.loc[keep, "standardName"] = manual_names[keep]
    p["nameSource"] = p.get("nameSource", pd.Series(index=p.index, dtype=object)).where(lambda s: s.eq("manual"), "generado")

    # ---------- (6a) duplicados exactos -> clusters
    weak = p.description.fillna("").str.match(WEAK_DESC) | p.familyCode.isna()
    p["dupKey"] = (p.description.map(norm_key) + "|" + p.manufacturer.fillna("").map(norm_key) + "|" +
                   p.model.fillna("").map(norm_key) + "|" + p.itemCategory.fillna("") + "|" +
                   p.productType.fillna("") + "|" + p.familyCode.fillna("") + "|" + p.categoryCode.fillna(""))
    p.loc[weak, "dupKey"] = "UNIQUE|" + p.loc[weak, "legacyProductId"]
    p = p.sort_values(["dupKey", "totalStock", "nSites"], ascending=[True, False, False])
    p["clusterId"] = p.groupby("dupKey").ngroup()
    p["matchType"] = np.where(p.duplicated("dupKey"), "duplicado_exacto", "canonico")

    # duplicados CONFIRMADOS por una persona en la app -> se fusionan en un solo código
    dec = store.read_table("duplicate_decisions")
    reg0 = store.read_table("code_registry")
    if len(dec) and len(reg0):
        conf = dec[dec.decision == "CONFIRMADO"]
        code2cl = p.merge(reg0[["legacyProductId", "globalCode"]], on="legacyProductId").groupby("globalCode").clusterId.min()
        parent = {}
        def find(x):
            while parent.get(x, x) != x:
                x = parent[x]
            return x
        for k in conf.pairKey:
            a, b = k.split("~")
            if a in code2cl and b in code2cl:
                ra, rb = find(code2cl[a]), find(code2cl[b])
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
        if parent:
            newc = p.clusterId.map(lambda c: find(c))
            merged = newc != p.clusterId
            p["clusterId"] = newc
            p = p.sort_values(["clusterId", "totalStock"], ascending=[True, False])
            first = ~p.duplicated("clusterId")
            p.loc[~first & merged.reindex(p.index), "matchType"] = "duplicado_confirmado"
            p.loc[first, "matchType"] = "canonico"
            p.loc[~first & (p.matchType == "canonico"), "matchType"] = "duplicado_confirmado"

    # ---------- (4) código global estable
    reg = store.read_table("code_registry")
    reg_map = dict(zip(reg.legacyProductId, reg.globalCode)) if len(reg) else {}
    used = set(reg_map.values())
    counters: dict[str, int] = {}
    for c in used:
        pre, n = c.rsplit("-", 1)
        counters[pre] = max(counters.get(pre, 0), int(n))
    codes = {}
    for cid, g in p.groupby("clusterId", sort=False):
        fam = g.familyCode.iloc[0] if pd.notna(g.familyCode.iloc[0]) else "SCL"
        cat = g.categoryCode.iloc[0] if pd.notna(g.categoryCode.iloc[0]) else "XXX"
        pre = f"{fam}-{cat}"
        prev = sorted(reg_map[l] for l in g.legacyProductId if l in reg_map and reg_map[l].startswith(pre + "-"))
        if prev:
            code = prev[0]
        else:
            counters[pre] = counters.get(pre, 0) + 1
            code = f"{pre}-{counters[pre]:04d}"
        codes[cid] = code
    p["globalCode"] = p.clusterId.map(codes)
    ts = store.now()
    newreg = p[["legacyProductId", "globalCode", "familyCode", "categoryCode"]].copy()
    old_first = dict(zip(reg.legacyProductId, reg.firstSeen)) if len(reg) else {}
    newreg["firstSeen"] = newreg.legacyProductId.map(old_first).fillna(ts)
    newreg["lastSeen"] = ts
    if len(reg):  # conservar historial de códigos no presentes en este archivo
        newreg = pd.concat([newreg, reg[~reg.legacyProductId.isin(newreg.legacyProductId)]])
    store.write_table(newreg, "code_registry")

    # ---------- tablas de salida
    df = df.merge(p[["legacyProductId", "globalCode"]], on="legacyProductId", how="left")
    site_stock = df[["globalCode", "legacyProductId", "country", "siteId", "siteName", "siteType",
                     "stockQty", "unitCost", "replacementCost", "retailPrice", "canRent", "canSell", "canSubrent"]]

    def join_sites(s):
        return " | ".join(sorted(set(s.dropna())))
    op = site_stock[site_stock.siteType != "VIRTUAL_O_BAJA"]
    pc = op.groupby(["globalCode", "country"]).agg(
        nSitesEnabled=("siteId", "nunique"), sitesEnabled=("siteName", join_sites),
        totalStock=("stockQty", "sum")).reset_index()
    ws = op[op.stockQty > 0].groupby(["globalCode", "country"]).agg(
        nSitesWithStock=("siteId", "nunique"), sitesWithStock=("siteName", join_sites)).reset_index()
    pc = pc.merge(ws, on=["globalCode", "country"], how="left")
    pc["nSitesWithStock"] = pc.nSitesWithStock.fillna(0).astype(int)
    pc["isAvailable"] = pc.totalStock > 0

    canon = p[p.matchType == "canonico"].set_index("globalCode")
    agg = p.groupby("globalCode").agg(
        legacyIds=("legacyProductId", lambda s: " | ".join(s)), nLegacyIds=("legacyProductId", "size"),
        codeCountries=("codeCountry", join_sites), totalStock=("totalStock", "sum"),
        anyDeletionFlag=("isDeletionCandidate", "all"))
    cty = pc[pc.isAvailable].groupby("globalCode").country.agg(join_sites).rename("countriesWithStock")
    cte = pc.groupby("globalCode").country.agg(join_sites).rename("countriesEnabled")
    cols = ["standardName", "familyCode", "familyName", "categoryCode", "categoryName", "manufacturer", "model",
            "keyAttribute", "keyAttributeValue", "description", "itemCategory", "productType", "packageType",
            "canRent", "canSell", "canSubrent",
            "classificationSource", "manufacturerSource", "modelSource", "keyAttributeSource", "nameSource"]
    products = canon[cols].join(agg).join(cte).join(cty).reset_index()
    products["status"] = np.where(products.anyDeletionFlag & (products.totalStock <= 0), "INACTIVO_REVISAR_BAJA", "ACTIVO")
    products = products.drop(columns="anyDeletionFlag")
    src_cols = ["classificationSource", "manufacturerSource", "modelSource", "keyAttributeSource"]
    products["aiFields"] = products[src_cols].apply(
        lambda r: ", ".join(c.replace("Source", "") for c in src_cols if r[c] == "ia"), axis=1)
    products["needsReview"] = products.classificationSource.eq("sin_clasificar") | products.aiFields.ne("")

    legacy_map = p[["legacyProductId", "globalCode", "matchType", "description", "descriptionOriginal",
                    "codeCountry", "totalStock", "nSites", "isDeletionCandidate", "deletionFlagReason"]]

    # ---------- (6b) duplicados aproximados con puntaje
    cands = []
    pr = products[products.status == "ACTIVO"].copy()
    pr["nd"] = pr.description.map(norm_key)
    for c in ("manufacturer", "model", "keyAttributeValue"):
        pr[c] = pr[c].astype(object).where(pr[c].notna(), None)
    for (fam, cat), g in pr.groupby(["familyCode", "categoryCode"]):
        if (fam, cat) in FUZZY_SKIP or len(g) < 2:
            continue
        g = g.reset_index(drop=True)
        sim = process.cdist(g.nd.tolist(), g.nd.tolist(), scorer=fuzz.token_sort_ratio, score_cutoff=75, workers=-1)
        ii, jj = np.where(np.triu(sim, 1) > 0)
        for i, j in zip(ii, jj):
            a, b = g.iloc[i], g.iloc[j]
            s = float(sim[i, j]); why = [f"texto {s:.0f}"]
            if pd.notna(a.manufacturer) and a.manufacturer == b.manufacturer:
                s += 10; why.append("misma marca")
            elif pd.notna(a.manufacturer) and pd.notna(b.manufacturer) and "GENERICO" not in (a.manufacturer, b.manufacturer):
                s -= 25; why.append("marca distinta")
            if pd.notna(a.model) and pd.notna(b.model):
                if norm_key(a.model) == norm_key(b.model):
                    s += 15; why.append("mismo modelo")
                else:
                    s -= 30; why.append("modelo distinto")
            na, nb = set(re.findall(r"\d+", a.nd)), set(re.findall(r"\d+", b.nd))
            if na and nb and na != nb:
                s -= 15; why.append("números distintos")
            if pd.notna(a.keyAttributeValue) and pd.notna(b.keyAttributeValue) and a.keyAttributeValue != b.keyAttributeValue:
                s -= 25; why.append("atributo distinto")
            s = min(s, 100)
            if s >= FUZZY_MIN:
                k = "~".join(sorted([a.globalCode, b.globalCode]))
                cands.append({"pairKey": k, "codeA": a.globalCode, "nameA": a.standardName, "legacyA": a.legacyIds,
                              "codeB": b.globalCode, "nameB": b.standardName, "legacyB": b.legacyIds,
                              "familyCode": fam, "categoryCode": cat, "score": round(s, 1), "reasons": ", ".join(why)})
    dups = pd.DataFrame(cands, columns=["pairKey", "codeA", "nameA", "legacyA", "codeB", "nameB", "legacyB",
                                        "familyCode", "categoryCode", "score", "reasons"])
    dec = store.read_table("duplicate_decisions")
    if len(dec):
        dups = dups.merge(dec[["pairKey", "decision"]], on="pairKey", how="left")
    else:
        dups["decision"] = None
    dups["decision"] = dups["decision"].fillna("PENDIENTE")
    dups = dups.sort_values("score", ascending=False)

    out = {"products": products, "product_legacy_map": legacy_map, "product_site_stock": site_stock,
           "product_country": pc, "duplicate_candidates": dups}
    for k, v in out.items():
        v.to_parquet(INTERIM / f"04_{k}.parquet", index=False)
    print(f"[Paso 4] {p.legacyProductId.nunique()} códigos legacy -> {products.globalCode.nunique()} códigos globales "
          f"({int((p.matchType=='duplicado_exacto').sum())} legacy consolidados como duplicado exacto)")
    print(f"[Paso 5] atributo clave extraído en {int(products.keyAttributeValue.notna().sum())} productos; "
          f"con marca {int(products.manufacturer.notna().sum())}, con modelo {int(products.model.notna().sum())}")
    print(f"[Paso 6] {len(dups)} pares candidatos a duplicado (score >= {FUZZY_MIN}); "
          f"{int((products.status!='ACTIVO').sum())} productos INACTIVO_REVISAR_BAJA")
    return out


if __name__ == "__main__":
    run()
