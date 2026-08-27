# Changelog

Delta history for the memory protocol. Each entry documents what changed and
why, while `STRUCTURE.md` and `RULES.md` describe only the current model and
current mandatory behavior.

## 2026-08-27T09:35:43-04:00 — shared memory is always read

Agents read the shared half only during memory work, so canonical Mandatory
conventions that bind every agent (commit attribution, scratch hygiene) were
missed in ordinary sessions. Rule 2 now makes the shared half an always-read:
load `shared/MEMORY.md` and its mandatory groups at the start of every session
and task, memory work or not.

No memory-tree content migration is required.

## 2026-08-26T12:39:09-04:00 — keep attribution out of the structural specification

The **Version control** section listed the commit-attribution trailers
themselves — human author, `Assisted-by` and `Signed-off-by`, no
`Co-authored-by` — while the same rule already bound from each agent's memory
conventions. A rule stated in two places drifts silently in one of them, and
this copy also pushed a machine-local convention into a specification that is
meant to describe shape and invariants only.

- Reduced the paragraph to what belongs here: both repositories follow the
  agent's own attribution convention, wherever the agent records it, and keep
  one commit per logical change. The trailers are no longer named.
- Left the rule itself untouched. Agents that held it only by way of this
  document must record it in their own **Mandatory conventions**, which the
  migration entry requires before the marker advances.
- Generalized the lesson into a shared convention: a convention records only
  what is genuinely new, or an explicit narrowing or override, and one whose
  whole content points at a rule already in force is deleted.

No memory-tree content migration is required.

## 2026-08-21T17:03:00-04:00 — compact mandatory rules and expose root `RULES.md`

`RULES.md` is paid as mandatory context on every memory task, so allowing it to
grow like an explanatory specification would recreate the same reliability
problem it was introduced to solve.

- Compressed the checklist from fourteen rules to eight without removing the
  protocol-version check, mandatory convention chain, on-demand node policy,
  paired-log trigger, prerequisites, routing discipline, classification, or
  shared-tree write boundary.
- Made compactness itself mandatory: new or changed rules must be concise,
  action-oriented, non-duplicative, and operationally complete. Agents should
  consolidate existing rules before adding another when possible.
- Directed rationale, examples, and historical explanation out of `RULES.md`
  into `STRUCTURE.md`, `changelog.md`, or on-demand memory so the mandatory
  checklist stays cheap to read.
- Added a direct `<agent-home>/RULES.md` symlink beside the existing root
  `STRUCTURE.md` symlink. The memory directory does not gain duplicate
  structural-document symlinks; `memory/shared` remains only as data topology.
- Kept `memory/MEMORY.md` minimal: it still monitors `Structure-Version` and
  points at `../STRUCTURE.md`; the root `RULES.md` path is a fixed protocol
  convention rather than another metadata field.

No memory-tree content migration is required. Agents only add/refresh the root
`RULES.md` symlink, read the compact checklist, and advance their applied
protocol marker.

## 2026-08-21T16:34:28-04:00 — pair every memory with a log and add mandatory `RULES.md`

Two reliability failures had become visible in deployed memory trees. First,
archive handling depended on the agent recognizing that an ordinary node edit
should become a structural archival action, so superseded state often remained
inside active nodes and `## Log` sections instead of moving to `archive/`.
Second, mandatory conventions could still be missed because routine memory work
did not have a single compact mandatory operational checklist independent of
the larger structural specification.

- Replaced the archive model with deterministic same-name `log/` counterparts.
  Every active `.md` at a memory directory level now has
  `log/<same-filename>.md`; active files hold current truth and logs hold
  displaced semantic history.
- Made the history trigger unconditional for semantic edits: before rewriting
  active memory, an agent must determine whether existing semantic state is
  becoming non-current and preserve that state in the paired log first.
- Removed competing history mechanisms from the current model: no `archive/`
  trees, no active-body `## Log` chronology, no archive indexes, and no
  lifecycle/supersession storage conventions. Git remains mechanical edit
  history; paired logs are semantic history.
- Made logs terminal and on-demand. They have no `log/log/`, need no routing
  index because lookup is deterministic, and never override current memory.
- Added `RULES.md` as the mandatory operational checklist for every memory
  task. It centralizes the protocol-version check, mandatory-convention read
  order, on-demand node policy, semantic-log trigger, prerequisites, routing,
  classification, and shared-tree write discipline.
