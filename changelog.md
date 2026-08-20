# Changelog

Delta history for `STRUCTURE.md`. Each entry documents *what changed and
why*, so `STRUCTURE.md` itself only needs to describe the current model, not
narrate how it got there.

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
