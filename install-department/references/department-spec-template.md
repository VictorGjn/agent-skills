# Department Spec — canonical template

The `synthesize.py` script renders this template with the interview
answers + probe results. The output is what gets committed to the brain
as `<brain-root>/departments/<function>/department-spec.md`.

Section order, heading levels, and field names are **canonical** — the
runtime indexes against them. Do not rename, reorder, or omit sections.

```markdown
# {Department Name} — Department Spec

> **Head:** {head name + role}
> **Installed:** {ISO-8601 date}
> **Spec version:** 1.0
> **Source skill:** install-department v{semver}

---

## 1. Tools

For each tool the department uses day-to-day:

### {Tool name}
- **Primary use:** {one sentence, from interview Q1}
- **Entities tracked:** {list from probe}
- **Access:** {r | rw}
- **Volume:** {estimate from probe}
- **Cross-references:** {linked entities from probe}

(repeat per tool)

---

## 2. Roles

For each role in the department:

### {Role name or person name}
- **Accountable for:** {one statement, from interview Q2}
- **Tools used:** {subset of §1 tools}
- **Reports to:** {role name}
- **Backed up by:** {role name, if known}

(repeat per role)

---

## 3. Cadence

Recurring rhythms that structure the department's time.

| Name | Frequency | Attendees | Trigger | Output |
|---|---|---|---|---|
| {meeting/review/cycle} | {daily/weekly/monthly/quarterly} | {role list} | {calendar/cron/threshold/external} | {artifact produced} |

---

## 4. Pipeline

The sequential stages work moves through.

```
{Stage 1} → {Stage 2} → {Stage 3} → ...
```

For each stage:

### {Stage N — Name}
- **What happens:** {description}
- **Who works in this stage:** {role list}
- **Artifact:** {Notion page / Linear ticket / HubSpot deal / etc.}
- **Moves forward when:** {trigger event}
- **Owner of the move:** {role}

(repeat per stage)

---

## 5. Taxonomy

How the department classifies work.

- **Taxonomy name:** {name, e.g. "Themes", "Categories", "Lanes"}
- **Classification:** {manual / automatic / hybrid}
- **Categories:** {flat list or hierarchy}
- **Add/merge/retire rule:** {when categories change}
- **Conflict rule:** {what wins when an item fits two categories}

---

## 6. Automations

Things that run without human intervention.

| Name | Trigger | Action | Owner | Breakage symptom |
|---|---|---|---|---|
| {automation name} | {cron / webhook / threshold} | {what happens} | {role} | {what wouldn't work if it stopped} |

---

## 7. Metrics

Numbers the department reports on or watches.

For each metric:

### {Metric name}
- **Source:** {tool.entity}
- **Frequency reported:** {daily/weekly/monthly/quarterly}
- **Healthy range:** {value}
- **Action threshold:** {value that triggers intervention}

(repeat per metric)

---

## Annex — Unverified claims

Items that came up in the interview but didn't ground in a probed
entity. These are NOT part of the canonical spec; they're the shortlist
for the next interview pass.

(Annex section is optional — omitted if empty)
```

## Section requirements

Every section must be **non-empty** before the spec can be committed.
The `validate.py` script enforces this. If a section is genuinely empty
for the department (rare), the head must explicitly write `*(none)*`
under that section heading — silence is not an answer.

## Anti-patterns to flag

- **Aspirational tools** — the spec mentions a tool that wasn't probed.
  Validator rejects.
- **Pipeline with one stage** — every department has at least two
  stages, even if one is "intake" and the other is "done."
- **Roles without accountabilities** — listing names without "decides X"
  / "owns X" defeats the point. Validator warns.
- **Cadence with no triggers** — a recurring item without a trigger is
  an unrooted habit, not a process.
- **Metrics without thresholds** — a number with no action threshold is
  a vanity metric. Validator warns; head can override per metric.

## How the runtime uses this

The runtime ingests `department-spec.md` once at install time and
`department.json` continuously. Downstream skills (`find-links`,
`audit-process`, `sota-search`) operate on the canonical sections by
name. If two departments use different section names, those skills break
silently — that's why the canonical shape is enforced here, not
discovered by the runtime.
