/* P3 Screen 6 — Personal Scorecard (`/cskh/scorecard`, Staff self-scope).
   Strict self-scope only: NEVER renders peer leaderboards or other employees' PII.
*/

;(function () {
	'use strict'

	const cskh = window.cskh
	const api = window.cskhP3Api
	const w = window.cskhP3Widgets
	const t = cskh.t
	const el = cskh.el

	const bannersHost = document.getElementById('p3-banners')
	const filterHost = document.getElementById('p3-filter-host')
	const watermarkHost = document.getElementById('p3-watermark-host')
	const alertHost = document.getElementById('p3-state-alert')
	const mainHost = document.getElementById('p3-main-content')

	const seq = cskh.sequencer()

	const filterBar = w.mountFilterBar(filterHost, {
		showCampaign: true,
		showTeam: false,
		showEmployee: false,
		onChange: function () {
			loadPage()
		},
	})

	function getSelfFilters() {
		return Object.assign({}, filterBar.getState(), {
			scope_type: 'employee',
			scope_id: cskh.currentUser(),
		})
	}

	w.bindHeaderActions(
		'personal_scorecard',
		function () {
			return getSelfFilters()
		},
		function () {
			loadPage()
		}
	)

	;(async function () {
		try {
			const lists = await api.getFilterOptions()
			filterBar.setFilterLists({ campaigns: lists.campaigns, revisions: lists.revisions })
		} catch (_err) {
			/* ignore */
		}
	})()

	function renderScorecard(data) {
		cskh.clear(mainHost)

		// Score & components card
		if (data.score || (data.components && data.components.length)) {
			const scoreCard = el('div', { class: 'cskh-card', id: 'p3-scorecard-summary' }, [
				el('h2', { text: t('My process score & components') }),
			])
			if (data.score) {
				scoreCard.appendChild(
					w.renderMetricCard(data.score, {
						onDrilldown: function (m) {
							w.openDrilldownModal(m, getSelfFilters())
						},
					})
				)
			}
			if (data.components && data.components.length) {
				const validWeightText =
					data.score && data.score.valid_weight_sum !== null && data.score.valid_weight_sum !== undefined
						? w.formatDecimal(data.score.valid_weight_sum, 0) +
							(data.configured_weight_total
								? ' / ' + w.formatDecimal(data.configured_weight_total, 0)
								: '')
						: '—'
				const rawSumText =
					data.raw_points_total !== null && data.raw_points_total !== undefined
						? w.formatDecimal(data.raw_points_total, 2) +
							(data.score && data.score.valid_weight_sum
								? ' / ' + w.formatDecimal(data.score.valid_weight_sum, 0)
								: '')
						: t('N/A')
				const totalScoreDisp = data.score ? w.formatMetricDisplay(data.score) : { main: t('N/A') }

				scoreCard.appendChild(
					el('div', { class: 'cskh-table-scroll', style: 'margin-top: 12px;' }, [
						el('table', { class: 'cskh-table', id: 'p3-scorecard-components' }, [
							el('thead', {}, [
								el('tr', {}, [
									el('th', { text: t('Component') }),
									el('th', { class: 'num', text: t('Weight') }),
									el('th', { class: 'num', text: t('My rate') }),
									el('th', { class: 'num', text: t('Numerator / Denominator') }),
									el('th', { class: 'num', text: t('Raw weighted points') }),
									el('th', { class: 'num', text: t('Normalized contribution (0-100)') }),
									el('th', { text: t('Data confidence') }),
								]),
							]),
							el(
								'tbody',
								{},
								data.components.map(function (comp) {
									const norm = api.normalizeMetric(comp.metric_key || comp.key, comp, data.meta)
									const disp = w.formatMetricDisplay(norm)
									const hasRaw =
										!norm.is_na &&
										comp.raw_weighted_points !== undefined &&
										comp.raw_weighted_points !== null
									const hasNorm =
										!norm.is_na &&
										comp.normalized_contribution !== undefined &&
										comp.normalized_contribution !== null
									return el(
										'tr',
										{
											'data-component-key': comp.component_code || comp.key || '',
											'data-metric-key': norm.key,
											'data-weight': comp.weight !== undefined && comp.weight !== null ? String(comp.weight) : '',
											'data-raw-points': hasRaw ? String(comp.raw_weighted_points) : '',
											'data-normalized-points': hasNorm ? String(comp.normalized_contribution) : '',
										},
										[
											el('td', { text: comp.component_label ? t(comp.component_label) : t(norm.label) }),
											el('td', { class: 'num', text: w.formatDecimal(comp.weight, 0) }),
											el('td', { class: 'num', text: disp.main }),
											el('td', { class: 'num', text: disp.ratio || '—' }),
											el('td', {
												class: 'num',
												text: hasRaw
													? w.formatDecimal(comp.raw_weighted_points, 2) +
														' / ' +
														w.formatDecimal(comp.weight, 0)
													: t('N/A'),
											}),
											el('td', {
												class: 'num',
												text: hasNorm
													? w.formatDecimal(comp.normalized_contribution, 2)
													: t('N/A'),
											}),
											el('td', {}, [
												el('span', {
													class: cskh.stateBadge(norm.status),
													text: w.confidenceLabel(norm.status),
												}),
											]),
										]
									)
								})
							),
							el('tfoot', { id: 'p3-scorecard-totals' }, [
								el('tr', {}, [
									el('td', {}, [el('strong', { text: t('Valid weight sum') })]),
									el('td', { class: 'num' }, [el('strong', { text: validWeightText })]),
									el('td', { class: 'num', text: '—' }),
									el('td', { class: 'num', text: '—' }),
									el('td', { class: 'num', 'data-total-raw-points': data.raw_points_total != null ? String(data.raw_points_total) : '' }, [
										el('strong', { text: rawSumText }),
									]),
									el('td', { class: 'num', 'data-total-score': data.score && data.score.value != null ? String(data.score.value) : '' }, [
										el('strong', { text: totalScoreDisp.main }),
									]),
									el('td', {}, [
										data.score
											? el('span', {
													class: cskh.stateBadge(data.score.status),
													text: w.confidenceLabel(data.score.status),
												})
											: el('span', { text: '—' }),
									]),
								]),
							]),
						]),
					])
				)
			}
			mainHost.appendChild(scoreCard)
		}

		// 3-pillar personal metrics
		if (data.metrics && data.metrics.length) {
			w.renderThreePillars(mainHost, data.metrics, {
				onDrilldown: function (metric) {
					w.openDrilldownModal(metric, getSelfFilters())
				},
			})
		}

		// Actionable SLA obligations in own scope
		const actionCard = el('div', { class: 'cskh-card' }, [
			el('h2', { text: t('Actionable SLA obligations & callbacks') }),
		])
		if (!data.actionable_obligations || !data.actionable_obligations.length) {
			actionCard.appendChild(
				el('p', { class: 'cskh-muted', text: t('No SLA obligations match the current filter.') })
			)
		} else {
			actionCard.appendChild(
				el('div', { class: 'cskh-table-scroll' }, [
					el('table', { class: 'cskh-table', id: 'p3-scorecard-obligations' }, [
						el('thead', {}, [
							el('tr', {}, [
								el('th', { text: t('Work item') }),
								el('th', { text: t('Customer ref') }),
								el('th', { text: t('Obligation type') }),
								el('th', { text: t('Effective due at') }),
								el('th', { text: t('SLA status') }),
							]),
						]),
						el(
							'tbody',
							{},
							data.actionable_obligations.map(function (row) {
								const wi = row.work_item || row.work_item_id || '—'
								const canOpen = wi !== '—' && !row.pii_redacted
								return el('tr', {}, [
									el('td', {}, [
										canOpen
											? el('a', {
													href: '/cskh/work-item?id=' + encodeURIComponent(wi),
													class: 'cskh-mono',
													text: wi,
												})
											: el('span', { class: 'cskh-mono', text: wi }),
									]),
									el('td', {
										text: row.pii_redacted
											? t('PII redacted after handover or by role policy')
											: row.customer_display || row.customer_ref || '—',
									}),
									el('td', { text: w.obligationTypeLabel(row.obligation_type) }),
									el('td', { text: row.effective_due_at ? cskh.formatDate(row.effective_due_at) : '—' }),
									el('td', {}, [
										el('span', {
											class: cskh.stateBadge(row.sla_status || 'pending'),
											text: w.slaStatusLabel(row.sla_status || 'pending'),
										}),
									]),
								])
							})
						),
					]),
				])
			)
		}
		mainHost.appendChild(actionCard)
	}

	async function loadPage() {
		const token = seq.next()
		cskh.clear(alertHost)
		cskh.clear(mainHost)
		mainHost.appendChild(el('div', { class: 'cskh-loading', text: t('Loading...') }))
		try {
			const data = await api.getPersonalScorecard(filterBar.getState())
			if (token !== seq.current()) return
			w.renderBanners(bannersHost, data.meta)
			w.renderWatermarkBar(watermarkHost, data.meta, filterBar.getState())
			renderScorecard(data)
		} catch (err) {
			if (token !== seq.current()) return
			cskh.clear(mainHost)
			cskh.renderError(alertHost, err)
		}
	}

	loadPage()
})()
