# Root-memory hook layer

This directory is an **additive runtime reinforcement layer** for the existing
`agent-mem-struct` protocol. It does not replace, duplicate, or version the
memory structure.

The canonical chain remains unchanged:

```text
<agent-home>/memory/MEMORY.md
  -> Structure: ../STRUCTURE.md
  -> Shared: <absolute native path>
<agent-home>/RULES.md
```

`STRUCTURE.md`, `RULES.md`, `MIGRATION.md`, and the root memory tree remain
authoritative. Root `STRUCTURE.md` and `RULES.md` are protected
installer-managed regular-file copies on every platform.

## What the hook does

The shared hook `root-memory-context.py`:

1. reads the agent's existing root `memory/MEMORY.md`;
2. resolves and validates its `Structure:` target against root `STRUCTURE.md`;
3. for a current-version root, parses `Shared:` as a literal, unquoted absolute
   native path without expansion, locates that exact pre-existing physical
   directory, rejects a terminal symlink, junction, or reparse-point alias,
   and validates its root mandatory conventions while allowing ancestor path
   components to resolve normally;
4. reads root `RULES.md`;
5. compares the applied and canonical `Structure-Version` values;
6. verifies the installed root rules and structure content against the
   canonical documents beside the running hook, catching detached hardlinks
   and stale copies that a version-only comparison misses;
7. injects a short read catalog (the required source paths and navigation
   rules, never the convention bodies) at session start and on each user turn;
8. injects the same catalog, plus the read-only boundary, into spawned
   subagents;
9. gates mutating and unknown actions, including writes embedded in an
   interpreter one-liner or heredoc rather than a shell redirect, and fails
   closed when the root authority is missing or malformed;
10. resolves the full convention set for each mutation, adding ancestor group
   manifests and recursive `requires_read` prerequisites for scoped memory
   writes;
11. records each convention source the agent opens with its read tool (a bare
    `cat`, a complete `sed -n` range, or `print(open(...).read())` count for a
    shell-only host) and each complete structure read of the declared shared
    root - the shipped `hooks/rm_tree.py` lister, `tree`, `find DIR [-print]`,
    `ls -aR`, or PowerShell `Get-ChildItem -Recurse -Force` - then refuses a
    mutation or turn completion until every current source and the shared
    structure have been read;
12. reports a stale structure as a mandatory migrate-first condition without
    hard-blocking the migration itself;
13. exposes the declared shared-memory directory, including whether it is an
    available Git worktree, so requested durable cross-agent records can be
    inserted directly instead of remaining only in a session or artifact
    upload;
14. writes a bounded, per-session continuity checkpoint immediately before
    manual or automatic compaction, refusing a manual compaction it cannot
    checkpoint and warning about an automatic one;
15. restores that checkpoint together with the authoritative root memory after
    compaction at the compact-sourced session start; and
16. deletes a checkpoint after successful restoration and scavenges
    crash-orphaned checkpoints after seven days.
17. refuses a memory commit or a shell `git push` whose commit message breaks
    the mechanical convention, and reports a message-shape deviation in the
    publish attestation only as advice.
18. appends a read-only leftovers report at a non-compaction session start:
    the local-only commits of repositories this agent touched, plus stale
    immediate children of the scratch root; and
19. runs the convention judge after a Pi settle (on by default for every host,
    with one shared settings document and the `/judge-settings` surface over
    it), recording only the rule ids and string offsets of evidence it could
    validate against the supplied turns, never command-authored text.

The runtime hook creates no documents. The installer manages only the two root
`RULES.md` and `STRUCTURE.md` copies described above; it does not add another
`MEMORY.md`, create a shared directory, or add aliases under `memory/`.

## Codex only

From the repository root:

```sh
python3 hooks/codex/manage.py install
```

This also deploys managed regular-file copies of root `RULES.md` and
`STRUCTURE.md`. A reinstall safely refreshes an unchanged installer-owned copy
and replaces a legacy matching root symlink. It refuses foreign files,
user-edited copies, and symlinks to other targets rather than overwriting them.

For a known managed regular copy installed before ownership markers existed,
one explicit bootstrap may be needed:

```sh
python3 hooks/codex/manage.py install --refresh-root-documents
```

Use this only after confirming the untracked copy was not edited. The flag
cannot override edit protection for an already tracked copy and still refuses
a foreign document.

