---
name: agent-patterns
description: "Select and implement the right agentic architecture pattern for a task. Use when designing a multi-step AI workflow, choosing between chaining/routing/parallelization/orchestrator-workers/evaluator-optimizer, or when the user asks how to structure an agent system. Do NOT use for single LLM calls or prompt writing (use prompt-craft)."
requiredApps: []
---

# Agent Patterns

Five composable workflow patterns from Anthropic's "Building Effective Agents" guide. Pick the simplest pattern that solves the problem. Add complexity only when it demonstrably improves outcomes.

## Decision Framework

Before picking a pattern, ask:

1. **Can a single optimized LLM call with retrieval + examples solve this?** If yes, stop here. No agent needed.
2. **Are the subtasks predictable and fixed?** → Use a **workflow** (patterns 1-4)
3. **Are subtasks unpredictable, requiring model-driven decisions?** → Use an **agent** (pattern 5+)

## The 5 Patterns

### 1. Prompt Chaining

**What**: Sequential steps, each LLM call processes the previous output. Optional programmatic gates between steps.

**When**: Task cleanly decomposes into fixed subtasks. Trade latency for accuracy by making each call easier.

**Structure**:
```
Input → LLM₁ → [Gate] → LLM₂ → [Gate] → LLM₃ → Output
```

**Examples**:
- Generate copy → translate to target language
- Write outline → validate against criteria → write full document
- Extract data → transform → generate report

**Implementation**: Chain calls with validation checks between steps. If a gate fails, loop back or abort.

---

### 2. Routing

**What**: Classify input, direct to specialized handler. Separation of concerns.

**When**: Distinct input categories need different prompts/tools/models. Optimizing for one category hurts others.

**Structure**:
```
Input → Classifier → Route A (specialized prompt)
                    → Route B (specialized prompt)
                    → Route C (different model)
```

**Examples**:
- CS queries → refund / technical support / general FAQ
- Easy questions → Haiku (cheap), hard questions → Opus (powerful)
- Code review → security / performance / style handlers

**Implementation**: Classification can be LLM-based or traditional (regex, keyword matching). Each route gets its own optimized prompt.

---

### 3. Parallelization

**What**: Run subtasks simultaneously, aggregate results. Two variants:

| Variant | How | When |
|---------|-----|------|
| **Sectioning** | Split task into independent parts | Speed: parts have no dependencies |
| **Voting** | Run same task multiple times | Confidence: want diverse perspectives |

**Structure**:
```
Input → LLM₁ ──┐
      → LLM₂ ──┼→ Aggregator → Output
      → LLM₃ ──┘
```

**Examples**:
- **Sectioning**: Guardrails check + main response in parallel; eval multiple aspects simultaneously
- **Voting**: Multiple code reviewers flag vulnerabilities; content moderation with vote threshold

**Implementation**: Fire parallel calls, collect results, merge programmatically. For voting, set threshold (e.g., 2/3 must flag).

---

### 4. Orchestrator-Workers

**What**: Central LLM dynamically breaks down task, delegates to workers, synthesizes results.

**When**: Can't predict subtasks in advance. Key difference from parallelization: subtasks are determined at runtime, not predefined.

**Structure**:
```
Input → Orchestrator → [discovers subtasks]
                      → Worker₁ → Result₁ ──┐
                      → Worker₂ → Result₂ ──┼→ Orchestrator → Output
                      → Worker₃ → Result₃ ──┘
```

**Examples**:
- Multi-file code changes (orchestrator determines which files need edits)
- Research across multiple sources (orchestrator decides what to search)

**Implementation**: Orchestrator prompt includes task analysis + delegation instructions. Workers get focused, specific sub-tasks.

---

### 5. Evaluator-Optimizer

**What**: Generator LLM produces output, evaluator LLM critiques it, loop until quality threshold met.

**When**: Clear evaluation criteria exist AND iterative refinement measurably improves output. Two signals of good fit: (a) human feedback demonstrably improves the output, (b) the LLM can provide similar feedback.

**Structure**:
```
Input → Generator → Output Draft
                      ↓
                  Evaluator → Feedback
                      ↓
                  Generator → Improved Draft
                      ↓
                  [Repeat until pass or max iterations]
```

**Examples**:
- Literary translation with quality critique loop
- Complex search requiring multiple rounds to gather comprehensive info
- Code generation with test validation feedback

**Implementation**: Set max iterations (3-5 typical). Evaluator returns structured feedback (pass/fail + specific issues). Generator receives feedback in context.

---

## Combining Patterns

Patterns are composable building blocks:

- **Routing + Chaining**: Route to category, then chain specialized steps
- **Orchestrator + Parallelization**: Orchestrator delegates, workers run in parallel
- **Chaining + Evaluator**: Chain produces draft, evaluator loop polishes it

## Agent Design Principles

When building full agents (LLMs using tools in a loop):

1. **Simplicity**: Agents are just LLMs + tools + environmental feedback in a loop. Don't over-engineer.
2. **Transparency**: Show planning steps explicitly. The user should see what the agent is doing.
3. **ACI > HCI effort**: Invest as much in agent-computer interface (tool design) as you would in human-computer interface. See `prompt-craft` skill for tool design guidance.

## Agent Guardrails

- Sandbox extensively before production
- Set stopping conditions (max iterations, max tool calls)
- Build in human checkpoints for high-stakes actions
- Get "ground truth" from environment at each step (tool results, code execution output)
- Higher autonomy = higher cost + compounding error risk
