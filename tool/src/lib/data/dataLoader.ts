import type {
	OutcomeRequirementTree,
	OutcomeNode,
	OutcomeContributionsData,
	OutcomeContribution,
	RequirementRow,
	NodeAnalysisData,
	NodeAnalysis,
	RelatedUtterance,
	OutputsDependencyEdge
} from '$lib/types';
import { compareOutcomeIds, outcomeSortKey } from '$lib/utils';

/**
 * Data directory. VITE_DATA_BASE overrides; default is sample (static/sample, e.g. paper-demo).
 */
const BASE =
	typeof import.meta.env?.VITE_DATA_BASE === 'string' && import.meta.env.VITE_DATA_BASE.trim()
		? '/' + String(import.meta.env.VITE_DATA_BASE).replace(/^\/+/, '')
		: '/sample';

interface RequirementOutputDependency {
	dialogue_id: string;
	final_turn: number;
	requirement_to_outcome: Record<string, string>;
}

interface ReqOutputListEntry {
	id: string;
	content: string;
	turn_id: number;
	parent_outcome_id?: string | null;
	child_outcome_ids?: string[];
	related_outcome_ids?: string[];
}

interface OutputsRelatedEdge {
	outcome_id: string;
	related_id: string;
}

interface IntentMapFile {
	intentions?: Array<{ intention_id: string; intention: string }>;
	outcome_to_intention?: Record<string, string>;
}

interface RequirementsOutputsLists {
	dialogue_id: string;
	requirements: ReqOutputListEntry[];
	outputs: ReqOutputListEntry[];
	outputs_dependency?: OutputsDependencyEdge[];
	outputs_related?: OutputsRelatedEdge[];
}

interface RequirementRelationRow {
	t: number;
	requirement_id: string;
	operation_type: string;
	related_prev_requirement: string | string[] | null;
	outcome_id: string;
}

interface OutputContributionsRole {
	user: { rate: number; M_total: number; count: number; M_dir?: number; M_ind?: number };
	assistant: { rate: number; M_total: number; count: number; M_dir?: number; M_ind?: number };
}

interface OutputContributionEntry {
	role_contributions: Record<string, OutputContributionsRole>;
	overall: { user: OutputContributionsRole['user']; assistant: OutputContributionsRole['assistant'] };
}

export interface ActionUtteranceMapEntry {
	evidence_quote: string;
	turn_id: number;
}

interface OriginAction {
	action_id: string;
	role: string;
	action_text: string;
}

interface RequirementActionMapEntry {
	origin_actions: OriginAction[];
	contributing_actions?: OriginAction[];
	implementation_actions?: OriginAction[];
	related_actions: unknown[];
}

interface UtteranceTurn {
	turn_id: number;
	/** "user" | "assistant" for 1:1 chat; Slack exports use display names (human side). */
	speaker: string;
	utterance: string;
	/** English translation of utterance (used for chat log display when present) */
	utterance_en?: string;
}

interface UtteranceList {
	dialogue_id: string;
	utterances: UtteranceTurn[];
}

// --- Load raw JSON (supports inline embedded data for single-file HTML) ---

declare global {
	interface Window {
		__INLINE_DATA__?: Record<string, unknown>;
	}
}

function getDataBase(run?: string | null): string {
	return run ? `${BASE}/${run}` : BASE;
}

const FETCH_TIMEOUT_MS = 30000;

async function fetchWithTimeout(url: string, options?: RequestInit): Promise<Response> {
	const ctrl = new AbortController();
	const id = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
	try {
		const res = await fetch(url, { ...options, signal: ctrl.signal });
		return res;
	} finally {
		clearTimeout(id);
	}
}

function getInlineData<T>(path: string, run?: string | null): T | null {
	const base = getDataBase(run);
	const key = `${base}${path}`;
	const store = typeof window !== 'undefined' ? window.__INLINE_DATA__ : undefined;
	if (store && key in store) return store[key] as T;
	return null;
}

