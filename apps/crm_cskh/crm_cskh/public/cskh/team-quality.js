/* P3 Screen 2 — Team Quality Comparison (`/cskh/team-quality`). */

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
		showTeam: true,
		onChange: function () {
			loadPage()
		},
	})

	w.bindHeaderActions(
		'team_quality',
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
			filterBar.setFilterLists(lists)
		} catch (_err) {
			/* ignore */
		}
	})()

	function extractNumeric(cell) {
		if (cell === null || cell === undefined) return null
		if (typeof cell === 'object') {
			if (cell.is_na || cell.denominator === 0 || cell.value === null) return null
			return cell.value
		}
		return cell === 'N/A' ? null : cell
	}

	function renderTeams(data) {
		cskh.clear(mainHost)

		if (data.summaryMetrics && data.summaryMetrics.length) {
			w.renderThreePillars(mainHost, data.summaryMetrics, {
				onDrilldown: function (metric) {
					w.openDrilldownModal(metric, filterBar.getState())
				},
			})
		}

		if (!data.rows || !data.rows.length) {
			mainHost.appendChild(
				el('div', { class: 'cskh-alert is-empty', text: t('No teams match the current filters.') })
			)
			return
		}

		// Visual bar chart for First-Touch On-Time Rate by Team
		mainHost.appendChild(
			el('div', { class: 'cskh-card' }, [
				el('h2', { text: t('First-touch on-time rate') + ' — ' + t('Team') }),
				w.renderBarChart(
					data.rows.map(function (r) {
						const v = extractNumeric(r.first_touch_on_time_rate)
						return {
							label:
								r.team === 'historical_attribution_unknown'
									? t('Historical attribution unknown')
									: r.team_label || r.team || '—',
							value: v,
							is_na: v === null,
							color: '#0d9488',
						}
					}),
					{ suffix: '%', maxValue: 100 }
				),
			])
		)

		const tbody = el(
			'tbody',
			{},
			data.rows.map(function (row) {
				const rawTeam = row.team || row.team_id || ''
				const isUnknown = rawTeam === 'historical_attribution_unknown'
				const teamName = isUnknown
					? t('Historical attribution unknown')
					: row.team_label || rawTeam || '—'
				const conf = row.score_status || row.confidence_status || 'provisional'

				return el('tr', { 'data-team': rawTeam }, [
					el('td', {}, [el('strong', { text: teamName })]),
					el('td', {
						class: 'num',
						text: w.formatInlineRateCell(row.eligible_staff_count, 'count'),
					}),
					el('td', {
						class: 'num',
						text: w.formatInlineRateCell(row.assigned_intervals, 'count'),
					}),
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
						text: w.formatInlineRateCell(row.outcome_completeness_rate, 'percent'),
					}),
					el('td', {
						class: 'num',
						text: w.formatInlineRateCell(row.overdue_work_item_backlog, 'count'),
					}),
					el('td', {
						class: 'num',
						text: w.formatInlineRateCell(row.process_compliance_score, 'score'),
					}),
					el('td', {}, [
						el('span', {
							class: cskh.stateBadge(conf),
							text: w.confidenceLabel(conf),
						}),
					]),
					el('td', {}, [
						!isUnknown && rawTeam
							? el('a', {
									class: 'cskh-btn is-small',
									href: '/cskh/employee-quality?team=' + encodeURIComponent(rawTeam),
									text: t('Open team employees'),
								})
							: el('span', { class: 'cskh-muted', text: '—' }),
					]),
				])
			})
		)

		mainHost.appendChild(
			el('div', { class: 'cskh-card' }, [
				el('h2', { text: t('Team quality comparison') }),
				el('div', { class: 'cskh-table-scroll' }, [
					el('table', { class: 'cskh-table', id: 'p3-team-table' }, [
						el('thead', {}, [
							el('tr', {}, [
								el('th', { text: t('Team at event') }),
								el('th', { class: 'num', text: t('Eligible staff with due obligations') }),
								el('th', { class: 'num', text: t('Assigned intervals') }),
								el('th', { class: 'num', text: t('First-touch on-time rate') }),
								el('th', { class: 'num', text: t('Follow-up on-time rate') }),
								el('th', { class: 'num', text: t('Connected rate') }),
								el('th', { class: 'num', text: t('Outcome completeness rate') }),
								el('th', { class: 'num', text: t('Overdue work item backlog') }),
								el('th', { class: 'num', text: t('Process compliance score') }),
								el('th', { text: t('Data confidence') }),
								el('th', { text: t('Actions') }),
							]),
						]),
						tbody,
					]),
				]),
			])
		)
	}

	async function loadPage() {
		const token = seq.next()
		cskh.clear(alertHost)
		cskh.clear(mainHost)
		mainHost.appendChild(el('div', { class: 'cskh-loading', text: t('Loading...') }))
		try {
			const data = await api.getTeamComparison(filterBar.getState())
			if (token !== seq.current()) return
			w.renderBanners(bannersHost, data.meta)
			w.renderWatermarkBar(watermarkHost, data.meta, filterBar.getState())
			renderTeams(data)
		} catch (err) {
			if (token !== seq.current()) return
			cskh.clear(mainHost)
			cskh.renderError(alertHost, err)
		}
	}

	loadPage()
})()
