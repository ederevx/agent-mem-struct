Structure-Version: 2026-08-20T16:24:00-04:00

# Memory structure

Meta-doc for the memory tree itself. Not part of routine recall — consult and
update this only when the *structure* changes. Routine work reads `MEMORY.md`
files and on-demand nodes, not this file.

## Structure-change preflight

Before changing the tree's structure, schema, governing protocol, group scope,
inline conventions, indexes, node-placement rules, or agent instructions that
govern memory:

1. Read this file in full and complete the applied-version handshake below.
2. Read every applicable group `MEMORY.md` from the relevant half-root
   (`local/` or `shared/`) down to the affected group.
3. Read the affected node collection's `MEMORY.md`, target files, and any
   explicit `requires_read` prerequisites.
4. If the target is archived or is being archived, also read its direct
   parent collection's `archive/MEMORY.md`.
5. For additions, decide local vs shared before choosing scope or placement.

Never infer the model from a partial index or version marker alone.

## Version control

### Applied-version handshake

The first line of this file is the canonical `Structure-Version:` timestamp.
Every structural edit to `STRUCTURE.md` must replace it with the current
ISO 8601 timestamp in the same commit.

Every agent copies that line as the first line of its own root
`memory/MEMORY.md`, recording the newest structural version it has actually
read and applied. Before relying on the tree, compare the two first lines:

- **Match:** structural state is current.
- **Differ:** do not merely copy the timestamp. Locate the commit carrying the
  root's recorded version, review every later `STRUCTURE.md` change in order,
  apply each migration, validate the result, then update the root marker.
  If the recorded version cannot be found, review the complete
  `STRUCTURE.md` history first.

Agents update only their own root `MEMORY.md`; another agent's stale marker is
a signal for that agent, never permission to edit its tree.

### Structural changelog

Every semantic structural change must update `changelog.md` in the same
logical change. Record both **what changed** and the **context/rationale**,
including important behavioral consequences, migrations, removed assumptions,
or replaced mechanisms. Preserve enough context that a future agent can
understand the transition without reconstructing the original conversation.

`STRUCTURE.md` defines the current model; `changelog.md` explains how and why
it changed. Pure wording, formatting, or typo-only edits that do not alter
behavioral meaning do not require a structural changelog entry.

### What is under version control

Two things here are independently version-controlled; everything else is
plain files on disk.

- **`STRUCTURE.md`** — canonical copy in the public
  `github.com/ederevx/agent-mem-struct` repository, cloned at
  `~/agent-mem-struct/STRUCTURE.md`. Every change must be committed and pushed
  to that remote before the turn that made it ends.
- **`.shared/`** — the directory every agent's `shared/` symlink resolves to
  (`~/agent-mem-struct/.shared/`). It has its own separate **private** Git
  remote and is `.gitignore`d by the public structure repository. Every change
  under `.shared/` must be committed and pushed from within `.shared/` before
  the turn ends.

Both follow each agent's own commit-attribution convention
(human author, `Assisted-by`/`Signed-off-by` trailers, no `Co-authored-by`).
Use one commit per logical change; no fixup/followup commits. `local/` is never
added to either repository.

The user notifies the agent when changes have landed elsewhere; there is no
unprompted polling/sync step.

### Discoverability

Each agent recreates only its own symlinks, all pointing at the canonical
clone:

```sh
ln -sf ~/agent-mem-struct/STRUCTURE.md <agent-home>/STRUCTURE.md
ln -sf ~/agent-mem-struct/STRUCTURE.md <agent-home>/memory/STRUCTURE.md
ln -sf ~/agent-mem-struct/.shared      <agent-home>/memory/shared
```

`<agent-home>` and the exact memory-tree root are agent configuration, not
part of this shared specification.

## Model

The tree is a recursive hierarchy of **memory groups**. Each group may contain:

- **`MEMORY.md`** — mandatory scope manifest: **Scope**, concise
  **Conventions introduced at this scope**, navigation to `nodes/MEMORY.md`,
  and **Submemories**.
- **`nodes/`** — current on-demand facts, findings, decisions, project records,
  rationale, incident detail, and other knowledge. Project/topic collections
  may use their own `MEMORY.md` routing index.
