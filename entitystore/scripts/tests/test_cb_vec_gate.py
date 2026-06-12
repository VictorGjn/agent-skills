#!/usr/bin/env python3
"""Exit-code contract tests for cb_vec_gate.py (the recall-gate harness).

Runs the harness as a subprocess (the contract is the CLI rc + report), in
both turbovec and CE_DISABLE_TURBOVEC=1 modes. All vectors seeded with
np.random.default_rng(42).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parent.parent
GATE = SCRIPTS / "cb_vec_gate.py"


def _has_turbovec() -> bool:
    try:
        import turbovec  # noqa: F401
        return True
    except ImportError:
        return False


def run_gate(*argv: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("CE_DISABLE_TURBOVEC", None)
    env.update(env_extra or {})
    # stdin=DEVNULL: under pytest's fd capture on Windows the inherited
    # stdin handle is invalid and DuplicateHandle raises WinError 6.
    return subprocess.run([sys.executable, str(GATE), *argv],
                          capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL)


SMALL = ("--synthetic", "400", "--dim", "64", "--queries", "20", "--seed", "42")


class TestGateExitCodes(unittest.TestCase):
    env_extra: dict = {}

    def test_exit0_synthetic_pass(self):
        cp = run_gate(*SMALL, env_extra=self.env_extra)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("recall gate PASSED", cp.stdout)
        self.assertIn("round-trip OK", cp.stdout)

    def test_exit2_k_out_of_range(self):
        cp = run_gate(*SMALL, "--k", "0", env_extra=self.env_extra)
        self.assertEqual(cp.returncode, 2, cp.stderr)

    def test_exit2_zero_queries(self):
        cp = run_gate("--synthetic", "400", "--dim", "64", "--queries", "0",
                      env_extra=self.env_extra)
        self.assertEqual(cp.returncode, 2, cp.stderr)
        self.assertIn("--queries", cp.stderr)

    def test_exit2_missing_vectors_file(self):
        cp = run_gate("--vectors-npy", str(Path("does") / "not" / "exist.npy"),
                      env_extra=self.env_extra)
        self.assertEqual(cp.returncode, 2, cp.stderr)
        self.assertIn("cannot load --vectors-npy", cp.stderr)

    def test_exit2_query_shape_mismatch(self):
        rng = np.random.default_rng(42)
        with tempfile.TemporaryDirectory() as td:
            q = Path(td) / "q.npy"
            np.save(q, rng.standard_normal((5, 32), dtype=np.float32))
            cp = run_gate(*SMALL, "--queries-npy", str(q), env_extra=self.env_extra)
        self.assertEqual(cp.returncode, 2, cp.stderr)
        self.assertIn("shape mismatch", cp.stderr)

    def test_exit2_string_dtype_vectors(self):
        with tempfile.TemporaryDirectory() as td:
            v = Path(td) / "v.npy"
            np.save(v, np.array([["a", "b"], ["c", "d"]]))  # non-numeric dtype
            cp = run_gate("--vectors-npy", str(v), env_extra=self.env_extra)
        self.assertEqual(cp.returncode, 2, cp.stderr)
        self.assertIn("--vectors-npy must be numeric", cp.stderr)
        self.assertNotIn("Traceback", cp.stderr)

    def test_exit3_zero_dim_ids(self):
        with tempfile.TemporaryDirectory() as td:
            ids = Path(td) / "ids.npy"
            np.save(ids, np.array("scalar-not-array"))  # 0-d, len() unusable
            cp = run_gate(*SMALL, "--ids", str(ids), env_extra=self.env_extra)
        self.assertEqual(cp.returncode, 3, cp.stderr)
        self.assertIn("provenance misalignment: --ids must be 1-D", cp.stderr)
        self.assertNotIn("Traceback", cp.stderr)

    def test_exit3_ids_misalignment(self):
        with tempfile.TemporaryDirectory() as td:
            ids = Path(td) / "ids.npy"
            np.save(ids, np.array([f"e:{i}" for i in range(7)]))  # != 400 vectors
            cp = run_gate(*SMALL, "--ids", str(ids), env_extra=self.env_extra)
        self.assertEqual(cp.returncode, 3, cp.stderr)
        self.assertIn("provenance misalignment", cp.stderr)

    def test_exit0_npy_override_with_aligned_ids(self):
        rng = np.random.default_rng(42)
        with tempfile.TemporaryDirectory() as td:
            v, q, ids = (Path(td) / n for n in ("v.npy", "q.npy", "ids.npy"))
            np.save(v, rng.standard_normal((300, 64), dtype=np.float32))
            np.save(q, rng.standard_normal((10, 64), dtype=np.float32))
            np.save(ids, np.array([f"kind:slug-{i:04d}" for i in range(300)]))
            cp = run_gate("--vectors-npy", str(v), "--queries-npy", str(q),
                          "--ids", str(ids), env_extra=self.env_extra)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("mode=npy", cp.stdout)


class TestGateExitCodesNumpyMode(TestGateExitCodes):
    """Same contract with turbovec disabled (numpy brute-force tier)."""
    env_extra = {"CE_DISABLE_TURBOVEC": "1"}

    def test_exit2_require_turbovec_when_disabled(self):
        cp = run_gate(*SMALL, "--require-turbovec", env_extra=self.env_extra)
        self.assertEqual(cp.returncode, 2, cp.stderr)
        self.assertIn("turbovec unavailable", cp.stderr)

    def test_engine_reported_numpy(self):
        cp = run_gate(*SMALL, env_extra=self.env_extra)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("engine=numpy", cp.stdout)


@unittest.skipUnless(_has_turbovec(), "turbovec not installed")
class TestGateDegradedIndex(unittest.TestCase):
    def test_exit4_on_degraded_index(self):
        # Near-duplicate vectors quantize to identical 4-bit codes, so the
        # retrieval pool is arbitrary and pool recall collapses -> exit 4.
        rng = np.random.default_rng(42)
        base = rng.standard_normal(64, dtype=np.float32)
        vecs = base + rng.standard_normal((1500, 64), dtype=np.float32) * 1e-4
        with tempfile.TemporaryDirectory() as td:
            v = Path(td) / "v.npy"
            np.save(v, vecs.astype(np.float32))
            cp = run_gate("--vectors-npy", str(v), "--queries", "20")
        self.assertEqual(cp.returncode, 4, cp.stdout + cp.stderr)
        self.assertIn("recall gate FAILED", cp.stderr)

    def test_engine_reported_turbovec(self):
        cp = run_gate(*SMALL)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("engine=turbovec", cp.stdout)


if __name__ == "__main__":
    unittest.main()
