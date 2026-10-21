---
name: issue
description: "Present one issue, incident, bug, or decision as an STE issue block: Context, Problem, Solution, Risk, Action. The fields come from the ATA Service Bulletin planning sections; the Risk line follows ASD-STE100 Section 7. Use when the user says 'present the issue', 'write up this problem', 'issue block', 'ste issue', or '/ste100:issue', or when an answer reports a problem that needs a decision. Do NOT use for a status update with no problem, or for a procedure (steps only)."
---

# STE issue block

Write the block in ASD-STE100: obey the rules in [rules.md](../../rules.md). Write one block for each issue. If there are many issues, start with the most serious one.

## Format

```markdown
**Issue:** <title, 3 to 8 words>

- **Context:** <what is affected: system, version, scope, users>
- **Problem:** <what occurs>. Cause: <cause>.
- **Solution:** <what to change>
- **Risk:** <WARNING|CAUTION>: <condition or command>. <result if not done>.
- **Action:** <who> <does what>, <when>.
```

| Field | Source | Rules |
|---|---|---|
| Context | SB Effectivity | Descriptive sentences, max 25 words (R6.3). |
| Problem | SB Reason | Make a clear difference between facts and assumptions. For an unverified cause, write "Possible cause:". For an unknown cause, write "Cause: not known. To find it: <test>." |
| Solution | SB Description | One change in each sentence. If there is more than one option, give max 3 options. Put the recommended option first and give its tradeoff in one sentence. |
| Risk | STE R7.1–R7.3 | Use WARNING for a risk to people, data, security, or money. Use CAUTION for a risk to systems, time, or work. Put the condition first (R7.2). Then give the result (R7.3). If the solution has a risk too, add a second Risk line. If there is no risk, write "Risk: none known." |
| Action | SB Compliance | Instructions, max 20 words (R5.1). Give the owner and the time: now, before <event>, or when convenient. |

Do not leave a field empty. If you do not have the data, write "Not known."

## Example

**Issue:** Reserved marketplace name

- **Context:** The plugin marketplace in `victorgjn/agent-skills`. This affects all users who add the marketplace.
- **Problem:** `claude plugin marketplace add` fails. Cause: Anthropic keeps the name `agent-skills` for its official marketplaces.
- **Solution:** Change the marketplace name to `victorgjn-skills`. The repository name does not change.
- **Risk:** CAUTION: Tell current users before you change the name. Their old install commands will fail.
- **Action:** Victor approves the new name today. Claude then updates `marketplace.json` and the README.
