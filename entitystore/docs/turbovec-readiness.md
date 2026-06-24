# TurboVec readiness — vector index for the EntityStore semantic layer

Status: **ready, dormant**. Built 2026-06-11 on `feature/turbovec-semantic-backend` so the
scribes pipeline gets a real vector index the day it starts writing entities at volume.
Nothing changes for existing callers until then — `semantic_rank()` keeps its exact
contract (`[{id, score, identity}]`, score rounded to 4 d.p., `min_score` floor).

## What was built

| Piece | File | Role |
|---|---|---|
| Vector backend | `entitystore/scripts/cb_vec.py` | TurboQuant `IdMapIndex` (4-bit) when `turbovec` is importable; numpy brute-force tier with identical API otherwise. Persisted str→uint64 id mapping (collision-checked), content-hash invalidation via remove+re-add, allowlist (entity-id subset) filtering, sidecar save/load next to the corpus. |
| Wiring | `entitystore/scripts/cb_embed.py`, `cb_engine.py` | `semantic_rank()` serves from the index sidecars; JSON embedding cache remains the source of truth (byte-identical, provenance untouched). Sidecar failure never blocks the JSON path. Auto-rebuild on provider/model swap and on >10 % drift. |
| Matmul fix | `context-engineering/scripts/embed_resolve.py` | `resolve_semantic` / `resolve_hybrid` per-entry Python cosine loops replaced by one numpy matmul top-k. Same results, same RRF fusion. |
| Recall gate | `entitystore/scripts/cb_vec_gate.py` | recall@10 of the quantized index vs exact float baseline, gate ≥ 0.98 (configurable). Exit codes follow `shrink-vector-store` conventions (0 pass / 2 precondition / 3 provenance misalignment / 4 gate fail). |

## Switches

- `turbovec` is an **optional dependency** (0.8.0 verified, abi3 win_amd64 wheel,
  Python 3.14). Not installed → numpy tier, silently.
- `CE_DISABLE_TURBOVEC=1` → forces the numpy tier even when turbovec is installed.
  All test suites pass identically in both modes.

## Flipping it on for scribes

Nothing to flip: the first `semantic_rank()` / `build_embeddings()` call against a
corpus builds the sidecars and serves from them. For a scribe-written corpus, just
ensure `turbovec` is installed in the runtime env (`python -m pip install turbovec`)
and run the gate once against the real vectors before trusting it (below).

## Re-running the recall gate on real corpus vectors

```bash
python entitystore/scripts/cb_vec_gate.py --corpus <corpus_dir> --k 10
# or with explicit arrays:
python entitystore/scripts/cb_vec_gate.py --vectors vecs.npy --queries q.npy --ids ids.txt --k 10
```

Measured 2026-06-11 on the real `company-brain/corpora/syroco` corpus
(2 593 vectors × 1 024 dims, Mistral, 50 queries): **recall@10 = 1.0000** on every
tier — numpy, quantized pool (gate ≥ 0.98), exact-float rescore, and allowlist
subset (n = 648). Identical under `CE_DISABLE_TURBOVEC=1`. Id round-trip: 0 collisions.

Re-run the gate whenever the corpus grows ~10× or the embedding provider changes.

## Tests

```bash
python -m pytest entitystore/scripts/tests/ -q              # 118 tests
cd context-engineering/scripts && python -m pytest tests/ -q # 329 tests + 4 subtests
CB_CORPUS_DIR=<corpus> python entitystore/scripts/cb_engine.py --self-test
```
