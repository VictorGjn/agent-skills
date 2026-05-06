"""Probe Mistral codestral-embed's per-minute throttle threshold.

Fires N concurrent `embed_query` calls and tracks success / 429 / latency
to surface where parallel-indexing benches start hitting per-minute token
caps. Mistral doesn't publish a hard tokens/min number for codestral-embed;
this is the operator's empirical answer.

Usage:

    MISTRAL_API_KEY=... python eval/mistral_throttle_probe.py \\
      --concurrency 4 --total 200 --tokens-per-call 8000

    # Sweep concurrency to find the wall:
    for n in 1 2 4 8 16; do
      python eval/mistral_throttle_probe.py --concurrency $n --total 100
    done

Cost: each call is ~tokens-per-call tokens at $0.10/1M = ~$0.001 per 100
calls at default 8K tokens. Order of cents per probe sweep; safe to run.

Output: JSONL records to stdout (one per call) + a summary line at the end.
Pipe to a file for analysis.

NOT run automatically. Operator decides when to probe (e.g. before kicking
off a big bench launch with parallel indexing). Threshold goes in
`.planning/v1.2/findings.md` § "Mistral throttle".
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path

# Late import — keeps `--help` cheap and lets us run without server-prod env.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server-prod"))


def _load_text(target_chars: int) -> str:
    """Build a stable test payload of approximately target_chars chars.

    Repeats a Lorem-ipsum-style stem so token count is deterministic-ish at
    the per-call level. codestral-embed is ~3 chars/token on code; this stem
    targets that density loosely."""
    stem = (
        "def process(items): "
        "return [item.strip().lower() for item in items if item] "
    )
    out = stem * max(1, target_chars // len(stem))
    return out[:target_chars]


def _one_call(text: str, idx: int, timeout_s: float) -> dict:
    from _lib import embed as embed_lib
    start = time.time()
    rec: dict = {"idx": idx, "started_at": start}
    try:
        v = embed_lib.embed_query(text, timeout=timeout_s)
        rec["ok"] = True
        rec["dims"] = len(v)
    except embed_lib.EmbedError as e:
        rec["ok"] = False
        rec["error_code"] = e.code
        rec["error_message"] = e.message[:200]
        rec["details"] = e.details
        # 429 is the throttle signal; carries Retry-After in body sometimes.
        if "429" in e.message or e.code == "EMBED_HTTP" and "429" in str(e.details.get("status", "")):
            rec["throttled"] = True
    except Exception as e:  # noqa: BLE001
        rec["ok"] = False
        rec["error_message"] = f"{type(e).__name__}: {e}"
    rec["took_ms"] = int((time.time() - start) * 1000)
    return rec


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--concurrency", type=int, default=4,
                   help="Worker pool size. Default 4 = the bench's intended parallelism.")
    p.add_argument("--total", type=int, default=100,
                   help="Total calls to fire. Default 100.")
    p.add_argument("--tokens-per-call", type=int, default=2000,
                   help="Approx target tokens per call (text length is 3x in chars). "
                        "Default 2000 = mid-size source file. Use 8000 to probe near the cap.")
    p.add_argument("--timeout-s", type=float, default=30.0)
    p.add_argument("--output", type=Path, default=None,
                   help="JSONL output file. Default stdout.")
    args = p.parse_args()

    if not os.environ.get("MISTRAL_API_KEY"):
        print("ERROR: MISTRAL_API_KEY not set", file=sys.stderr)
        return 1

    text = _load_text(args.tokens_per_call * 3)
    sink = args.output.open("w", encoding="utf-8") if args.output else sys.stdout

    t0 = time.time()
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = [ex.submit(_one_call, text, i, args.timeout_s)
                   for i in range(args.total)]
        for fut in concurrent.futures.as_completed(futures):
            rec = fut.result()
            results.append(rec)
            print(json.dumps(rec), file=sink, flush=True)

    elapsed = time.time() - t0
    n_ok = sum(1 for r in results if r["ok"])
    n_throttled = sum(1 for r in results if r.get("throttled"))
    n_other_err = len(results) - n_ok - n_throttled
    p50 = sorted(r["took_ms"] for r in results if r["ok"])
    p50v = p50[len(p50) // 2] if p50 else 0
    summary = {
        "_summary": True,
        "concurrency": args.concurrency,
        "total": args.total,
        "elapsed_s": round(elapsed, 1),
        "throughput_calls_per_s": round(len(results) / max(elapsed, 0.001), 2),
        "ok": n_ok,
        "throttled_429": n_throttled,
        "other_errors": n_other_err,
        "p50_ok_ms": p50v,
        "tokens_per_call_target": args.tokens_per_call,
    }
    print(json.dumps(summary), file=sink)
    if args.output:
        sink.close()
        print(f"\nWrote {args.total} records + summary to {args.output}", file=sys.stderr)
    print(f"\nSummary: {summary}", file=sys.stderr)
    return 0 if n_throttled == 0 else 2  # nonzero if we hit throttling


if __name__ == "__main__":
    sys.exit(main())