This non-destructively merges protocol-owned handlers into:

```text
$CODEX_HOME/hooks.json
```

or `~/.codex/hooks.json` when `CODEX_HOME` is unset.

Installed events:

- `SessionStart`
- `UserPromptSubmit`
- `SubagentStart`
- `SubagentStop`
- `PreCompact`
- `PreToolUse`
- `Stop`

Existing hook groups and unrelated JSON fields are preserved. Current Codex
builds may require user hooks to be reviewed/trusted; after installation,
restart Codex and inspect `/hooks` when applicable.

The installer explicitly sets `[features].memories = false` in `config.toml`
because Codex's generated local memories under `$CODEX_HOME/memories/` are a
separate recall layer and do not implement the structured root's paired-log and
write rules. The prior value or absence is restored on uninstall unless the
user changes the managed line after installation. Each hook is bound to its
owning `CODEX_HOME`, preventing a hook sourced from another profile from
injecting the wrong root. Native `AGENTS.md` discovery remains enabled and is
still Codex's instruction layer; only its separate generated-memory feature is
disabled.

Uninstall only these entries:

```sh
python3 hooks/codex/manage.py uninstall
```

## Claude Code only

Claude Code 2.1.196 or newer is recommended: the convention receipt is
session-scoped and keyed by `session_id` and `agent_id`, so the per-user-prompt
`prompt_id` is no longer required.

From the repository root:

```sh
python3 hooks/claude/manage.py install
```

This also deploys managed regular-file copies of root `RULES.md` and
`STRUCTURE.md`. Reinstall behavior and conflict protection are the same as for
Codex: only unchanged owned copies and legacy matching root symlinks are
refreshed or replaced automatically. The same one-time
`--refresh-root-documents` bootstrap applies to a confirmed pre-marker managed
regular copy.

This non-destructively merges protocol-owned handlers into:

```text
$CLAUDE_CONFIG_DIR/settings.json
```

or `~/.claude/settings.json` when `CLAUDE_CONFIG_DIR` is unset.

Installed events:

- `SessionStart`
- `UserPromptSubmit`
- `SubagentStart`
- `SubagentStop`
- `PreCompact`
- `PreToolUse`
- `Stop`

Existing settings, hooks, permissions, environment values, and instructions are
preserved. The installer sets `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` because
Claude's native per-project auto-memory directory can overlap the protocol's
structured root and does not implement its paired-log/write rules. The prior
value is restored on uninstall unless the user changes it after installation.
Each installed hook is also bound to its owning `CLAUDE_CONFIG_DIR`; this stops
project-scoped hooks from another Claude profile from injecting a second root.
If `disableAllHooks: true` is already configured, the installer leaves it
unchanged and warns that the new hooks will not run.

Uninstall only these entries:

```sh
python3 hooks/claude/manage.py uninstall
```

## Pi only

Pi has no hooks-configuration file, so the integration is a managed TypeScript
bridge extension instead of JSON hook entries. From the repository root:

```sh
python3 hooks/pi/manage.py install
```

The installer deploys two things and never reads or writes Pi's own
`settings.json`:

- the managed bridge extension at `$PI_CODING_AGENT_DIR/extensions/agent-mem-struct.ts`
  (default `~/.pi/agent/extensions/agent-mem-struct.ts`), with the hook, memory
  home, and canonical checkout paths baked in; and
- the same protected root `RULES.md`/`STRUCTURE.md` copies as the other hosts,
  with identical refresh and conflict protection (`--refresh-root-documents`
  bootstraps a confirmed untracked copy).

The same bridge can be installed as a pi package instead:

```sh
pi install git:github.com/ederevx/agent-mem-struct@v1.x
```

`package.json` declares `pi.extensions` -> `./extensions/agent-mem-struct.ts`,
which resolves the hook path, interpreter, memory home, config home, and
canonical root at load time (`AMS_PYTHON`, `AMS_HOOK`, `AMS_MEMORY_HOME`,
`AMS_CONFIG_HOME`, `AMS_CANONICAL_ROOT` override them) and imports the one
`RootMemoryBridge` implementation from the installer template. The managed copy
and the package entry load from separate module roots, so pi's own dedup cannot
stop both from registering; the package entry therefore stands down while the
managed bridge and its `pi-root-memory-hook.json` marker are present. Uninstall
the managed bridge (`hooks/pi/manage.py uninstall`) to let a package install
take over. The active package entry then deploys the protected root
`RULES.md`/`STRUCTURE.md` copies itself by running the installer's
`sync-documents` action at load, under a package-owned marker
(`pi-package-root-documents.json`) and the same refresh and conflict
protection: a missing or identical copy is deployed or adopted, a tracked
unmodified copy is refreshed to the canonical bytes, and a user-modified or
foreign copy is refused. The installer keeps owning the documents whenever
its managed bridge is present.

