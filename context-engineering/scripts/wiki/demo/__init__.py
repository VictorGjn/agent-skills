"""Wave 0 demo — synthetic 50-entity brain that exercises the closed loop.

Per ``plan/prd-closed-loop.md`` P6:
  Real routine emits events -> wiki refreshes -> Auditor flags. Wave 0 sign-off.

Run:
    python -m wiki.demo.run_demo

The demo seeds a 50-entity brain across three scopes (default,
competitive-intel, lead-qual), exercises wiki.add -> wiki_init -> wiki.ask
-> wiki.audit end-to-end, and asserts every PRD acceptance criterion
that's in scope for Wave 0 (AC1-AC8).
"""