- **`submemory/<name>/`** — child groups with the same shape, used only for a
  genuinely distinct ongoing body of work with its own scope.

There is no separate convention storage. A standing rule that must shape every
task in a group lives inline in that group's `MEMORY.md`; optional explanation
or evidence belongs in `nodes/`.

An active node collection may own one direct `archive/` child for historically
useful knowledge that is no longer current. The archive is always exactly one
directory level below the active collection it belongs to and always has its
own `MEMORY.md` index.

The actual current tree is transient filesystem state (`find memory -name
MEMORY.md` or `tree memory`); this file documents durable shape and rules, not
a snapshot.

### Group `MEMORY.md` and convention inheritance

Every scoped group `MEMORY.md` has these roles:

```markdown
# <group>

**Scope:** ...

## Conventions

<mandatory rules introduced here, or (none)>

## Nodes

[[nodes/MEMORY.md]]

## Submemories

<child groups, or (none)>
```

Before acting in a scoped group, read the full group `MEMORY.md` chain from
the relevant half-root down through every ancestor to the target group, in
that order. Effective mandatory context is the ordered union of conventions
introduced along that path.

A child group contains only conventions introduced, narrowed, or overridden
there. Never duplicate inherited conventions into descendants. A descendant
may override an ancestor inside its narrower scope, but must state the override
explicitly.

Reading a group's `MEMORY.md` must be enough to obey every rule introduced by
that group. Links from a convention may provide optional depth, but must never
hide required behavior.

### Current vs archival knowledge

- **Active nodes** are authoritative for current on-demand knowledge.
- **Archived nodes** preserve useful non-current knowledge: superseded
  decisions, former configurations, disproven hypotheses, regression context,
  failed approaches worth remembering, and similar historical state.

If active and archived knowledge conflict, the active node wins by default.
Archive material is evidence about a prior state or reasoning path, not a
competing source of present truth, unless an active node explicitly reinstates
it.

Normal retrieval prefers active nodes. Read archives when the task concerns
history, prior attempts, regressions, provenance/reasoning, avoiding repeated
work, or when an active node explicitly links to archive material.

Archive is semantic history, not mechanical edit history. Git retains ordinary
revisions; do not archive typo fixes, formatting, rewrites, or routine
refinement. Archive only when the former semantic state is no longer current
and retaining it can materially improve future reasoning.

### Local vs shared

The root `memory/` is not itself a scoped group. Its `MEMORY.md` links exactly
two mandatory child groups, each treated as a half-root with **Scope:** `*`:

- **`local/`** — this agent's private, real directory. Other agents may read it
  for cross-agent context but must never create, edit, or delete within it.
- **`shared/`** — symlink to `~/agent-mem-struct/.shared/`, physically common
  to all agents on the machine and writable by all of them.

Both halves use the same group model recursively. Decide local vs shared before
choosing scope or node placement.

## Leaf memory files

### Principle: structure carries semantics

Leaf metadata is exceptional, not the primary source of truth. An agent must
interpret a leaf from its structure first:

- **local/shared path** → storage/visibility boundary;
- **group path** → scope;
- **`nodes/` vs direct `archive/` path** → current vs historical authority;
- **filename stem** → node identity and `[[link]]` key;
- **parent collection `MEMORY.md`** → concise routing description;
- **Git** → edit history and attribution where versioned;
- **node body** → durable content, optional provenance, chronology, and
  supersession context;
- **`requires_read` frontmatter** → exceptional hard prerequisites.

Do not duplicate these facts back into metadata. When legacy metadata
disagrees with the migrated structure, the structure is authoritative.

### Identity and links

Every leaf filename is kebab-case. Its filename stem is its canonical identity:

```text
warm-reset.md  <->  [[warm-reset]]
```

Filename stems used as link keys must be unique among leaves addressable from
the same memory tree. Add a short subject qualifier when necessary.

A rename changes the link key and therefore requires updating inbound
`[[old-stem]]` links. Moving a file without renaming it does not change its
link key.

Project/topic records split by distinct **subject and activity performed**, not
an arbitrary size threshold. Keep one coherent subject/activity together even
when long; start another leaf when the subject or activity changes. Connect a
multi-leaf sequence with explicit previous/next links when useful.

