// Minimal ambient typing for Electron's node-integration surface.
// Obsidian runs inside Electron; `require("electron")` exposes Node's
// child_process when the plugin has Node access (desktop only).
declare module "electron" {
	export interface ChildProcessLike {
		stdout: { on(event: "data", cb: (d: unknown) => void): void };
		on(event: "error", cb: () => void): void;
		on(event: "close", cb: (code: number | null) => void): void;
	}
	export function spawn(
		command: string,
		args: string[],
		options?: { timeout?: number },
	): ChildProcessLike;
}
