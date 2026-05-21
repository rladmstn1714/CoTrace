import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import fs from 'fs';
import path from 'path';
import { resolveDataBasePath } from '$lib/server/resolveDataBasePath';

const RUN_MARKERS = ['requirements_outputs_lists.json', 'requirement_output_dependency.json'] as const;

/** True if this directory looks like a run folder (JSON next to or under `run/`). */
function isRunDirectory(absDir: string): boolean {
	for (const name of RUN_MARKERS) {
		if (fs.existsSync(path.join(absDir, name))) return true;
		if (fs.existsSync(path.join(absDir, 'run', name))) return true;
	}
	return false;
}

/**
 * Collect run paths relative to root: any nested folder that contains run data,
 * plus recurse into non-run subfolders so category/subfolder layouts work.
 */
function collectRunsUnderRoot(rootAbs: string, relative = ''): string[] {
	const found: string[] = [];
	let entries: fs.Dirent[];
	try {
		entries = fs.readdirSync(rootAbs, { withFileTypes: true });
	} catch {
		return found;
	}
	for (const e of entries) {
		if (!e.isDirectory() || e.name.startsWith('.')) continue;
		const childAbs = path.join(rootAbs, e.name);
		const relPath = relative ? `${relative}/${e.name}` : e.name;
		if (isRunDirectory(childAbs)) {
			found.push(relPath);
		} else {
			found.push(...collectRunsUnderRoot(childAbs, relPath));
		}
	}
	return found;
}

/** List runs: from VITE_DATA_BASE (if set) or bundled static/sample. */
export const GET: RequestHandler = async () => {
	const dataBase = (typeof process !== 'undefined' && process.env?.VITE_DATA_BASE?.trim()) ?? '';
	let dataRuns: string[] = [];

	// 1) Try env path first
	if (dataBase) {
		try {
			const resolved = resolveDataBasePath(dataBase);
			const collected = isRunDirectory(resolved) ? ['__default__'] : collectRunsUnderRoot(resolved);
			dataRuns = [...new Set(collected)].sort((a, b) => a.localeCompare(b));
		} catch {
			// fall through
		}
	}

	// 2) If no env path was provided, use bundled static/sample (paper-demo, etc.)
	if (!dataBase && dataRuns.length === 0) {
		try {
			const samplePath = path.join(process.cwd(), 'static', 'sample');
			const collected = collectRunsUnderRoot(samplePath);
			dataRuns = [...new Set(collected)].sort((a, b) => a.localeCompare(b));
		} catch {
			dataRuns = [];
		}
	}

	// Exclude legacy sample so it never appears in the run dropdown
	const filtered = dataRuns.filter(
		(name) => name !== 'sampledata_try_14' && !name.endsWith('/sampledata_try_14')
	);

	return json({ runs: filtered });
};
