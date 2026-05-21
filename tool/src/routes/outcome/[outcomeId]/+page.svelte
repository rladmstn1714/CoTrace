<script lang="ts">
	import { base } from '$app/paths';
	import { page } from '$app/stores';
	import { get } from 'svelte/store';
	import { onMount } from 'svelte';
	import { selectedRun } from '$lib/stores/selectedRun';
	import { loadOutcomeRequirementTree } from '$lib/data/dataLoader';

	const outcomeId = $derived($page.params.outcomeId);

	let tree = $state<Awaited<ReturnType<typeof loadOutcomeRequirementTree>> | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	onMount(async () => {
		const run = get(selectedRun);
		try {
			tree = await loadOutcomeRequirementTree(run);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});

	const outcome = $derived(tree?.outcomes.find((o) => o.outcome_id === outcomeId) ?? null);
</script>

<svelte:head>
	<title>{outcome?.outcome?.slice(0, 50) ?? outcomeId} — Outcome</title>
</svelte:head>

<div class="detail-page">
	{#if loading}
		<div class="loading">Loading…</div>
	{:else if error}
		<div class="error">{error}</div>
	{:else if !outcome}
		<div class="error">Outcome not found.</div>
		<a href="{base}/explorer" class="back">← Back to explorer</a>
	{:else}
		<header class="detail-header">
			<a href="{base}/explorer" class="back">← Back to explorer</a>
			<h1 class="outcome-title">{outcome.outcome}</h1>
			<p class="outcome-sub">
				Shaper / Executor mini-charts for this goal are on the main screen — select this goal in the left outcome hierarchy.
			</p>
		</header>
	{/if}
</div>

<style>
	.detail-page {
		min-height: 100vh;
		background: #fafafa;
		display: flex;
		flex-direction: column;
		padding: 1rem;
	}
	.loading,
	.error {
		padding: 2rem;
		text-align: center;
		color: #6b7280;
	}
	.error {
		color: #dc2626;
	}
	.back {
		display: inline-block;
		margin-bottom: 0.5rem;
		color: #1976d2;
		text-decoration: none;
		font-size: 0.9rem;
	}
	.back:hover {
		text-decoration: underline;
	}
	.detail-header {
		margin-bottom: 1rem;
	}
	.outcome-title {
		margin: 0;
		font-size: 1.25rem;
		font-weight: 700;
		color: #111827;
		line-height: 1.3;
	}
	.outcome-sub {
		margin: 0.5rem 0 0 0;
		font-size: 0.875rem;
		color: #64748b;
		line-height: 1.45;
		max-width: 42rem;
	}
</style>
