"""Asistente IA (opcional). La IA PROPONE, las personas deciden.

Conexión: API compatible con OpenAI (OpenRouter por defecto), configurada SOLO por variables de entorno / .env:
  LLM_API_KEY, LLM_BASE_URL, LLM_MODELS (lista en orden de preferencia), LLM_RETRY_DELAY_SECONDS,
  LLM_RETRIES_PER_MODEL, LLM_TIMEOUT_SECONDS, LLM_BATCH_SIZE.

Política de conexión por cada lote:
  1) intenta con el modelo actual
  2) si falla, reintenta el MISMO modelo tras LLM_RETRY_DELAY_SECONDS (0.1 s; puede ser un error transitorio)
  3) si sigue fallando, fallback al siguiente modelo de la lista (con la misma política)
  4) si ninguno responde -> se detiene y se informa que el archivo no se pudo analizar con IA

Qué propone: categoría (solo productos sin clasificar) y, opcionalmente, marca/modelo/atributo donde están vacíos.
Todo se guarda en ai_suggestions; el pipeline lo aplica SOLO en campos vacíos y lo marca como 'ia'.
Cualquier corrección manual en la app gana y queda como 'manual'.
"""
from __future__ import annotations
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Callable

import pandas as pd
import store
from common import load_config  # noqa: F401  (carga .env al importar common)

EQUIPMENT = {"AUD", "VID", "ILU", "INF", "TRA", "ELE"}
Notify = Callable[[str, str], None]  # (nivel: info|success|warning|error, mensaje)


# ------------------------------------------------------------------ configuración
def cfg() -> dict:
    return {
        "api_key": os.environ.get("LLM_API_KEY", "").strip(),
        "base_url": os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
        "models": [m.strip() for m in os.environ.get("LLM_MODELS", "").split(",") if m.strip()],
        "retry_delay": float(os.environ.get("LLM_RETRY_DELAY_SECONDS", "0.1")),
        "retries": int(os.environ.get("LLM_RETRIES_PER_MODEL", "1")),
        "timeout": float(os.environ.get("LLM_TIMEOUT_SECONDS", "60")),
        "batch": int(os.environ.get("LLM_BATCH_SIZE", "25")),
        "auto": os.environ.get("LLM_AUTO_CLASSIFY", "false").lower() == "true",
    }


SECONDS_PER_BATCH = (10, 40)  # rango observado típico en modelos gratuitos (depende de la carga del proveedor)


def estimate(n: int) -> dict:
    """Lotes y tiempo aproximado para n productos."""
    size = max(1, cfg()["batch"])
    batches = (n + size - 1) // size
    lo, hi = batches * SECONDS_PER_BATCH[0], batches * SECONDS_PER_BATCH[1]
    fmt = lambda s: f"{s // 60} min {s % 60:02d} s" if s >= 60 else f"{s} s"  # noqa: E731
    return {"batches": batches, "size": size, "min": fmt(lo), "max": fmt(hi)}


def available() -> bool:
    c = cfg()
    return bool(c["api_key"] and c["models"])


def _print_notify(level: str, msg: str) -> None:
    print(f"[IA] {msg}", flush=True)


class AllModelsFailed(Exception):
    pass