- Changed the protocol control path so the agent root `memory/MEMORY.md`
  explicitly points to `../STRUCTURE.md` and monitors the canonical
  `Structure-Version` on every memory task. Semantic changes to either
  `STRUCTURE.md` or `RULES.md` now bump that one protocol version.
- Reduced structural-document discoverability to one root `STRUCTURE.md`
  symlink. The old duplicate `memory/STRUCTURE.md` symlink is removed;
  `memory/shared` remains because it is the shared-data topology link rather
  than a documentation alias.
- Kept document responsibilities separate: `RULES.md` says what agents must do
  routinely, `STRUCTURE.md` says what the tree is, `MIGRATION.md` says how to
  upgrade older trees, and this changelog says why the protocol changed.

The goal is to remove optional judgment from history preservation and routine
rule enforcement. A memory update now has a fixed historical destination, and
every memory task has one short mandatory operational entry point.

## 2026-08-20T19:43:49-04:00 — label mandatory conventions explicitly

The group-rule heading `Conventions` did not visibly distinguish mandatory
inherited instructions from optional practices or on-demand context, even
though the structure already required agents to read and obey every rule in
the applicable group chain.

- Renamed the scoped group heading to **Mandatory conventions**.
- Updated the current structure model, group template, classification rules,
  submemory creation instructions, and README to use the explicit label.
- Added a migration requiring each agent to rename the heading in its own
  local half and in the shared half before advancing its applied-version
  marker.
- Kept prior changelog entries and migration instructions unchanged as
  historical records of the terminology used at those versions.

This is a labeling clarification, not a change to inheritance or enforcement:
all rules introduced by an applicable group remain mandatory.

## 2026-08-20T16:34:00-04:00 — extract migration procedures into `MIGRATION.md`

As structural changes accumulated, `STRUCTURE.md` had begun carrying both the
current model and detailed procedures for converting older trees. Those are
different agent tasks: routine structural understanding needs only the current
model, while migration detail matters only when an applied version is stale.
Keeping both together made the canonical spec longer and made historical
upgrade procedures compete with current rules for attention.

- Added `MIGRATION.md` as the canonical ordered home for version-to-version
  tree transformations.
- Changed the applied-version handshake so a stale agent reads and applies
  `MIGRATION.md` entries in ascending `Structure-Version` order before
  advancing its root marker.
- Established explicit document roles: `STRUCTURE.md` says what is true now,
  `MIGRATION.md` says how to reach it from an older version, and
  `changelog.md` preserves why the transition happened.
- Required every future `Structure-Version` bump to have a matching
  `MIGRATION.md` entry; versions that require no tree mutation record an
  explicit no-op entry rather than leaving the agent to guess.
- Moved the existing direct-archive, convention-inlining, and leaf-schema
  migration procedures out of `STRUCTURE.md` and into `MIGRATION.md` without
  changing their intended tree semantics.
- Recorded this version itself as a no-tree-mutation migration: agents already
  at `2026-08-20T16:24:00-04:00` only adopt the new documentation/handshake
  convention.
- Updated the README to expose the three-document split.

The goal is to keep `STRUCTURE.md` compact and current while retaining precise,
agent-executable migration procedures separately.

## 2026-08-20T16:24:00-04:00 — make structure authoritative and collapse leaf metadata

The leaf schema had accumulated fields for identity, description, taxonomy,
creator/session provenance, topics, lifecycle, supersession, chronology, and
modification time. After the local/shared split, convention inlining, and
direct archive model, many of those values duplicated information already
encoded more reliably by the filesystem, indexes, node body, or Git. Keeping
both representations increased write cost and created disagreement states an
agent then had to reconcile.

- Made **structure carries semantics** an explicit agent rule. Local/shared
  placement expresses the storage/visibility boundary, group path expresses
  scope, `nodes/` versus direct `archive/` expresses current authority, and the
  parent `MEMORY.md` provides routing context.
- Replaced `name:` with the kebab-case filename stem as the canonical
  `[[link]]` key. The migration preserves existing links by renaming legacy
  files to their old `name:` before deleting the field when they differ.
- Removed mandatory `description:` from leaves; concise descriptions now live
  only in collection indexes and must describe what a node contains rather
  than restating conclusions.
- Made frontmatter optional and reserved it for non-empty `requires_read`
  prerequisites. Ordinary leaves are plain Markdown with no frontmatter.
- Removed `node_type`, `type`, `originAgent`, `originSessionId`, `topics`,
  `lifecycle`, and `modified` from the live leaf schema. Their useful roles are
  implicit in structure, non-mandatory, or better handled by Git/body content.