/** Try primary path; if 404, try pipeline layout: {base}/run/{path}. */
async function fetchJson<T>(path: string, run?: string | null): Promise<T> {
	const base = getDataBase(run);
	const inline = getInlineData<T>(path, run);
	if (inline != null) return inline;
	let res = await fetch(`${base}${path}`);
	if (!res.ok) {
		res = await fetch(`${base}/run${path}`);
	}
	if (!res.ok) throw new Error(`Failed to load ${path}`);
	return res.json();
}

/** Like fetchJson but returns null on 404 (for optional files like outcome_action_map.json). */
async function fetchJsonOptional<T>(path: string, run?: string | null): Promise<T | null> {
	const base = getDataBase(run);
	const inline = getInlineData<T>(path, run);
	if (inline != null) return inline;
	let res = await fetch(`${base}${path}`);
	if (!res.ok) {
		res = await fetch(`${base}/run${path}`);
	}
	if (res.status === 204) return null;
	if (!res.ok) return null;
	return res.json();
}

/** Try primary path; if 404, try pipeline layout: {base}/run/{path}. */
async function fetchJsonl<T>(path: string, run?: string | null): Promise<T[]> {
	const base = getDataBase(run);
	const inlineRaw = getInlineData<string>(path, run);
	if (inlineRaw != null) {
		const text = typeof inlineRaw === 'string' ? inlineRaw : JSON.stringify(inlineRaw);
		const lines = text.trim().split('\n').filter((l: string) => l.trim());
		return lines.map((line: string) => JSON.parse(line) as T);
	}
	let res: Response;
	try {
		res = await fetchWithTimeout(`${base}${path}`);
	} catch (e) {
		if ((e as Error)?.name === 'AbortError') throw new Error(`Timeout loading ${path}`);
		throw e;
	}
	if (!res.ok) {
		res = await fetchWithTimeout(`${base}/run${path}`).catch(() => res);
	}
	if (!res.ok) throw new Error(`Failed to load ${path}`);
	const text = await res.text();
	const lines = text.trim().split('\n').filter((l) => l.trim());
	return lines.map((line) => JSON.parse(line) as T);
}

// --- Build OutcomeRequirementTree from new files ---

/** Derive outcome_id from output id (e.g. outcome_1_0 -> outcome_1) */
function outcomeIdFromOutputId(outputId: string): string {
	const m = outputId.match(/^(.+)_\d+$/);
	return m ? m[1] : outputId;
}

/** Match output id to a specific outcome_id (e.g. outcome_1_0 for outcome_1). Avoids outcome_1 matching outcome_10_0. */
function outputBelongsToOutcome(outputId: string, outcomeId: string): boolean {
	if (outputId === outcomeId + '_0') return true;
	const prefix = outcomeId + '_';
	if (!outputId.startsWith(prefix)) return false;
	const suffix = outputId.slice(prefix.length);
	return /^\d+$/.test(suffix);
}

