/* P3 Screen 1 — Executive Quality Overview (`/cskh/quality-overview`, BOD & CRM Admin). */

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
		showLeadSource: true,
		showTeam: true,
		showEmployee: false,
		onChange: function () {
			loadPage()
		},
	})

	w.bindHeaderActions(
		'quality_overview',
		function () {
			return filterBar.getState()
		},
		function () {
			loadPage()
		}
	)

	async function initFilters() {
		try {
			const lists = await api.getFilterOptions()
			filterBar.setFilterLists(lists)
		} catch (_err) {
			/* filter options are progressive enhancement */
		}
	}

	function renderSummarySection(data) {
		cskh.clear(mainHost)

		w.renderThreePillars(mainHost, data.metrics, {
			onDrilldown: function (metric) {
				w.openDrilldownModal(metric, filterBar.getState())
			},
		})

		const cols = el('div', { class: 'cskh-two-col' })

		// Trend chart card
		const trendCard = el('div', { class: 'cskh-card', id: 'p3-overview-trend-card' }, [
			el('h2', { text: t('Trend over period') }),
			w.renderBarChart(
				(data.trends || []).map(function (pt) {
					const rawVal =
						pt.first_touch_on_time_rate !== undefined
							? pt.first_touch_on_time_rate
							: pt.first_attempt_sla_rate !== undefined
								? pt.first_attempt_sla_rate
								: pt.FIRST_ATTEMPT_SLA_RATE !== undefined
									? pt.FIRST_ATTEMPT_SLA_RATE
									: pt.value
					return {
						label: pt.period_label || pt.bucket || pt.date || '—',
						value: rawVal,
						is_na: Boolean(pt.is_na || rawVal === null || rawVal === undefined),
						color: '#2563eb',
					}
				}),
				{ suffix: '%', maxValue: 100 }
			),
		])
		cols.appendChild(trendCard)

		// Alerts / Anomalies card
		const alertsCard = el('div', { class: 'cskh-card', id: 'p3-overview-alerts-card' }, [
			el('h2', { text: t('SLA & data quality alerts') }),
		])
		if (!data.alerts || !data.alerts.length) {
			alertsCard.appendChild(
				el('p', { class: 'cskh-muted', text: t('No active alerts or anomalies in this period.') })
			)
		} else {
			data.alerts.forEach(function (al) {
				alertsCard.appendChild(
					el(
						'div',
						{
							class: 'cskh-alert is-' + (al.severity === 'error' ? 'error' : 'warning'),
						},
						[el('span', { text: al.message ? t(al.message) : al.code || '—' })]
					)
				)
			})
		}
		cols.appendChild(alertsCard)
		mainHost.appendChild(cols)

		// Team summary table
		const teamCard = el('div', { class: 'cskh-card', id: 'p3-overview-teams-card' }, [
			el('div', { class: 'cskh-pillar-head' }, [
				el('h2', { text: t('Team comparison summary') }),
				el('a', { class: 'cskh-btn is-small', href: '/cskh/team-quality', text: t('Team comparison') }),
			]),
		])
		if (!data.teams_summary || !data.teams_summary.length) {
			teamCard.appendChild(
				el('p', { class: 'cskh-muted', text: t('No teams match the current filters.') })
			)
		} else {
			teamCard.appendChild(
				el('div', { class: 'cskh-table-scroll' }, [
					el('table', { class: 'cskh-table' }, [
						el('thead', {}, [
							el('tr', {}, [
								el('th', { text: t('Team') }),
								el('th', { class: 'num', text: t('Assigned intervals') }),
								el('th', { class: 'num', text: t('First-touch on-time rate') }),
								el('th', { class: 'num', text: t('Follow-up on-time rate') }),
								el('th', { class: 'num', text: t('Connected rate') }),
								el('th', { class: 'num', text: t('Overdue work item backlog') }),
								el('th', { class: 'num', text: t('Process compliance score') }),
							]),
						]),
						el(
							'tbody',
							{},
							data.teams_summary.map(function (row) {
								const rawTeam = row.team || row.team_id || ''
								const teamName =
									rawTeam === 'historical_attribution_unknown' ||
									rawTeam === '__unattributed_historical_team__'
										? t('Historical attribution unknown')
										: row.team_label ||
											row.team_name ||
											api.resolveTeamLabel(rawTeam) ||
											rawTeam ||
											'—'
								return el('tr', { 'data-team': rawTeam }, [
									el('td', { text: teamName }),
									el('td', { class: 'num', text: w.formatInlineRateCell(row.assigned_intervals, 'count') }),
									el('td', {
										class: 'num',
										text: w.formatInlineRateCell(row.first_touch_on_time_rate, 'percent'),
									}),
									el('td', {
										class: 'num',
										text: w.formatInlineRateCell(row.followup_on_time_rate, 'percent'),
									}),
									el('td', {
										class: 'num',
										text: w.formatInlineRateCell(row.connected_rate, 'percent'),
									}),
									el('td', {
										class: 'num',
										text: w.formatInlineRateCell(row.overdue_work_item_backlog, 'count'),
									}),
									el('td', {
										class: 'num',
										text: w.formatInlineRateCell(row.process_compliance_score, 'score'),
									}),
								])
							})
						),
					]),
				])
			)
		}
		mainHost.appendChild(teamCard)
	}

	async function loadPage() {
		const token = seq.next()
		cskh.clear(alertHost)
		cskh.clear(mainHost)
		mainHost.appendChild(el('div', { class: 'cskh-loading', text: t('Loading...') }))
		try {
			const data = await api.getExecutiveOverview(filterBar.getState())
			if (token !== seq.current()) return
			w.renderBanners(bannersHost, data.meta)
			w.renderWatermarkBar(watermarkHost, data.meta, filterBar.getState())
			renderSummarySection(data)
		} catch (err) {
			if (token !== seq.current()) return
			cskh.clear(mainHost)
			cskh.renderError(alertHost, err)
		}
	}

	initFilters()
	loadPage()
})()
