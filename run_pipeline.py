"""Pipeline: nueva información -> registro/verificación del archivo -> limpieza -> país -> consolidación en base acumulada
   -> clasificación -> código global / nombre / duplicados -> BD (Supabase o SQLite) + Excel -> IA opcional.

Uso:  python run_pipeline.py [archivo.xlsx] [--force] [--no-ai] [--user NOMBRE]
      python run_pipeline.py --reprocess        (recalcula pasos 3-7 desde la base acumulada; aplica IA / ediciones)
Códigos de salida: 0 ok · 3 archivo ya cargado · 1 error
"""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
import step1_clean, step2_country, step2b_merge, step3_classify, step4_master, step7_publish  # noqa: E402
import ai_assist, store  # noqa: E402
from common import latest_input  # noqa: E402


def derive(base, source_file: str = "") -> None:
    base = base[base["isPresent"].fillna(True).astype(bool)].copy()
    df = step3_classify.run(base)
    out = step4_master.run(df)
    step7_publish.run(out, source_file=source_file)


def main(args):
    t = time.time()
    print(f"== Pipeline de estandarización de productos RLA (BD: {store.backend()}) ==")
    if "--reprocess" in args:
        derive(step2b_merge.current_base(), "reproceso")
        print(f"== Listo en {time.time()-t:.0f} s ==")
        return 0
    user = args[args.index("--user") + 1] if "--user" in args else "pipeline"
    files = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--user")]
    path = Path(files[0]) if files else latest_input()
    print(f"Archivo: {path.name}")
    fh, prev = step2b_merge.check_file(path)
    if len(prev) and "--force" not in args:
        p = prev.iloc[0]
        print(f"⚠️ ARCHIVO YA CARGADO: el {p.uploadedAt} como '{p.fileName}' (carga {p.loadId}). No se procesa de nuevo. "
              "Usa --force para reprocesarlo igualmente.")
        return 3
    df = step1_clean.run(path)
    df = step2_country.run(df)
    base, stats = step2b_merge.run(df, path, user=user, force=True)
    derive(base, path.name)

    if "--no-ai" not in args and ai_assist.cfg()["auto"]:
        if not ai_assist.available():
            print("[IA] IA no configurada (.env sin LLM_API_KEY): se omite la clasificación con IA.")
        else:
            res = ai_assist.run(scope="unclassified")
            if not res["ok"]:
                print(f"⚠️ El archivo '{path.name}' no se pudo analizar con IA. Los productos sin clasificar quedan en la cola de revisión.")
            if res["saved"]:
                print("[IA] Aplicando sugerencias de IA (marcadas como 'ia')...")
                derive(step2b_merge.current_base(), path.name)
    print(f"== Listo en {time.time()-t:.0f} s ==")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