export async function loadOutcomeRequirementTree(run?: string | null): Promise<OutcomeRequirementTree> {
	const [dep, lists, outcomeActionMap] = await Promise.all([
		fetchJson<RequirementOutputDependency>('/requirement_output_dependency.json', run),
		fetchJson<RequirementsOutputsLists>('/requirements_outputs_lists.json', run),
		fetchJsonOptional<OutcomeActionMapData>('/outcome_action_map.json', run)
	]);

	const mapping = dep.requirement_to_outcome;
	// Include all outcomes: from requirement_to_outcome AND from outputs (so outcomes with no requirements are still plotted)
	const fromMapping = new Set(Object.values(mapping));
	const fromOutputs = lists.outputs.map((o) => outcomeIdFromOutputId(o.id));
	const outcomeIds = [...new Set([...fromMapping, ...fromOutputs])].sort(compareOutcomeIds);

	const outcomes: OutcomeNode[] = outcomeIds.map((outcomeId) => {
		const requirements = Object.entries(mapping)
			.filter(([, oid]) => oid === outcomeId)
			.map(([rid]) => rid)
			.sort();
		const reqTurnIds = requirements.flatMap((rid) => {
			const r = lists.requirements.find((x) => x.id === rid);
			return r ? [r.turn_id] : [];
		});
		const outputDesc = lists.outputs.find((o) => outputBelongsToOutcome(o.id, outcomeId));
		// Outcome display name: prefer outcome_action_map.description (per outcome_id), then outputs content, else outcome_id
		const oamEntry = outcomeActionMap?.outcome_action_map?.[outcomeId];
		const outcome =
			oamEntry?.description?.trim() ?? outputDesc?.content ?? outcomeId;
		// For outcomes with no requirements, use output turn_id for created_at/last_updated
		const created_at =
			reqTurnIds.length ? Math.min(...reqTurnIds) : (outputDesc?.turn_id ?? oamEntry?.created_at_turn ?? 0);
		const last_updated =
			reqTurnIds.length ? Math.max(...reqTurnIds) : (outputDesc?.turn_id ?? 0);
		return {
			outcome_id: outcomeId,
			outcome,
			created_at,
			last_updated,
			requirements
		};
	});

	// Build explicit hierarchy from parent_outcome_id / child_outcome_ids first.
	// Some datasets include broader dependency edges in outputs_dependency, which can incorrectly merge branches.
	const derivedHierarchy: OutputsDependencyEdge[] = [];
	const edgeSet = new Set<string>();
	for (const o of lists.outputs) {
		const childOutcomeId = outcomeIdFromOutputId(o.id);
		const parentId = o.parent_outcome_id;
		if (parentId) {
			const key = `${parentId}|${childOutcomeId}`;
			if (!edgeSet.has(key)) {
				edgeSet.add(key);
				derivedHierarchy.push({ parent_id: parentId + '_0', child_id: o.id });
			}
		}
		for (const cid of o.child_outcome_ids ?? []) {
			const key = `${childOutcomeId}|${cid}`;
			if (!edgeSet.has(key)) {
				edgeSet.add(key);
				derivedHierarchy.push({ parent_id: o.id, child_id: cid + '_0' });
			}
		}
	}
	const outputsDependency = derivedHierarchy.length > 0 ? derivedHierarchy : (lists.outputs_dependency ?? []);

	// Derive related edges from inline related_outcome_ids when outputs_related is absent
	let outputsRelated = lists.outputs_related ?? [];
	if (outputsRelated.length === 0) {
		const relSet = new Set<string>();
		for (const o of lists.outputs) {
			const oid = outcomeIdFromOutputId(o.id);
			for (const rid of o.related_outcome_ids ?? []) {
				const key = [oid, rid].sort().join('|');
				if (!relSet.has(key)) {
					relSet.add(key);
					outputsRelated.push({ outcome_id: o.id, related_id: rid + '_0' });
				}
			}
		}
	}

	return {
		dialogue_id: dep.dialogue_id,
		final_turn: dep.final_turn,
		outcomes,
		output_versions: lists.outputs ?? [],
		requirement_to_outcome_mapping: mapping,
		outputs_dependency: outputsDependency,
		outputs_related: outputsRelated
	};
}

// --- Load requirements (requirement_relations.jsonl + content from requirements list) ---

export async function loadRequirements(run?: string | null): Promise<RequirementRow[]> {
	const [relations, lists] = await Promise.all([
		fetchJsonl<RequirementRelationRow>('/requirement_relations.jsonl', run),
		fetchJson<RequirementsOutputsLists>('/requirements_outputs_lists.json', run)
	]);

	const contentById = new Map<string, string>();
	for (const r of lists.requirements) {
		contentById.set(r.id, r.content);
	}

	const opMap: Record<string, string> = {
		CREATE: 'ADD',
		REVISE: 'MODIFY',
		DELETE: 'DELETE'
	};

	const rows: RequirementRow[] = relations.map((row) => ({
		t: row.t,
		requirement_id: row.requirement_id,
		operation_type: opMap[row.operation_type] ?? row.operation_type,
		related_prev_requirement: row.related_prev_requirement,
		requirement_content: contentById.get(row.requirement_id) ?? null,
		outcome_id: row.outcome_id
	}));

	rows.sort((a, b) => a.t - b.t);
	return rows;
}

// --- Build OutcomeContributionsData from output_contributions.json ---

