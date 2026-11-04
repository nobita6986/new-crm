/* P3 Screen 3 — Employee Quality & Compliance (`/cskh/employee-quality`).
   Strictly separates `finalized`, `provisional`, and `insufficient_data` score groups
   so provisional scores are NEVER ranked together with finalized scores (P3.md §5.3 & §10.3).
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

	const urlParams = new URLSearchParams(window.location.search || '')
	const initialTeam = urlParams.get('team') || ''

	const seq = cskh.sequencer()

	const filterBar = w.mountFilterBar(filterHost, {
		initialState: { team: initialTeam },
		showCampaign: true,
		showTeam: true,
		showEmployee: true,
		showScoreGroup: true,
		showInactiveToggle: true,
		onChange: function () {
			loadPage()
		},
	})

	w.bindHeaderActions(
		'employee_quality',
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

	function renderScoreGroupCard(groupKey, titleText, clsName, rows, showRank) {
		if (!rows.length) return null
		const tbody = el(
			'tbody',
			{},
			rows.map(function (row) {
				const empUser = row.employee || row.user || ''
				const empLabel = row.employee_name || row.full_name || empUser || '—'
				const scoreObj =
					row.process_compliance_score && typeof row.process_compliance_score === 'object'
						? row.process_compliance_score
						: {
								value: row.process_compliance_score,
								status: groupKey,
								is_na: groupKey === 'insufficient_data',
							}
				const normScore = api.normalizeMetric('process_compliance_score', scoreObj, {})
				const scoreDisp = w.formatMetricDisplay(normScore)

				return el('tr', { 'data-employee': empUser, 'data-score-group': groupKey }, [
					el('td', {
						class: 'num',
						text:
							showRank && row.rank_in_group !== undefined && row.rank_in_group !== null
								? '#' + row.rank_in_group
								: t('Unranked'),
					}),
					el('td', {}, [
						el('strong', { text: empLabel }),
						empLabel !== empUser
							? el('div', { class: 'cskh-muted cskh-mono', text: empUser })
							: null,
					]),
					el('td', {
						text:
							row.team === 'historical_attribution_unknown'
								? t('Historical attribution unknown')
								: row.team_label || row.team || '—',
					}),
					el('td', { class: 'num' }, [
						el('strong', { text: scoreDisp.main }),
						scoreDisp.ratio
							? el('div', { class: 'cskh-muted', style: 'font-size: 11px;', text: scoreDisp.ratio })
							: null,
					]),
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
						text: w.formatInlineRateCell(row.callback_completion_rate, 'percent'),
					}),
					el('td', {
						class: 'num',
						text: w.formatInlineRateCell(row.assigned_intervals, 'count'),
					}),
					el('td', {
						class: 'num',
						text: w.formatInlineRateCell(row.effective_attempts, 'count'),
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
						text: w.formatInlineRateCell(row.dnc_compliance_rate, 'percent'),
					}),
					el('td', {}, [
						el('button', {
							type: 'button',
							class: 'cskh-btn is-small',
							text: t('Drill down'),
							onclick: function () {
								const m = api.normalizeMetric(
									'FIRST_ATTEMPT_SLA_RATE',
									row.first_touch_on_time_rate || {},
									{}
								)
								w.openDrilldownModal(
									m,
									Object.assign({}, filterBar.getState(), { employee: empUser })
								)
							},
						}),
					]),
				])
			})
		)

		return el('section', { class: 'cskh-score-group ' + clsName, 'data-score-section': groupKey }, [
			el('div', { class: 'cskh-score-group-head' }, [
				el('h3', { text: titleText }),
				el('span', {
					class: cskh.stateBadge(groupKey),
					text: cskh.formatNumber(rows.length),
				}),
			]),
			el('div', { class: 'cskh-table-scroll' }, [
				el('table', { class: 'cskh-table' }, [
					el('thead', {}, [
						el('tr', {}, [
							el('th', { class: 'num', text: t('Rank (within group)') }),
							el('th', { text: t('Employee') }),
							el('th', { text: t('Team at event') }),
							el('th', { class: 'num', text: t('Process compliance score') }),
							el('th', { class: 'num', text: t('First-touch on-time rate') }),
							el('th', { class: 'num', text: t('Follow-up on-time rate') }),
							el('th', { class: 'num', text: t('Callback completion rate') }),
							el('th', { class: 'num', text: t('Assigned intervals') }),
							el('th', { class: 'num', text: t('Effective attempts (activity reference)') }),
							el('th', { class: 'num', text: t('Connected rate') }),
							el('th', { class: 'num', text: t('Outcome completeness rate') }),
							el('th', { class: 'num', text: t('DNC & closure compliance rate') }),
							el('th', { text: t('Actions') }),
						]),
					]),
					tbody,
				]),
			]),
		])
	}

	function renderEmployees(data) {
		cskh.clear(mainHost)
		if (!data.rows || !data.rows.length) {
			mainHost.appendChild(
				el('div', {
					class: 'cskh-alert is-empty',
					text: t('No employees match the current filters.'),
				})
			)
			return
		}

		const partitioned = w.partitionScoreGroups(data.rows)
		const sections = [
			renderScoreGroupCard(
				'finalized',
				t('Finalized score group (ranked within finalized only)'),
				'is-finalized',
				partitioned.finalized,
				true
			),
			renderScoreGroupCard(
				'provisional',
				t('Provisional score group (not ranked with finalized)'),
				'is-provisional',
				partitioned.provisional,
				false
			),
			renderScoreGroupCard(
				'insufficient_data',
				t('Insufficient data group (N/A — unranked)'),
				'is-na',
				partitioned.insufficient_data,
				false
			),
		]
		sections.forEach(function (sec) {
			if (sec) mainHost.appendChild(sec)
		})
	}

	async function loadPage() {
		const token = seq.next()
		cskh.clear(alertHost)
		cskh.clear(mainHost)
		mainHost.appendChild(el('div', { class: 'cskh-loading', text: t('Loading...') }))
		try {
			const data = await api.getEmployeeQuality(filterBar.getState())
			if (token !== seq.current()) return
			w.renderBanners(bannersHost, data.meta)
			w.renderWatermarkBar(watermarkHost, data.meta, filterBar.getState())
			renderEmployees(data)
		} catch (err) {
			if (token !== seq.current()) return
			cskh.clear(mainHost)
			cskh.renderError(alertHost, err)
		}
	}

	loadPage()
})()
