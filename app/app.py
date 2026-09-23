"""App de gestión del maestro de productos estandarizado — RLA.
Ejecutar:  streamlit run app/app.py
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from rapidfuzz import fuzz, process

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import store  # noqa: E402
import ai_assist  # noqa: E402
from common import INPUT, OUTPUT, norm_key, load_config  # noqa: E402
from step3_classify import compile_rules, classify_row  # noqa: E402

st.set_page_config(page_title="RLA · Maestro de productos", page_icon="📦", layout="wide")

SRC_LABEL = {"ia": "🤖 IA", "manual": "✍️ manual", "origen": "R2", "regla_regex": "regla",
             "regla_descripcion": "regla (descripción)", "regla_grupo": "regla (grupo legacy)",
             "regla_departamento": "regla (departamento)", "regla_tipo": "regla (tipo)",
             "sin_clasificar": "⚠️ sin clasificar", "vacio": "—", "generado": "generado"}


@st.cache_data(ttl=600)
def load(name: str) -> pd.DataFrame:
    return store.read_table(name)


def refresh():
    st.cache_data.clear()


def db_ready() -> bool:
    return not load("products").empty


def ai_classify_ui(scope: str = "unclassified", source_label: str = "", limit: int | None = None) -> None:
    """Ejecuta la IA mostrando el estado de conexión y luego reprocesa para aplicar las sugerencias."""
    with st.status("🤖 Clasificando con IA...", expanded=True) as box:
        def notify(level, msg):
            (box.error if level == "error" else box.warning if level == "warning" else box.write)(msg)
        bar = st.progress(0.0)
        res = ai_assist.run(scope=scope, limit=limit, notify=notify, progress=lambda f: bar.progress(f))
        if res["saved"]:
            box.write("🔄 Aplicando sugerencias (se marcan como 🤖 IA)...")
            r = subprocess.run([sys.executable, str(ROOT / "run_pipeline.py"), "--reprocess"],
                               capture_output=True, text=True, cwd=ROOT)
            if r.returncode:
                box.error("Error al reprocesar:\n" + r.stderr[-1500:])
        refresh()
        if res["ok"]:
            box.update(label=f"✅ Éxito: {res['saved']} productos analizados con IA", state="complete")
        else:
            box.update(label="❌ No se pudo completar la clasificación con IA", state="error")
    if not res["ok"]:
        st.error(("El archivo " + source_label + " no se pudo analizar con IA. " if source_label else "") + res["message"])
    elif res["saved"]:
        st.success(res["message"] + " Revísalos en **Revisar y editar → Solo con IA 🤖**.")


def ai_button(key: str) -> None:
    n = len(ai_assist.pending("unclassified")) if db_ready() else 0
    if n == 0:
        return
    if not ai_assist.available():
        st.info(f"Hay {n} productos sin clasificar. Configura `LLM_API_KEY` en el archivo `.env` para clasificarlos con IA.")
        return
    if st.button(f"🤖 Intentar clasificar con IA ({n} sin clasificar)", key=key):
        ai_classify_ui("unclassified")


tax = load_config("taxonomy.csv")
tax["code"] = tax.familyCode + "-" + tax.categoryCode
tax["label"] = tax.code + " · " + tax.familyName + " > " + tax.categoryName
CAT_OPTIONS = tax.label.tolist()
CODE2LABEL = dict(zip(tax.code, tax.label))

st.sidebar.title("📦 Maestro de productos")
st.sidebar.caption("RLA · estandarización multi-país")
page = st.sidebar.radio("Sección", ["Resumen", "Buscar producto", "Revisar y editar", "Duplicados",
                                    "Crear producto", "Cargar datos / IA"])
user = st.sidebar.text_input("Usuario (para trazabilidad)", value="analista")

if not db_ready() and page != "Cargar datos / IA":
    st.warning("La base está vacía. Ve a **Cargar datos / IA** y procesa un archivo.")
    st.stop()


# =====================================================================  RESUMEN
if page == "Resumen":
    p, pc, dup, sites = load("products"), load("product_country"), load("duplicate_candidates"), load("sites")
    lm = load("product_legacy_map")
    st.header("Resumen del maestro")
    c = st.columns(6)
    c[0].metric("Códigos legacy (R2)", f"{len(lm):,}")
    c[1].metric("Códigos globales", f"{len(p):,}", f"-{len(lm)-len(p)} consolidados")
    c[2].metric("Clasificados", f"{(p.classificationSource!='sin_clasificar').mean():.1%}")
    c[3].metric("Campos completados por IA", int((p.aiFields.fillna('') != '').sum()))
    c[4].metric("Pares duplicado pendientes", int((dup.decision == 'PENDIENTE').sum()))
    c[5].metric("Sitios sin país", int((sites.country == 'SIN_ASIGNAR').sum()))
    ai_button("ai_resumen")

    a, b = st.columns(2)
    a.subheader("Productos por familia")
    a.bar_chart(p.groupby("familyName", dropna=False).size().rename("productos").sort_values())
    b.subheader("Origen de la clasificación")
    b.bar_chart(p.classificationSource.map(SRC_LABEL).value_counts().rename("productos"))
    a, b = st.columns(2)
    a.subheader("Productos con stock por país")
    a.bar_chart(pc[pc.isAvailable == 1].groupby("country").globalCode.nunique().rename("productos"))
    b.subheader("Completitud de atributos")
    comp = pd.Series({"Marca": p.manufacturer.notna().mean(), "Modelo": p.model.notna().mean(),
                      "Atributo clave (si aplica)": p.loc[p.keyAttribute.notna() & (p.keyAttribute != ''), 'keyAttributeValue'].notna().mean(),
                      "Categoría": (p.classificationSource != 'sin_clasificar').mean()}, name="% completo")
    b.bar_chart(comp)
    st.subheader("Historial de corridas")
    st.dataframe(load("run_log").pivot_table(index=["runAt", "sourceFile"], columns="metric", values="value", aggfunc="first")
                 .sort_index(ascending=False), width="stretch")


# =====================================================================  BUSCAR
elif page == "Buscar producto":
    p, pc, pss, lm = load("products"), load("product_country"), load("product_site_stock"), load("product_legacy_map")
    st.header("Buscar producto")
    q = st.text_input("Buscar por nombre, descripción, código global o código legacy", placeholder="ej: proyector 5000, CO ACC55, micrófono shure")
    f1, f2, f3, f4 = st.columns(4)
    fam = f1.multiselect("Familia", sorted(p.familyName.dropna().unique()))
    ctry = f2.multiselect("País (con stock)", sorted(pc.country.unique()))
    stt = f3.multiselect("Estado", sorted(p.status.unique()), default=["ACTIVO"])
    only_ai = f4.checkbox("Solo con campos de IA 🤖")
    r = p.copy()
    if fam: r = r[r.familyName.isin(fam)]
    if stt: r = r[r.status.isin(stt)]
    if only_ai: r = r[r.aiFields.fillna("") != ""]
    if ctry:
        codes = pc[pc.country.isin(ctry) & (pc.isAvailable == 1)].globalCode
        r = r[r.globalCode.isin(codes)]
    if q:
        hay = (r.globalCode + " " + r.standardName.fillna("") + " " + r.description.fillna("") + " " + r.legacyIds.fillna("")).map(norm_key)
        toks = norm_key(q).split()
        exact = hay.map(lambda h: all(t in h for t in toks))
        if exact.sum() < 5:  # búsqueda tolerante a errores
            sc = process.extract(norm_key(q), hay.to_dict(), scorer=fuzz.token_set_ratio, limit=50, score_cutoff=70)
            idx = [k for _, _, k in sc]
            r = pd.concat([r[exact], r.loc[idx]]).drop_duplicates("globalCode")
        else:
            r = r[exact]
    st.caption(f"{len(r):,} productos")
    show = r[["globalCode", "standardName", "familyName", "categoryName", "manufacturer", "model", "keyAttributeValue",
              "countriesWithStock", "totalStock", "nLegacyIds", "aiFields", "status"]]
    ev = st.dataframe(show, width="stretch", hide_index=True, on_select="rerun", selection_mode="single-row", height=380)
    sel = ev.selection.rows
    if sel:
        row = r.iloc[sel[0]]
        st.subheader(f"{row.globalCode} · {row.standardName}")
        a, b = st.columns([1, 2])
        with a:
            info = pd.DataFrame({
                "Campo": ["Familia > Categoría", "Marca", "Modelo", "Atributo clave", "Nombre estándar", "Descripción R2", "Tipo / Serial"],
                "Valor": [f"{row.familyName} > {row.categoryName}", row.manufacturer, row.model, row.keyAttributeValue,
                          row.standardName, row.description, f"{row.productType} / {row.itemCategory}"],
                "Fuente": [SRC_LABEL.get(row.classificationSource, row.classificationSource), SRC_LABEL.get(row.manufacturerSource),
                           SRC_LABEL.get(row.modelSource), SRC_LABEL.get(row.keyAttributeSource), SRC_LABEL.get(row.nameSource), "R2", "R2"]})
            st.dataframe(info, hide_index=True, width="stretch")
            st.markdown("**Códigos legacy equivalentes**")
            st.dataframe(lm[lm.globalCode == row.globalCode][["legacyProductId", "codeCountry", "matchType", "descriptionOriginal", "totalStock"]],
                         hide_index=True, width="stretch")
        with b:
            st.markdown("**Disponibilidad por país**")
            st.dataframe(pc[pc.globalCode == row.globalCode][["country", "totalStock", "nSitesWithStock", "sitesWithStock", "nSitesEnabled"]],
                         hide_index=True, width="stretch")
            st.markdown("**Detalle por sitio**")
            st.dataframe(pss[pss.globalCode == row.globalCode][["country", "siteName", "siteType", "legacyProductId", "stockQty", "unitCost", "canRent", "canSell"]]
                         .sort_values(["country", "stockQty"], ascending=[True, False]), hide_index=True, width="stretch")


# =====================================================================  REVISAR
elif page == "Revisar y editar":
    p, lm = load("products"), load("product_legacy_map")
    st.header("Revisar y editar")
    st.caption("Lo que edites aquí se guarda como **✍️ manual**, tiene prioridad sobre reglas e IA y se mantiene en futuras cargas. "
               "Los campos completados por IA aparecen marcados con 🤖 en la columna *camposIA*.")
    ai_button("ai_revisar")
    mode = st.radio("Mostrar", ["Pendientes (sin clasificar o con IA)", "Solo con IA 🤖", "Sin clasificar", "Todos"], horizontal=True)
    r = p[p.status == "ACTIVO"]
    if mode.startswith("Pendientes"): r = r[r.needsReview == 1]
    elif mode.startswith("Solo con IA"): r = r[r.aiFields.fillna("") != ""]
    elif mode == "Sin clasificar": r = r[r.classificationSource == "sin_clasificar"]
    fam = st.selectbox("Familia", ["(todas)"] + sorted(p.familyName.dropna().unique()))
    if fam != "(todas)": r = r[r.familyName == fam]
    r = r.head(500).copy()
    r["categoria"] = (r.familyCode.fillna("") + "-" + r.categoryCode.fillna("")).map(CODE2LABEL)
    r["camposIA"] = r.aiFields.fillna("").map(lambda s: f"🤖 {s}" if s else "")
    cols = ["globalCode", "description", "categoria", "manufacturer", "model", "keyAttributeValue", "standardName", "camposIA", "classificationSource"]
    ed = st.data_editor(r[cols], hide_index=True, width="stretch", height=480, key="editor",
                        disabled=["globalCode", "description", "camposIA", "classificationSource"],
                        column_config={"categoria": st.column_config.SelectboxColumn("categoría", options=CAT_OPTIONS, width="large"),
                                       "classificationSource": st.column_config.TextColumn("fuente categoría")})
    if st.button("💾 Guardar cambios", type="primary"):
        orig = r[cols].set_index("globalCode"); new = ed.set_index("globalCode")
        canon = lm[lm.matchType == "canonico"].set_index("globalCode").legacyProductId
        allmap = lm.groupby("globalCode").legacyProductId.apply(list)
        n = 0
        for code in new.index:
            for f in ["categoria", "manufacturer", "model", "keyAttributeValue", "standardName"]:
                a, b = orig.at[code, f], new.at[code, f]
                if (pd.isna(a) and pd.isna(b)) or a == b:
                    continue
                ids = allmap.get(code, [canon.get(code)])
                if f == "categoria":
                    fc, cc = str(b).split(" · ")[0].split("-")
                    store.save_override(ids, "familyCode", fc, orig.at[code, f], user)
                    store.save_override(ids, "categoryCode", cc, orig.at[code, f], user)
                    with store.connect() as con:
                        t = tax.set_index("code").loc[f"{fc}-{cc}"]
                        con.execute("UPDATE products SET familyCode=?, categoryCode=?, familyName=?, categoryName=?, classificationSource='manual' WHERE globalCode=?",
                                    (fc, cc, t.familyName, t.categoryName, code))
                else:
                    val = None if (pd.isna(b) or str(b).strip() == "") else str(b).strip()
                    if f in ("manufacturer", "model") and val:
                        val = val.upper()
                    store.save_override(ids, f, val, a, user)
                    srccol = {"manufacturer": "manufacturerSource", "model": "modelSource",
                              "keyAttributeValue": "keyAttributeSource", "standardName": "nameSource"}[f]
                    with store.connect() as con:
                        con.execute(f"UPDATE products SET {f}=?, {srccol}='manual' WHERE globalCode=?", (val, code))
                n += 1
        with store.connect() as con:  # recalcula marca de campos IA
            con.execute("""UPDATE products SET aiFields = trim(
                 (CASE WHEN classificationSource='ia' THEN 'classification, ' ELSE '' END) ||
                 (CASE WHEN manufacturerSource='ia' THEN 'manufacturer, ' ELSE '' END) ||
                 (CASE WHEN modelSource='ia' THEN 'model, ' ELSE '' END) ||
                 (CASE WHEN keyAttributeSource='ia' THEN 'keyAttribute' ELSE '' END), ', ')""")
            con.execute("UPDATE products SET needsReview = (classificationSource='sin_clasificar' OR aiFields<>'')")
        refresh()
        st.success(f"{n} cambios guardados como manuales. Si cambiaste la categoría, el código global se reasigna al reprocesar (el anterior queda en el historial).")


# =====================================================================  DUPLICADOS
elif page == "Duplicados":
    d = load("duplicate_candidates")
    st.header("Candidatos a duplicado")
    st.caption("Puntaje 0-100: similitud de texto + misma marca/modelo − diferencias de modelo, marca, números o atributo clave. "
               "Los **CONFIRMADOS** se fusionan en un solo código global al reprocesar; los **RECHAZADOS** no vuelven a aparecer como pendientes.")
    a, b, c = st.columns(3)
    smin = a.slider("Puntaje mínimo", 85, 100, 90)
    dec = b.multiselect("Estado", ["PENDIENTE", "CONFIRMADO", "RECHAZADO"], default=["PENDIENTE"])
    fam = c.selectbox("Familia", ["(todas)"] + sorted(d.familyCode.unique()))
    r = d[(d.score >= smin) & d.decision.isin(dec)]
    if fam != "(todas)": r = r[r.familyCode == fam]
    st.caption(f"{len(r):,} pares")
    r = r.head(300)
    ed = st.data_editor(r[["pairKey", "score", "nameA", "legacyA", "nameB", "legacyB", "reasons", "decision"]], hide_index=True,
                        width="stretch", height=500, disabled=["pairKey", "score", "nameA", "legacyA", "nameB", "legacyB", "reasons"],
                        column_config={"decision": st.column_config.SelectboxColumn("decisión", options=["PENDIENTE", "CONFIRMADO", "RECHAZADO"])})
    if st.button("💾 Guardar decisiones", type="primary"):
        ch = ed.merge(r[["pairKey", "decision"]], on="pairKey", suffixes=("", "_old"))
        ch = ch[ch.decision != ch.decision_old]
        with store.connect() as con:
            con.executemany("INSERT OR REPLACE INTO duplicate_decisions VALUES (?,?,?,?,?)",
                            [(k, v, user, store.now(), "") for k, v in zip(ch.pairKey, ch.decision)])
            con.executemany("UPDATE duplicate_candidates SET decision=? WHERE pairKey=?", list(zip(ch.decision, ch.pairKey)))
        refresh()
        st.success(f"{len(ch)} decisiones guardadas.")


# =====================================================================  CREAR
elif page == "Crear producto":
    p = load("products")
    st.header("Crear producto nuevo")
    st.caption("Antes de crear, la app busca productos similares para evitar fichas duplicadas y sugiere la categoría y el código.")
    desc = st.text_input("Descripción", placeholder="ej: Micrófono inalámbrico de mano Shure QLXD24")
    a, b = st.columns(2)
    mfr = a.text_input("Marca").upper().strip() or None
    mdl = b.text_input("Modelo").upper().strip() or None
    if desc:
        rules, cab = compile_rules()
        f, c, *_ = classify_row({"description": norm_key(desc), "productType": "ITEM", "packageType": "ITEM",
                                 "groups": "", "department": ""}, rules, cab)
        sug = CODE2LABEL.get(f"{f}-{c}")
        cat = st.selectbox("Categoría (sugerida por reglas)", CAT_OPTIONS, index=CAT_OPTIONS.index(sug) if sug in CAT_OPTIONS else 0)
        hay = (p.standardName.fillna("") + " " + p.description.fillna("")).map(norm_key)
        sims = process.extract(norm_key(f"{desc} {mfr or ''} {mdl or ''}"), hay.to_dict(), scorer=fuzz.token_set_ratio, limit=8, score_cutoff=70)
        if sims:
            st.warning("⚠️ Productos similares ya existentes — revisa antes de crear:")
            st.dataframe(pd.DataFrame([{"similitud": round(s), **p.loc[k, ["globalCode", "standardName", "countriesWithStock", "legacyIds"]].to_dict()}
                                       for _, s, k in sims]), hide_index=True, width="stretch")
        fc, cc = cat.split(" · ")[0].split("-")
        reg = store.read_table("code_registry"); newp = store.read_table("new_products")
        used = pd.concat([reg.globalCode if len(reg) else pd.Series(dtype=str), newp.globalCode if len(newp) else pd.Series(dtype=str)])
        nums = used[used.str.startswith(f"{fc}-{cc}-")].str[-4:].astype(int)
        code = f"{fc}-{cc}-{(nums.max() if len(nums) else 0) + 1:04d}"
        tname = tax.set_index("code").loc[f"{fc}-{cc}", "categoryName"].split(" / ")[0]
        name = " ".join(x for x in [tname, mfr, mdl] if x) if (mfr or mdl) else f"{tname} - {desc}"
        c1, c2 = st.columns(2)
        c1.text_input("Código global propuesto", code, disabled=True)
        name = c2.text_input("Nombre estándar", name)
        ctry = st.multiselect("País(es) donde se habilitará", ["CHILE", "COLOMBIA", "PERU", "PANAMA", "MEXICO", "USA"])
        if st.button("➕ Crear producto", type="primary"):
            row = pd.DataFrame([{"globalCode": code, "standardName": name, "description": desc, "familyCode": fc, "categoryCode": cc,
                                 "manufacturer": mfr, "model": mdl, "countries": " | ".join(ctry), "createdBy": user,
                                 "createdAt": store.now(), "status": "NUEVO_PENDIENTE_ALTA_R2"}])
            store.write_table(row, "new_products", if_exists="append")
            refresh()
            st.success(f"Producto {code} creado (pendiente de alta en R2).")
    np_ = store.read_table("new_products")
    if len(np_):
        st.subheader("Productos creados desde la app")
        st.dataframe(np_, hide_index=True, width="stretch")


# =====================================================================  CARGAR
elif page == "Cargar datos / IA":
    st.header("Cargar nueva información y procesar")
    st.markdown("**Flujo:** archivo nuevo → limpieza → país → clasificación → código global / nombre / duplicados → BD. "
                "Las decisiones manuales, de duplicados y de IA se conservan entre cargas.")
    up = st.file_uploader("Exportación de productos desde R2 (.xlsx)", type=["xlsx"])
    if up is not None:
        dest = INPUT / up.name
        dest.write_bytes(up.getvalue())
        st.info(f"Archivo guardado en data/input/{up.name}")
    files = sorted([f.name for f in INPUT.glob("*.xlsx") if not f.name.startswith("~$")])
    target = st.selectbox("Archivo a procesar", files, index=len(files) - 1 if files else 0)
    if st.button("▶️ Ejecutar pipeline", type="primary", disabled=not files):
        with st.status("Procesando archivo (≈40 s)...", expanded=True) as box:
            box.write("🧹 Limpieza → 🌎 país → 🏷️ clasificación → 🔢 códigos / nombres / duplicados → 💾 BD")
            res = subprocess.run([sys.executable, str(ROOT / "run_pipeline.py"), str(INPUT / target), "--no-ai"],
                                 capture_output=True, text=True, cwd=ROOT)
            box.code(res.stdout + ("\n" + res.stderr[-2000:] if res.returncode else ""))
            box.update(label="✅ Archivo procesado" if res.returncode == 0 else "❌ Error al procesar",
                       state="complete" if res.returncode == 0 else "error")
        refresh()
        if res.returncode == 0 and ai_assist.cfg()["auto"]:
            if ai_assist.available():
                ai_classify_ui("unclassified", source_label=f"'{target}'")
            else:
                st.info("IA no configurada (.env sin LLM_API_KEY): los productos sin clasificar quedan en la cola de revisión.")
    xl = OUTPUT / "maestro_productos_estandarizado.xlsx"
    if xl.exists():
        st.download_button("⬇️ Descargar Excel del maestro", xl.read_bytes(), file_name=xl.name)

    st.divider()
    st.header("🤖 Asistente IA (opcional)")
    c = ai_assist.cfg()
    st.markdown("La IA **propone** la categoría de los productos que las reglas no clasificaron (y, si se pide, marca / modelo / "
                "atributo donde están vacíos). Todo lo que completa queda marcado como 🤖 IA y se corrige a mano en *Revisar y editar*.")
    st.markdown(f"**Modelos (orden de uso / fallback):** {', '.join(c['models']) or '—'}  \n"
                f"**Reintento:** {c['retries']} vez tras {c['retry_delay']:g} s por modelo · **Lote:** {c['batch']} productos")
    if not ai_assist.available():
        st.info("IA desactivada: pega tu clave en `LLM_API_KEY` dentro del archivo `.env` y reinicia la app.")
    elif db_ready():
        a, b = st.columns(2)
        if a.button("🔌 Probar conexión"):
            with st.status("Probando conexión...", expanded=True) as box:
                r = ai_assist.test_connection(lambda lvl, m: (box.error if lvl == "error" else box.write)(m))
                box.update(label="✅ Conectado" if r["ok"] else "❌ Sin conexión", state="complete" if r["ok"] else "error")
        full = b.checkbox("Además completar marca / modelo / atributo faltantes")
        scope = "all" if full else "unclassified"
        pend = ai_assist.pending(scope)
        st.write(f"Productos pendientes para IA: **{len(pend):,}**")
        if len(pend):
            lim = st.number_input("Máximo a procesar ahora", 1, len(pend), min(200, len(pend)), step=25)
            if st.button("🤖 Ejecutar IA y aplicar"):
                ai_classify_ui(scope, limit=int(lim))
    ai = store.read_table("ai_suggestions")
    if len(ai):
        st.subheader("Sugerencias de IA registradas")
        st.dataframe(ai, hide_index=True, width="stretch")