- Moved `superseded_by` to a visible `**Superseded by:** [[...]]` body line
  and moved durable `log` entries to an optional body `## Log` section.
- Made archive placement alone authoritative for historical status; removed the
  duplicate lifecycle flag so path and metadata can no longer disagree.
- Added explicit agent interpretation and legacy-migration rules, including
  the instruction not to recreate removed metadata merely because older nodes
  or history contain it.
- Kept creator/session provenance available as ordinary body content only when
  it materially matters and is not adequately represented elsewhere.
- Updated the README to describe the structure-first leaf model.

The goal is that metadata exists only for information the structure cannot
already express. This lowers token/write overhead, reduces synchronization
invariants, and gives agents one source of truth for each semantic property.

## 2026-08-20T16:14:00-04:00 — inline mandatory conventions into group `MEMORY.md`

Conventions were mandatory for every task in a group's scope but lived behind
a group index, `conventions/MEMORY.md`, and one or more convention leaves.
That indirection offered no legitimate retrieval selectivity: an agent still
had to read all applicable rules, while every extra traversal added tool-call
cost and another opportunity to stop before required context was loaded.

- Removed the `conventions/` directory from the recursive group model.
- Made each scoped group `MEMORY.md` the mandatory scope manifest containing
  its **Scope**, concise conventions introduced at that scope, navigation to
  `nodes/MEMORY.md`, and its child submemories.
- Made convention inheritance an explicit read algorithm: before acting in a
  target group, read every group `MEMORY.md` from the relevant half-root down
  to the target. Descendants do not duplicate inherited rules; a narrower
  descendant override must say explicitly that it overrides the ancestor.
- Made convention conciseness a hard requirement because every task in scope
  pays that context cost. A convention must still be operationally complete
  without following links: rule, optional short rationale, and only the
  execution detail needed to obey it.
- Defined optional explanatory links from conventions to **active nodes** for
  detailed current rationale, evidence, examples, and edge-case analysis.
  Create such a node when the depth is worth preserving and no suitable node
  exists; the node remains on-demand and never becomes mandatory merely
  because the convention links it.
- Separated current justification from historical evolution. Active rationale
  nodes explain why a rule is justified now; superseded rationale, former
  rules, rejected alternatives, and investigation history belong in the
  corresponding direct archive when worth retaining.
- Restricted leaf frontmatter to actual `nodes/` leaves; group `MEMORY.md`
  files are scope manifests, not leaf memories.
- Added an explicit migration procedure for existing trees: inline concise
  rules, move worthwhile current detail into active nodes, archive worthwhile
  non-current reasoning separately, remove obsolete convention indexes/leaves,
  and delete each migrated `conventions/` directory only after validation.
- Updated the README to describe the current model rather than the removed
  convention-directory and older linking/marker concepts.

The architectural intent is that mandatory information is structurally present
at the scope entry point, while optional current and historical knowledge stay
behind node retrieval. This reduces procedural failure modes without inflating
mandatory context with explanatory history.

## 2026-08-20T16:13:00-04:00 — make archive placement a direct-parent invariant

The initial archive lifecycle allowed any active node collection to own an
archive but told agents to use the "nearest coherent" one. That still left a
classification decision whenever both a broader `nodes/archive/` and a nested
project/topic archive were plausible. Historical knowledge could therefore
drift away from the exact active memory collection whose truth it superseded.

- Made archive placement mechanical: the active collection whose `MEMORY.md`
  directly indexes the current knowledge owns its archive as a direct
  `archive/` child.
- Defined the path rule as `A/B/current.md` →
  `A/B/archive/<historical-memory>.md`; nested project/topic memories use their
  own direct archive rather than an ancestor's.
- Kept `archive/MEMORY.md` mandatory and required the active parent index to
  expose it separately so historical entries never look equally authoritative.
- Made archives terminal historical collections: `archive/archive/` is
  forbidden. Later revisions to archived content belong to Git rather than
  another archival layer.
- Updated the archiving procedure and classification rules so archive location
  no longer depends on an agent judging which archive is "nearest" or most
  coherent.

This refines placement only; the active-vs-archived authority model,
`lifecycle`, optional `superseded_by`, and on-demand retrieval semantics remain
unchanged.

## 2026-08-20T15:57:00-04:00 — add active/archive lifecycle for node knowledge

