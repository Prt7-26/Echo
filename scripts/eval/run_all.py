"""Parallel experiment coordinator.

Launches every LLM-heavy experiment shard concurrently (capped to avoid API
throttling), each as an isolated subprocess writing its own shard file + full
log (kept as supplementary material). Then merges shards, runs the deterministic
Metric-2 sweep (n_bad ∈ {3,10} × 5 seeds) and the micro-metrics, and analyses.

Shards (process-level isolation — each has its own temp Echo DB):
  * closed-loop : one process per persona (15) × --seeds 3 × 3 conditions
  * PersonaMem  : one process per seed (3)
  * PrefEval    : one process per seed (3)

Usage:
  PY -m scripts.eval.run_all --concurrency 6
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).parents[2]
RES = pathlib.Path(__file__).parent / "results"
SHARDS = RES / "shards"
LOGS = RES / "logs"
PY = sys.executable

PERSONAMEM_SEEDS = [7, 8, 9]
PREFEVAL_SEEDS = [11, 12, 13]
CLOSEDLOOP_SEEDS = 3
TURNS = 10


def _tasks():
    from scripts.eval.personas import get_personas
    tasks = []
    # closed-loop: one shard per persona
    for p in get_personas():
        tasks.append((
            f"cl_{p.pid}",
            [PY, "-m", "scripts.eval.exp_closedloop",
             "--personas", p.pid, "--seeds", str(CLOSEDLOOP_SEEDS),
             "--turns", str(TURNS), "--conditions", "A,B,echo",
             "--out", str(SHARDS / f"cl_{p.pid}.jsonl"),
             "--usage-out", str(SHARDS / f"usage_{p.pid}.json")],
        ))
    # PersonaMem: one shard per seed
    for s in PERSONAMEM_SEEDS:
        tasks.append((
            f"pm_s{s}",
            [PY, "-m", "scripts.eval.exp_personamem",
             "--limit", "180", "--seed", str(s), "--tag", f"s{s}"],
        ))
    # PrefEval: one shard per seed
    for s in PREFEVAL_SEEDS:
        tasks.append((
            f"pe_s{s}",
            [PY, "-m", "scripts.eval.exp_prefeval",
             "--limit", "100", "--pool", "200", "--seed", str(s), "--tag", f"s{s}"],
        ))
    return tasks


def _run_task(label, argv):
    LOGS.mkdir(parents=True, exist_ok=True)
    logf = LOGS / f"{label}.log"
    t0 = time.time()
    with open(logf, "w") as lf:
        lf.write(f"# {label}\n# {' '.join(argv)}\n\n"); lf.flush()
        rc = subprocess.run(argv, stdout=lf, stderr=subprocess.STDOUT, cwd=str(ROOT)).returncode
    return label, rc, time.time() - t0


def merge_closedloop():
    runs = []
    for shard in sorted(SHARDS.glob("cl_*.jsonl")):
        for line in shard.open():
            line = line.strip()
            if line:
                runs.append(line)
    (RES / "closedloop_runs.jsonl").write_text("\n".join(runs) + ("\n" if runs else ""))

    per_run, totals = [], {}
    for shard in sorted(SHARDS.glob("usage_*.json")):
        try:
            d = json.loads(shard.read_text())
        except Exception:
            continue
        per_run.extend(d.get("per_run", []))
        for role, u in d.get("totals", {}).items():
            t = totals.setdefault(role, {"name": role, "calls": 0, "prompt_tokens": 0,
                                         "completion_tokens": 0, "total_tokens": 0, "errors": 0})
            for k in ("calls", "prompt_tokens", "completion_tokens", "total_tokens", "errors"):
                t[k] += u.get(k, 0)
    (RES / "closedloop_usage.json").write_text(
        json.dumps({"totals": totals, "per_run": per_run}, indent=2, ensure_ascii=False))
    return len(runs), len(per_run)


def metric2_deterministic():
    """Run Metric 2's deterministic harness at n_bad ∈ {3,10}, 5 seeds each."""
    from scripts.eval.metrics import error_propagation as ep
    out = {}
    for n_bad in (3, 10):
        rows = []
        for seed in range(5):
            r = ep.compute(n_bad=n_bad, n_good=max(2, n_bad // 2), uses=12,
                           seed=seed, noise=0.15).to_dict()
            rows.append(r)
        import statistics as st
        caught = [r["echo_bad_caught"] for r in rows]
        b_caught = [r["baseline_b_bad_caught"] for r in rows]
        fp = [r["echo_good_false_positives"] for r in rows]
        out[f"n_bad_{n_bad}"] = {
            "n_bad": n_bad, "seeds": 5, "noise": 0.15,
            "echo_caught_mean": st.mean(caught), "echo_caught_min": min(caught), "echo_caught_max": max(caught),
            "baseline_b_caught_mean": st.mean(b_caught),
            "echo_good_false_positives_mean": st.mean(fp),
            "per_seed": rows,
        }
    (RES / "metric2_deterministic.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--skip-shards", action="store_true", help="only merge+analyze existing shards")
    args = ap.parse_args()
    RES.mkdir(parents=True, exist_ok=True); SHARDS.mkdir(parents=True, exist_ok=True)

    if not args.skip_shards:
        tasks = _tasks()
        print(f"[run_all] launching {len(tasks)} shards, concurrency={args.concurrency}")
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futs = {ex.submit(_run_task, lbl, argv): lbl for lbl, argv in tasks}
            for fut in as_completed(futs):
                lbl, rc, dt = fut.result()
                print(f"[run_all] {lbl} done rc={rc} ({dt:.0f}s)  log=results/logs/{lbl}.log", flush=True)
        print(f"[run_all] all shards done in {time.time()-t0:.0f}s")

    print("[run_all] merging closed-loop shards ...")
    nr, npu = merge_closedloop()
    print(f"[run_all] merged {nr} run-records, {npu} per-run usage entries")

    print("[run_all] Metric 2 deterministic (n_bad 3 & 10, 5 seeds) ...")
    m2 = metric2_deterministic()
    for k, v in m2.items():
        print(f"  {k}: echo {v['echo_caught_mean']}/{v['n_bad']} (min {v['echo_caught_min']}), "
              f"B {v['baseline_b_caught_mean']}/{v['n_bad']}")

    print("[run_all] micro-metrics (M1/M3/M4/M5 + det error-prop) ...")
    subprocess.run([PY, "-m", "scripts.eval.run_micrometrics"], cwd=str(ROOT))

    print("[run_all] analysing ...")
    subprocess.run([PY, "-m", "scripts.eval.analyze"], cwd=str(ROOT))
    print("[run_all] DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