export async function loadOutcomeContributions(run?: string | null): Promise<OutcomeContributionsData> {
	const [outputContrib, lists, dep, outcomeActionMap] = await Promise.all([
		fetchJson<Record<string, OutputContributionEntry>>('/output_contributions.json', run),
		fetchJson<RequirementsOutputsLists>('/requirements_outputs_lists.json', run),
		fetchJson<RequirementOutputDependency>('/requirement_output_dependency.json', run),
		fetchJsonOptional<OutcomeActionMapData>('/outcome_action_map.json', run)
	]);

	const getOutcomeDesc = (outcomeId: string): string => {
		const oamEntry = outcomeActionMap?.outcome_action_map?.[outcomeId];
		if (oamEntry?.description?.trim()) return oamEntry.description.trim();
		const o = lists.outputs.find((x) => outputBelongsToOutcome(x.id, outcomeId));
		return o?.content ?? outcomeId;
	};

	// Use collaboration rate from static as-is (no post-processing: use rate, not M_total)
	const result: OutcomeContributionsData = {};
	for (const [outcomeId, entry] of Object.entries(outputContrib)) {
		const overall = entry?.overall;
		const userRate = overall?.user?.rate ?? 0;
		const asstRate = overall?.assistant?.rate ?? 0;
		const roleContribUser: Record<string, { M_dir: number; M_ind: number; M_total: number; count: number; action_examples: never[] }> = {};
		const roleContribAsst: Record<string, { M_dir: number; M_ind: number; M_total: number; count: number; action_examples: never[] }> = {};
		for (const [role, roleEntry] of Object.entries(entry?.role_contributions ?? {})) {
			const u = roleEntry?.user;
			const a = roleEntry?.assistant;
			const uRate = u?.rate ?? 0;
			const aRate = a?.rate ?? 0;
			roleContribUser[role] = {
				M_dir: u?.M_dir ?? uRate,
				M_ind: u?.M_ind ?? 0,
				M_total: uRate,
				count: u?.count ?? 0,
				action_examples: []
			};
			roleContribAsst[role] = {
				M_dir: a?.M_dir ?? aRate,
				M_ind: a?.M_ind ?? 0,
				M_total: aRate,
				count: a?.count ?? 0,
				action_examples: []
			};
		}
		const requirements = Object.entries(dep.requirement_to_outcome)
			.filter(([, oid]) => oid === outcomeId)
			.map(([rid]) => rid)
			.sort();

		result[outcomeId] = {
			thread_id: outcomeSortKey(outcomeId),
			outcome_id: outcomeId,
			outcome: getOutcomeDesc(outcomeId),
			speaker_contributions: {
				user: {
					direct_influence: userRate,
					indirect_influence: 0,
					total_influence: userRate,
					requirement_count: overall?.user?.count ?? 0
				},
				assistant: {
					direct_influence: asstRate,
					indirect_influence: 0,
					total_influence: asstRate,
					requirement_count: overall?.assistant?.count ?? 0
				}
			},
			role_contributions: {
				user: roleContribUser,
				assistant: roleContribAsst
			},
			role_frequencies: { user: {}, assistant: {} },
			requirements
		};
	}
	return result;
}

// --- Build NodeAnalysisData from requirement_action_map + action_utterance_map + utterance_list ---

