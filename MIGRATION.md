# Memory migrations

Canonical ordered procedures for moving an older memory tree to a newer
`Structure-Version`.

`RULES.md` defines mandatory operational behavior, `STRUCTURE.md` defines the
current model, this file defines the transformations needed to reach it, and
`changelog.md` records why those transformations were made.

## How agents use this file

1. Compare canonical `STRUCTURE.md` with the agent root
   `memory/MEMORY.md` marker.
2. If they differ, apply every migration newer than the recorded version in
   ascending timestamp order through the canonical version.
3. Read the affected group/index/target files and prerequisites required by
   each migration before writing them.
4. Validate each migration before proceeding to the next.
5. Only after all applicable migrations are complete may the agent update its
   root `Structure-Version:` marker.

Every future protocol version bump must add a matching entry here in the same
logical change. If no memory-tree mutation is required, say so explicitly.

If an agent's recorded version predates the earliest migration documented here,
or the marker cannot be resolved in repository history, review the relevant
`STRUCTURE.md` Git history and `changelog.md` first, bring the tree to the
starting point covered here, then continue with these entries.

## 2026-08-21T16:34:28-04:00 — paired logs and mandatory `RULES.md`

Replace discretionary archive/history handling with deterministic per-file
logs, make `RULES.md` mandatory for every memory task, and reduce structural
document symlinks to one at the agent root.

### Protocol entry and symlinks

1. Add the new canonical `RULES.md` beside `STRUCTURE.md`.
2. Keep only the structural-document symlink
   `<agent-home>/STRUCTURE.md -> ~/agent-mem-struct/STRUCTURE.md`; remove the
   former `<agent-home>/memory/STRUCTURE.md` symlink.
3. Keep `<agent-home>/memory/shared -> ~/agent-mem-struct/.shared`; this is the
   shared-data topology link, not a structural-document symlink.
4. Make the agent root `memory/MEMORY.md` begin with:

   ```text
   Structure-Version: <currently-applied-version>
   Structure: ../STRUCTURE.md
   ```

   Do not advance the version yet.
5. Adopt the routine entry rule: on every memory task read this root control
   header, resolve `Structure`, compare versions, apply migrations when stale,
   then read `RULES.md` from beside the resolved canonical `STRUCTURE.md`
   target before scoped memory work.

### Replace archive/history with paired logs

Apply recursively to `local/` and `shared/`.

1. For every memory directory containing active `.md` files, create a direct
   `log/` child if absent.
2. For every active `.md` directly in that directory, create
   `log/<same-filename>.md` if absent. This includes group and collection
   `MEMORY.md` files. An empty counterpart may contain only:

   ```markdown
   # Log: <name>

   No semantic history yet.
   ```
3. Move active-body `## Log` chronology into the same-named counterpart,
   preserving useful semantic transitions in chronological order. Remove that
   active-body history after verifying the transfer.
4. Migrate existing `archive/` content before deleting archives:
   - when an archived record is clearly a former state of an active file,
     merge its useful historical content into that active file's
     `log/<same-filename>.md`;
   - when no active counterpart remains, move the historical record to
     `log/<archived-filename>.md` as a retired log that may outlive its active
     identity;
   - preserve enough context to understand what changed and why, but do not
     mechanically retain redundant snapshots when Git already carries them.
5. Remove obsolete `archive/MEMORY.md`, archive indexes, archive-only routing,
   lifecycle/supersession conventions, and the now-empty `archive/`
   directories after validating their history is represented in paired logs.
6. Do not create `log/log/`. Log files are terminal historical records and do
   not receive their own counterparts.
7. Update active indexes so they route only current nodes. Logs are not indexed
   because lookup is deterministic by `log/<same-file>`.
8. For every future semantic edit, apply the mandatory trigger in `RULES.md`:
   preserve displaced current state in the paired log before rewriting the
   active file.
9. Validate that every active memory `.md` has exactly one same-named log
   counterpart and that no active file retains semantic-history `## Log`
   sections or depends on `archive/`.
10. If changing `shared/`, commit and push the shared-tree migration before
    advancing the agent root protocol marker.
11. After both applicable halves are migrated and validated, set the agent root
    marker to `2026-08-21T16:34:28-04:00`.

## 2026-08-20T19:43:49-04:00 — label mandatory conventions explicitly

Make the mandatory nature of inherited group rules visible in every group
manifest.

1. Rename every scoped group heading from `## Conventions` to
   `## Mandatory conventions` without changing the rules beneath it.
2. Update current instructions, templates, and routing prose that name the
   heading so they use **Mandatory conventions**.