The bridge maps Pi events onto the hook's event vocabulary:

- `before_agent_start` emits only a one-line pointer (`SessionStart` on the
  first turn of a session and the turn after any compaction, the compact
  per-turn reminder otherwise) telling the model to call `pre_memory`; the full
  root bundle is never injected. This is the only hook-context injectable
  point Pi offers; `session_start` and `agent_settled` carry no context of
  their own, so the judge's notices ride the next turn instead.
- `session_compact` marks the session for continuity restoration and steers
  the same one-line pointer into the ongoing context immediately. A mid-run
  compaction (threshold, overflow retry) resumes without another
  `before_agent_start`, so without this the model would continue on the
  summarized transcript unaware that memory and conventions load through
  `pre_memory`. A failed load is surfaced on the next turn instead of dropped.
- `tool_call` drives the `PreToolUse` memory-mutation gate. Until the session
  has called `pre_memory` and then read every listed source and run
  `tree <shared>` (or the `find` fallback), a memory mutation is denied with the missing paths; the read
  catalog is delivered only by the `pre_memory` tool, so the gate's enforcement
  contract survives on Pi because Pi can block tool calls. Bridge failures fail
  open with a notice injected on the next turn rather than silently disabling
  memory.
- The bridge registers `pre_memory`, which runs the hook's `PreMemory` event:
  it fast-forwards the declared shared worktree, loads the root/shared
  convention union, records a session-scoped receipt, and returns that catalog
  plus the `pre_memory` feature summary.
- The bridge also registers `memory_update`, the structural mutation tool,
  which runs the hook's `MemoryUpdate` event once the shared worktree has
  fast-forwarded. `rm_update.MemoryUpdater` materializes every required
  counterpart (the same-named log, the nodes index, and group scaffolding),
  moves displaced current state into the log unless the edit is declared
  mechanical, and supports create, set, log, rename, retire, requires,
  attach, detach, and commit. `retire` requires the retirement account as
  `text` and moves the leaf's final state into the paired log before removing
  it. `log` and `commit` also take a retired leaf's paired `log/<stem>.md`;
  for a live leaf that path is a `log-target` conflict.
  `log` accepts ASCII history only: a non-ASCII
  character returns a `non-ascii` conflict naming up to five offending
  characters by line, column, and code point, and writes nothing. `commit`
  pairs with the built-in `edit` tool for
  content while the tool keeps commit ownership: it publishes edits you made
  to memory files and requires `commit_message` (a missing one gives
  `bad-request`). Its `path` is optional and names the edited active `.md`
  leaf; when omitted, every dirty path inside the agent's memory tree and the
  declared shared root is published. It stages only that target set, never the
  whole index, and derives the displaced state from Git: for a dirty active
  leaf whose paired log is not itself dirty, the lines the edit dropped are
  appended to the paired log before the commit. A write that lands in the
  declared shared root is then committed and pushed by
  `rm_commit.CommitPublisher`, which stages only the
  paths the mutation wrote, commits the supplied `commit_message` exactly as
  written (it never composes, wraps, or annotates one, but it checks it
  against the mechanical convention and returns a `message-lint` conflict,
  creating no commit, when it breaks it; it leaves the paths uncommitted when
  none is given), replays its commit onto a racing shared
  tip, and never force-pushes; without a remote or an upstream it commits
  locally and says so. Every git invocation goes through `rm_git.GitWorktree`.
  A `commit` whose target set is already clean is a no-op, not a failure: it
  reports `nothing-to-commit` and succeeds without committing. The tool never
  resolves a conflict itself: `control-invalid`, `stale-protocol`,
  `shared-diverged`, `shared-unpushed`, `git-conflict`, `git-failed`,
  `outside-memory`, `missing-counterpart`, `not-git`, `missing-prerequisite`,
  `exists`, `message-lint`, `non-ascii`, and `bad-request`
  come back as a conflict report for the agent to fix and retry. Every
  publish now attests itself in its own report, so an agent need not
  re-inspect the commit by hand. A read-only `rm_verify.PublishAttestation`
  inspects the worktree the publish committed into and the report gains
  `verified:` lines for the worktree being clean, HEAD versus its upstream
  (`; no upstream to compare` when there is none), the paired log carrying
  the appended lines, and the message shape. The clean wording is about the
  whole worktree, so a path the publish did not commit keeps it off: it is
  reported as one `attention:` line naming those paths, while a dirty path the
  publish did commit stays the `unverified-dirty` conflict. A status query git
  does not answer is reported as unverified instead of clean. A durability failure
  (`unverified-dirty`, `unverified-unpushed`, `unverified-log`) comes back as
  a conflict the agent must fix before the turn ends, because the memory is
  then not durably published. The mechanical convention is enforced before
  the commit; this re-check stays advisory, reporting a deviation as
  `attention:` lines rather than failing the publish. The check is read-only and goes
  through `rm_git.GitWorktree`; `rm_verify` never stages, commits, or writes.
  A subagent-attributed `MemoryUpdate` event is refused in the hook,
  independent of the PreToolUse gate.