export async function loadNodeAnalysis(run?: string | null): Promise<NodeAnalysisData> {
	const [actionMap, utteranceMap, utteranceList, lists, dep] = await Promise.all([
		fetchJson<Record<string, RequirementActionMapEntry>>('/requirement_action_map.json', run),
		fetchJson<Record<string, ActionUtteranceMapEntry>>('/action_utterance_map.json', run),
		fetchJson<UtteranceList>('/utterance_list.json', run),
		fetchJson<RequirementsOutputsLists>('/requirements_outputs_lists.json', run),
		fetchJson<RequirementOutputDependency>('/requirement_output_dependency.json', run)
	]);

	const speakerByTurnId = new Map<number, string>();
	for (const u of utteranceList.utterances) {
		speakerByTurnId.set(u.turn_id, u.speaker);
	}

	const contentById = new Map<string, string>();
	for (const r of lists.requirements) {
		contentById.set(r.id, r.content);
	}

	const node_analyses: Record<string, NodeAnalysis> = {};
	for (const [reqId, entry] of Object.entries(actionMap)) {
		const direct: RelatedUtterance[] = [];
		const originActions = Array.isArray(entry?.origin_actions) ? entry.origin_actions : [];
		for (const action of originActions) {
			const ev = utteranceMap[action.action_id];
			if (!ev) continue;
			const speaker = speakerByTurnId.get(ev.turn_id) ?? 'user';
			direct.push({
				t: ev.turn_id,
				speaker,
				content: ev.evidence_quote,
				relationship_type: 'DIRECT',
				influence_score: 1.0
			});
		}
		direct.sort((a, b) => a.t - b.t);
		const firstTurn = direct[0]?.t ?? 0;
		const primaryCreator = direct[0]?.speaker ?? 'user';
		node_analyses[reqId] = {
			requirement_id: reqId,
			requirement_content: contentById.get(reqId) ?? '',
			who_made_this_requirement: {
				primary_creator: primaryCreator,
				contributors: [...new Set(direct.map((d) => d.speaker))],
				creation_turn: firstTurn
			},
			related_utterances: {
				direct,
				indirect: []
			}
		};
	}

	const dialogue_id = utteranceList.dialogue_id;
	const final_turn =
		dep.final_turn ??
		(utteranceList.utterances.length
			? Math.max(...utteranceList.utterances.map((u) => u.turn_id))
			: 0);

	return {
		dialogue_id,
		final_turn,
		node_analyses
	};
}

// --- Types and loaders for action timeline ---

export type RequirementActionMap = Record<
	string,
	{ origin_actions: OriginAction[]; contributing_actions?: OriginAction[]; implementation_actions?: OriginAction[]; related_actions: unknown[] }
>;

export type UtteranceListData = UtteranceList;

export async function loadRequirementActionMap(run?: string | null): Promise<RequirementActionMap> {
	return fetchJson<RequirementActionMap>('/requirement_action_map.json', run);
}

/** Per-requirement contribution: overall.user.rate / overall.assistant.rate (0–1) */
export interface RequirementContributionEntry {
	overall: {
		user: { rate: number; M_total: number; count: number };
		assistant: { rate: number; M_total: number; count: number };
	};
}

export type RequirementContributionsData = Record<string, RequirementContributionEntry>;

export async function loadRequirementContributions(run?: string | null): Promise<RequirementContributionsData> {
	const inline = getInlineData<RequirementContributionsData>('/requirement_contributions.json', run);
	if (inline != null) return inline;
	// Prefer API: server reads from VITE_DATA_BASE (same folder as /api/runs), so file on disk is used
	if (run) {
		try {
			const res = await fetch(
				`${import.meta.env.BASE_URL}api/requirement_contributions/${encodeURIComponent(run)}`
			);
			if (res.ok) return (await res.json()) as RequirementContributionsData;
		} catch {
			/* fall through to static path */
		}
	}
	// Fallback: static path {BASE}/{run}/requirement_contributions.json (wine) or {run}/run/... (wine2)
	try {
		return await fetchJson<RequirementContributionsData>('/requirement_contributions.json', run);
	} catch {
		return {};
	}
}

export async function loadUtteranceList(run?: string | null): Promise<UtteranceListData> {
	return fetchJson<UtteranceListData>('/utterance_list.json', run);
}

export type ActionUtteranceMap = Record<string, ActionUtteranceMapEntry>;

export async function loadActionUtteranceMap(run?: string | null): Promise<ActionUtteranceMap> {
	return fetchJson<ActionUtteranceMap>('/action_utterance_map.json', run);
}

// --- Outcome–action map (optional; when present, used for outcomes with no requirements) ---

export interface OutcomeActionItem {
	action_id: string;
	turn_id: number;
	speaker?: string;
	action_text: string;
	role?: string;
	action_type?: string;
	evidence_quote?: string;
	bound_outcome_id?: string;
}

export interface OutcomeActionEntry {
	outcome_id: string;
	description?: string;
	created_at_turn?: number;
	actions: OutcomeActionItem[];
	prev_action_relationships?: Array<{
		prev_action_id: string;
		prev_outcome_id: string;
		relationship_type: string;
		relationship_score: number | null;
		explanation?: string;
		similarity?: number;
	}>;
}

