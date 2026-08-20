Structure-Version: 2026-08-20T16:14:00-04:00

# Memory structure

Meta-doc for the memory tree itself. Not part of routine recall — consult and
update this only when the *structure* changes (new submemory, reclassifying
a memory, adding a group). Routine work reads `MEMORY.md` files, not this.

## Structure-change preflight

Before changing a memory tree's structure, schema, or governing protocol —
adding/removing/moving/reclassifying a leaf or group; changing a group index,
scope, hierarchy, or inline convention; changing leaf frontmatter/schema or a
rule that governs how memory is read or edited; or changing an agent
instruction that governs the tree — read this file in full and complete the
applied-version handshake below before writing any affected memory file.

Then read every applicable group `MEMORY.md` from the relevant half-root
(`local/` or `shared/`) down to the affected group, followed by that group's
`nodes/MEMORY.md` and the target files. For additions, also decide local vs
shared first (see Local vs shared). Never infer the tree model from a partial
index or version marker alone.

For work inside a `nodes/` topic/project directory, also read that directory's
`MEMORY.md`. If the target is archived or is being archived, read the direct
parent collection's `archive/MEMORY.md` as well. Follow every `requires_read`
dependency before changing a leaf.

**Why:** a routine recall index is not a complete model of the tree; this
preflight makes the canonical model and each affected layer explicit
prerequisites.

## Version control

### Applied-version handshake

The first line of this file is the canonical `Structure-Version:` timestamp
— mandatory state, not prose. **Every edit to `STRUCTURE.md` must replace it
with the current ISO 8601 timestamp in the same commit.**

Every agent copies that line as the first line of its own root
`memory/MEMORY.md`, recording the newest structural version it has actually
read and applied. Before relying on the tree, compare the two first lines:

- Match — structural state is current.
- Differ — do not merely copy the timestamp. Locate the commit carrying the
  root's recorded version, review every later change with repository history
  (`git log -p <applied>..HEAD -- STRUCTURE.md`), and apply each migration to
  the agent's own tree in order. Only after everything is understood,
  applied, and validated may the agent update its root line to the canonical
  one. If the recorded version can't be found in history, review the
  complete `STRUCTURE.md` history first.

Agents update only their own root `MEMORY.md`; another agent's stale
timestamp is a signal for that agent, never permission to edit its tree.

### Structural changelog

Every structural change must update `changelog.md` in the same logical
change. A changelog entry records both **what changed** and the **context and
rationale** for changing it, including important behavioral consequences,
migration implications, removed assumptions, or replaced mechanisms. Preserve
enough context that a future agent can understand why the transition happened
without reconstructing the original conversation.

`STRUCTURE.md` defines the current canonical model; `changelog.md` explains
how and why that model changed. Do not duplicate the specification into the
changelog. Pure wording, formatting, or typo-only edits that do not alter
behavioral meaning do not require a structural changelog entry.

A semantic structural change is incomplete if `STRUCTURE.md` changes without
a corresponding contextual changelog entry.

### What is under version control

Two things here are independently version-controlled; everything else is
plain, un-versioned files on disk.

- **`STRUCTURE.md`** (this file) — canonical copy in its own public
  repository, `github.com/ederevx/agent-mem-struct`, cloned at
  `~/agent-mem-struct/STRUCTURE.md`. **Every change must be committed and
  pushed from that clone, to that remote, before the turn that made it
  ends** — edit the clone (or a symlink resolving to it), never a
  disconnected copy.
- **`.shared/`** — the directory every agent's `shared/` symlink resolves to
  (`~/agent-mem-struct/.shared/`, see Local vs shared below). Its own
  separate git repository with its own **private** remote — distinct from
  this one, its location deliberately not recorded here since it may carry
  personal or otherwise sensitive content. It sits inside the same directory
  tree as the `STRUCTURE.md` clone but is `.gitignore`d there; commit and
  push it only to its own remote. **Every change under `.shared/` must be
  committed and pushed from within `.shared/`, to that remote, before the
  turn that made it ends.**