- A shell `git push` tool call in a recognized form is linted on every host
  before it runs.
  `MutationScanner.git_push_invocation` resolves the repository and refspecs
  from a plain `git push`, `git -C DIR push`, or a leading `cd DIR &&`, and
  `rm_lint.PushLint` reads the commits the push would publish with one
  `git log <sources> --not --remotes=<remote>`, linting only commits whose
  author email equals the local `user.email`. A violating commit denies the
  call with `message-lint: push blocked: <sha12> <subject>: <violation>; ...`;
  a push that cannot be resolved, times out, or would publish more than 200
  commits is allowed with a `push not fully linted` note instead. A push in the
  same call as `am`, `cherry-pick`, `commit`, `commit-tree`, `merge`, `rebase`
  or `revert` is denied with `message-lint: a push cannot be linted in the call
  that creates its commits`; a push made from a script file is still not
  detected.
- At a session start whose source is not `compact` (on Pi that is the
  first-turn `before_agent_start`), the hook appends a read-only leftovers
  report. `rm_ledger.RepoLedger` remembers each repository top level an
  allowed history-changing git command touched in a private `0700` state file
  (`touched-repos.json`, at most 50 entries, expiring after 14 days), and
  `rm_leftovers.LeftoverReport` lists each remembered repository's local-only
  commits (`git rev-list --branches --not --remotes`, up to three
  `sha12 subject` samples) and the immediate children of `AMS_SCRATCH_ROOT`
  (default `~/tmp`) older than 24 hours, pruning clean or missing
  repositories. It is report only, bounded to two seconds, and writes nothing
  except pruning its own ledger; an unfinished scan adds a
  `leftover check incomplete` line.
- `session_before_compact` drives `PreCompact` with the session entries passed
  inline, so checkpoints need no transcript file. A failed checkpoint cancels a
  manual compaction; an automatic one proceeds with a warning (cancelling an
  overflow recovery would wedge the session at its context ceiling).
