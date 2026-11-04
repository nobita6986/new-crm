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

	w.bindHeaderActions(
		'personal_scorecard',
		function () {
			return filterBar.getState()
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
							w.openDrilldownModal(m, filterBar.getState())
						},
					})
				)
			}
			if (data.components && data.components.length) {
				scoreCard.appendChild(
					el('div', { class: 'cskh-table-scroll', style: 'margin-top: 12px;' }, [
						el('table', { class: 'cskh-table', id: 'p3-scorecard-components' }, [
							el('thead', {}, [
								el('tr', {}, [
									el('th', { text: t('Component') }),
									el('th', { class: 'num', text: t('Weight') }),
									el('th', { class: 'num', text: t('My rate') }),
									el('th', { class: 'num', text: t('Numerator / Denominator') }),
									el('th', { class: 'num', text: t('Weighted contribution') }),
									el('th', { text: t('Data confidence') }),
								]),
							]),
							el(
								'tbody',
								{},
								data.components.map(function (comp) {
									const norm = api.normalizeMetric(comp.metric_key || comp.key, comp, data.meta)
									const disp = w.formatMetricDisplay(norm)
									return el('tr', {}, [
										el('td', { text: t(norm.label) }),
										el('td', { class: 'num', text: w.formatDecimal(comp.weight, 0) }),
										el('td', { class: 'num', text: disp.main }),
										el('td', { class: 'num', text: disp.ratio || '—' }),
										el('td', {
											class: 'num',
											text:
												comp.weighted_contribution !== undefined &&
												comp.weighted_contribution !== null
													? w.formatDecimal(comp.weighted_contribution, 2)
													: t('N/A'),
										}),
										el('td', {}, [
											el('span', {
												class: cskh.stateBadge(norm.status),
												text: w.confidenceLabel(norm.status),
											}),
										]),
									])
								})
							),
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
					w.openDrilldownModal(metric, filterBar.getState())
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
													href: '/cskh/work-item?name=' + encodeURIComponent(wi),
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