The node model could preserve edit timestamps and concise chronological
`log:` entries, but neither mechanism told an agent whether a retrieved claim
was authoritative now or merely useful historical context. That left stale
facts, replaced decisions, and disproven hypotheses capable of competing with
current knowledge during retrieval. At the same time, deleting them outright
would lose valuable negative findings and make future agents more likely to
repeat already-closed investigations.

- Added a binary `metadata.lifecycle: active|archived` distinction for nodes.
  New nodes declare it explicitly; legacy nodes without the field are treated
  as active for backward compatibility, so adopting the protocol does not
  require rewriting every existing leaf at once.
- Added `archive/` beneath `nodes/` collections, including nested project/topic
  node directories. Every archive has its own `MEMORY.md` and is indexed
  separately from active nodes so historical entries do not look equally
  authoritative in routine navigation.
- Active nodes now win conflicts with archived nodes by default. Normal
  retrieval prefers active knowledge; archives are on-demand for history,
  regressions, provenance/reasoning, prior attempts, or avoiding repeated
  work.
- Added optional `superseded_by:` metadata on archived nodes using the same
  logical `name:` key used by `[[name]]` links. The direction is deliberately
  one-way to avoid maintaining redundant bidirectional graph state.
- Kept `log:` distinct from archive storage: logs remain concise chronological
  state/event summaries, while archives preserve detailed former semantic
  states when they still have reasoning value.
- Explicitly separated semantic archival knowledge from Git history. Typos,
  rewrites, formatting changes, and ordinary revisions stay in Git; archive a
  former state only when it is no longer current and remembering it can
  materially improve future reasoning.
- Did not add confidence scores, fine-grained states such as `rejected` vs
  `superseded`, or required validity dates. The goal is a simple agent-facing
  current-vs-historical distinction without turning memory into a temporal
  database or forcing agents to invent unavailable precision.
- Made contextual changelog maintenance itself a structural invariant:
  semantic changes to `STRUCTURE.md` must update this file in the same logical
  change with enough rationale and migration context for future agents to
  understand why the transition occurred.

## 2026-08-19T16:43:00-04:00 — split project memory by subject and activity, not size

The immediately preceding 500-word cap made leaf size predictable, but it
created an arbitrary storage boundary that could force one coherent subject or
performed activity to be fragmented simply because its explanation was long.
That made an agent reconstruct a single unit of work from multiple leaves even
when there was no semantic reason to split it.

- Removed the 500-word leaf limit introduced at `2026-08-19T16:33:35-04:00`.
- Made **subject and activity performed** the criterion for splitting project
  records: keep one coherent subject/activity together even when long, and
  start another leaf when the subject or activity changes.
- Retained project/topic directories, concise directory `MEMORY.md` indexes,
  topical leaf names, and explicit previous/next links when a project record
  genuinely spans multiple coherent leaves.

This superseded only the arbitrary word-count boundary; the surrounding
organization rules from the prior structural change remained in effect.

## 2026-08-19T16:33:35-04:00 — bound and organize project/topic leaves

As project memories grew, flat collections and taxonomy-prefixed names made it
harder for agents to identify the actual subject of a leaf and to navigate
long-running work coherently. This change introduced explicit project/topic
organization and, initially, a hard size limit intended to keep retrieval
units small.

- Allowed related conventions to be collected in topic directories with their
  own `MEMORY.md` indexes.
- Required ongoing projects under `nodes/` to use kebab-case project
  directories with concise indexes and linked topical leaves when the record
  spans multiple parts.
- Required leaf `name:` values to describe the subject itself rather than
  storage taxonomy such as `project-`, `feedback-`, or parent-directory
  prefixes, except for a short qualifier when needed for global uniqueness.
- Introduced a 500-word total limit per leaf, including frontmatter, as the
  initial mechanism for bounding retrieval units.

The word-count rule was intentionally short-lived: it was replaced at
`2026-08-19T16:43:00-04:00` by semantic splitting based on subject/activity.
The project/topic organization and topical naming rules remained.

## 2026-08-19T16:29:08-04:00 — add topical classification and durable event logs

The existing leaf schema described ownership and broad memory type, but it did
not provide a stable subject classification for grouping related knowledge or
an explicit place for concise, searchable state transitions. Agents otherwise
had to infer topic from paths/body prose and either bury chronology in the
body or overload `modified:` as if it described semantic history.

- Added `metadata.topics` as the canonical leaf-level topical classification,
  using stable kebab-case subject labels ordered primary to secondary.
- Kept physical groups responsible for scope and mandatory-read boundaries;
  topics organize related material within those scopes instead of replacing
  the hierarchy.