- `agent_settled` drives the convention judge, which is **on by default** for Pi,
  Claude, and Codex alike. One user-owned settings document,
  `<user-home>/.agent-mem-struct/judge.json`, is shared by all three, so
  enabling, redirecting, or narrowing the judge is one edit rather than three.
  The document is optional: an absent file is the shipped default, not a
  refusal, and the installers never create, replace, or remove it.

  The defaults come from `hooks/judge-policy.json` - protocol 2, `timeout_s` 30,
  `max_turns` 32, `max_input_bytes` 65536, `max_output_bytes` 4096 - and the
  document may carry `schema` (2), `enabled`, `command`, `protocol`, those four
  limits, and per-host `hosts.<host>.command` / `hosts.<host>.extra_env`.
  `command: null`, the default, means the shipped adapter
  `hooks/rm_judge_adapter.py`, which turns one review request into a single
  constrained prompt for the host CLI named in the policy: `pi -p --no-session
  --no-tools --no-mcp --no-extensions --no-skills --no-prompt-templates
  --no-context-files`, `claude -p --safe-mode --tools "" ...
  --no-session-persistence`, or `codex exec - --ephemeral -s read-only ...
  --disable hooks --disable plugins --disable apps`. The hook passes the
  descriptor it already validated - the host override included - in
  `AMS_JUDGE_LAUNCH`, so the adapter runs what the settings say rather than
  re-reading a policy file the user cannot edit. The adapter bounds its own
  capture while reading, and exits `3` for output beyond the policy's capture
  bound and `4` for bytes that are not UTF-8; the hook maps those to
  `oversized_output` and `invalid_response` instead of repairing the answer. An argv `command` replaces
  the adapter and runs under protocol 1 unless the document also declares
  `protocol: 2`; a document without `schema` is read exactly as the pre-schema
  five keys, and `import` copies such a file verbatim, protocol included.
  `hosts.<host>.extra_env` states the whole forwarded list for that host, so an
  explicit `[]` forwards nothing: names cannot be removed by adding to the
  shipped set. `enabled: false` is the only way to turn the judge off. A document that is
  unreadable, is not a regular file, or is malformed is reported as
  `invalid_config` rather than running or silently disabling the judge.

  Protocol 2 keeps the command's freedom to a single line. The request names a
  `chain` and the reply must begin with that chain followed by either `pass`
  and nothing else, or one line per finding - `<rule> <turn> "<quote>"`, both
  indices 1-based - in strictly ascending `(rule, turn)` order, the quote
  occurring exactly once in the turn it names. Anything else (a wrong chain,
  `pass` beside findings, a finding out of order, an unquoted or repeated
  field, surrounding whitespace, a carriage return, trailing material) is
  `invalid_response` and leaves the watermark where it was. A finding may name
  only a turn that was actually transmitted: when the byte bound drops trailing
  turns, an answer about a dropped turn is refused rather than resolved against
  text the command never saw.

  Each settle runs `prepare` (configuration, cursor, and transport limits; the
  command is not invoked) and then `review`, which sends the contiguous branch
  from the watermark together with one rule unit per acknowledged convention
  source, and receives a reply bound to that exact payload by the `chain` it
  must echo. Two layers bound the window and neither can skip a turn silently:
  the bridge sends the longest prefix of the branch whose whole request,
  envelope included, fits the configured turns and bytes, and reports that
  prefix's own end as `leaf_id` beside the session's `session_leaf_id`; the hook
  then takes the configured number of turns after the watermark and drops
  trailing turns until the request fits `max_input_bytes`. A short window
  therefore still advances the watermark one window at a time, and a window that
  stops at the reviewed anchor while the session runs past it is refused as
  `oversized_input` rather than repeated as a silent no-op: the turns after it
  were not reviewed, and the watermark stays where it was. One serializer both
  measures and writes the request, so the bound covers every byte; when no
  candidate review request fits, the bridge transmits nothing, reports
  `oversized_input` itself and leaves the receipt untouched, since no hook call
  can annotate a refusal it never saw. Activation is its own phase: at session
  start the host anchors the watermark at the session's current leaf and
  reviews nothing, so the turns that follow are reviewed and the history that
  came before is accepted as pre-activation. A session that starts with no
  history at all begins at the empty origin, which anchors at no turn, so its
  first completed turn is reviewed rather than exempted; a review that finds no
  epoch establishes the same empty origin before reviewing, so a session whose
  host never sent the activation phase still reviews every turn it was given.
  An empty inline window or an opened-but-unwritten transcript is that empty
  origin, while a source that exists and cannot be read is `invalid_window`:
  accepting it would claim a baseline nobody could verify and then present the
  history it hid as new work.
  A `coverage_gap` rebaseline anchors at the supplied window's leaf. `timeout_s` is bounded by
  what `threading` and a host timer can hold, and a larger value is
  `invalid_config` before anything is spawned. A finding may name only a
  supplied rule, a supplied turn, and a quote that occurs there exactly once;
  its offsets are string indices derived locally. Continuity proves as much as
  the window can: the anchor's own record always, so a rewritten anchor is
  `invalid_window`, and the whole ordered history through it whenever the
  window starts at the branch root - Claude and Codex read the transcript from
  its first record, while Pi's is trimmed at its head by the transport bound
  and can prove only the anchor. An anchor the source no longer holds at all is
  a `coverage_gap` that rebaselines instead of stranding the session. Refusal
  codes are local:
  `disabled`, `invalid_config`, `invalid_cursor`, `invalid_window`,
  `stale_cursor`, `stale_config`, `unreadable_rules`, `stale_rules`,
  `oversized_input`, `oversized_output`, `oversized_receipt`, `timeout`,
  `command_failed`, `invalid_response`, `unsupported_host`, `settings_busy`,
  `invalid_settings_request`, `settings_write_failed`, `settings_conflict`, and
  `persistence_failed`, which
  `ConventionJudge.review` returns when it cannot convert a refusal into a
  stored one. A recorded refusal sets its code on the stored state and keeps
  every other field, including previously accepted findings; a session with no
  watermark is left uninitialized, so a refusal never becomes a watermark that
  later reviews read as nothing to do. A settled turn whose anchor has left the
  supplied branch records `coverage_gap` and rebaselines a new epoch without
  claiming any review of the skipped turns. The request carries no task
  statement: the judge sees the settled turns, the rule units, and nothing else.

  `/judge-settings` in Pi is the one surface over this document: no argument or
  `show` lists every effective value with its origin (shipped default or the
  document), the file in use, the resolved launch argv and environment names
  for the current host, and any legacy document; `key=value` applies one edit,
  `unset <key>` removes one, `reset` restores the shipped numeric limits, and
  `import` copies the legacy per-harness document verbatim. Edits are
  transactional and revision-checked: the surface reads the current revision,
  applies the change, and prints the state the hook actually holds, so a
  concurrent edit reports `stale_config` instead of overwriting it. Validation
  and persistence belong to `hooks/rm_judge_settings.py`, which owns the
  document and takes a short lock (reporting `settings_busy`); the command
  holds no state of its own. A review carries the revision it was prepared
  under and is refused as `stale_config` if the document changed in between.
  Provenance is what the document states, not what a value looks like: an
  explicit `max_turns: 32` reads as explicit. The listing shows the effective
  host command, the adapter's argv when it is in use, the activation bound the
  shipped policy sets, and the last review this session actually holds - its
  refusal code or its acceptance, the boundary it reached, and any notice a
  turn has yet to collect.

  `timeout_s` is capped by the deadline the installed `Stop` and `SubagentStop`
  handlers carry, less the room the receipt write, the notice, and teardown
  need, so a review that times out reports its own deadline instead of being
  killed before it can record one. Neither number has two owners: the bound is
  the installed deadline in `hooks/manage_common.py`, and the margin is
  `review_teardown_s` in `hooks/judge-policy.json`, beside `capture_bytes`. A
  larger value is `invalid_config`. Activation reads at most the policy's
  `baseline_entries` of history, so how much is accepted as pre-activation is a
  shipped value rather than a constant in the bridge.

  The one receipt writer enforces `RECEIPT_MAX_BYTES` on the exact payload it is
  about to replace, for every mutator rather than the judge alone, and refuses
  as `oversized_receipt` while the previous receipt stays in place. Notices
  claim only what is known locally: `disabled`, `invalid_config`,
  `invalid_window`, `invalid_cursor`, `stale_cursor`, `stale_config`,
  `unreadable_rules`, `stale_rules` and `oversized_input` say the review did not
  run, because no command was invoked; `timeout`, `command_failed`,
  `invalid_response` and `oversized_output` say the result is unavailable,
  because the command may have run and produced output that cannot be accepted;
  and `oversized_receipt` or `persistence_failed` say the state could not be
  saved. An unreadable hook reply is reported the same way, without claiming
  whether a finding was recorded. Findings and errors reach the model on the
  next turn of the session that produced them and never block a tool call or a
  settle.