export interface OutcomeActionMapData {
	dialogue_id: string;
	outcome_action_map: Record<string, OutcomeActionEntry>;
}

export async function loadOutcomeActionMap(run?: string | null): Promise<OutcomeActionMapData | null> {
	return fetchJsonOptional<OutcomeActionMapData>('/outcome_action_map.json', run);
}

export type RequirementStatusById = Record<
	string,
	{ is_executed: boolean; is_dismissed: boolean; dismissed_at_turn: number | null }
>;

interface RequirementStatusFile {
	dialogue_id: string;
	requirements: Array<{
		id: string;
		is_dismissed: boolean;
		dismissed_by_action_ids?: string[];
		dismissed_at_turn: number | null;
		is_executed: boolean;
	}>;
}

/** Optional requirement execution/dismiss status from requirement_status.json */
export async function loadRequirementStatus(run?: string | null): Promise<RequirementStatusById> {
	const status = await fetchJsonOptional<RequirementStatusFile>('/requirement_status.json', run);
	if (!status?.requirements?.length) return {};
	const byId: RequirementStatusById = {};
	for (const row of status.requirements) {
		if (!row?.id) continue;
		byId[row.id] = {
			is_executed: !!row.is_executed,
			is_dismissed: !!row.is_dismissed,
			dismissed_at_turn: row.dismissed_at_turn ?? null
		};
	}
	return byId;
}

/** Optional outcome -> intention text mapping from intent_outcome_map.json (or legacy step05b_output.json). */
export async function loadOutcomeIntentionMap(run?: string | null): Promise<Record<string, string>> {
	const intentMap =
		(await fetchJsonOptional<IntentMapFile>('/intent_outcome_map.json', run)) ??
		(await fetchJsonOptional<IntentMapFile>('/step05b_output.json', run));
	if (!intentMap) return {};
	const intentions = new Map<string, string>();
	for (const item of intentMap.intentions ?? []) {
		if (item?.intention_id && item?.intention) intentions.set(item.intention_id, item.intention);
	}
	const byOutcome: Record<string, string> = {};
	for (const [outcomeId, intentionId] of Object.entries(intentMap.outcome_to_intention ?? {})) {
		byOutcome[outcomeId] = intentions.get(intentionId) ?? intentionId;
	}
	return byOutcome;
}

/**
 * Map each requirement_id to its latest requirement_content (by max t).
 * MODIFY chain: when id is e.g. 12_1 (modified from 12), both 12 and 12_1 get their
 * own entry; tree typically lists the latest id (12_1) for display.
 */
export function buildRequirementContentMap(rows: RequirementRow[]): Map<string, string> {
	const byId = new Map<string, { content: string; t: number }>();
	for (const r of rows) {
		if (r.requirement_content == null) continue;
		const id = String(r.requirement_id ?? '').trim();
		if (!id) continue;
		const existing = byId.get(id);
		if (!existing || r.t > existing.t) {
			byId.set(id, { content: r.requirement_content, t: r.t });
		}
	}
	const out = new Map<string, string>();
	for (const [id, { content }] of byId) {
		out.set(id, content);
	}
	return out;
}

/**
 * Resolve display content for a requirement id, following MODIFY chain.
 * - First try exact id (e.g. 12_1).
 * - If not found and id looks like "base_version" (e.g. 12_1), try content for "base" (12).
 * So tree can list either 12 or 12_1 and we still show the best available content.
 */
export function getRequirementContent(
	reqId: string,
	contentByReqId: Map<string, string>
): string | null {
	const id = String(reqId ?? '').trim();
	if (!id) return null;
	const exact = contentByReqId.get(id);
	if (exact) return exact;
	// MODIFY chain: 12_1 -> try 12; 3_1 -> try 3
	const underscoreIdx = id.indexOf('_');
	if (underscoreIdx > 0) {
		const baseId = id.slice(0, underscoreIdx);
		return contentByReqId.get(baseId) ?? null;
	}
	return null;
}