3. Preserve historical changelog and older migration wording as historical
   context; do not rewrite earlier records to imply they used the new label.
4. Validate that no active group manifest retains `## Conventions`.
5. After applying the heading migration to the agent's local half and the
   shared half, update only that agent's root `Structure-Version:` marker.

## 2026-08-20T16:13:00-04:00 — direct-parent archive placement

Ensure archival knowledge is structurally local to the active collection it
belongs to.

1. For every active node collection that has historical content, ensure its
   archive is a direct `archive/` child of that same collection.
2. Move archived material out of ancestor or arbitrarily deeper archives into
   the direct archive owned by the collection whose `MEMORY.md` indexes the
   corresponding active knowledge.
3. Ensure every archive has `archive/MEMORY.md` and that the active parent index
   links it under a separate **Archive** heading.
4. Remove any nested `archive/archive/`; archives are terminal historical
   collections.
5. Validate active-vs-archived authority after moves and repair affected links
   or paths.

## 2026-08-20T16:14:00-04:00 — inline mandatory conventions

Remove `conventions/` from every memory group and make the group's `MEMORY.md`
the mandatory scope manifest.

Apply group by group:

1. Read the old group `MEMORY.md`, `conventions/MEMORY.md`, every convention
   leaf it indexes, `nodes/MEMORY.md`, and any node that will be reused or
   changed.
2. Inline each standing rule concisely under the group's **Conventions**
   section. Preserve only rules introduced at that scope; do not copy inherited
   ancestor rules into descendants.
3. If a convention contains worthwhile current explanation, move that detail
   into an active node and link it from the inline convention. The inline rule
   must remain operationally complete without following the link.
4. Move worthwhile non-current rationale, former rules, rejected alternatives,
   or investigation history into the direct archive of the active collection
   that owns the corresponding current explanatory node.
5. Make the group `MEMORY.md` contain **Scope**, inline **Conventions**,
   navigation to `nodes/MEMORY.md`, and **Submemories**.
6. Remove obsolete convention leaves/indexes and the now-empty `conventions/`
   directory.
7. Validate that no live path, index, instruction, or dependency still expects
   `conventions/`.
8. If the migrated group is under `shared/`, commit and push the shared-tree
   migration before continuing.

## 2026-08-20T16:24:00-04:00 — collapse legacy leaf metadata

Remove the old general-purpose leaf schema and make structure/body/indexes the
canonical sources of leaf semantics.

For each leaf under `nodes/`:

1. **Preserve link identity first.** If legacy `name:` differs from the
   filename stem, rename the file to `<name>.md` (or an equivalent unique
   kebab-case filename preserving that old link key) before deleting `name:`.
   Existing `[[name]]` links should continue to resolve without a global link
   rewrite.
2. Move useful `description:` text into the parent `MEMORY.md` routing line,
   phrased as what the node contains rather than a factual conclusion.
3. If `requires_read` is empty, remove it and all frontmatter. If non-empty,
   retain only `requires_read`.
4. Remove `node_type`, `type`, `originAgent`, `originSessionId`, `topics`,
   `lifecycle`, and `modified`. Their former roles are implicit, redundant, or
   non-mandatory under the current model.
5. Convert `superseded_by` into a visible
   `**Superseded by:** [[current-node]]` body line when present.
6. Convert useful `log` entries into a concise body `## Log` section,
   oldest-to-newest.
7. Verify authority from placement: active knowledge must be outside
   `archive/`; historical knowledge must be in the direct archive owned by its
   active collection. Resolve any metadata/path disagreement in favor of the
   intended state, then let path be authoritative.
8. Preserve creator/session provenance in the body only when it materially
   matters and is not adequately represented elsewhere.
9. Validate filename-link uniqueness, indexes, prerequisite paths, and
   active/archive placement.

After this migration, do not reintroduce removed metadata merely because older
history used it.

## 2026-08-20T16:34:00-04:00 — extract migration procedures

No memory-tree mutation is required.

Migration procedures moved from `STRUCTURE.md` into this canonical
`MIGRATION.md`. Agents upgrading from `2026-08-20T16:24:00-04:00` only need to:

1. Read the current `STRUCTURE.md` and this migration entry.
2. Adopt the document-role rule: `STRUCTURE.md` = current model,
   `MIGRATION.md` = upgrade procedures, `changelog.md` = historical rationale.
3. Use `MIGRATION.md` for future version mismatches.
4. After validation, advance the agent's root marker to
   `2026-08-20T16:34:00-04:00`.

---

© 2026 Edrick Sinsuan. Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — see [LICENSE](LICENSE).