Limitations: Pi exposes no subagent identity on events, so the bridge cannot
mark a spawned child as read-only the way `SubagentStart` does for the other
hosts — a Pi subagent session must report memory additions back to its parent
(enforced only by instruction, not by the hook). Turn-end convention checking
(`Stop`) has no Pi surface and is intentionally absent; the PreToolUse gate
remains the enforcement point. A project-local `.pi/extensions/agent-mem-struct.ts`
shadows the managed copy; remove it if the managed bridge seems inert.

Uninstall removes only the owned bridge and its state:

```sh
python3 hooks/pi/manage.py uninstall
```

## Migration from the text-only integration

Installing the hook layer does not independently require a memory-tree
migration. The root-document and explicit-shared-path policy is versioned by
the canonical protocol; follow the newest applicable `MIGRATION.md` entry
before advancing the agent's marker.

Keep the managed root copies described by the main protocol. Add only the
appropriate runtime hook installation for each agent. The two agents are
independent; installing one does not configure the other.

## Failure behavior

When root control is invalid or the continuity checkpoint cannot be written,
the hook refuses a *manually* requested compaction. `PreCompact` honors no JSON
decision field, so the refusal is a non-zero exit with the reason on stderr;
the hook reads the trigger from either `triggered_by` or `trigger`, and treats
an unlabelled trigger as manual.

