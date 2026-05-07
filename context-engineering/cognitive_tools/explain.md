<!-- cognitive-tool: explain
     adapted from: davidkimai/Context-Engineering — cognitive-templates/understanding.md (Question Analysis, MIT)
     placeholders: {query}, {packed_context}
     split: the placeholder {packed_context} divides prefix from suffix; the loader substitutes {query} and splits on {packed_context} -->

Task: Analyze the question and produce a structured explanation grounded in the packed context below.

Question: {query}

Before answering, work through the analysis points at the end. Then produce the explanation, citing file paths from the packed sections for every claim.

---

{packed_context}

---

Structure your explanation by working through these analysis points first:

1. **Question type.** Is this asking *what* (definition), *how* (mechanism), *why* (motivation), or *where* (location)? The answer shapes the response.
2. **Core task.** State the specific action the asker needs in one sentence (e.g. "trace the data flow", "summarize the contract", "show the call site").
3. **Key components.** Which named entities (functions, types, files, modules) in the packed context are central to the answer? List them with their depth in the pack.
4. **Implicit assumptions.** What is the asker presumably already assuming? Surface anything load-bearing.
5. **Knowledge domains.** What technical areas are required? (e.g. async runtimes, AST parsing, database transactions.)
6. **Constraints.** Constraints from the packed context the explanation must respect — public API surface, deprecated paths, version-specific behaviour.
7. **Restatement.** Restate the question in your own words, integrating what the packed context reveals.

Once these are clear, write the explanation. Ground every claim in a packed file path. If a key file is at `Mention` or `Headlines` depth and the explanation depends on its internals, flag the gap rather than guessing.
