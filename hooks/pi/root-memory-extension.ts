// agent-mem-struct root memory: managed copy; the installer owns these bytes.
// Pi bridge for the shared root-memory hook. Pi has no hooks.json surface, so
// this extension translates Pi lifecycle events into the Claude-style event
// JSON the Python hook consumes:
//
//   before_agent_start  -> SessionStart (first turn, and after compaction)
//                       or UserPromptSubmit (per-turn reminder)
//   tool_call           -> PreToolUse (memory-mutation gate; Pi can block)
//   memory_update tool   -> MemoryUpdate (structural mutation or conflict report)
//   session_before_compact -> PreCompact (cancel manual compaction on failure)
//   session_compact      -> inject the memory/convention pointer immediately
//                           (a mid-run compaction has no before_agent_start)
//
// Pi's session_start and agent_settled events cannot inject or block anything,
// so they are deliberately not mapped: turn-end enforcement relies on the
// PreToolUse gate. All context reaches the model through the single
// injectable point, before_agent_start.
//
// Two deployment modes share this one implementation:
//
//   1. The installer renders this file with its absolute paths baked in and
//      drops it at <pi-home>/extensions/agent-mem-struct.ts. That managed copy
//      is self-contained and registers directly (pi-root-memory-hook.json
//      records its ownership).
//   2. Installed as a pi package, extensions/agent-mem-struct.ts imports
//      createRootMemoryBridge() and supplies paths resolved at load time. That
//      entry stands down when the managed copy is present, so the bridge never
//      registers twice (separate module roots defeat pi's own dedup).
import { execFile, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { accessSync, constants, realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { basename, delimiter, dirname, join, resolve as resolvePath } from "node:path";

import { Type } from "typebox";

const PYTHON = "__AMS_PYTHON__";
const HOOK = "__AMS_HOOK__";
const MEMORY_HOME = "__AMS_HOME__";
const CONFIG_HOME = "__AMS_CONFIG_HOME__";
const CANONICAL_ROOT = "__AMS_CANONICAL_ROOT__";

const DEFAULT_TIMEOUT_MS = 8000;
const PRECOMPACT_TIMEOUT_MS = 120000;
const PREMEMORY_TIMEOUT_MS = 30000;
const MEMORYUPDATE_TIMEOUT_MS = 30000;
const MAX_INLINE_ENTRIES = 150;
const LISTER_TIMEOUT_MS = 30000;
const INTERPRETER_NAME_RE = /^(?:python(?:3(?:\.\d+)?)?|py)(?:\.exe)?$/i;
const INTERPRETER_FLAGS = new Set(["-I", "-B", "-E", "-S", "-u"]);

/** Decodes collected output chunks in one pass; decoding per chunk would
 *  split a multi-byte UTF-8 sequence at a stream buffer boundary and corrupt
 *  the hook's JSON. */
function decodeUtf8(chunks: Buffer[]): string {
	return Buffer.concat(chunks).toString("utf-8");
}

/** Resolved locations the bridge needs; the managed copy bakes them, the
 *  package entry derives them at load time. */
export interface BridgeConfig {
	python: string;
	hook: string;
	memoryHome: string;
	configHome: string;
	canonicalRoot: string;
}

/** The five baked constants the installer substitutes, kept as the managed
 *  copy's own configuration. */
export const BAKED_CONFIG: BridgeConfig = {
	python: PYTHON,
	hook: HOOK,
	memoryHome: MEMORY_HOME,
	configHome: CONFIG_HOME,
	canonicalRoot: CANONICAL_ROOT,
};

interface HookResult {
	ok: boolean;
	timedOut?: boolean;
	stdout: string;
	stderr: string;
}

/** The one `rm_tree.py` invocation form the PreToolUse gate credits
 *  (`MutationScanner.lister_invocation` in hooks/rm_scan.py). The bridge runs
 *  the shim's own interpreter, never the model's token, and refuses anything
 *  else before it runs, so a successful `rm_tree_exec` always means the gate
 *  recorded the shared-structure read. */
export class ListerCommand {
	constructor(readonly lister: string) {}

	/** The gate's `QUOTED_TOKEN_RE` split, kept byte-for-byte. */
	private static tokens(text: string): string[] {
		const out: string[] = [];
		const re = /'([^']*)'|"([^"]*)"|(\S+)/g;
		for (let match = re.exec(text); match; match = re.exec(text)) {
			out.push(match[1] ?? match[2] ?? match[3]);
		}
		return out;
	}

	private static same(left: string, right: string): boolean {
		try {
			const a = realpathSync.native(left);
			const b = realpathSync.native(right);
			return process.platform === "win32"
				? a.toLowerCase() === b.toLowerCase()
				: a === b;
		} catch {
			return false;
		}
	}

	/** The gate's `_interpreter_available`: a path token must be an executable
	 *  file, a bare name must resolve on PATH. */
	private static interpreterAvailable(token: string, cwd: string): boolean {
		const executable = (path: string): boolean => {
			try {
				return statSync(path).isFile();
			} catch {
				return false;
			}
		};
		if (token.includes("/") || token.includes("\\") || /^[A-Za-z]:/.test(token)) {
			const candidate = resolvePath(cwd, token);
			try {
				accessSync(candidate, constants.X_OK);
				return executable(candidate);
			} catch {
				return false;
			}
		}
		const extensions = process.platform === "win32"
			? (process.env.PATHEXT ?? ".EXE").split(";")
			: [""];
		return (process.env.PATH ?? "").split(delimiter).some((directory) =>
			extensions.some((extension) => {
				const candidate = join(directory, token + extension);
				try {
					accessSync(candidate, constants.X_OK);
					return executable(candidate);
				} catch {
					return false;
				}
			}));
	}

	/** The gate's `path_from_string` for the one root operand: strip quotes,
	 *  reject a blank or multi-line value, expand `~` and environment
	 *  variables, then resolve against the call's working directory. */
	private static rootPath(value: string, cwd: string): string | null {
		let text = value.trim().replace(/^['"]+|['"]+$/g, "");
		if (!text || text.includes("\n") || text.length > 4096) return null;
		text = text.replace(/^~(?=$|[\\/])/, homedir());
		text = text.replace(/\$\{(\w+)\}|\$(\w+)/g, (whole, braced, bare) =>
			process.env[braced ?? bare] ?? whole);
		return resolvePath(cwd, text);
	}

	/** The accepted form, shown when a call is refused. */
	usage(): string {
		return `rm_tree_exec accepts exactly: python3 "${this.lister}" --list <shared-root>`;
	}

	/** The resolved shared root, or null when the line is not one plain call. */
	parse(command: string, cwd: string): string | null {
		if (/[|<>`]|\$\(|\n|\r|&/.test(command)) return null;
		const words = ListerCommand.tokens(command);
		const interpreter = basename(words[0] ?? "");
		if (!INTERPRETER_NAME_RE.test(interpreter)) return null;
		if (!ListerCommand.interpreterAvailable(words[0] ?? "", cwd)) return null;
		let index = 1;
		while (index < words.length && INTERPRETER_FLAGS.has(words[index])) index += 1;
		if (/^py(?:\.exe)?$/i.test(interpreter) && /^-\d(?:\.\d+)?$/.test(words[index] ?? "")) {
			index += 1;
		}
		const script = words[index];
		index += 1;
		if (!script || !ListerCommand.same(resolvePath(cwd, script), this.lister)) return null;
		let root: string | null = null;
		while (index < words.length) {
			const word = words[index];
			index += 1;
			if (word === "--list") continue;
			if (word === "--") {
				if (index < words.length && root === null) {
					root = words[index];
					index += 1;
				}
				continue;
			}
			if (word.startsWith("-") || root !== null) return null;
			root = word;
		}
		if (root === null) return null;
		return ListerCommand.rootPath(root, cwd);
	}
}

/**
 * One agent session's Pi bridge. All mutable state is instance state owned by
 * this object; the event handlers are single-responsibility methods and no
 * module-level state is mutated.
 */
export class RootMemoryBridge {
	private sessionLabel = "";
	private firstTurn = true; // emit the full root bundle on the next turn
	private afterCompaction = false; // restore continuity on the next turn
	private turnCounter = 0;
	private turnId = "bootstrap";
	private pendingNotice = "";

	constructor(private readonly config: BridgeConfig) {}

	/** Registers every Pi lifecycle handler this bridge owns. */
	register(pi: any): void {
		this.registerPreMemoryTool(pi);
		this.registerMemoryUpdateTool(pi);
		this.registerListerTool(pi);
		pi.on("session_start", async (_event: any, ctx: any) => {
			this.sessionLabel = this.sessionId(ctx);
			// A reload reruns discovery; the next turn re-delivers the pre_memory
			// pointer and any continuity checkpoint so a stale runtime cannot
			// outlive the documents it points at.
			this.firstTurn = true;
		});

		pi.on("before_agent_start", async (event: any) => this.onBeforeAgentStart(event));
		pi.on("tool_call", async (event: any, ctx: any) => this.onToolCall(event, ctx));
		pi.on("session_compact", async () => this.onSessionCompact(pi));
		pi.on("session_before_compact", async (event: any) => this.onBeforeCompact(event));
	}

	/** Registers `pre_memory`, the one call that pulls the shared worktree,
	 *  loads the conventions, and acknowledges them for this session. */
	private registerPreMemoryTool(pi: any): void {
		pi.registerTool({
			name: "pre_memory",
			label: "pre_memory",
			description:
				"Call once per session before any memory write or agent-mem-struct " +
				"action. Fast-forwards the declared shared worktree, records the " +
				"current convention sources, and returns the read list and navigation " +
				"rules. The conventions are not inlined: read the listed sources ENTIRELY " +
				"with the read tool (the whole file, not a segment) to acknowledge them.",
			parameters: Type.Object({}),
			annotations: { readOnlyHint: false },
			execute: () => this.loadPreMemory(),
		});
	}

	/** Runs the PreMemory hook and turns its catalog, or its failure, into a
	 *  result the model sees. */
	private async loadPreMemory(): Promise<unknown> {
		const result = await this.callHook("PreMemory", {}, PREMEMORY_TIMEOUT_MS);
		const context = this.additionalContext(result);
		if (!result.ok || context === null) {
			const fallback = result.timedOut
				? "agent-mem-struct pre_memory timed out; retry before memory work."
				: "agent-mem-struct pre_memory failed; repair the root control before memory work.";
			return {
				isError: true,
				content: [{ type: "text", text: context ?? (result.stderr.trim() || fallback) }],
				details: {},
			};
		}
		return {
			content: [{ type: "text", text: context }],
			details: {},
		};
	}

	/** Registers `memory_update`, the spec-shaped structural mutation tool:
	 *  it creates or updates a node and its required counterparts, and returns
	 *  any conflicts for the agent to fix before re-calling. */
	private registerMemoryUpdateTool(pi: any): void {
		pi.registerTool({
			name: "memory_update",
			label: "memory_update",
			description:
				"Create or update a memory node and its required counterparts in " +
				"one spec-shaped operation. It auto-creates the paired log, the " +
				"nodes index, and any missing group scaffolding, moves displaced " +
				"current state into the log, and returns any conflicts to fix " +
				"before re-calling. A write that lands in the declared shared root " +
				"is committed and pushed under the `commit_message` you supply, " +
				"which the tool uses verbatim. Operations: create (node or " +
				"group), set (current state), log (history), rename, retire, " +
				"requires, attach, detach, commit. `commit` publishes edits made " +
				"with the built-in edit tool and captures the displaced current " +
				"state into the paired log. `path` is absolute or relative to the " +
				"agent home and must resolve inside this agent's memory tree or the " +
				"declared shared root.",
			parameters: Type.Object({
				operation: Type.Union([
					Type.Literal("create"), Type.Literal("set"),
					Type.Literal("log"), Type.Literal("rename"),
					Type.Literal("retire"), Type.Literal("requires"),
					Type.Literal("attach"), Type.Literal("detach"),
					Type.Literal("commit"),
				], { description: "The mutation to perform." }),
				path: Type.String({
					description:
						"create: the group directory (node) or parent of the new " +
						"group (kind=group). All others: the active .md leaf. " +
						"commit: optional, the edited active .md leaf; omitted " +
						"publishes every dirty path inside the agent's memory tree " +
						"and the declared shared root.",
				}),
				name: Type.Optional(Type.String({
					description: "create/rename: the kebab-case leaf or group stem.",
				})),
				body: Type.Optional(Type.String({
					description: "create/set: the active current-state body.",
				})),
				text: Type.Optional(Type.String({
					description: "log: the history or displaced-state text to append.",
				})),
				requires: Type.Optional(Type.Array(Type.String(), {
					description: "requires: active memory files to read first.",
				})),
				summary: Type.Optional(Type.String({
					description: "create: the routing-index line describing the node.",
				})),
				kind: Type.Optional(Type.Union([
					Type.Literal("node"), Type.Literal("group"),
				], { description: "create: what to build (default node)." })),
				mechanical: Type.Optional(Type.Boolean({
					description:
						"set: a typo/format edit that needs no semantic log entry.",
				})),
				commit_message: Type.Optional(Type.String({
					description:
						"The commit subject and body, committed exactly as written. " +
						"Required for `commit` and for the tool to commit and push a " +
						"write that lands in the declared shared root; the tool never " +
						"composes, wraps, or annotates it, and without it the paths " +
						"are written but left uncommitted.",
				})),
				attachment_name: Type.Optional(Type.String({
					description: "attach/detach: the file name in the leaf's directory.",
				})),
				attachment_content: Type.Optional(Type.String({
					description: "attach: the file content as text.",
				})),
				attachment_source: Type.Optional(Type.String({
					description: "attach: a path to copy the attachment bytes from.",
				})),
				overwrite: Type.Optional(Type.Boolean({
					description: "attach: replace an existing attachment file.",
				})),
			}),
			annotations: { readOnlyHint: false },
			execute: (_id: string, params: unknown) =>
				this.runMemoryUpdate((params ?? {}) as Record<string, unknown>),
		});
	}

	/** Runs the MemoryUpdate hook and turns its report, or its failure, into a
	 *  result the model sees; a conflict report is an error result so the agent
	 *  fixes it before re-calling. */
	private async runMemoryUpdate(params: Record<string, unknown>): Promise<unknown> {
		const result = await this.callHook("MemoryUpdate", params, MEMORYUPDATE_TIMEOUT_MS);
		const context = this.additionalContext(result);
		if (!result.ok) {
			const fallback = result.timedOut
				? "agent-mem-struct memory_update timed out; retry before memory work."
				: "agent-mem-struct memory_update failed; repair the root control before memory work.";
			return {
				isError: true,
				content: [{ type: "text", text: context ?? (result.stderr.trim() || fallback) }],
				details: {},
			};
		}
		return {
			content: [{ type: "text", text: context ?? "memory_update completed." }],
			details: {},
		};
	}

	/** Registers `rm_tree_exec`: the shared-structure read the convention gate
	 *  requires, run without a shell. Its name carries "exec" and its input a
	 *  `command` key, the two things `MutationScanner.shell_commands` needs to
	 *  credit it, so it is the only structure read reachable before
	 *  `pre_daemon` unlocks `bash`. */
	private registerListerTool(pi: any): void {
		const parser = new ListerCommand(join(dirname(this.config.hook), "rm_tree.py"));
		pi.registerTool({
			name: "rm_tree_exec",
			label: "rm_tree_exec",
			description:
				"Run the agent-mem-struct shared-structure lister when the convention " +
				"gate asks for `rm_tree.py --list <shared>`. `command` must be exactly " +
				`one plain invocation: python3 "${parser.lister}" --list <shared-root>. ` +
				"Anything else is refused before it runs.",
			parameters: Type.Object({ command: Type.String() }),
			annotations: { readOnlyHint: true },
			execute: (
				_id: string,
				params: unknown,
				signal?: AbortSignal,
				_onUpdate?: unknown,
				ctx?: any,
			) => this.runLister(
				parser,
				String((params as { command?: unknown } | null)?.command ?? ""),
				ctx?.cwd ?? process.cwd(),
				signal,
			),
		});
	}

	/** Runs the lister for a validated call, or reports the accepted form. */
	private runLister(
		parser: ListerCommand,
		command: string,
		cwd: string,
		signal?: AbortSignal,
	): Promise<unknown> {
		const root = parser.parse(command, cwd);
		if (root === null) {
			return Promise.resolve({
				isError: true,
				content: [{ type: "text", text: parser.usage() }],
				details: {},
			});
		}
		return new Promise((resolve) => {
			execFile(
				this.config.python,
				[parser.lister, "--list", "--", root],
				{
					cwd,
					timeout: LISTER_TIMEOUT_MS,
					maxBuffer: 16 << 20,
					windowsHide: true,
					signal,
				},
				(error, stdout, stderr) => resolve({
					isError: Boolean(error),
					content: [{ type: "text", text: error ? `${stderr || error}` : stdout }],
					details: {},
				}),
			);
		});
	}

	private sessionId(pi: any): string {
		try {
			const id = pi.sessionManager?.getSessionId?.();
			if (typeof id === "string" && id.trim()) return id.trim();
		} catch {
			// fall through to a process-local identity
		}
		const fromEnv = process.env.PI_SESSION_ID;
		if (typeof fromEnv === "string" && fromEnv.trim()) {
			return fromEnv.trim();
		}
		return `pi-process-${process.pid}-${randomUUID().slice(0, 8)}`;
	}

	private callHook(
		eventName: string,
		extra: Record<string, unknown>,
		timeoutMs: number,
	): Promise<HookResult> {
		return new Promise((resolve) => {
			const body = JSON.stringify({
				hook_event_name: eventName,
				session_id: this.sessionLabel,
				turn_id: this.turnId,
				...extra,
			});
			const stdout: Buffer[] = [];
			const stderr: Buffer[] = [];
			let settled = false;
			const child = spawn(
				this.config.python,
				[
					this.config.hook,
					"--agent", "pi",
					"--home", this.config.memoryHome,
					"--config-home", this.config.configHome,
					"--canonical-root", this.config.canonicalRoot,
				],
				{ stdio: ["pipe", "pipe", "pipe"] },
			);
			const timer = setTimeout(() => {
				if (settled) return;
				settled = true;
				child.kill("SIGKILL");
				resolve({
					ok: false,
					timedOut: true,
					stdout: decodeUtf8(stdout),
					stderr: decodeUtf8(stderr),
				});
			}, timeoutMs);
			child.stdout.on("data", (chunk) => {
				stdout.push(chunk);
			});
			child.stderr.on("data", (chunk) => {
				stderr.push(chunk);
			});
			child.on("error", (error) => {
				if (settled) return;
				settled = true;
				clearTimeout(timer);
				resolve({
					ok: false,
					stdout: decodeUtf8(stdout),
					stderr: decodeUtf8(stderr) + error,
				});
			});
			child.on("close", (code) => {
				if (settled) return;
				settled = true;
				clearTimeout(timer);
				resolve({
					ok: code === 0,
					timedOut: false,
					stdout: decodeUtf8(stdout),
					stderr: decodeUtf8(stderr),
				});
			});
			// The hook can exit without draining its stdin (an early crash, an
			// event profile it does not read); once the body outgrows the pipe
			// buffer that surfaces as EPIPE, and without a handler the error is
			// uncaught and takes the whole host process down.
			child.stdin.on("error", () => {});
			child.stdin.end(body);
		});
	}

	private additionalContext(result: HookResult): string | null {
		try {
			const parsed = JSON.parse(result.stdout);
			const context = parsed?.hookSpecificOutput?.additionalContext;
			if (typeof context === "string" && context) return context;
		} catch {
			// non-JSON output is treated as a hook failure below
		}
		return null;
	}

	private denialReason(result: HookResult): { reason: string; context: string } | null {
		try {
			const parsed = JSON.parse(result.stdout);
			const output = parsed?.hookSpecificOutput;
			if (output?.permissionDecision === "deny") {
				return {
					reason: String(output.permissionDecisionReason ?? "denied by agent-mem-struct"),
					context: typeof output.additionalContext === "string" ? output.additionalContext : "",
				};
			}
		} catch {
			// fall through
		}
		return null;
	}

	/** The single injectable point: the full bundle on a session's first turn
	 *  or after a compaction, the turn reminder otherwise. */
	private async onBeforeAgentStart(event: any): Promise<unknown> {
		this.turnCounter += 1;
		this.turnId = `turn-${Date.now()}-${this.turnCounter}`;
		const eventName = this.firstTurn || this.afterCompaction
			? "SessionStart"
			: "UserPromptSubmit";
		const source = this.afterCompaction ? "compact" : "startup";
		const wasContinuity = this.afterCompaction;
		this.firstTurn = false;
		this.afterCompaction = false;
		const result = await this.callHook(
			eventName,
			eventName === "SessionStart" ? { source } : { prompt: event.prompt ?? "" },
			DEFAULT_TIMEOUT_MS,
		);
		const context = this.additionalContext(result);
		const parts: string[] = [];
		if (this.pendingNotice) {
			parts.push(this.pendingNotice);
			this.pendingNotice = "";
		}
		if (context === null) {
			// Surface bridge failures instead of failing silently; retry the
			// full bundle next turn rather than dropping the session load.
			if (eventName === "SessionStart") this.firstTurn = true;
			this.pendingNotice =
				`agent-mem-struct: the root-memory hook failed (${eventName}` +
				`${result.timedOut ? " timed out" : ""}); root memory was NOT re-injected. ` +
				"Read memory/MEMORY.md and RULES.md before memory work.";
			return undefined;
		}
		parts.push(context);
		if (wasContinuity && !context.includes("CONTINUITY CHECKPOINT") &&
				!context.includes("CONTINUITY WARNING")) {
			parts.push(
				"agent-mem-struct: this turn follows a compaction; reconfirm the " +
				"active objective and next action from the transcript or user.",
			);
		}
		return {
			message: {
				customType: "agent-mem-struct-root-memory",
				content: parts.join("\n\n"),
				display: false,
			},
		};
	}

	/** Pi can block, so the memory-mutation gate is enforced here. */
	private async onToolCall(event: any, ctx: any): Promise<unknown> {
		const result = await this.callHook(
			"PreToolUse",
			{
				tool_name: event.toolName ?? "",
				tool_input: (event.input && typeof event.input === "object") ? event.input : {},
				cwd: ctx?.cwd ?? process.cwd(),
			},
			DEFAULT_TIMEOUT_MS,
		);
		const denial = this.denialReason(result);
		if (denial) {
			return { block: true, reason: denial.context || denial.reason };
		}
		if (!result.ok) {
			this.pendingNotice =
				`agent-mem-struct: the memory-mutation gate failed${result.timedOut ? " (timed out)" : ""}; ` +
				"this call was allowed without a convention check. Read memory/MEMORY.md " +
				"and RULES.md before writing memory.";
		}
		return undefined;
	}

	/** A compaction landed. Remember it so the next turn restores the
	 *  PreCompact continuity checkpoint, and remind the model of the memory
	 *  authority now: a mid-run compaction resumes without another
	 *  before_agent_start, so the pointer would otherwise go unseen. */
	private async onSessionCompact(pi: any): Promise<void> {
		this.afterCompaction = true;
		const reminder = await this.postCompactionReminder();
		if (reminder === null || typeof pi.sendMessage !== "function") return;
		pi.sendMessage(
			{
				customType: "agent-mem-struct-root-memory",
				content: reminder,
				display: false,
			},
			{ deliverAs: "steer" },
		);
	}

	/** Loads the post-compaction memory/convention pointer from the hook, its
	 *  one owner, and surfaces a failed load on the next turn instead of
	 *  dropping the reminder. */
	private async postCompactionReminder(): Promise<string | null> {
		const result = await this.callHook(
			"UserPromptSubmit",
			{ prompt: "" },
			DEFAULT_TIMEOUT_MS,
		);
		const context = this.additionalContext(result);
		if (context === null) {
			this.pendingNotice =
				"agent-mem-struct: this session was compacted, but the memory and " +
				"convention reminder could not be loaded. Call `pre_memory` before " +
				"any memory write or agent-mem-struct action.";
			return null;
		}
		return context;
	}

	/** Checkpoint before compaction: only a manual compaction may be
	 *  cancelled; an overflow recovery must never wedge the session. */
	private async onBeforeCompact(event: any): Promise<unknown> {
		const manual = event.reason === "manual";
		const branchEntries = Array.isArray(event.branchEntries) ? event.branchEntries : [];
		const sessionEntries = branchEntries.slice(-MAX_INLINE_ENTRIES);
		const result = await this.callHook(
			"PreCompact",
			{
				triggered_by: manual ? "manual" : "auto",
				session_entries: sessionEntries,
			},
			PRECOMPACT_TIMEOUT_MS,
		);
		if (manual && !result.ok) {
			return { cancel: true };
		}
		if (!manual && !result.ok) {
			this.pendingNotice =
				"agent-mem-struct: automatic compaction proceeded without a continuity " +
				"checkpoint. Reconfirm the active objective, completed actions, and " +
				"next action from the transcript or user.";
		}
		return undefined;
	}
}

/** Builds and registers a bridge for one Pi session. */
export function createRootMemoryBridge(pi: any, config: BridgeConfig): RootMemoryBridge {
	const bridge = new RootMemoryBridge(config);
	bridge.register(pi);
	return bridge;
}

/** The installer-rendered managed copy registers with its baked paths. */
export default function (pi: any): void {
	createRootMemoryBridge(pi, BAKED_CONFIG);
}