### Optional `requires_read`

Ordinary leaves have **no frontmatter**.

Use frontmatter only when a node has hard prerequisites that must be read before
the node may be changed:

```yaml
---
requires_read:
  - ../hardware-reference.md
  - reset-rationale.md
---
```

If there are no prerequisites, omit frontmatter entirely. Do not write
`requires_read: []`.

`requires_read` paths are relative to the node unless absolute and must point
to memory files. Before changing a node, read the applicable group
`MEMORY.md` chain, its parent collection `MEMORY.md`, the target itself, and
every `requires_read` path. For a new node, read every path that will enter its
initial `requires_read`. An unavailable prerequisite blocks the change.

Frontmatter is reserved for structural exceptions; do not recreate removed
legacy metadata there.

### Indexes are routing, not duplicate memory

Every active collection `MEMORY.md` is a concise routing index. Give each leaf
one line that describes **what the node contains**, not its conclusions:

```markdown
- [[warm-reset]] — Investigation and current state of warm-reset behavior.
```

The leaf does not repeat that description in metadata. Index summaries must
remain topic/routing descriptions so they do not become stale competing truth.

Archive indexes follow the same rule but clearly label their entries as
historical and non-authoritative.

### Body conventions

Put optional chronology in the body:

```markdown
## Log

- 2026-08-18 — Watchdog hypothesis remained plausible.
- 2026-08-20 — Watchdog hypothesis ruled out.
```

Keep logs concise and oldest-to-newest. They summarize durable transitions;
detailed former reasoning belongs in archive.

An archived node must visibly state why it is historical, for example:

```markdown
**Archived because:** Controlled comparison ruled this hypothesis out.
```

When a clear current successor exists, use a visible body link:

```markdown
**Superseded by:** [[warm-reset]]
```

Creator/session provenance is not mandatory schema. If provenance materially
matters to future reasoning and is not adequately represented by Git/history,
state it in the body.

## Archive directories and indexes

Archive placement is mechanical. Every active node collection — the group's
`nodes/` directory or a nested active project/topic directory with its own
`MEMORY.md` — may own at most one `archive/` directly beneath it.

Historical knowledge must use the archive owned by the same collection that
directly indexes the active knowledge it supersedes:

```text
A/B/current.md
A/B/archive/<historical-memory>.md
```

Do not use an ancestor archive or create a deeper arbitrary archive. If current
knowledge lives in `nodes/project/`, use `nodes/project/archive/`; if it lives
directly in `nodes/`, use `nodes/archive/`.

Whenever `archive/` exists, `archive/MEMORY.md` is mandatory. It is a concise
routing index whose opening text says its entries are historical, belong to
the parent active collection, and are non-authoritative for current truth.
The active parent index links the archive under a separate **Archive** heading.

Archives are terminal: never create `archive/archive/`. Later edits to archived
knowledge are ordinary revisions handled by Git/history.

## Archiving a node

Archive only a former semantic state that is no longer current but still has
durable reasoning value.

1. Read the applicable group chain, parent indexes, target, and prerequisites.
2. If the former state has no durable reasoning value, leave it to ordinary
   edit/Git history.
3. Put the coherent historical content in the direct `archive/` of the active
   collection that owns the corresponding current knowledge.
4. Add a visible `**Archived because:** ...` statement.
5. Add `**Superseded by:** [[current-node]]` when a clear successor exists.
6. Rewrite/update the active node so present truth is explicit.
7. Add a concise body `## Log` transition when useful.
8. Update active/archive indexes.

Do not archive every intermediate thought. Preserve closed reasoning branches
only when remembering them can prevent repeated work or otherwise improve
future reasoning.

## Classifying a new memory

1. **Local vs shared:** choose the physical half first.
2. **Scope:** choose the narrowest existing group whose scope actually covers
   the knowledge.
3. **Mandatory convention vs node:** standing behavior goes inline under the
   group's **Conventions**; facts/findings/decisions/projects/rationale and
   other on-demand material go in `nodes/`. If something is both, inline only
   the concise operational rule and link an active rationale node for detail.
4. **Current vs archived:** new knowledge is active by default. Create archive
   content only when intentionally preserving a non-current semantic state.
