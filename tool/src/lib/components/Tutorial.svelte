<script lang="ts">
	import { tick } from 'svelte';

	export type TutorialStep = {
		id: string;
		title: string;
		body: string;
		/** Optional HTML (trusted app content only). When set, used instead of plain `body`. */
		bodyHtml?: string;
		/** Single highlight target (`[data-tutorial="…"]`). */
		target?: string;
		/** Union of several targets (e.g. goal column + requirements column). */
		targets?: string[];
		/** No dimming or spotlight—only the instruction card (full UI stays bright). */
		noBackdropDim?: boolean;
	};

	let {
		open = false,
		steps = [],
		stepIndex = 0,
		onClose,
		onNext,
		onBack
	}: {
		open: boolean;
		steps: TutorialStep[];
		stepIndex?: number;
		onClose?: () => void;
		onNext?: () => void;
		onBack?: () => void;
	} = $props();

	const step = $derived(steps[stepIndex] ?? null);
	const isFirst = $derived(stepIndex <= 0);
	const isLast = $derived(stepIndex >= steps.length - 1);
	/** Steps that wait for user action (no Next, advance on click) */
	const isClickOutcomeStep = $derived(step?.id === 'click-outcome');
	const isClickTimelineMarkerStep = $derived(step?.id === 'click-timeline-marker');
	const isExploreReqsChatStep = $derived(step?.id === 'explore-reqs-timeline-chat');
	const isWaitForUserStep = $derived(
		isClickOutcomeStep || isClickTimelineMarkerStep || isExploreReqsChatStep
	);

	/** Rect of the highlighted element so the backdrop can show a "hole" (no dim there) */
	let highlightRect = $state<{ top: number; left: number; width: number; height: number } | null>(null);
	/** When on timeline step: rect of the first marker circle (for pulse overlay only; no app code changes) */
	let firstMarkerRect = $state<{ top: number; left: number; width: number; height: number } | null>(null);
	const shouldWaitForUserStep = $derived(
		isWaitForUserStep && (step?.target === 'timeline-marker' ? highlightRect != null : true)
	);

	const isWideTutorialHighlight = $derived(
		step?.id === 'goals-hierarchy' || (step?.targets != null && step.targets.length > 1)
	);

	/** Keep the card off the center requirements column (e.g. dock to the chat side). */
	const dockTutorialCardRight = $derived(
		step?.id === 'explore-goals-requirements' ||
			step?.id === 'explore-reqs-timeline-chat' ||
			step?.id === 'wrap-up-google-form'
	);

	function measureHighlight() {
		if (typeof document === 'undefined') return;
		const s = step;
		if (!s) return;

		if (s.noBackdropDim) {
			highlightRect = null;
			firstMarkerRect = null;
			return;
		}

		if (s.target === 'timeline-marker') {
			const el = document.querySelector('[data-tutorial-marker]');
			if (!el) {
				highlightRect = null;
				firstMarkerRect = null;
				return;
			}
			const r = el.getBoundingClientRect();
			highlightRect = { top: r.top, left: r.left, width: r.width, height: r.height };
			const firstBtn = el instanceof HTMLButtonElement ? el : el.querySelector('button[class*="marker"]');
			if (firstBtn) {
				const br = firstBtn.getBoundingClientRect();
				firstMarkerRect = { top: br.top, left: br.left, width: br.width, height: br.height };
			} else firstMarkerRect = null;
			return;
		}

		const keys =
			s.targets != null && s.targets.length > 0 ? s.targets : s.target != null && s.target !== '' ? [s.target] : [];
		if (keys.length === 0) {
			highlightRect = null;
			firstMarkerRect = null;
			return;
		}

		let minL = Infinity;
		let minT = Infinity;
		let maxR = -Infinity;
		let maxB = -Infinity;
		let found = false;
		for (const k of keys) {
			const el = document.querySelector(`[data-tutorial="${k}"]`);
			if (!el) continue;
			found = true;
			const r = el.getBoundingClientRect();
			minL = Math.min(minL, r.left);
			minT = Math.min(minT, r.top);
			maxR = Math.max(maxR, r.right);
			maxB = Math.max(maxB, r.bottom);
		}
		if (!found) {
			highlightRect = null;
			firstMarkerRect = null;
			return;
		}
		highlightRect = { top: minT, left: minL, width: maxR - minL, height: maxB - minT };
		firstMarkerRect = null;
	}

	$effect(() => {
		if (typeof document === 'undefined') return;
		if (!open || !step?.id) {
			document.body.removeAttribute('data-tutorial-step');
			highlightRect = null;
			return;
		}
		document.body.setAttribute('data-tutorial-step', step.id);
		highlightRect = null;
		firstMarkerRect = null;
		tick().then(measureHighlight);
		const onUpdate = () => measureHighlight();
		window.addEventListener('resize', onUpdate);
		window.addEventListener('scroll', onUpdate, true);
		return () => {
			document.body.removeAttribute('data-tutorial-step');
			window.removeEventListener('resize', onUpdate);
			window.removeEventListener('scroll', onUpdate, true);
		};
	});

	/** Four bands (top, left, right, bottom) around the hole so only the highlighted area stays bright */
	function backdropBands(): { top: number; left: number; width: number; height: number }[] {
		if (!highlightRect) return [];
		const { top, left, width, height } = highlightRect;
		const vw = typeof window !== 'undefined' ? window.innerWidth : 1000;
		const vh = typeof window !== 'undefined' ? window.innerHeight : 1000;
		return [
			{ top: 0, left: 0, width: vw, height: top },
			{ top, left: 0, width: left, height },
			{ top, left: left + width, width: vw - (left + width), height },
			{ top: top + height, left: 0, width: vw, height: vh - (top + height) }
		];
	}

	function handleNext() {
		if (isLast) onClose?.();
		else onNext?.();
	}

