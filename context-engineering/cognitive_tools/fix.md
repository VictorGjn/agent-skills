<!-- cognitive-tool: fix
     adapted from: davidkimai/Context-Engineering — cognitive-templates/verification.md (MIT)
     placeholders: {query}, {packed_context}
     split: the placeholder {packed_context} divides prefix from suffix; the loader substitutes {query} and splits on {packed_context} -->

Task: Diagnose the issue described in the query against the packed context below, then propose a fix. Verify the proposed fix using the process at the end.

Query: {query}

Packed context follows. The most relevant files are rendered at depth `Full`; supporting files are progressively compressed (`Detail` → `Summary` → `Headlines` → `Mention`). For every claim about the codebase, cite the file path from the packed sections — do not invent functions or types.

---

{packed_context}

---

Now apply this verification process to your proposed fix:

1. **Restate the problem.** Confirm what was actually asked, in one sentence. Distinguish symptom from root cause.
2. **Check methodology.** Is the approach to the fix appropriate for this problem class? Is there a simpler one?
3. **Verify against the packed context.** Every claim about the codebase must cite a file path from above. Flag gaps where a relevant file is at `Mention` or `Headlines` depth but the fix depends on its internals.
4. **Check logic.** Examine the change for logical errors, off-by-ones, or missing edge cases.
5. **Test with examples.** Walk through the fix with a concrete input. If tests exist in the packed context (look for `*test*` or `*spec*` paths), reference them.
6. **Check constraints.** Any documented invariants, type constraints, or behavioural contracts the fix must preserve.
7. **Final assessment.** State the fix as one of:
   - **Correct** — fully addresses the root cause, no new risks.
   - **Partially correct** — addresses the symptom but leaves caveats (specify).
   - **Incorrect** — flawed; explain why and propose a different approach.

If errors are found, explain them clearly and revise.