An *automatic* compaction is never refused. Blocking it would pin the session
at its context ceiling, destroying more continuity than the missing checkpoint
does, so the hook emits a `systemMessage` warning and lets the compaction run;
the compact-sourced `SessionStart` then injects its `CONTINUITY WARNING`.
Compact-sourced `SessionStart` cannot block, so the pre-compaction checkpoint
remains the only enforcement point. Neither path blocks ordinary non-memory
work.

Because a timed-out hook is a silently skipped checkpoint, the installed
`PreCompact` handler carries a 60-second timeout; the other events keep the
5-second default.

Checkpoint files contain bounded transcript-derived execution anchors, use
mode `0600` on POSIX, and are transient; the state directories holding them
and the first-install settings backup are `0700` on POSIX at every owned
level. A successful post-compaction restoration
consumes them. A checkpoint stranded by a process crash is removed after seven
days at the next session, subagent, or compaction boundary; uninstall removes
the owned checkpoint tree immediately. The first-install settings backup is
available as recovery state while hooks are installed; uninstall removes it
and removes each owned state directory once empty.

A stale but valid `Structure-Version` remains writable so the agent can apply
`MIGRATION.md`; the hook injects the stale-state warning and the canonical
migration path on every relevant context refresh.

On every platform, the installer creates managed regular-file copies. It
replaces a legacy root symlink only when the link still resolves to the
corresponding canonical source. Reinstallation refreshes only an unchanged
installer-owned copy; it refuses a foreign file, a user-edited copy, or a link
to another target. At runtime, later content drift blocks mutations and turn
completion until the installed root authority is repaired.

There is no `memory/shared` alias or copy. The root `Shared:` value is accepted
only when it is literal, unquoted, absolute in native syntax, and names a
pre-existing physical directory with valid mandatory conventions. The declared
terminal directory must not itself be a symlink, junction, or other
reparse-point alias; ancestor components may resolve normally. Missing or
invalid values are reported with the canonical checkout's `.shared/` directory
as a possible discovery hint; the installer and hook never create that
directory, fall back to it, or silently substitute it.

Convention receipts are private, session-scoped files keyed by host,
`session_id`, and `agent_id` (or parent). Every agent reads the conventions
once per session and re-reads a source only when its digest changes; a
`resume` keeps the receipt, a fresh session clears it, and a compaction clears
the recorded reads and tree so they are re-read. The declared shared root must
be read once per session with the shipped lister
(`python3 hooks/rm_tree.py --list <root>`, which skips `.git` and is bounded),
or with a complete `tree` / `find DIR [-print]` / `ls -aR` (PowerShell
`Get-ChildItem -Recurse -Force`). For Pi the denial points at `pre_memory`
until the receipt is seeded; for Codex and Claude the gate seeds the receipt
itself and denies with the exact missing source paths and the `tree` command.
A read is recorded only when it shows a whole source; a piped or windowed read
does not count. Changing any source or the declared shared root invalidates
that part of the receipt. For memory writes the required set expands from
shared conventions through active ancestor group manifests and any declared
`requires_read` files, recursively. Node-collection indexes and historical
`log/MEMORY.md` files are not convention manifests. Targets beneath `nodes/`
or `log/` retain their enclosing group conventions; historical logs do not
introduce independent prerequisites.

`Stop` and `SubagentStop` block only the first unacknowledged completion
attempt. If the host re-enters either event with `stop_hook_active` set, the
hook never blocks again, preventing a continuation loop even when root state
or event identity is damaged.

When declared shared memory is valid, the injected context directs agents to
edit that exact shared tree under the existing paired-log and narrow
commit-and-push rules. It also keeps raw dumps and complete logs in artifact
storage: an artifact upload and a distilled shared-memory update are separate
durability steps, not substitutes for one another.