</script>

{#if open && steps.length > 0}
	<div
		class="tutorial-overlay"
		class:tutorial-overlay--dock-right={dockTutorialCardRight}
		role="dialog"
		aria-modal="true"
		aria-labelledby="tutorial-title"
	>
		{#if step?.id !== 'click-outcome' && !step?.noBackdropDim}
			<div class="tutorial-backdrop-wrap">
				{#if highlightRect}
					{#each backdropBands() as band}
						<div
							class="tutorial-backdrop-band"
							style="top: {band.top}px; left: {band.left}px; width: {band.width}px; height: {band.height}px;"
							aria-hidden="true"
						></div>
					{/each}
				{:else}
					<div class="tutorial-backdrop-full" aria-hidden="true"></div>
				{/if}
			</div>
		{/if}
		{#if (step?.id === 'timeline' || step?.id === 'click-timeline-marker') && firstMarkerRect}
			<div
				class="tutorial-marker-pulse"
				style="left: {firstMarkerRect.left}px; top: {firstMarkerRect.top}px; width: {firstMarkerRect.width}px; height: {firstMarkerRect.height}px;"
				aria-hidden="true"
			></div>
		{/if}
		{#if highlightRect}
			<div
				class="tutorial-focus-ring"
				class:tutorial-focus-ring-pulse={isWaitForUserStep}
				class:tutorial-focus-ring--panel={isWideTutorialHighlight}
				style="top: {highlightRect.top - 6}px; left: {highlightRect.left - 6}px; width: {highlightRect.width + 12}px; height: {highlightRect.height + 12}px;"
				aria-hidden="true"
			></div>
		{/if}
		<div class="tutorial-card">
			<h3 id="tutorial-title" class="tutorial-title">{step?.title ?? ''}</h3>
			{#if step?.bodyHtml}
				<div class="tutorial-body tutorial-body--html">{@html step.bodyHtml}</div>
			{:else}
				<p class="tutorial-body">{step?.body ?? ''}</p>
			{/if}
			<div class="tutorial-actions">
				<div class="tutorial-actions-left">
					<button type="button" class="tutorial-btn secondary" onclick={onClose}>Skip</button>
					<button type="button" class="tutorial-btn secondary" onclick={onBack} disabled={isFirst}>Previous</button>
				</div>
				{#if !shouldWaitForUserStep}
					<button type="button" class="tutorial-btn primary" onclick={handleNext}>
						{isLast ? 'Done' : 'Next'}
					</button>
				{/if}
			</div>
			<span class="tutorial-progress">{stepIndex + 1} / {steps.length}</span>
		</div>
	</div>
{/if}

<style>
	.tutorial-overlay {
		position: fixed;
		inset: 0;
		z-index: 9999;
		display: flex;
		align-items: flex-end;
		justify-content: center;
		padding: 1.5rem;
		pointer-events: none;
		isolation: isolate;
	}
	.tutorial-overlay--dock-right {
		align-items: center;
		justify-content: flex-end;
		padding: 1rem 0.75rem 1rem 2rem;
	}
	.tutorial-overlay--dock-right .tutorial-card {
		max-width: min(360px, calc(100vw - 1.5rem));
	}
	.tutorial-backdrop-wrap {
		position: absolute;
		inset: 0;
		pointer-events: none;
	}
	.tutorial-backdrop-band {
		position: fixed;
		padding: 0;
		border: none;
		background: rgba(0, 0, 0, 0.4);
		cursor: default;
		pointer-events: auto;
	}
	.tutorial-backdrop-full {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		padding: 0;
		border: none;
		background: rgba(0, 0, 0, 0.4);
		cursor: default;
		pointer-events: auto;
	}
	.tutorial-card {
		position: relative;
		z-index: 10001;
		pointer-events: auto;
		width: 100%;
		max-width: 420px;
		padding: 1.3rem 1.5rem;
		box-sizing: border-box;
		background: #ffffff;
		border-radius: 20px;
		box-shadow: 0 16px 40px rgba(36, 39, 43, 0.2);
		border: 1.5px solid var(--ct-line);
	}
	.tutorial-title {
		margin: 0 0 0.5rem 0;
		font-size: 1.15rem;
		font-weight: 800;
		color: var(--ct-ink);
	}
	.tutorial-body {
		margin: 0 0 1.25rem 0;
		font-size: 0.95rem;
		line-height: 1.55;
		color: var(--ct-text);
	}
	.tutorial-body--html {
		color: var(--ct-text);
	}
	.tutorial-body--html :global(p) {
		margin: 0 0 0.75rem 0;
		color: inherit;
	}
	.tutorial-body--html :global(p:last-child) {
		margin-bottom: 0;
	}
	.tutorial-body--html :global(strong) {
		font-weight: 800;
		color: #0f172a;
	}
	.tutorial-actions {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		flex-wrap: wrap;
	}
	.tutorial-actions-left {
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
	}
	.tutorial-btn {
		padding: 0.5rem 1.1rem;
		font-size: 0.88rem;
		font-weight: 700;
		border-radius: 999px;
		cursor: pointer;
		border: none;
	}
	.tutorial-btn.primary {
		background: var(--ct-ink);
		color: #fff;
	}
	.tutorial-btn.primary:hover {
		background: #3a3e44;
	}
	.tutorial-btn.secondary {
		background: var(--ct-panel);
		color: var(--ct-text);
	}
	.tutorial-btn.secondary:hover:not(:disabled) {
		background: #e7e8eb;
	}
	.tutorial-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.tutorial-progress {
		position: absolute;
		top: 1.1rem;
		right: 1.5rem;
		font-size: 0.78rem;
		font-weight: 700;
		color: var(--ct-faint);
	}

	.tutorial-focus-ring {
		position: fixed;
		z-index: 9998;
		pointer-events: none;
		border-radius: 22px;
		border: 2.5px solid #ffffff;
		box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.15), 0 0 0 4px rgba(255, 255, 255, 0.35);
	}
	/* Full left column: stronger dim outside + light border so the panel reads clearly “bright”. */
	.tutorial-focus-ring--panel {
		border: 3px solid rgba(255, 255, 255, 0.95);
		box-shadow:
			0 0 0 9999px rgba(0, 0, 0, 0.48),
			0 0 0 2px rgba(59, 130, 246, 0.35),
			inset 0 0 0 1px rgba(255, 255, 255, 0.5);
	}
	.tutorial-focus-ring-pulse {
		animation: tutorial-focus-pulse 1.2s ease-in-out infinite;
	}
	.tutorial-marker-pulse {
		position: fixed;
		z-index: 9998;
		pointer-events: none;
		border-radius: 50%;
		animation: tutorial-click-pulse 1.4s ease-in-out infinite;
		box-shadow: 0 0 0 2px rgba(25, 118, 210, 0.5);
	}
	@keyframes tutorial-highlight-blink {
		0%, 100% { opacity: 1; }
		50% { opacity: 0.85; }
	}
	@keyframes tutorial-focus-pulse {
		0%, 100% { transform: scale(1); border-color: #ffffff; }
		50% { transform: scale(1.01); border-color: #e7e8eb; }
	}
	@keyframes tutorial-click-pulse {
		0%, 100% { transform: scale(1); box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08); }
		50% { transform: scale(1.04); box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15); }
	}
</style>
