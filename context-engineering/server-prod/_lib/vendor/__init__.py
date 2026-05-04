"""Vendored third-party / cross-package modules.

These files are byte-identical copies of their canonical homes in the
broader `context-engineering/` tree. They live here so Vercel's function
bundler — which can't reach parent directories outside its scope — can
still ship them.

A sync test (`tests/test_phase5.py::test_vendor_pack_context_lib_in_sync_with_canonical`) sha-checks every file in this
directory against the canonical source. If the canonical changes, the test
breaks until the vendor copy is refreshed via:

    cp ../scripts/pack_context_lib.py _lib/vendor/pack_context_lib.py
"""
