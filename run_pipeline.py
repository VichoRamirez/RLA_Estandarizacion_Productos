"""Ejecuta el pipeline completo:
   nueva información -> limpieza -> país -> clasificación -> maestro/códigos/duplicados -> BD + Excel
   -> (opcional) IA para lo que las reglas no clasificaron -> reproceso para aplicar lo propuesto por IA

Uso:  python run_pipeline.py                        (toma el .xlsx más reciente de data/input/)
      python run_pipeline.py ruta/al/archivo.xlsx
      python run_pipeline.py --no-ai                (no intenta IA)
      python run_pipeline.py --reprocess            (solo pasos 3-7, reutilizando pasos 1-2; aplica IA/ediciones)
"""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
import step1_clean, step2_country, step3_classify, step4_master, step7_publish  # noqa: E402
import ai_assist  # noqa: E402
from common import latest_input, INTERIM  # noqa: E402


def reprocess(source_file: str = "") -> None:
    """Pasos 3-7 desde el resultado del paso 2 (aplica overrides manuales, decisiones y sugerencias IA)."""
    import pandas as pd
    df = pd.read_parquet(INTERIM / "02_country.parquet")
    source_file = source_file or (df["sourceFile"].iloc[0] if len(df) else "")
    df = step3_classify.run(df)
    out = step4_master.run(df)
    step7_publish.run(out, source_file=source_file)


def main(args):
    t = time.time()
    no_ai = "--no-ai" in args
    if "--reprocess" in args:
        reprocess()
        return
    files = [a for a in args if not a.startswith("--")]
    path = Path(files[0]) if files else latest_input()
    print(f"== Pipeline de estandarización de productos RLA ==\nArchivo: {path.name}")
    df = step1_clean.run(path)
    df = step2_country.run(df)
    df = step3_classify.run(df)
    out = step4_master.run(df)
    step7_publish.run(out, source_file=path.name)

    if not no_ai and ai_assist.cfg()["auto"]:
        if not ai_assist.available():
            print("[IA] IA no configurada (.env sin LLM_API_KEY): se omite la clasificación con IA.")
        else:
            res = ai_assist.run(scope="unclassified")
            if not res["ok"]:
                print(f"⚠️ El archivo '{path.name}' no se pudo analizar con IA. Los productos sin clasificar quedan en la cola de revisión.")
            if res["saved"]:
                print("[IA] Aplicando sugerencias de IA (marcadas como 'ia')...")
                reprocess(path.name)
    print(f"== Listo en {time.time()-t:.0f} s ==")


if __name__ == "__main__":
    main(sys.argv[1:])
