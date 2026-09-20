// agent-mem-struct package entry for Pi.
//
// This is the pi-package deployment mode: `pi install` clones the repository
// and loads this file, which imports the one shared bridge implementation and
// supplies paths resolved at load time. The installer-managed copy at
// <pi-home>/extensions/agent-mem-struct.ts is self-contained and wins; this
// entry stands down while that copy is present so the bridge never registers
// twice.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
	createRootMemoryBridge,
	type BridgeConfig,
} from "../hooks/pi/root-memory-extension.ts";

const MANAGED_MARKER = "pi-root-memory-hook.json";
const MANAGED_BRIDGE = "agent-mem-struct.ts";

/** Resolves the bridge's runtime paths for a package install. Every value has
 *  an environment override; defaults derive from this file's location so
 *  nothing is baked and any checkout or clone works. */
export class PackageBridgePaths {
	private readonly packageRoot: string;

	constructor(private readonly env: Record<string, string | undefined>) {
		this.packageRoot = PackageBridgePaths.locatePackageRoot();
	}

	/** The package root = this entry's parent directory's parent. */
	private static locatePackageRoot(): string {
		const here = dirname(fileURLToPath(import.meta.url));
		return dirname(here);
	}

	/** Expands a leading `~` against the user's home; other values pass
	 *  through unchanged. */
	private static expand(value: string): string {
		if (value === "~") return homedir();
		if (value.startsWith("~/") || value.startsWith("~\\")) {
			return join(homedir(), value.slice(2));
		}
		return value;
	}

	private override(name: string): string | null {
		const raw = this.env[name];
		if (typeof raw !== "string" || !raw.trim()) return null;
		return PackageBridgePaths.expand(raw.trim());
	}

	/** The Pi agent home, shared by the memory home and the managed-copy
	 *  marker unless overridden. */
	configHome(): string {
		return this.override("AMS_CONFIG_HOME")
			?? this.override("PI_CODING_AGENT_DIR")
			?? join(homedir(), ".pi", "agent");
	}

	config(): BridgeConfig {
		const configHome = this.configHome();
		return {
			python: this.override("AMS_PYTHON")
				?? (process.platform === "win32" ? "python" : "python3"),
			hook: this.override("AMS_HOOK")
				?? join(this.packageRoot, "hooks", "root-memory-context.py"),
			memoryHome: this.override("AMS_MEMORY_HOME") ?? configHome,
			configHome,
			canonicalRoot: this.override("AMS_CANONICAL_ROOT") ?? this.packageRoot,
		};
	}

	/** True while the installer-managed bridge owns this agent home. */
	managedCopyPresent(): boolean {
		const home = this.configHome();
		return existsSync(join(home, ".agent-mem-struct", MANAGED_MARKER))
			&& existsSync(join(home, "extensions", MANAGED_BRIDGE));
	}
}

/** Deploys the protected root documents for a package install.
 *
 *  The installer owns `RULES.md`/`STRUCTURE.md` for a managed copy. A package
 *  install has no installer bridge, so this runs the shipped installer's
 *  `sync-documents` action once at load: a missing or identical copy is
 *  deployed or adopted, a copy this action previously tracked and that is
 *  still unmodified is refreshed to the canonical bytes, and a user-modified
 *  or foreign copy is refused rather than overwritten. Best-effort: a refusal
 *  or a missing interpreter never stops the bridge from registering. */
export class RootDocumentSync {
	constructor(
		private readonly config: BridgeConfig,
		private readonly script: string,
	) {}

	/** Runs the deployer and surfaces only a refusal on stderr. */
	sync(): void {
		const result = spawnSync(
			this.config.python,
			[
				this.script, "sync-documents",
				"--home", this.config.configHome,
				"--memory-home", this.config.memoryHome,
			],
			{ encoding: "utf-8", timeout: 15000 },
		);
		if (result.error) {
			console.error(
				"agent-mem-struct: root document sync could not run " +
				`(${result.error.message}); run the installer to deploy ` +
				"RULES.md and STRUCTURE.md.",
			);
			return;
		}
		if (result.status !== 0) {
			const detail = (result.stderr || result.stdout || "").trim();
			console.error(
				"agent-mem-struct: root document sync refused " +
				`(${detail || `exit ${result.status}`}); RULES.md and ` +
				"STRUCTURE.md were left untouched.",
			);
		}
	}
}

export default function (pi: any): void {
	try {
		const paths = new PackageBridgePaths(process.env);
		if (paths.managedCopyPresent()) {
			// The managed copy registered the bridge for this agent home.
			return;
		}
		const config = paths.config();
		new RootDocumentSync(config, join(dirname(config.hook), "pi", "manage.py")).sync();
		createRootMemoryBridge(pi, config);
	} catch (error) {
		// Never spawn a literal placeholder path: report and stay inert
		// instead of failing open silently.
		console.error(
			"agent-mem-struct: the package bridge could not resolve its paths " +
				`and is not active (${error instanceof Error ? error.message : String(error)}). ` +
				"Set AMS_HOOK and AMS_MEMORY_HOME, or run the installer.",
		);
	}
}