- Added optional chronological `metadata.log` entries with a `date` and concise
  `event` for durable state transitions and incident outcomes.
- Kept `modified:` strictly as edit time, and kept detailed explanation,
  current state, and actionable information in the body rather than turning
  `log` into transcript or edit history.
- Directed indexes with multiple topics to group links by primary topic and
  allowed crosslinks to connect related nodes without duplicating content.

## 2026-08-18T16:00:00-04:00 — `.shared/` gets a private remote; commit-and-push made mandatory

An audit pass over the freshly-split trees found an edit sitting
uncommitted in `.shared/` from a prior session — the "commit right after
editing" rule existed only in `STRUCTURE.md`'s version-control section,
nowhere an agent actually reads before touching a node, so it went
unfollowed. Also found four references inside shared kernel nodes pointing
at `[[feedback-commit-convention]]` and similar `[[...]]` names that the
split had moved into Claude's private `local/` — unresolvable to any other
agent.

- `.shared/` now has its own **private** remote (previously local-only, no
  remote) — a repository distinct from this one, its location deliberately
  not recorded here since it may carry personal or sensitive content. Every
  edit under `.shared/` must now be committed **and pushed** to that remote
  before the turn ends, not just committed.
- Moved the commit-after-edit rule into `.shared/conventions/memory_change_protocol.md`
  — the file both agents actually read as part of the mandatory node-edit
  protocol — with explicit per-agent trailer guidance and a narrow-staging
  warning (another agent's uncommitted work may already be sitting in the
  same working tree; never `git add -A` blindly).
- Fixed the four broken `[[feedback-commit-convention]]`-family links inside
  shared kernel nodes by replacing the specific cross-boundary link name
  with generic prose ("the agent's own commit-attribution convention") —
  each agent keeps a differently-named copy of that convention in its own
  `local/`, so a shared node can reference the concept but not a specific
  agent's private node.
- One pre-existing dead link, `[[local-coding-model]]` in
  `submemory/msm8998-kernel/nodes/lineage_419_port.md` (`.shared/`, not this
  repo), predates this migration and was left as-is rather than guessed at.

## 2026-08-18T14:10:00-04:00 — local/shared split, drop linking protocol

Replaced the single-tree-with-`visibility`-flags model with a physical
local/shared split, per-agent:

- Every agent's memory root now holds exactly two mandatory child groups:
  `local/` (private, real directory, mutually read-only across agents) and
  `shared/` (a symlink to one common directory, `~/agent-mem-struct/.shared/`,
  that every agent reads and writes directly).
- `.shared/` physically consolidates all content that was previously marked
  `visibility: shared` and duplicated (via linked stubs) across agents'
  trees. Content was migrated mechanically (frontmatter/marker stripping via
  script, no manual retyping) to avoid transcription drift on large files.
  Where both agents had a copy, Claude's side was kept as the source (Codex's
  side was, in every case, already just a linking stub pointing back to it);
  Codex's genuinely original shared nodes were carried over as-is.
- `.shared/` is now its own local-only git repository (`git init`, no
  remote) — it lives inside the `agent-mem-struct` working tree but is
  excluded from that repo via `.gitignore`, since `agent-mem-struct` is
  public and `.shared/` is personal infra/project content.
- Dropped, as made redundant by having one physical shared copy instead of
  N per-agent copies:
  - the `visibility: shared|private` frontmatter field (privacy is now
    structural — which directory a node lives in — not a flag on it);
  - the cross-agent linking protocol (linked stubs, `**Origin:**`/
    `**Linked-modified:**` provenance lines, source-drift detection);
  - numbered marker blocks (`N: ***...***`) used to anchor a linking stub to
    one sub-part of a source node;
  - the `Tree-Version:`/`Tracked-Tree-Version:` handshake used to detect
    newly-added shared nodes across trees needing a link.
- Kept unchanged: the `Structure-Version:` handshake (canonical
  `STRUCTURE.md` timestamp vs. each agent's root `MEMORY.md` first line);
  the recursive group model (`MEMORY.md`/`conventions/`/`nodes/`/
  `submemory/<name>/`); the leaf frontmatter fields `name`, `description`,
  `requires_read`, and `metadata.{node_type, type, originAgent,
  originSessionId, modified}`.
- Added a third per-agent discoverability symlink,
  `<agent-home>/memory/shared` → `~/agent-mem-struct/.shared`, alongside the
  two pre-existing `STRUCTURE.md` symlinks.