# ------------------------------------------------------------------ cliente con reintento + fallback
class LLMClient:
    def __init__(self, notify: Notify = _print_notify):
        self.c = cfg()
        self.notify = notify
        self.models = list(self.c["models"])
        self.current = 0  # índice del último modelo que funcionó

    def _post(self, model: str, prompt: str) -> str:
        body = json.dumps({"model": model, "temperature": 0,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(
            f"{self.c['base_url']}/chat/completions", data=body, method="POST",
            headers={"Authorization": f"Bearer {self.c['api_key']}", "Content-Type": "application/json",
                     "X-Title": "RLA Maestro de Productos"})
        try:
            with urllib.request.urlopen(req, timeout=self.c["timeout"]) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:200]
            raise RuntimeError(f"HTTP {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"sin conexión ({e.reason})") from None
        except TimeoutError:
            raise RuntimeError("tiempo de espera agotado") from None
        if "error" in data:
            raise RuntimeError(str(data["error"])[:200])
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
        if not content:
            raise RuntimeError("respuesta vacía")
        return content

    def complete(self, prompt: str, parse: Callable[[str], object]):
        """Devuelve (resultado_parseado, modelo). Lanza AllModelsFailed si ningún modelo responde."""
        order = self.models[self.current:] + self.models[:self.current]
        errors = []
        for i, model in enumerate(order):
            if i > 0:
                self.notify("warning", f"↪️ Fallback: probando con {model}")
            for attempt in range(1 + self.c["retries"]):
                if attempt == 0:
                    self.notify("info", f"🔌 Intentando conectar con {model}...")
                try:
                    out = parse(self._post(model, prompt))
                    self.current = self.models.index(model)
                    return out, model
                except Exception as e:  # conexión, HTTP, JSON inválido
                    errors.append(f"{model}: {e}")
                    if attempt < self.c["retries"]:
                        self.notify("warning", f"⚠️ {model} falló ({e}). Reintentando en {self.c['retry_delay']:g} s...")
                        time.sleep(self.c["retry_delay"])
                    else:
                        self.notify("warning", f"⚠️ {model} no respondió tras {self.c['retries']} reintento(s): {e}")
        last = {}
        for e in errors:
            last[e.split(": ", 1)[0]] = e
        raise AllModelsFailed(" | ".join(last.values()))


# ------------------------------------------------------------------ selección y prompt
def pending(scope: str = "unclassified") -> pd.DataFrame:
    p = store.read_table("products")
    if p.empty:
        return p
    lm = store.read_table("product_legacy_map")
    done = store.read_table("ai_suggestions")
    p = p[p.status == "ACTIVO"]
    mask = p.classificationSource == "sin_clasificar"
    if scope == "all":
        mask |= p.familyCode.isin(EQUIPMENT) & (p.manufacturer.isna() | p.model.isna())
    sel = p[mask].merge(lm[lm.matchType == "canonico"][["globalCode", "legacyProductId"]], on="globalCode")
    if len(done):
        sel = sel[~sel.legacyProductId.isin(done.legacyProductId)]
    return sel


def _prompt(rows: pd.DataFrame, tax: pd.DataFrame) -> str:
    cats = "\n".join(f"{r.familyCode}-{r.categoryCode}: {r.familyName} > {r.categoryName}" for r in tax.itertuples())
    items = "\n".join(json.dumps({"id": r.legacyProductId, "descripcion": r.description,
                                  "marca_actual": None if pd.isna(r.manufacturer) else r.manufacturer,
                                  "modelo_actual": None if pd.isna(r.model) else r.model,
                                  "categoria_actual": None if pd.isna(r.familyCode) else f"{r.familyCode}-{r.categoryCode}"},
                                 ensure_ascii=False) for r in rows.itertuples())
    return f"""Eres analista de datos maestros de una empresa de producción audiovisual para eventos (arriendo de equipos).
Para cada producto, propone:
- categoria: un código EXACTO de la taxonomía (formato FAM-CAT) solo si categoria_actual es null. Si nada aplica usa "SRV-OTR".
- marca y modelo: solo si aparecen explícitamente en la descripción (no inventes). Marca en MAYÚSCULAS. Si no, null.
- atributo_clave: metraje, pulgadas, lúmenes, GB/TB, watts o canales si aparece en la descripción; si no, null.
- confianza: número entre 0 y 1.
- razon: máximo 12 palabras.

TAXONOMÍA:
{cats}

PRODUCTOS (un JSON por línea):
{items}

Responde ÚNICAMENTE con un arreglo JSON, sin texto adicional:
[{{"id": "...", "categoria": "FAM-CAT" o null, "marca": null, "modelo": null, "atributo_clave": null, "confianza": 0.0, "razon": "..."}}]"""


def _parse(text: str) -> list:
    text = re.sub(r"```(?:json)?", "", text)
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("la respuesta no contiene un arreglo JSON")
    data = json.loads(m.group(0))
    if not isinstance(data, list):
        raise ValueError("JSON inesperado")
    return data


# ------------------------------------------------------------------ ejecución
def run(scope: str = "unclassified", limit: int | None = None, notify: Notify = _print_notify,
        progress: Callable[[float], None] | None = None) -> dict:
    """Devuelve {ok, saved, total, models, message}."""
    if not available():
        msg = "IA no configurada: falta LLM_API_KEY o LLM_MODELS en el archivo .env"
        notify("error", f"❌ {msg}")
        return {"ok": False, "saved": 0, "total": 0, "models": [], "message": msg}
    tax = load_config("taxonomy.csv")
    valid = set(tax.familyCode + "-" + tax.categoryCode)
    todo = pending(scope)
    if limit:
        todo = todo.head(limit)
    if todo.empty:
        notify("success", "✅ No hay productos pendientes para IA.")
        return {"ok": True, "saved": 0, "total": 0, "models": [], "message": "sin pendientes"}
    client = LLMClient(notify)
    size = client.c["batch"]
    nb = (len(todo) + size - 1) // size
    saved, used = 0, set()
    for b, i in enumerate(range(0, len(todo), size), start=1):
        chunk = todo.iloc[i:i + size]
        notify("info", f"⏳ Procesando lote {b}/{nb} ({len(chunk)} productos)...")
        try:
            items, model = client.complete(_prompt(chunk, tax), _parse)
        except AllModelsFailed as e:
            msg = (f"No se pudo conectar con ningún modelo ({', '.join(client.models)}). "
                   f"El archivo no se pudo analizar con IA. {saved} de {len(todo)} productos alcanzaron a procesarse. Detalle: {e}")
            notify("error", f"❌ {msg}")
            return {"ok": False, "saved": saved, "total": len(todo), "models": sorted(used), "message": msg}
        used.add(model)
        ids = set(chunk.legacyProductId.astype(str))
        rows = []
        for it in items:
            lid = str(it.get("id"))
            if lid not in ids:
                continue
            cat = it.get("categoria")
            fam, catc = cat.split("-", 1) if isinstance(cat, str) and cat in valid else (None, None)
            try:
                conf = float(it.get("confianza") or 0)
            except (TypeError, ValueError):
                conf = 0.0
            rows.append({"legacyProductId": lid, "familyCode": fam, "categoryCode": catc,
                         "manufacturer": it.get("marca") or None, "model": it.get("modelo") or None,
                         "keyAttributeValue": it.get("atributo_clave") or None, "confidence": conf,
                         "reasoning": it.get("razon"), "aiModel": model, "createdAt": store.now()})
        store.upsert("ai_suggestions", pd.DataFrame(rows))
        saved += len(rows)
        notify("info", f"✔️ Lote {b}/{nb} procesado con {model}: {len(rows)} sugerencias")
        if progress:
            progress(b / nb)
    msg = f"Éxito: {saved} de {len(todo)} productos analizados con IA ({', '.join(sorted(used))})."
    notify("success", f"🎉 {msg}")
    return {"ok": True, "saved": saved, "total": len(todo), "models": sorted(used), "message": msg}


def test_connection(notify: Notify = _print_notify) -> dict:
    if not available():
        notify("error", "❌ IA no configurada: falta LLM_API_KEY o LLM_MODELS en .env")
        return {"ok": False}
    try:
        _, model = LLMClient(notify).complete('Responde solo con: ["ok"]', _parse)
        notify("success", f"✅ Conexión exitosa con {model}")
        return {"ok": True, "model": model}
    except AllModelsFailed as e:
        notify("error", f"❌ No se pudo conectar con ningún modelo. {e}")
        return {"ok": False}


if __name__ == "__main__":
    res = run(scope=sys.argv[1] if len(sys.argv) > 1 else "unclassified")
    if res["saved"]:
        print("Ejecuta run_pipeline.py --reprocess para aplicar las sugerencias (quedan marcadas como 'ia').")
