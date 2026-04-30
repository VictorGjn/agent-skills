# Interview prompts (7 questions)

Each prompt gets exactly one structured answer, followed by 0-3
standardized follow-ups if the initial answer doesn't satisfy the
required shape. The interviewer (the skill) does NOT improvise; it uses
this script.

The dept head should expect ~5-10 minutes per prompt.

## Convention

Each prompt below has:
- **Prompt** — what the skill asks the head, verbatim
- **Required answer shape** — what makes a valid answer
- **Follow-ups** — what the skill asks if the answer doesn't match the shape
- **Maps to** — which Department Spec section this answer fills

---

## 1. Tools

**Prompt**
> "Here's the inventory we probed from your connected tools (list shown).
> For each tool, in one sentence: what is the *primary thing* you use
> this tool for in your day-to-day?"

**Required answer shape**
- One sentence per probed tool, beginning with a verb
- Names the primary entity in that tool (database, channel, project)
- If the head says "I don't use this tool" → mark for exclusion

**Follow-ups**
- *If "everything"*: "Pick the single most important workflow you do in
  this tool. What is it?"
- *If "I'm not sure"*: "When was the last time you opened it? What did
  you do there?"
- *If a probed tool is unmentioned*: "We saw <tool> connected — should
  it be excluded from this department's spec?"

**Maps to** — Tools section + populates `tools[]` in `department.json`

---

## 2. Roles

**Prompt**
> "Who is on your team, and for each person: what's the one thing they
> are *accountable* for that no one else can decide on their behalf?"

**Required answer shape**
- List of people (or stable role names if anonymized)
- Per person: one accountability statement using "decides X" or "owns X"
- At least one role marked as the head (the interviewee)

**Follow-ups**
- *If "we all do everything"*: "Who would the team escalate to about
  <pick a contentious topic from probed tool data, e.g. budget,
  prioritization, hiring>?"
- *If only one role given*: "Who do you delegate to? Who covers when
  you're out?"
- *If contractor/vendor roles missing*: "Are there external collaborators
  who own anything in your function?"

**Maps to** — Roles section

---

## 3. Cadence

**Prompt**
> "Walk me through your week. What recurring meetings, reviews, or
> deadlines structure your time?"

**Required answer shape**
- At least one recurring item (meeting / review / deadline / cycle)
- Per item: name, frequency, attendees, trigger (calendar / cron /
  threshold / external)
- At least one cycle that exceeds one week (monthly close, quarterly
  OKR, betting table, etc.)

**Follow-ups**
- *If "we don't really have one"*: "When did you last meet with your
  whole team? Why did you call that meeting?"
- *If only daily/weekly given*: "What happens at month-end? Quarter-end?"
- *If no external triggers*: "What forces you to drop everything when it
  happens?"

**Maps to** — Cadence section + populates `cadence[]` in `department.json`

---

## 4. Pipeline

**Prompt**
> "Take one piece of work that's currently in flight in your function.
> Walk me through every state it has been in, from when it first showed
> up to now — what moved it from each state to the next, and who
> approved that move?"

**Required answer shape**
- Ordered list of stages (≥ 2)
- Per stage: name, who works in it, what artifact represents work in
  that stage
- Per stage transition: the event that moves work forward (approval,
  threshold, automation, calendar)

**Follow-ups**
- *If only 2 stages*: "What happens between <stage 1> and <stage 2>?
  Walk me through the silent step."
- *If no approval gates mentioned*: "Has work ever moved forward and
  then had to move back? What happened?"
- *If artifact unclear*: "Where physically does this work live when it's
  in <stage>? A Notion page? A Linear ticket? A Slack thread?"

**Maps to** — Pipeline section + populates `pipeline_stages[]` in
`department.json`

---

## 5. Taxonomy

**Prompt**
> "How do you classify the work in your function — what categories,
> themes, tags, or labels do you use, and what's the rule for picking
> one?"

**Required answer shape**
- Named taxonomy with ≥ 3 categories
- The classification rule (manual tag / automatic / hybrid)
- The escalation: when does a category get added, merged, or retired

**Follow-ups**
- *If "we don't use categories"*: "When you report up, how do you group
  what you've done? By client? By product area? By cost center?"
- *If categories aren't in any tool*: "If we look at <probed tool with
  tag-like field>, how do those values map to what you're describing?"
- *If categories overlap*: "When something fits two, which wins? Why?"

**Maps to** — Taxonomy section

---

## 6. Automations

**Prompt**
> "What runs without you touching it? Cron jobs, webhooks, integrations,
> sync scripts, anything that fires on its own."

**Required answer shape**
- List of automations (can be empty if truly nothing automated)
- Per automation: trigger, action, who owns it, what would break if it
  stopped

**Follow-ups**
- *If "I don't know"*: "What would happen on a Monday morning if no
  automation ran over the weekend? What wouldn't be ready that
  normally is?"
- *If owner unknown*: "Who do you ping when it breaks?"
- *If list is short but probed tools suggest more*: "We saw <integration>
  configured in <tool>. What does that do?"

**Maps to** — Automations section

---

## 7. Metrics

**Prompt**
> "What numbers do you report on or watch, and what threshold means
> 'something is wrong'?"

**Required answer shape**
- At least one metric
- Per metric: name, source (tool + entity), frequency reported, what
  threshold triggers action

**Follow-ups**
- *If "we don't measure"*: "When was the last time you said 'this is
  going well' or 'this is going badly'? What told you that?"
- *If metrics have no thresholds*: "At what value do you stop the
  presses? At what value do you celebrate?"
- *If metrics aren't in any probed tool*: "Where do you actually look at
  this number?"

**Maps to** — Metrics section + populates `metrics[]` in `department.json`

---

## Hard rules for the interviewer

- **Never accept "no methodology" as an answer.** Every functioning
  department has one. If the head says they don't, use the follow-ups to
  surface the implicit pattern.
- **Never improvise prompts.** Use this script verbatim so spec quality
  is comparable across departments.
- **Never let an answer skip the required shape.** A vague answer becomes
  a `??-needs-verification.md` annex item, not a Department Spec entry.
- **Stop after 3 follow-ups per prompt.** If the head still can't
  answer, mark the section as `INCOMPLETE` and continue. Phase 5 will
  surface incompletes for re-interview.
