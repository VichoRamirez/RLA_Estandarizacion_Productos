# RLA · Estandarización del maestro de productos (Problema #1)

Pipeline repetible + base de datos + app web para llevar el catálogo de productos de R2 (distinto por país)
a una **estructura única**: columnas estándar, país por sitio, taxonomía común, código global,
nombre estándar, detección de duplicados y búsqueda/alta de productos con alerta de similares.

```
Nueva exportación R2 (.xlsx) → 1 limpieza → 2 país → 3 categoría → 4 código global + disponibilidad
                             → 5 nombre estándar → 6 duplicados → 7 BD SQLite + Excel → 8 app (revisión humana)
```

## Cómo ejecutarlo

```bash
pip install -r requirements.txt
python run_pipeline.py            # procesa el .xlsx más reciente de data/input/ (≈40 s)
streamlit run app/app.py          # abre la app en http://localhost:8501
```
En Windows también: doble clic en `iniciar_app.bat`.
Desde la app (sección **Cargar datos / IA**) se puede subir un archivo nuevo y ejecutar el pipeline sin usar la terminal.

IA opcional: copiar `.env.example` como `.env` y pegar la clave en `LLM_API_KEY` (el `.env` está en `.gitignore`; no hay claves en el código).
En Streamlit Cloud, las mismas variables se definen en *Settings → Secrets*.

### Política de conexión con los LLM
Modelos en `LLM_MODELS` (orden = preferencia): `inclusionai/ling-3.0-flash-vl:free` → `inclusionai/ling-3.0-flash-sante:free` → `nvidia/nemotron-3.5-lightning:free`.
Por cada lote: intenta el modelo → si falla, reintenta a los 0,1 s (error transitorio) → si vuelve a fallar, fallback al siguiente →
si ninguno responde, se informa que el archivo no se pudo analizar con IA y los productos quedan en la cola de revisión.
La app muestra el estado en vivo ("Intentando conectar", "Reintentando", "Fallback", "Procesando lote", "Éxito" / "No se pudo conectar")
y tiene un botón **🤖 Intentar clasificar con IA** siempre que existan productos sin clasificar.

## Estructura

| Carpeta / archivo | Contenido |
|---|---|
| `data/input/` | Exportaciones de R2 (se procesa la más reciente) |
| `config/*.csv` | **Toda la lógica editable sin programar**: mapeo de columnas, correcciones ortográficas, alias de marcas, reglas de país, taxonomía, reglas de categoría, patrones de atributos |
| `src/step1_clean.py` | Paso 1: columnas camelCase en inglés, tipos (números formato latino, booleanos), ortografía por mapeo, placeholders, marca de candidatos a baja |
| `src/step2_country.py` | Paso 2: país y tipo de sitio (manual > regla por nombre > inferencia por prefijo de código > SIN_ASIGNAR) |
| `src/step3_classify.py` | Paso 3: Familia > Categoría (manual > reglas descripción > grupos legacy > departamento > IA) |
| `src/step4_master.py` | Pasos 4-6: código global `FAM-CAT-NNNN`, disponibilidad país/sitio, nombre estándar, duplicados exactos (consolidación) y aproximados (puntaje) |
| `src/step7_publish.py` | Paso 7: escribe SQLite (`db/rla_products.db`) y `data/output/maestro_productos_estandarizado.xlsx` |
| `src/ai_assist.py` | IA opcional (API compatible OpenAI / OpenRouter) con reintento y fallback entre modelos; propone solo lo que las reglas no resolvieron y todo queda marcado como IA |
| `app/app.py` | App Streamlit: resumen, búsqueda, revisión/edición, duplicados, alta de productos, carga de datos |
| `data/output/reports/` | Trazabilidad: log de cada corrección, mapeo de columnas, sitios/país, resumen de clasificación |

## Modelo de datos (SQLite)

- **Se regeneran en cada carga:** `products` (1 fila por código global), `product_legacy_map` (código legacy → global),
  `product_site_stock` (producto × sitio), `product_country` (producto × país con sitios), `duplicate_candidates`, `sites`, `taxonomy`.
- **Persistentes (decisiones):** `manual_overrides`, `ai_suggestions`, `duplicate_decisions`, `code_registry`, `new_products`, `run_log`.
  Por eso una carga nueva **no pierde** el trabajo manual: se reaplica automáticamente.

Cada campo derivado guarda su **fuente** (`origen`, `regla_*`, `ia`, `manual`). La columna `aiFields` lista los campos completados por IA.

## Qué es automático y qué requiere intervención (archivo nuevo del mismo tipo)

| Paso | Automático | Requiere persona |
|---|---|---|
| Columnas | Renombre y tipado; columnas nuevas se convierten solas | Revisar `01_column_mapping.csv` si aparece columna NUEVA o FALTANTE |
| Ortografía | Correcciones de los diccionarios existentes | Agregar errores nuevos a `word_corrections.csv` / `manufacturer_aliases.csv` |
| País | Sitios conocidos o con nombre reconocible | Sitios nuevos que queden `SIN_ASIGNAR` → `config/sites_manual.csv` |
| Categoría | ~95% por reglas | Cola "sin clasificar" en la app (o IA + validación) |
| Código global | Emisión y estabilidad (mismo legacy → mismo código) | — |
| Duplicados | Exactos se consolidan solos | Confirmar/rechazar pares aproximados en la app |
| Publicación | BD + Excel | — |

## Decisiones de diseño

- **No se borran filas por clave compuesta**: en esta base, la clave (descripción, fabricante, itemCategory, type, siteId) eliminaría 1.888 filas con 1.500 unidades de stock
  de productos distintos (ej. descripciones "."). Los duplicados se resuelven a nivel producto, consolidando bajo un mismo código global y
  conservando los códigos legacy en `product_legacy_map`.
- **El código legacy no se modifica** (es clave en R2).
- **Sitios virtuales** (Ghost, Diferencias, Botar, Remate…) se marcan `VIRTUAL_O_BAJA` y no cuentan como disponibilidad.
- **La IA propone, la persona decide**: solo llena campos vacíos, se marca, y cualquier edición manual gana.