Both follow each agent's own commit-attribution convention for
authorship/trailers (human author, `Assisted-by`/`Signed-off-by` trailers, no
`Co-authored-by`). Squash into one commit per logical change; no
fixup/followup commits. `local/` (each agent's private half) is never added
to either repo.

The user notifies the agent directly when changes have landed from elsewhere
— there is no separate polling/sync step to run unprompted.

**Why two repos:** `STRUCTURE.md` is meant to be edited and contributed to by
any agent — clone `agent-mem-struct` to propose a structural change.
`.shared/` is meant to be edited and contributed to by every agent on this
one machine only, so it gets its own local history instead of riding on the
public repo's. `local/` is agent-private and has no business in either.

### Discoverability

Every agent's tree carries three symlinks, all resolving to the canonical
clone, never a copy. Recreate a missing one (fresh machine, moved config) —
**each agent recreates only its own**, never another's:

```
ln -sf ~/agent-mem-struct/STRUCTURE.md <agent-home>/STRUCTURE.md
ln -sf ~/agent-mem-struct/STRUCTURE.md <agent-home>/memory/STRUCTURE.md
ln -sf ~/agent-mem-struct/.shared      <agent-home>/memory/shared
```

`<agent-home>` and the exact root of an agent's memory tree are that agent's
own configuration, not recorded here.

## Model

The tree is a recursive hierarchy of **memory groups**. Each group is a
directory holding up to three things:

- `MEMORY.md` — the group's mandatory scope manifest. It contains the group's
  **Scope**, all concise **Conventions introduced at this scope**, navigation
  to its `nodes/MEMORY.md`, and its **Submemories**. It is not a leaf memory
  file and does not use leaf frontmatter.
- `nodes/` — findings, facts, decisions, incident logs, current rationale, and
  other on-demand knowledge scoped to this group. Read only when the current
  task actually touches it. Each ongoing project may use a kebab-case
  directory with its own `MEMORY.md`; topical files inside it form a linked
  chain when one file cannot coherently hold the project record. Every active
  node collection may own one direct `archive/` child for historically useful
  knowledge that is no longer current; the archive is always exactly one
  directory level below the active collection it belongs to and always has its
  own `MEMORY.md` index.
- `submemory/<name>/` — child groups, each recursively the same shape (own
  `MEMORY.md`, `nodes/`, optionally further `submemory/`). Only for a genuinely
  distinct body of ongoing work with its own scope — not every small fact.

There is no separate `conventions/` storage layer. A standing rule that must
shape every task in a group's scope lives inline in that group's `MEMORY.md`.
On-demand explanation or evidence belongs in `nodes/` instead.

Every leaf file under `nodes/` and every group is a **node** in this tree —
hence `node_type: memory` in leaf frontmatter and the `nodes/` directory name
for the on-demand half of a group.

The actual current tree (which groups/submemories exist right now) isn't
recorded here — that's transient filesystem state (`find memory -name
MEMORY.md` or `tree memory` shows it). This file documents durable shape and
rules only, not a snapshot of today's tree.

### Group `MEMORY.md` and convention inheritance

Every scoped group `MEMORY.md` follows this semantic shape:

```markdown
# <group>

**Scope:** ...

## Conventions

<mandatory rules introduced at this scope, or (none)>

## Nodes

[[nodes/MEMORY.md]]

## Submemories

<child groups, or (none)>
```

Exact headings may vary only when the same roles remain unambiguous.

Before acting in a scoped group, read the full `MEMORY.md` chain from the
relevant half-root (`local/MEMORY.md` or `shared/MEMORY.md`) down through every
ancestor group to the target group, in that order. The effective mandatory
context is the ordered union of conventions introduced along that path.

A child group contains only conventions introduced, narrowed, or overridden at
that scope. **Never duplicate inherited ancestor conventions into descendants.**
A more-specific descendant convention may narrow or override an ancestor rule
inside its scope, but the descendant must state the override explicitly so the
agent does not have to infer that two conflicting rules are intentional.

Reading a group's `MEMORY.md` must be sufficient to acquire every mandatory
rule introduced by that group. Links from an inline convention may provide
optional depth, but following them must never be required merely to discover
what the rule says or how to obey it.

### Current vs archival knowledge

`nodes/` has an explicit knowledge lifecycle:

- **Active nodes** are authoritative for current on-demand factual/project
  knowledge.
- **Archived nodes** preserve historically useful non-current knowledge — for
  example superseded decisions, former configurations, disproven hypotheses,
  old behavior relevant to regression analysis, or failed approaches worth
  remembering so they are not repeated.

If active and archived knowledge conflict, the active node wins by default.
Archived material is evidence about a prior state or reasoning path, not a
competing source of present truth, unless an active node explicitly reinstates
it.

Normal retrieval prefers active nodes. Read archive material when the task
concerns history, prior attempts, regressions, provenance/reasoning, avoiding
repeated work, or when an active node explicitly links to it. Do not routinely
load an archive merely because it exists.

Archival knowledge is semantic history, not mechanical edit history. Git
retains ordinary revisions; do not archive a node for typo fixes, formatting,
rewrites, metadata cleanup, or routine refinement. Archive only when the
former semantic state is no longer current **and** retaining it can materially
improve future reasoning.

### Local vs shared

A memory-tree root (`memory/`) is not itself a scoped group — it holds no
`nodes/` or `submemory/` of its own, and its `MEMORY.md` only links to exactly
two mandatory child groups, each treated as if it were its own root
(**Scope:** `*`):

- **`local/`** — this agent's own private half. A real directory, unique to
  this agent's tree. Mutually read-only across agents: another agent may
  read it for cross-agent context but must never create, edit, or delete
  anything under it.
- **`shared/`** — a symlink to `~/agent-mem-struct/.shared/`, one directory
  physically common to every agent on the machine. Every agent reads *and
  writes* it directly, in place — no per-agent copy to keep in sync, so a
  change any agent makes is immediately visible to every other agent's
  `shared/`.

Both halves use the exact same group model recursively.

**Before writing new memory, decide local vs shared first** (see Classifying a
new memory below). This determines the physical half before scope and
mandatory-vs-on-demand classification.

## Leaf memory files

Every leaf `.md` under `nodes/` — including leaves below an `archive/` — uses
the following frontmatter:

```yaml
---
name: kebab-case-slug        # must be dash-case, matches [[link]] targets
description: "one line"
requires_read: []
metadata:
  node_type: memory
  type: feedback|project|user|reference
  originAgent: <agent-name> # immutable creator/owner of this node identity
  originSessionId: ...
  topics: [kebab-case-topic]
  lifecycle: active|archived # required on new nodes; legacy absence = active
  superseded_by: replacement-name # optional, archived nodes only
  log:                    # optional; chronological state/event summaries
    - date: YYYY-MM-DD
      event: "concise durable event"
  modified: ISO-timestamp
---
```

- `name:` is the link key — `[[name]]` anywhere in memory content must match
  a `name:` field exactly (dash-case, no prefix duplication of the filename).
- Filename doesn't need to match `name:`; keep filenames short/descriptive and
  do not encode storage taxonomy with prefixes such as `feedback_` or
  `project_`.
- Split project records by distinct **subject and activity performed**, not by
  an arbitrary length threshold. Each leaf should be coherent on its own;
  keep one subject/activity together even when it is long, and start a new
  topical leaf when the subject or activity changes. Connect the resulting
  project/topic sequence with explicit previous/next `[[links]]`. A
  directory's `MEMORY.md` remains a concise index of those topical leaves.
- Leaf `name:` values describe the topic itself, not storage taxonomy. Do not
  prefix them with `project-`, `feedback-`, `reference-`, or the parent
  directory name merely to encode location. Add a short subject qualifier
  only when required to keep names globally unique.
- `type:` still reflects the original taxonomy (feedback/project/user/
  reference), orthogonal to *where* the file lives. Placement (local vs
  shared, which submemory), lifecycle, and archive placement are about
  privacy, scope, read policy, and current authority — not this field.
- `originAgent:` records the agent that first created this node — immutable,
  kept for attribution even after a node moves into `shared/`. Set it on
  every new node. For legacy nodes without the field, infer ownership from
  the tree/session where `name:` and `originSessionId` first appeared, then
  backfill the field when the node is next touched.
- `modified:` records edits, not ownership — never use it to choose a source
  owner or to decide whether knowledge is current.
- `topics:` is the canonical topical classification for a leaf. Use one or
  more stable, kebab-case subject labels ordered from primary to secondary.
  Physical groups still express scope and mandatory-read boundaries; topics
  organize related material within that scope. Active and archived nodes may
  share topics; lifecycle/placement, not topic, determines current authority.
  Indexes should group links by primary topic when a directory contains more
  than one topic, and crosslinks may connect related nodes across groups
  without duplicating their content.
- `lifecycle:` is required on every newly-created node. `active` means current
  on-demand knowledge; `archived` means intentionally retained non-current
  knowledge. Existing legacy nodes without the field are treated as `active`
  for backward compatibility and should be backfilled when next touched.
  Any leaf placed under `archive/` must explicitly declare `archived`; do not
  rely on path inference when writing new archival content.
- `superseded_by:` is optional and valid only on archived nodes. When there is
  a clear current successor, set it to that successor's logical `name:` key
  (the same key used by `[[name]]` links). Do not invent a successor when none
  exists. The archived-to-current direction is canonical; no reciprocal
  `supersedes` field is required.
- Do not invent applicability dates. If a historical period matters, record
  known dates in the body or `log`; this schema does not require
  `valid_from`/`valid_until`.
- `log:` is optional metadata for dated, durable state transitions or incident
  outcomes that fall under the leaf's topic. Each entry has a `date` and a
  concise `event`; keep entries oldest-to-newest. Put the searchable event
  summary here and retain only non-duplicative explanation, current state, and
  actionable detail in the body. `log` is concise chronology, not archival
  storage; preserve detailed former reasoning in an archived node when it has
  future value. Do not use `log` for edit history (that is `modified`) or for
  a blow-by-blow transcript.
- Every leaf under `nodes/`, active or archived, must declare a top-level
  `requires_read:` YAML list (`[]` if none). Before changing a node, first
  read the applicable group `MEMORY.md` chain, then its parent collection's
  `MEMORY.md` (and direct `archive/MEMORY.md` when applicable), the existing
  target if present, and every path in `requires_read`. For a new node, read
  every path going into its initial list before creating it. An unavailable
  required path blocks the change. Paths are relative to the node unless
  absolute and must point to memory files.

## Archive directories and indexes

Archive placement is mechanical. Every active node collection — the group's
`nodes/` directory or a nested active project/topic directory with its own
`MEMORY.md` — may own at most one `archive/` directory **directly beneath that
collection**. Historical knowledge must be placed in the archive owned by the
same collection that directly indexes the active knowledge it supersedes.

Given an active leaf at:

```
A/B/current.md
```

its archival knowledge belongs at:

```
A/B/archive/<historical-memory>.md
```

not in an ancestor's archive and not in a deeper arbitrary archive. If active
knowledge lives in `nodes/project/`, use `nodes/project/archive/`; if it lives
directly in `nodes/`, use `nodes/archive/`. This keeps every archive exactly
one structural level deeper than its current memory collection and removes an
agent placement decision.

Whenever `archive/` exists, `archive/MEMORY.md` is mandatory. It is a concise,
link-only index whose opening text states that its entries are historical,
belong to the parent active collection, and are non-authoritative for current
truth. Keep archived entries separate from active entries; do not intermingle
both in one undifferentiated index. The parent active `MEMORY.md` links its
archive under a separate **Archive** heading.

An archive is terminal historical storage: **never create `archive/archive/`**
and do not treat an archive as another active collection eligible to own its
own archive. Later edits to archived knowledge are ordinary revisions; Git
preserves those revisions.

## Archiving a node

Archive a former semantic state only when it is no longer current but has
durable reasoning value. Common cases include disproven but plausible
hypotheses, replaced decisions/configurations, prior version-specific
behavior, regression context, or a failed path future agents are likely to
repeat.

When archiving:

1. Read the applicable group `MEMORY.md` chain, parent indexes, and all target
   `requires_read` dependencies.
2. Decide whether the former state has durable reasoning value; if not, let
   ordinary Git/edit history carry it.
3. Identify the active collection whose `MEMORY.md` directly indexes the
   knowledge being superseded, and preserve the coherent historical content in
   that collection's direct `archive/`. Do not choose an ancestor archive or
   create an extra nesting level. Do not mechanically snapshot a whole file
   when only one obsolete portion matters.
4. Set `metadata.lifecycle: archived` and add a brief visible body statement
   such as `**Archived because:** ...` explaining why it is no longer current
   and why it remains useful.
5. Set `superseded_by:` when a clear active successor exists.
6. Rewrite or update the active node so present truth is explicit rather than
   forcing the reader to reconcile old and new claims.
7. Add a concise transition to `log` when it represents a durable state or
   investigation outcome.
8. Update active/archive indexes so authority is obvious.

Do not archive every intermediate thought or hypothesis. Preserve closed
reasoning branches only when remembering them can prevent repeated work or
otherwise materially improve future reasoning.

## Classifying a new memory

1. **Local vs shared**: private to this agent's own workflow — habits,
   tool-use preferences, output-style rules, anything with no real equivalent
   for another agent — goes in `local/`. Anything another agent might need —
   shared projects, infra any agent touches, facts either side might rely on —
   goes in `shared/`. Writing into `shared/` is writing the one copy every
   agent reads; there is no separate linking or provenance step.
2. **Scope**: does this apply everywhere within that half, or only within one
   submemory's scope (check each submemory's **Scope:** line)? Classify by the
   rule or fact's actual applicability, not by which task surfaced it.
3. **Mandatory convention vs node**: a standing rule/preference that must
   shape every future task at that scope goes inline under **Conventions** in
   the group's `MEMORY.md`. A fact, finding, decision, project record,
   investigation, rationale, or other on-demand knowledge goes in `nodes/`.
   If something is genuinely both, inline only the concise operational rule
   and let an active node hold detailed current rationale/evidence; link that
   node from the convention rather than duplicating it.
4. **Active vs archived**: new knowledge that is currently authoritative is
   `active`. Create archival knowledge only when intentionally preserving a
   non-current semantic state with future reasoning value; ordinary new facts
   do not start archived. Archived knowledge goes only in the direct
   `archive/` of the active collection that owns the corresponding current
   knowledge.
5. If it doesn't fit any existing submemory's scope and isn't global to its
   half, that's a signal a new `submemory/<name>/` group may be warranted —
   but only for a real distinct ongoing effort, not a one-off fact.
6. For a convention, edit the group's `MEMORY.md` inline. For an active node,
   write the leaf and add one concise routing line to the applicable
   `nodes/MEMORY.md`. Archived leaves belong in that active collection's
   direct `archive/MEMORY.md`, with the active parent index linking the archive
   separately. Do not inline ordinary node content into a group `MEMORY.md`.
7. If the change was under `shared/`, commit (and push) it there (see What is
   under version control above) before the turn ends.

## Adding a new submemory group

1. `mkdir -p <local|shared>/submemory/<name>/nodes`
2. Write `submemory/<name>/MEMORY.md` with its **Scope**, an inline
   **Conventions** section (`(none)` if empty), a link to `nodes/MEMORY.md`,
   and **Submemories** (`(none)` if empty). Do not copy inherited conventions
   from ancestors.
3. Write `nodes/MEMORY.md` as a concise index stub. Do not create `archive/`
   until there is actual historically useful content to index.
4. Add one line for the new group under the parent's **Submemories** list.
5. If added under `shared/`, commit (and push) it there before the turn ends.

## Keeping inline conventions concise

Inline conventions are mandatory context for every task in their scope, so
**conciseness is mandatory**. A convention should contain:

- the rule itself;
- at most a short **Why:** when useful; and
- only the minimum **How to apply:** detail needed to execute the rule
  correctly.

A convention must remain **operationally complete without following any
link**. Never replace the rule with prose such as "follow [[rationale]]" or
hide required execution detail in an on-demand node.

If deeper explanation is genuinely useful, link an active node and create one
when no suitable node exists. That node may hold detailed current rationale,
evidence, examples, edge-case analysis, or application guidance. The link is
optional depth: the node remains on-demand and does not become mandatory merely
because a convention references it. Do not duplicate the same detailed
explanation in both places.

Keep **current justification separate from historical evolution**. An active
rationale node explains why the rule is justified now. Superseded rationale,
former versions of the rule, rejected alternatives, investigation history, or
other non-current explanation belongs in that active collection's direct
`archive/` when it still has durable reasoning value. The active rationale may
link the archive when historical context is useful.

Push incident forensics, dates, blow-by-blow narrative, and other optional
supporting material into `nodes/` rather than padding mandatory conventions.
If a formerly-current finding is retained only for history, archive it rather
than turning it into a convention.

## Migrating existing `conventions/` directories

The `2026-08-20T16:14:00-04:00` migration removes `conventions/` from every
memory group. Apply it group by group after completing the version handshake:

1. Read the group's existing `MEMORY.md`, `conventions/MEMORY.md`, every
   convention leaf it indexes, `nodes/MEMORY.md`, and any node that will be
   reused or changed during migration.
2. For each standing rule, place a concise, operationally complete version
   inline under the group's **Conventions** section. Preserve only rules
   introduced at that scope; do not copy inherited ancestor rules into it.
3. If a convention contains detailed explanation that is still current and
   worth retaining, move that detail into an appropriate active node (create
   one if needed) and link it from the inline convention. Keep the rule usable
   without following the link.
4. If the moved explanation also contains superseded rationale, former rules,
   rejected alternatives, or other historically useful non-current material,
   separate that material into the direct archive of the active collection
   that owns the corresponding current explanatory node. Do not mix current
   and historical justification in one active rationale node.
5. Update the group `MEMORY.md` so it contains **Scope**, inline
   **Conventions**, the `nodes/MEMORY.md` navigation, and **Submemories**.
   Remove the obsolete convention index/leaves and delete the now-empty
   `conventions/` directory.
6. Validate that no path, index, instruction, or dependency still expects a
   `conventions/` directory, and that all newly-created or moved nodes satisfy
   the normal node/archive schema and index rules.
7. If the migrated group is under `shared/`, commit and push the shared-tree
   migration before the turn ends. Only after all required migrations are
   applied and validated may the agent advance its root `Structure-Version:`.

---

© 2026 Edrick Sinsuan. Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — see [LICENSE](LICENSE). This notice must be preserved in every copy, fork, or derivative of this file.