5. If no existing group fits and the knowledge represents a distinct ongoing
   body of work, create a submemory; do not create groups for one-off facts.
6. For active nodes, create the leaf and add one routing line to the applicable
   collection index. For archived nodes, use that collection's direct archive
   and archive index.
7. If the change is under `shared/`, commit and push it before the turn ends.

## Adding a new submemory group

1. `mkdir -p <local|shared>/submemory/<name>/nodes`
2. Write `submemory/<name>/MEMORY.md` with **Scope**, inline **Conventions**
   (`(none)` if empty), `[[nodes/MEMORY.md]]`, and **Submemories**
   (`(none)` if empty). Do not copy ancestor conventions.
3. Write `nodes/MEMORY.md` as a concise routing-index stub.
4. Add the child under the parent's **Submemories** list.
5. Create no archive until historical content actually exists.
6. If shared, commit and push before the turn ends.

## Keeping inline conventions concise

Inline conventions are mandatory context for every task in scope, so
**conciseness is mandatory**. A convention contains:

- the rule;
- at most a short **Why:** when useful; and
- only the minimum **How to apply:** detail required to obey it.

A convention must remain operationally complete without following any link.
Never hide required behavior in an on-demand rationale node.

If deeper explanation is useful, link an active node and create one when no
suitable node exists. That node may hold current rationale, evidence, examples,
edge cases, or application guidance. The link remains optional depth.

Keep current justification separate from historical evolution. An active
rationale node explains why the rule is justified now. Superseded rationale,
former rules, rejected alternatives, and investigation history belong in that
active collection's direct archive when worth retaining.

## Migrating existing `conventions/` directories

The `2026-08-20T16:14:00-04:00` migration removes `conventions/` from every
memory group. Apply it group by group after the version handshake:

1. Read the old group/index/convention files and any nodes affected.
2. Inline each standing rule concisely under the group's **Conventions**,
   preserving only rules introduced at that scope.
3. Move worthwhile current explanation into an active node and link it from
   the convention.
4. Move worthwhile non-current explanation into the corresponding active
   collection's direct archive.
5. Make the group `MEMORY.md` contain **Scope**, inline **Conventions**,
   `nodes/MEMORY.md` navigation, and **Submemories**.
6. Remove obsolete convention leaves/indexes and the empty `conventions/`
   directory; validate no live path or dependency still expects it.
7. If shared, commit/push the migration before advancing the root
   `Structure-Version:`.

## Migrating legacy leaf metadata

The `2026-08-20T16:24:00-04:00` migration removes the old general-purpose leaf
schema. Apply it before advancing an agent's root `Structure-Version:`.

For each leaf under `nodes/`:

1. **Preserve link identity first.** If legacy `name:` differs from the
   filename stem, rename the file to `<name>.md` (or an equivalent unique
   kebab-case filename using the same old link key) before removing `name:`.
   Existing `[[name]]` links should continue to resolve without a global link
   rewrite.
2. Move useful `description:` text into the parent `MEMORY.md` routing line,
   phrased as what the node contains rather than a factual conclusion.
3. If `requires_read` is empty, remove it and all frontmatter. If non-empty,
   retain only `requires_read`.
4. Remove `node_type`, `type`, `originAgent`, `originSessionId`, `topics`,
   `lifecycle`, and `modified`. Their former roles are implicit, redundant, or
   non-mandatory under the new model.
5. Convert `superseded_by` into a visible `**Superseded by:** [[...]]` body
   line when present.
6. Convert useful `log` entries into a concise body `## Log` section,
   oldest-to-newest.
7. Verify authority from placement: active knowledge must be outside
   `archive/`; historical knowledge must be in the direct archive owned by its
   active collection. Resolve any legacy metadata/path disagreement in favor
   of the intended current-vs-historical state, then let path be authoritative.
8. Preserve creator/session provenance in the body only when it materially
   matters and is not adequately represented elsewhere.
9. Validate filename-link uniqueness, indexes, prerequisite paths, and
   active/archive placement before advancing the version marker.

After migration, do not reintroduce removed metadata simply because older
history used it.

---

© 2026 Edrick Sinsuan. Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — see [LICENSE](LICENSE). This notice must be preserved in every copy, fork, or derivative of this file.
