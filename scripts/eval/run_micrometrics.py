"""Run Echo's deterministic per-module micro-metrics (no LLM) and collect them.

Builds the Echo artifact from the four built-in harness scenarios, then runs the
five metric modules (M1 trigger precision/recall vs the Hermes rule; M3 drift
precision/recall/F1; M4 confidence↔true-usefulness Spearman; M5 retrieval
recall@k with/without confidence weights; Metric 2 error propagation Echo vs
Baseline B). Writes results/micrometrics.json.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

RESULTS = pathlib.Path(__file__).parent / "results"
PY = sys.executable


def build_artifact() -> pathlib.Path:
    from scripts.eval.harness import Harness, build_default_scenarios
    out = RESULTS / "micrometrics_artifact.jsonl"
    h = Harness(out)
    for s in build_default_scenarios():
        h.add_scenario(s)
    h.run()
    p = h.dump()
    h.cleanup()
    return pathlib.Path(p)


def run_metric(module: str, artifact: pathlib.Path) -> dict:
    for argv in ([module, str(artifact), "--json"], [module, "--json"],
                 [module, "--artifact", str(artifact), "--json"]):
        try:
            r = subprocess.run([PY, "-m"] + argv, capture_output=True, text=True, cwd=str(pathlib.Path(__file__).parents[2]))
            if r.returncode == 0 and r.stdout.strip():
                # last JSON object in stdout
                txt = r.stdout.strip()
                a, b = txt.find("{"), txt.rfind("}")
                return json.loads(txt[a:b + 1])
        except Exception:
            continue
    return {"error": f"could not run {module}"}


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    print("building Echo artifact from built-in scenarios …")
    art = build_artifact()
    print(f"artifact -> {art}")
    out = {}
    for name, mod in [
        ("M1_trigger", "scripts.eval.metrics.m1"),
        ("M3_drift", "scripts.eval.metrics.m3"),
        ("M4_confidence", "scripts.eval.metrics.m4"),
        ("M5_retrieval", "scripts.eval.metrics.m5"),
        ("Metric2_error_propagation", "scripts.eval.metrics.error_propagation"),
    ]:
        print(f"running {name} …")
        out[name] = run_metric(mod, art)
        print("  ", json.dumps(out[name])[:200])
    (RESULTS / "micrometrics.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nsaved -> {RESULTS/'micrometrics.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
