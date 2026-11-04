/* Shared P3 UI Widgets (`window.cskhP3Widgets`).

   Zero `innerHTML`, zero client-side KPI math, 100% Vietnamese i18n via `cskh.t`,
   strict separation of the 3 P3 pillars and strict non-mixing of `provisional`
   vs `finalized` vs `insufficient_data` score groups.
*/

window.cskhP3Widgets = (function () {
	'use strict'

	const cskh = window.cskh
	const t = cskh.t
	const el = cskh.el
	const SVG_NS = 'http://www.w3.org/2000/svg'

	const sessionExportJobs = []

	function svgEl(tag, attrs, children) {
		const node = document.createElementNS(SVG_NS, tag)
		Object.keys(attrs || {}).forEach(function (k) {
			if (attrs[k] !== undefined && attrs[k] !== null) {
				node.setAttribute(k, String(attrs[k]))
			}
		})
		;(children || []).forEach(function (ch) {
			if (!ch) return
			node.appendChild(typeof ch === 'string' ? document.createTextNode(ch) : ch)
		})
		return node
	}

	function formatDecimal(val, maxFrac) {
		if (val === undefined || val === null || val === '' || isNaN(Number(val))) return '—'
		return new Intl.NumberFormat('vi-VN', {
			minimumFractionDigits: 0,
			maximumFractionDigits: maxFrac === undefined ? 2 : maxFrac,
		}).format(Number(val))
	}

	function confidenceLabel(status) {
		const s = String(status || 'fresh').toLowerCase()
		const map = {
			fresh: t('Fresh'),
			calculating: t('Calculating'),
			stale: t('Stale — recompute needed'),
			provisional: t('Provisional'),
			finalized: t('Finalized'),
			restated: t('Restated (prior period adjusted)'),
			insufficient_data: t('Insufficient data (N/A)'),
			na: t('N/A'),
			failed: t('Calculation / reconciliation failed'),
		}
		return map[s] || status || '—'
	}

	function grainLabel(grain) {
		const g = String(grain || '').toLowerCase()
		const map = {
			distinct_work_item: t('Distinct work item'),
			work_item: t('Distinct work item'),
			ownership_interval: t('Ownership interval'),
			sla_obligation: t('Due SLA obligation'),
			effective_attempt: t('Effective contact attempt'),
			qa_review: t('Eligible QA review'),
		}
		return map[g] || grain || '—'
	}

	function unitLabel(unit) {
		const u = String(unit || '').toLowerCase()
		const map = {
			percent: t('Percentage (%)'),
			score: t('Process score (0-100)'),
			count: t('Count'),
			minutes_business: t('Minutes (business hours)'),
			minutes_wall: t('Minutes (wall clock)'),
		}
		return map[u] || unit || '—'
	}

	function timeBasisLabel(basis) {
		const b = String(basis || '').toLowerCase()
		const map = {
			deadline_cohort: t('Deadline cohort'),
			event_time: t('Event time'),
			as_of_point_in_time: t('Point-in-time at as-of'),
		}
		return map[b] || basis || '—'
	}

	function obligationTypeLabel(obType) {
		const k = String(obType || '').toUpperCase()
		const map = {
			FIRST_ATTEMPT: t('First-touch SLA'),
			FOLLOWUP: t('Follow-up callback SLA'),
			MIN_ATTEMPTS: t('Minimum attempts SLA'),
			FIRST_CONNECTED: t('First-connected tracking'),
		}
		return map[k] || obType || '—'
	}

	function slaStatusLabel(status) {
		const s = String(status || '').toLowerCase()
		const map = {
			pending: t('Pending (not yet due)'),
			fulfilled_on_time: t('Fulfilled on time'),
			fulfilled_late: t('Fulfilled late'),
			breached_open: t('Breached (open)'),
			waived: t('Waived (approved)'),
			canceled_valid: t('Canceled validly'),
			superseded: t('Superseded (rescheduled)'),
			escalated: t('Escalated'),
			needs_review: t('Needs review'),
		}
		return map[s] || status || '—'
	}

	function exportStatusLabel(status) {
		const s = String(status || '').toLowerCase()
		const map = {
			running: t('Running'),
			queued: t('Running'),
			ready: t('Ready to download'),
			completed: t('Ready to download'),
			expired: t('Expired'),
			forbidden: t('Access denied'),
			failed: t('Failed'),
		}
		return map[s] || status || '—'
	}

	/**
	 * Format a normalized KPI metric for display WITHOUT computing KPI formulas.
	 * `denominator === 0` or `is_na === true` ALWAYS renders `N/A`, NEVER `0%`.
	 */
	function formatMetricDisplay(metric) {
		if (!metric) {
			return { main: t('N/A'), ratio: '', sub: '' }
		}
		const hasNumDen =
			metric.numerator !== null &&
			metric.numerator !== undefined &&
			metric.denominator !== null &&
			metric.denominator !== undefined

		const ratioText = hasNumDen
			? cskh.formatNumber(metric.numerator) + ' / ' + cskh.formatNumber(metric.denominator)
			: ''

		if (metric.is_na) {
			let naSub = t('N/A — Insufficient sample')
			if (metric.na_reason === 'qa_disabled') {
				naSub = t('N/A — QA disabled')
			} else if (metric.denominator === 0 || metric.na_reason === 'zero_denominator') {
				naSub = t('N/A — No eligible denominator (0/0)')
			}
			return {
				main: t('N/A'),
				ratio: ratioText,
				sub: naSub,
			}
		}

		if (metric.unit === 'percent') {
			return {
				main: formatDecimal(metric.value, 2) + '%',
				ratio: ratioText,
				sub: '',
			}
		}

		if (metric.unit === 'score') {
			const scoreStr = formatDecimal(metric.value, 2)
			const isProv = metric.status === 'provisional'
			return {
				main: isProv ? scoreStr + ' — ' + t('Provisional; missing QA sample') : scoreStr,
				ratio:
					metric.valid_weight_sum !== null && metric.valid_weight_sum !== undefined
						? t('Valid weight sum') + ': ' + formatDecimal(metric.valid_weight_sum, 0)
						: ratioText,
				sub: '',
			}
		}

		if (metric.unit === 'minutes_business' || metric.unit === 'minutes_wall') {
			if (metric.p50 !== null && metric.p50 !== undefined) {
				return {
					main:
						'p50: ' +
						formatDecimal(metric.p50, 1) +
						' · p90: ' +
						formatDecimal(metric.p90, 1),
					ratio: ratioText,
					sub: unitLabel(metric.unit),
				}
			}
			return {
				main: formatDecimal(metric.value, 1),
				ratio: ratioText,
				sub: unitLabel(metric.unit),
			}
		}

		return {
			main: cskh.formatNumber(metric.value),
			ratio: ratioText,
			sub: '',
		}
	}

	/**
	 * Format an inline cell rate/score object `{ value, numerator, denominator, status, is_na }`
	 * in comparison tables without computing math on the client.
	 */
	function formatInlineRateCell(cell, unit) {
		if (cell === null || cell === undefined) return t('N/A')
		if (typeof cell === 'object') {
			const norm = window.cskhP3Api.normalizeMetric('', Object.assign({ unit: unit || 'percent' }, cell), {})
			const d = formatMetricDisplay(norm)
			return d.ratio ? d.main + ' (' + d.ratio + ')' : d.main
		}
		if (cell === 'N/A') return t('N/A')
		if (unit === 'percent') return formatDecimal(cell, 2) + '%'
		if (unit === 'score') return formatDecimal(cell, 2)
		return cskh.formatNumber(cell)
	}

	function renderBanners(container, meta) {
		if (!container) return
		cskh.clear(container)
		const m = meta || {}

		if (!m.qa_enabled) {
			container.appendChild(
				el('div', { class: 'cskh-alert is-warning', 'data-banner': 'qa-disabled' }, [
					el('span', {
						text: t(
							'QA review module is currently disabled by feature flag (qa_enabled = false). No synthetic QA reviews or verified flags are generated; QA pass rate is N/A and Process Score is computed as Provisional over valid component weights.'
						),
					}),
				])
			)
		}

		if (m.is_dev_default_policy !== false) {
			container.appendChild(
				el('div', { class: 'cskh-alert is-info', 'data-banner': 'dev-policy' }, [
					el('span', {
						text: t(
							'SLA thresholds and scoring weights shown here are configurable DEV defaults and must not be treated as BOD-approved production policy.'
						),
					}),
				])
			)
		}

		if (m.freshness_status === 'stale') {
			container.appendChild(
				el('div', { class: 'cskh-alert is-warning', 'data-banner': 'snapshot-stale' }, [
					el('span', {
						text: t(
							'The KPI snapshot is older than recent source changes; refresh or request a recompute.'
						),
					}),
				])
			)
		}

		if (m.has_unknown_historical_attribution) {
			container.appendChild(
				el('div', { class: 'cskh-alert is-warning', 'data-banner': 'unknown-attribution' }, [
					el('span', {
						text: t(
							'Historical attribution warning: Some legacy records lack verifiable team/owner-at-event evidence and are isolated as historical_attribution_unknown rather than guessed from current lead_owner.'
						),
					}),
				])
			)
		}

		if (m.has_multiple_policies) {
			container.appendChild(
				el('div', { class: 'cskh-alert is-info', 'data-banner': 'multi-policy' }, [
					el('span', {
						text: t(
							'Policy comparison notice: Rows using different policy versions are annotated; do not compare raw scores across distinct policies without reviewing policy parameters.'
						),
					}),
				])
			)
		}
	}

	function renderWatermarkBar(container, meta, filterState) {
		if (!container) return
		cskh.clear(container)
		const m = meta || {}
		const bounds = cskh.computePeriodBounds(
			(filterState && filterState.period_type) || m.period_type || 'month',
			(filterState && filterState.anchor) || m.from_date || '',
			(filterState && filterState.custom_to) || '',
			cskh.siteTimeZone()
		)

		const bar = el('div', { class: 'cskh-watermark-bar', 'data-freshness': m.freshness_status || 'fresh' }, [
			el('div', { class: 'cskh-watermark-meta' }, [
				el('span', {
					class: cskh.stateBadge(m.freshness_status || 'fresh'),
					text: confidenceLabel(m.freshness_status || 'fresh'),
				}),
				el('span', {}, [
					el('strong', { text: t('Reporting period') + ': ' }),
					el('code', { class: 'cskh-mono', text: bounds.label }),
				]),
				el('span', {}, [
					el('strong', { text: t('Scope') + ': ' }),
					el('span', { text: cskh.scopeLabel(m.scope || 'system') }),
				]),
			]),
			el('div', { class: 'cskh-watermark-meta cskh-muted' }, [
				el('span', {
					text: t('Snapshot as-of') + ': ' + (m.as_of ? cskh.formatDate(m.as_of) : '—'),
				}),
				el('span', {
					text:
						t('Calculated at') +
						': ' +
						(m.calculated_at ? cskh.formatDate(m.calculated_at) : '—'),
				}),
				el('span', {
					text:
						t('Source watermark') +
						': ' +
						(m.source_watermark ? cskh.formatDate(m.source_watermark) : '—'),
				}),
				el('span', {
					text:
						t('Formula & policy version') +
						': ' +
						(m.metric_version || 'p3.v1') +
						' / ' +
						(m.policy_version || 'DEV-DEFAULT'),
				}),
			]),
		])
		container.appendChild(bar)
	}

	function defaultAnchorForPeriod(periodType) {
		const siteNow = cskh.partsAtEpochInTz(cskh.serverNow().getTime(), cskh.siteTimeZone())
		const pad = function (n) {
			return String(n).padStart(2, '0')
		}
		if (periodType === 'day' || periodType === 'custom' || periodType === 'week') {
			return siteNow.dateKey
		}
		if (periodType === 'quarter') {
			const q = Math.floor((siteNow.month - 1) / 3) + 1
			return siteNow.year + '-Q' + q
		}
		if (periodType === 'year') {
			return String(siteNow.year)
		}
		return siteNow.year + '-' + pad(siteNow.month)
	}

	function mountFilterBar(container, options) {
		if (!container) return null
		const opts = options || {}
		const state = Object.assign(
			{
				period_type: 'month',
				anchor: defaultAnchorForPeriod('month'),
				custom_to: defaultAnchorForPeriod('day'),
				campaign: '',
				lead_source: '',
				team: '',
				employee: '',
				snapshot_revision: '',
				include_inactive: false,
				score_group: '',
				obligation_type: '',
				sla_status: '',
				search: '',
				page: 1,
				page_size: 20,
			},
			opts.initialState || {}
		)

		let filterLists = {
			campaigns: [],
			teams: [],
			employees: [],
			lead_sources: [],
			revisions: [],
		}

		function emitChange() {
			state.page = 1
			renderControls()
			if (typeof opts.onChange === 'function') {
				opts.onChange(Object.assign({}, state))
			}
		}

		function renderAnchorInput() {
			const pType = state.period_type
			if (pType === 'day') {
				return el('label', { class: 'cskh-field' }, [
					t('Select date'),
					el('input', {
						type: 'date',
						class: 'cskh-input',
						id: 'p3-filter-anchor',
						value: state.anchor,
						onchange: function (e) {
							state.anchor = e.target.value
							emitChange()
						},
					}),
				])
			}
			if (pType === 'week') {
				return el('label', { class: 'cskh-field' }, [
					t('Select week'),
					el('input', {
						type: 'date',
						class: 'cskh-input',
						id: 'p3-filter-anchor',
						value: /^\d{4}-\d{2}-\d{2}$/.test(state.anchor)
							? state.anchor
							: defaultAnchorForPeriod('day'),
						onchange: function (e) {
							state.anchor = e.target.value
							emitChange()
						},
					}),
				])
			}
			if (pType === 'quarter') {
				const qMatch = /^(\d{4})-Q([1-4])$/i.exec(state.anchor)
				const curYear = qMatch ? qMatch[1] : defaultAnchorForPeriod('year')
				const curQ = qMatch ? 'Q' + qMatch[2] : 'Q1'
				const qSelect = el(
					'select',
					{
						class: 'cskh-select',
						id: 'p3-filter-quarter',
						onchange: function (e) {
							state.anchor = curYear + '-' + e.target.value
							emitChange()
						},
					},
					[
						el('option', { value: 'Q1', text: t('Quarter 1 (Jan-Mar)') }),
						el('option', { value: 'Q2', text: t('Quarter 2 (Apr-Jun)') }),
						el('option', { value: 'Q3', text: t('Quarter 3 (Jul-Sep)') }),
						el('option', { value: 'Q4', text: t('Quarter 4 (Oct-Dec)') }),
					]
				)
				qSelect.value = curQ
				const yInput = el('input', {
					type: 'number',
					min: '2020',
					max: '2099',
					class: 'cskh-input',
					id: 'p3-filter-year',
					style: 'min-width: 96px; width: 100px;',
					value: curYear,
					onchange: function (e) {
						state.anchor = e.target.value + '-' + curQ
						emitChange()
					},
				})
				return el('div', { class: 'cskh-actions' }, [
					el('label', { class: 'cskh-field' }, [t('Select quarter'), qSelect]),
					el('label', { class: 'cskh-field' }, [t('Select year'), yInput]),
				])
			}
			if (pType === 'year') {
				return el('label', { class: 'cskh-field' }, [
					t('Select year'),
					el('input', {
						type: 'number',
						min: '2020',
						max: '2099',
						class: 'cskh-input',
						id: 'p3-filter-anchor',
						style: 'min-width: 110px;',
						value: /^\d{4}$/.test(state.anchor)
							? state.anchor
							: defaultAnchorForPeriod('year'),
						onchange: function (e) {
							state.anchor = e.target.value
							emitChange()
						},
					}),
				])
			}
			if (pType === 'custom') {
				return el('div', { class: 'cskh-actions' }, [
					el('label', { class: 'cskh-field' }, [
						t('From date'),
						el('input', {
							type: 'date',
							class: 'cskh-input',
							id: 'p3-filter-from',
							value: /^\d{4}-\d{2}-\d{2}$/.test(state.anchor)
								? state.anchor
								: defaultAnchorForPeriod('day'),
							onchange: function (e) {
								state.anchor = e.target.value
								emitChange()
							},
						}),
					]),
					el('label', { class: 'cskh-field' }, [
						t('To date'),
						el('input', {
							type: 'date',
							class: 'cskh-input',
							id: 'p3-filter-to',
							value: state.custom_to || defaultAnchorForPeriod('day'),
							onchange: function (e) {
								state.custom_to = e.target.value
								emitChange()
							},
						}),
					]),
				])
			}
			// Default `month`
			return el('label', { class: 'cskh-field' }, [
				t('Select month'),
				el('input', {
					type: 'month',
					class: 'cskh-input',
					id: 'p3-filter-anchor',
					value: /^\d{4}-\d{2}$/.test(state.anchor)
						? state.anchor
						: defaultAnchorForPeriod('month'),
					onchange: function (e) {
						state.anchor = e.target.value
						emitChange()
					},
				}),
			])
		}

		function buildSelect(id, labelText, currentVal, items, allLabel, onPick) {
			const sel = el(
				'select',
				{
					class: 'cskh-select',
					id: id,
					onchange: function (e) {
						onPick(e.target.value)
					},
				},
				[el('option', { value: '', text: allLabel || t('All') })].concat(
					(items || []).map(function (it) {
						const val = typeof it === 'object' ? it.value || it.name || it.id || '' : String(it)
						const lbl =
							typeof it === 'object'
								? it.label || it.title || it.campaign_name || it.full_name || val
								: String(it)
						return el('option', { value: val, text: lbl })
					})
				)
			)
			sel.value = currentVal || ''
			return el('label', { class: 'cskh-field' }, [labelText, sel])
		}

		function renderControls() {
			cskh.clear(container)
			const periodSel = el(
				'select',
				{
					class: 'cskh-select',
					id: 'p3-filter-period-type',
					onchange: function (e) {
						state.period_type = e.target.value
						state.anchor = defaultAnchorForPeriod(state.period_type)
						emitChange()
					},
				},
				[
					el('option', { value: 'day', text: t('Daily') }),
					el('option', { value: 'week', text: t('Weekly') }),
					el('option', { value: 'month', text: t('Monthly') }),
					el('option', { value: 'quarter', text: t('Quarterly') }),
					el('option', { value: 'year', text: t('Yearly') }),
					el('option', { value: 'custom', text: t('Custom range') }),
				]
			)
			periodSel.value = state.period_type

			const controls = [
				el('label', { class: 'cskh-field' }, [t('Period type'), periodSel]),
				renderAnchorInput(),
			]

			if (opts.showCampaign !== false) {
				controls.push(
					buildSelect(
						'p3-filter-campaign',
						t('Campaign'),
						state.campaign,
						filterLists.campaigns,
						t('All'),
						function (val) {
							state.campaign = val
							emitChange()
						}
					)
				)
			}

			if (opts.showLeadSource) {
				controls.push(
					buildSelect(
						'p3-filter-source',
						t('Lead source'),
						state.lead_source,
						filterLists.lead_sources,
						t('All'),
						function (val) {
							state.lead_source = val
							emitChange()
						}
					)
				)
			}

			if (opts.showTeam) {
				controls.push(
					buildSelect('p3-filter-team', t('Team'), state.team, filterLists.teams, t('All'), function (
						val
					) {
						state.team = val
						emitChange()
					})
				)
			}

			if (opts.showEmployee) {
				controls.push(
					buildSelect(
						'p3-filter-employee',
						t('Employee'),
						state.employee,
						filterLists.employees,
						t('All'),
						function (val) {
							state.employee = val
							emitChange()
						}
					)
				)
			}

			if (opts.showScoreGroup) {
				controls.push(
					buildSelect(
						'p3-filter-score-group',
						t('Score group'),
						state.score_group,
						[
							{ value: 'finalized', label: t('Finalized') },
							{ value: 'provisional', label: t('Provisional') },
							{ value: 'insufficient_data', label: t('Insufficient data (N/A)') },
						],
						t('All score groups'),
						function (val) {
							state.score_group = val
							emitChange()
						}
					)
				)
			}

			if (opts.showInactiveToggle) {
				controls.push(
					el('label', { class: 'cskh-checkbox-row', style: 'margin-top: 18px;' }, [
						el('input', {
							type: 'checkbox',
							id: 'p3-filter-inactive',
							checked: !!state.include_inactive,
							onchange: function (e) {
								state.include_inactive = !!e.target.checked
								emitChange()
							},
						}),
						el('span', { text: t('Include inactive employees') }),
					])
				)
			}

			if (opts.showSlaFilters) {
				controls.push(
					buildSelect(
						'p3-filter-ob-type',
						t('Obligation type'),
						state.obligation_type,
						[
							{ value: 'FIRST_ATTEMPT', label: t('First-touch SLA') },
							{ value: 'FOLLOWUP', label: t('Follow-up callback SLA') },
							{ value: 'MIN_ATTEMPTS', label: t('Minimum attempts SLA') },
							{ value: 'FIRST_CONNECTED', label: t('First-connected tracking') },
						],
						t('All'),
						function (val) {
							state.obligation_type = val
							emitChange()
						}
					)
				)
				controls.push(
					buildSelect(
						'p3-filter-sla-status',
						t('SLA status'),
						state.sla_status,
						[
							{ value: 'breached_open', label: t('Breached (open)') },
							{ value: 'fulfilled_late', label: t('Fulfilled late') },
							{ value: 'fulfilled_on_time', label: t('Fulfilled on time') },
							{ value: 'pending', label: t('Pending (not yet due)') },
							{ value: 'waived', label: t('Waived (approved)') },
							{ value: 'superseded', label: t('Superseded (rescheduled)') },
							{ value: 'canceled_valid', label: t('Canceled validly') },
							{ value: 'escalated', label: t('Escalated') },
						],
						t('All'),
						function (val) {
							state.sla_status = val
							emitChange()
						}
					)
				)
			}

			if (opts.showRevision !== false && filterLists.revisions.length) {
				controls.push(
					buildSelect(
						'p3-filter-revision',
						t('Snapshot revision'),
						state.snapshot_revision,
						filterLists.revisions,
						t('Latest published snapshot'),
						function (val) {
							state.snapshot_revision = val
							emitChange()
						}
					)
				)
			}

			controls.push(
				el(
					'button',
					{
						type: 'button',
						id: 'p3-filter-refresh',
						class: 'cskh-btn',
						style: 'margin-top: 18px;',
						text: t('Refresh'),
						onclick: function () {
							if (typeof opts.onChange === 'function') {
								opts.onChange(Object.assign({}, state))
							}
						},
					},
					[]
				)
			)

			const bounds = cskh.computePeriodBounds(
				state.period_type,
				state.anchor,
				state.custom_to,
				cskh.siteTimeZone()
			)

			container.appendChild(el('div', { class: 'cskh-actions' }, controls))
			container.appendChild(
				el(
					'p',
					{
						class: 'cskh-muted',
						id: 'p3-period-bounds-preview',
						style: 'margin: 8px 0 0; font-size: 12px;',
					},
					[
						el('span', { text: t('Half-open interval [from, to_exclusive)') + ': ' }),
						el('code', { class: 'cskh-mono', text: bounds.label }),
					]
				)
			)
		}

		renderControls()

		return {
			getState: function () {
				return Object.assign({}, state)
			},
			setFilterLists: function (lists) {
				filterLists = Object.assign(filterLists, lists || {})
				renderControls()
			},
			setState: function (patch) {
				Object.assign(state, patch || {})
				renderControls()
			},
		}
	}

	/**
	 * Render an Explainable KPI Card with all 9 required P3 attributes:
	 * 1. Unit & Grain, 2. Numerator/Denominator, 3. Formula, 4. Period & Time Basis,
	 * 5. Scope, 6. Valid Exclusions, 7. as_of / calculated_at / source_watermark,
	 * 8. Metric & Policy Version, 9. Confidence Status (`fresh | stale | provisional | finalized | ...`).
	 */
	function renderMetricCard(metric, handlers) {
		const m = metric
		const disp = formatMetricDisplay(m)
		const provenanceNode = el('div', { class: 'cskh-kpi-provenance', hidden: true }, [
			el('dl', {}, [
				el('dt', { text: t('Metric key') }),
				el('dd', { class: 'cskh-mono', text: m.key }),
				el('dt', { text: t('Metric unit (grain)') }),
				el('dd', { text: unitLabel(m.unit) + ' · ' + grainLabel(m.grain) }),
				el('dt', { text: t('Numerator / Denominator') }),
				el('dd', { text: disp.ratio || '—' }),
				el('dt', { text: t('Formula') }),
				el('dd', { text: m.formula ? t(m.formula) : '—' }),
				el('dt', { text: t('Reporting period') }),
				el('dd', { text: (m.period_label || '—') + ' · ' + timeBasisLabel(m.time_basis) }),
				el('dt', { text: t('Scope') }),
				el('dd', { text: cskh.scopeLabel(m.scope) }),
				el('dt', { text: t('Valid exclusions') }),
				el('dd', {
					text:
						m.exclusions && m.exclusions.length
							? m.exclusions.join(', ') +
								(m.excluded_count !== null ? ' (' + cskh.formatNumber(m.excluded_count) + ')' : '')
							: m.excluded_count !== null
								? cskh.formatNumber(m.excluded_count)
								: '—',
				}),
				el('dt', { text: t('Snapshot as-of') }),
				el('dd', { text: m.as_of ? cskh.formatDate(m.as_of) : '—' }),
				el('dt', { text: t('Calculated at') }),
				el('dd', { text: m.calculated_at ? cskh.formatDate(m.calculated_at) : '—' }),
				el('dt', { text: t('Source watermark') }),
				el('dd', { text: m.source_watermark ? cskh.formatDate(m.source_watermark) : '—' }),
				el('dt', { text: t('Formula & policy version') }),
				el('dd', { text: m.metric_version + ' / ' + m.policy_version }),
				el('dt', { text: t('Data confidence status') }),
				el('dd', { text: confidenceLabel(m.status) }),
			]),
		])

		const toggleBtn = el('button', {
			type: 'button',
			class: 'cskh-btn is-small',
			text: t('Formula & provenance'),
			onclick: function () {
				provenanceNode.hidden = !provenanceNode.hidden
			},
		})

		const footButtons = [toggleBtn]
		if (m.drillable && handlers && typeof handlers.onDrilldown === 'function') {
			footButtons.push(
				el('button', {
					type: 'button',
					class: 'cskh-btn is-small is-primary',
					'data-drilldown-metric': m.key,
					text: t('Drill down'),
					onclick: function () {
						handlers.onDrilldown(m)
					},
				})
			)
		}

		return el(
			'div',
			{
				class: 'cskh-kpi-card' + (m.is_na ? ' is-na' : ''),
				'data-metric-key': m.key,
				'data-metric-status': m.status,
				'data-metric-na': m.is_na ? '1' : '0',
			},
			[
				el('div', {}, [
					el('div', { class: 'cskh-kpi-top' }, [
						el('div', { class: 'cskh-kpi-label', text: t(m.label) }),
						el('span', {
							class: cskh.stateBadge(m.status),
							text: confidenceLabel(m.status),
						}),
					]),
					el('div', { class: 'cskh-kpi-value-row', style: 'margin-top: 6px;' }, [
						el('div', { class: 'cskh-kpi-value', text: disp.main }),
						disp.ratio ? el('div', { class: 'cskh-kpi-ratio', text: '(' + disp.ratio + ')' }) : null,
					]),
					disp.sub ? el('div', { class: 'cskh-kpi-sub', text: disp.sub }) : null,
					el('div', { class: 'cskh-kpi-meta-pills', style: 'margin-top: 8px;' }, [
						el('span', { class: 'cskh-pill-subtle', text: grainLabel(m.grain) }),
						el('span', { class: 'cskh-pill-subtle', text: timeBasisLabel(m.time_basis) }),
						el('span', { class: 'cskh-pill-subtle', text: m.policy_version }),
					]),
					provenanceNode,
				]),
				el('div', { class: 'cskh-kpi-foot' }, footButtons),
			]
		)
	}

	/**
	 * Render KPI cards strictly separated into the 3 P3 pillars:
	 * 1. Process & SLA compliance
	 * 2. Connection & conversion effectiveness
	 * 3. Data reliability & QA
	 */
	function renderThreePillars(container, metricsList, handlers) {
		if (!container) return
		const groups = {
			compliance: [],
			connection: [],
			reliability: [],
		}
		;(metricsList || []).forEach(function (m) {
			const p = m.pillar || 'compliance'
			if (!groups[p]) groups[p] = []
			groups[p].push(m)
		})

		const defs = [
			{
				key: 'compliance',
				cls: 'is-compliance',
				title: t('1. Process & SLA compliance'),
				note: '',
			},
			{
				key: 'connection',
				cls: 'is-connection',
				title: t('2. Connection & conversion effectiveness'),
				note: t(
					'Note: Company totals count distinct Work Items, whereas employee accountability counts Ownership Intervals (e.g., A -> B -> A creates 3 intervals on 1 Work Item).'
				),
			},
			{
				key: 'reliability',
				cls: 'is-reliability',
				title: t('3. Data reliability & QA'),
				note: '',
			},
		]

		defs.forEach(function (def) {
			const items = groups[def.key] || []
			if (!items.length) return
			const section = el('section', { class: 'cskh-pillar ' + def.cls, 'data-pillar': def.key }, [
				el('div', { class: 'cskh-pillar-head' }, [
					el('h2', { text: def.title }),
					def.note ? el('span', { class: 'cskh-muted', style: 'font-size: 12px;', text: def.note }) : null,
				]),
				el(
					'div',
					{ class: 'cskh-kpi-grid' },
					items.map(function (m) {
						return renderMetricCard(m, handlers)
					})
				),
			])
			container.appendChild(section)
		})
	}

	/**
	 * Partition score rows strictly into `finalized`, `provisional`, and
	 * `insufficient_data` groups so `provisional` scores are NEVER ranked
	 * together with `finalized` scores (P3.md §5.3 & §10.3).
	 */
	function partitionScoreGroups(rows) {
		const finalized = []
		const provisional = []
		const insufficient = []
		;(rows || []).forEach(function (row) {
			const rawScore = row.process_compliance_score
			const status = String(
				row.score_status ||
					(rawScore && typeof rawScore === 'object' ? rawScore.status : '') ||
					row.confidence_status ||
					''
			).toLowerCase()
			const scoreVal =
				rawScore && typeof rawScore === 'object' ? rawScore.value : rawScore
			const isNa =
				status === 'insufficient_data' ||
				status === 'na' ||
				(rawScore && typeof rawScore === 'object' && rawScore.is_na) ||
				scoreVal === null ||
				scoreVal === undefined ||
				scoreVal === 'N/A'

			if (isNa) {
				insufficient.push(row)
			} else if (status === 'finalized') {
				finalized.push(row)
			} else {
				provisional.push(row)
			}
		})
		return {
			finalized: finalized,
			provisional: provisional,
			insufficient_data: insufficient,
		}
	}

	/**
	 * Render an inline horizontal bar chart using SVG DOM nodes (`createElementNS`).
	 * Rows with `N/A` are annotated as `N/A` and never plotted as `0%`.
	 */
	function renderBarChart(items, options) {
		const opts = options || {}
		const validItems = (items || []).slice(0, opts.maxBars || 10)
		if (!validItems.length) {
			return el('p', { class: 'cskh-muted', text: t('No data to display.') })
		}
		const rowH = 30
		const svgW = 640
		const labelW = 180
		const barMaxW = 340
		const svgH = validItems.length * rowH + 16
		const maxVal = Number(opts.maxValue || 100) || 100

		const svg = svgEl('svg', {
			class: 'cskh-svg-chart',
			viewBox: '0 0 ' + svgW + ' ' + svgH,
			role: 'img',
		})

		validItems.forEach(function (it, idx) {
			const y = 10 + idx * rowH
			const isNa = it.value === null || it.value === undefined || it.is_na
			const numericVal = isNa ? 0 : Math.max(0, Math.min(maxVal, Number(it.value)))
			const barW = isNa ? 0 : Math.round((numericVal / maxVal) * barMaxW)

			svg.appendChild(
				svgEl('text', { x: 4, y: y + 15, fill: '#1f272e', 'font-size': '12' }, [
					String(it.label || '—').slice(0, 26),
				])
			)
			svg.appendChild(
				svgEl('rect', {
					x: labelW,
					y: y + 3,
					width: barMaxW,
					height: 16,
					rx: 4,
					fill: '#f1f5f9',
				})
			)
			if (!isNa && barW > 0) {
				svg.appendChild(
					svgEl('rect', {
						x: labelW,
						y: y + 3,
						width: barW,
						height: 16,
						rx: 4,
						fill: it.color || '#2563eb',
					})
				)
			}
			svg.appendChild(
				svgEl(
					'text',
					{
						x: labelW + barMaxW + 10,
						y: y + 15,
						fill: isNa ? '#7c7c7c' : '#1f272e',
						'font-size': '12',
						'font-weight': '600',
					},
					[isNa ? t('N/A') : formatDecimal(it.value, 2) + (opts.suffix || '%')]
				)
			)
		})

		return el('div', { class: 'cskh-chart-wrap' }, [svg])
	}

	function closeModal(host) {
		cskh.clear(host || document.getElementById('p3-modal-host'))
	}

	function mountModalShell(titleText, subtitleText, bodyNodes, narrow) {
		const host = document.getElementById('p3-modal-host')
		if (!host) return null
		cskh.clear(host)

		const closeBtn = el('button', {
			type: 'button',
			class: 'cskh-btn is-small',
			text: t('Close'),
			onclick: function () {
				closeModal(host)
			},
		})

		const dialog = el(
			'div',
			{
				class: 'cskh-modal' + (narrow ? ' is-narrow' : ''),
				role: 'dialog',
				'aria-modal': 'true',
				onclick: function (e) {
					e.stopPropagation()
				},
			},
			[
				el('div', { class: 'cskh-modal-head' }, [
					el('div', {}, [
						el('h2', { text: titleText }),
						subtitleText
							? el('p', { class: 'cskh-muted', style: 'margin: 4px 0 0; font-size: 12px;', text: subtitleText })
							: null,
					]),
					closeBtn,
				]),
			].concat(bodyNodes || [])
		)

		const backdrop = el(
			'div',
			{
				class: 'cskh-modal-backdrop',
				onclick: function () {
					closeModal(host)
				},
			},
			[dialog]
		)
		host.appendChild(backdrop)
		return host
	}

	/**
	 * Open Server-Side Paginated Drill-Down Modal (P3.md §11.1).
	 * Reconciles e.g. `80 / 100` first-touch into `all (100)`, `met (80)`, `unmet (20)`, `excluded`.
	 */
	function openDrilldownModal(metric, baseFilters) {
		const state = Object.assign({}, baseFilters || {}, {
			metric_key: metric.key,
			segment: 'all',
			search: '',
			sort_by: 'effective_due_at',
			sort_dir: 'desc',
			page: 1,
			page_size: 20,
		})

		const alertBox = el('div', {})
		const tabsHost = el('div', { class: 'cskh-filter-chips', style: 'margin-bottom: 12px;' })
		const tableHost = el('div', {})
		const rangeNode = el('span', {})
		const prevBtn = el('button', { type: 'button', class: 'cskh-btn is-small', text: t('Previous') })
		const nextBtn = el('button', { type: 'button', class: 'cskh-btn is-small', text: t('Next') })
		const pagerCtrl = cskh.pager({
			pageSize: 20,
			range: rangeNode,
			prev: prevBtn,
			next: nextBtn,
		})

		prevBtn.addEventListener('click', function () {
			if (pagerCtrl.page() > 1) {
				pagerCtrl.goto(pagerCtrl.page() - 1)
				state.page = pagerCtrl.page()
				loadDrilldown()
			}
		})
		nextBtn.addEventListener('click', function () {
			if (pagerCtrl.page() < pagerCtrl.pages()) {
				pagerCtrl.goto(pagerCtrl.page() + 1)
				state.page = pagerCtrl.page()
				loadDrilldown()
			}
		})

		const searchInput = el('input', {
			type: 'search',
			class: 'cskh-input',
			id: 'p3-drilldown-search',
			placeholder: t('Filter by work item ID...'),
			onchange: function (e) {
				state.search = e.target.value.trim()
				pagerCtrl.goto(1)
				state.page = 1
				loadDrilldown()
			},
		})

		const sortSelect = el(
			'select',
			{
				class: 'cskh-select',
				id: 'p3-drilldown-sort',
				onchange: function (e) {
					state.sort_by = e.target.value
					pagerCtrl.goto(1)
					state.page = 1
					loadDrilldown()
				},
			},
			[
				el('option', { value: 'effective_due_at', text: t('Effective due at') }),
				el('option', { value: 'fulfilled_at', text: t('Fulfilled at') }),
				el('option', { value: 'breached_at', text: t('Breached at') }),
			]
		)

		const sortDirSelect = el(
			'select',
			{
				class: 'cskh-select',
				id: 'p3-drilldown-sort-dir',
				onchange: function (e) {
					state.sort_dir = e.target.value
					pagerCtrl.goto(1)
					state.page = 1
					loadDrilldown()
				},
			},
			[
				el('option', { value: 'desc', text: t('Descending') }),
				el('option', { value: 'asc', text: t('Ascending') }),
			]
		)

		const disp = formatMetricDisplay(metric)
		mountModalShell(
			t('Metric drill-down') + ' — ' + t(metric.label),
			disp.main + (disp.ratio ? ' (' + disp.ratio + ')' : '') + ' · ' + grainLabel(metric.grain),
			[
				alertBox,
				tabsHost,
				el('div', { class: 'cskh-actions', style: 'margin-bottom: 12px;' }, [
					searchInput,
					el('label', { class: 'cskh-field' }, [t('Sort by'), sortSelect]),
					el('label', { class: 'cskh-field' }, [t('Sort by'), sortDirSelect]),
				]),
				tableHost,
				el('div', { class: 'cskh-pager' }, [prevBtn, rangeNode, nextBtn]),
			],
			false
		)

		const seq = cskh.sequencer()

		async function loadDrilldown() {
			const token = seq.next()
			cskh.clear(alertBox)
			cskh.clear(tableHost)
			tableHost.appendChild(el('div', { class: 'cskh-loading', text: t('Loading...') }))
			try {
				const res = await window.cskhP3Api.drilldownMetric(state)
				if (token !== seq.current()) return
				cskh.clear(tableHost)
				renderSegmentTabs(res.segment_counts || {})
				pagerCtrl.setTotal(res.total)
				pagerCtrl.render()

				if (!res.rows.length) {
					tableHost.appendChild(
						el('div', {
							class: 'cskh-alert is-empty',
							text: t('No source records match the selected cohort segment.'),
						})
					)
					return
				}

				const tbody = el(
					'tbody',
					{},
					res.rows.map(function (row) {
						const wi = row.work_item || row.work_item_id || '—'
						const canOpenWorkItem =
							cskh.capabilities().pii && wi && wi !== '—' && !row.pii_redacted
						const wiCell = canOpenWorkItem
							? el('a', {
									href: '/cskh/work-item?name=' + encodeURIComponent(wi),
									class: 'cskh-mono',
									text: wi,
								})
							: el('span', { class: 'cskh-mono', text: wi })

						return el('tr', {}, [
							el('td', {}, [wiCell]),
							el('td', { class: 'cskh-mono', text: row.obligation_id || row.interval_id || row.attempt_id || '—' }),
							el('td', { text: row.obligation_type ? obligationTypeLabel(row.obligation_type) : grainLabel(metric.grain) }),
							el('td', { text: row.responsible_user || row.owner_user || '—' }),
							el('td', {
								text:
									row.team_at_event === 'historical_attribution_unknown'
										? t('Historical attribution unknown')
										: row.team_at_event || row.team || '—',
							}),
							el('td', {
								text: row.pii_redacted
									? t('PII redacted after handover or by role policy')
									: row.customer_display || row.customer_ref || '—',
							}),
							el('td', { text: row.effective_due_at ? cskh.formatDate(row.effective_due_at) : '—' }),
							el('td', {
								text: row.fulfilled_at
									? cskh.formatDate(row.fulfilled_at)
									: row.breached_at
										? cskh.formatDate(row.breached_at)
										: '—',
							}),
							el('td', {}, [
								el('span', {
									class: cskh.stateBadge(row.sla_status || row.status || ''),
									text: row.sla_status ? slaStatusLabel(row.sla_status) : row.status || '—',
								}),
							]),
							el('td', { text: row.waiver_reason || row.exclusion_reason || '—' }),
						])
					})
				)

				tableHost.appendChild(
					el('div', { class: 'cskh-table-scroll' }, [
						el('table', { class: 'cskh-table', id: 'p3-drilldown-table' }, [
							el('thead', {}, [
								el('tr', {}, [
									el('th', { text: t('Work item') }),
									el('th', { text: t('Obligation ID') }),
									el('th', { text: t('Obligation type') }),
									el('th', { text: t('Responsible user') }),
									el('th', { text: t('Team at event') }),
									el('th', { text: t('Customer ref') }),
									el('th', { text: t('Effective due at') }),
									el('th', { text: t('Fulfilled at') }),
									el('th', { text: t('SLA status') }),
									el('th', { text: t('Exemption / waiver reason') }),
								]),
							]),
							tbody,
						]),
					])
				)
			} catch (err) {
				if (token !== seq.current()) return
				cskh.clear(tableHost)
				cskh.renderError(alertBox, err)
			}
		}

		function renderSegmentTabs(counts) {
			cskh.clear(tabsHost)
			const segs = [
				{ key: 'all', label: t('All eligible'), count: counts.all },
				{ key: 'met', label: t('Met / On-time'), count: counts.met },
				{ key: 'unmet', label: t('Unmet / Late or breached'), count: counts.unmet },
				{ key: 'excluded', label: t('Excluded / Waived'), count: counts.excluded },
			]
			segs.forEach(function (seg) {
				const countSuffix =
					seg.count !== null && seg.count !== undefined
						? ' (' + cskh.formatNumber(seg.count) + ')'
						: ''
				tabsHost.appendChild(
					el('button', {
						type: 'button',
						class: 'cskh-chip' + (state.segment === seg.key ? ' is-active' : ''),
						'data-segment': seg.key,
						text: seg.label + countSuffix,
						onclick: function () {
							state.segment = seg.key
							pagerCtrl.goto(1)
							state.page = 1
							loadDrilldown()
						},
					})
				)
			})
		}

		loadDrilldown()
	}

	/**
	 * Open Server-Authorized Export Modal (P3.md §11.2).
	 * Never synthesizes CSV/XLSX in browser; tracks server job, expiry & download URL.
	 */
	function openExportModal(reportKey, currentFilters) {
		const alertBox = el('div', {})
		const jobsHost = el('div', { style: 'margin-top: 14px;' })
		const formatSelect = el(
			'select',
			{ class: 'cskh-select', id: 'p3-export-format' },
			[
				el('option', { value: 'csv', text: t('CSV (.csv)') }),
				el('option', { value: 'xlsx', text: t('Excel (.xlsx)') }),
			]
		)
		const purposeInput = el('textarea', {
			class: 'cskh-textarea',
			id: 'p3-export-purpose',
			placeholder: t('Enter reason or business purpose for export...'),
		})
		const submitBtn = el('button', {
			type: 'button',
			id: 'p3-export-submit',
			class: 'cskh-btn is-primary',
			text: t('Queue export job'),
		})

		function renderJobsTable() {
			cskh.clear(jobsHost)
			jobsHost.appendChild(el('h3', { style: 'font-size: 14px; margin: 0 0 8px;', text: t('Authorized export jobs') }))
			if (!sessionExportJobs.length) {
				jobsHost.appendChild(
					el('p', { class: 'cskh-muted', text: t('No export jobs requested in this session yet.') })
				)
				return
			}
			const tbody = el(
				'tbody',
				{},
				sessionExportJobs.map(function (job) {
					const canDownload =
						(job.status === 'ready' || job.status === 'completed') && job.download_url
					return el('tr', { 'data-export-id': job.export_id }, [
						el('td', { class: 'cskh-mono', text: job.export_id }),
						el('td', { text: String(job.format || 'csv').toUpperCase() }),
						el('td', {}, [
							el('span', {
								class: cskh.stateBadge(job.status),
								text: exportStatusLabel(job.status),
							}),
						]),
						el('td', {
							class: 'num',
							text: job.row_count !== null ? cskh.formatNumber(job.row_count) : '—',
						}),
						el('td', { text: job.expires_at ? cskh.formatDate(job.expires_at) : '—' }),
						el('td', { class: 'cskh-row-actions' }, [
							canDownload
								? el('a', {
										href: job.download_url,
										class: 'cskh-download',
										'data-export-download': job.export_id,
										text: t('Download file'),
									})
								: job.error_message
									? el('span', { class: 'cskh-muted', text: job.error_message })
									: null,
							el('button', {
								type: 'button',
								class: 'cskh-btn is-small',
								text: t('Refresh'),
								onclick: async function () {
									try {
										const fresh = await window.cskhP3Api.getExportStatus(job.export_id)
										if (fresh) Object.assign(job, fresh)
										renderJobsTable()
									} catch (err) {
										if (err && err.code === 'EXPORT_EXPIRED') job.status = 'expired'
										else if (err && (err.code === 'FORBIDDEN' || err.code === 'FORBIDDEN_SCOPE'))
											job.status = 'forbidden'
										cskh.renderError(alertBox, err)
										renderJobsTable()
									}
								},
							}),
						]),
					])
				})
			)
			jobsHost.appendChild(
				el('div', { class: 'cskh-table-scroll' }, [
					el('table', { class: 'cskh-table', id: 'p3-export-jobs-table' }, [
						el('thead', {}, [
							el('tr', {}, [
								el('th', { text: t('Export ID') }),
								el('th', { text: t('Export format') }),
								el('th', { text: t('Status') }),
								el('th', { class: 'num', text: t('Row count') }),
								el('th', { text: t('Expires at') }),
								el('th', { text: t('Actions') }),
							]),
						]),
						tbody,
					]),
				])
			)
		}

		submitBtn.addEventListener('click', async function () {
			cskh.clear(alertBox)
			const purpose = purposeInput.value.trim()
			if (!purpose) {
				cskh.alert(alertBox, 'warning', t('Please enter an export purpose.'))
				return
			}
			submitBtn.disabled = true
			try {
				const job = await window.cskhP3Api.createExport({
					report_key: reportKey,
					export_format: formatSelect.value,
					purpose: purpose,
					filters: currentFilters,
				})
				if (job) {
					sessionExportJobs.unshift(job)
					cskh.toast('success', t('Export job submitted.'))
					if (job.status === 'running' || job.status === 'queued') {
						try {
							const settled = await cskh.poll({
								interval: 800,
								timeout: 10000,
								check: function () {
									return window.cskhP3Api.getExportStatus(job.export_id)
								},
								done: function (st) {
									return !st || (st.status !== 'running' && st.status !== 'queued')
								},
							})
							if (settled) Object.assign(job, settled)
						} catch (_pollErr) {
							/* keep running status visible with manual Refresh button */
						}
					}
				}
				renderJobsTable()
			} catch (err) {
				cskh.renderError(alertBox, err)
			} finally {
				submitBtn.disabled = false
			}
		})

		mountModalShell(
			t('Request authorized export'),
			t('Export is generated and authorized on the server; client-side bulk export is disabled.'),
			[
				alertBox,
				el('div', { class: 'cskh-form-grid' }, [
					el('label', { class: 'cskh-field' }, [t('Export format'), formatSelect]),
				]),
				el('label', { class: 'cskh-field', style: 'margin-bottom: 12px;' }, [
					t('Export purpose / audit note'),
					purposeInput,
				]),
				el('div', { class: 'cskh-actions' }, [submitBtn]),
				jobsHost,
			],
			false
		)
		renderJobsTable()
	}

	function openRecomputeModal(currentFilters, onCompleted) {
		const alertBox = el('div', {})
		const reasonInput = el('textarea', {
			class: 'cskh-textarea',
			id: 'p3-recompute-reason',
		})
		const submitBtn = el('button', {
			type: 'button',
			id: 'p3-recompute-submit',
			class: 'cskh-btn is-primary',
			text: t('Trigger recompute'),
		})

		submitBtn.addEventListener('click', async function () {
			cskh.clear(alertBox)
			const reason = reasonInput.value.trim()
			if (!reason) {
				cskh.alert(alertBox, 'warning', t('Please enter a reason for KPI recompute.'))
				return
			}
			submitBtn.disabled = true
			try {
				await window.cskhP3Api.requestRecompute(
					Object.assign({}, currentFilters || {}, { reason: reason })
				)
				cskh.toast('success', t('KPI recompute job queued.'))
				closeModal()
				if (typeof onCompleted === 'function') onCompleted()
			} catch (err) {
				cskh.renderError(alertBox, err)
			} finally {
				submitBtn.disabled = false
			}
		})

		mountModalShell(
			t('Request KPI recompute'),
			'',
			[
				alertBox,
				el('label', { class: 'cskh-field', style: 'margin-bottom: 12px;' }, [
					t('Recompute reason'),
					reasonInput,
				]),
				el('div', { class: 'cskh-actions' }, [submitBtn]),
			],
			true
		)
	}

	function openRevisionsModal(currentFilters) {
		const alertBox = el('div', {})
		const bodyHost = el('div', {}, [el('div', { class: 'cskh-loading', text: t('Loading...') })])

		mountModalShell(t('Revision & restatement history'), '', [alertBox, bodyHost], false)

		;(async function () {
			try {
				const res = await window.cskhP3Api.listRevisions(currentFilters)
				cskh.clear(bodyHost)
				if (!res.rows.length) {
					bodyHost.appendChild(
						el('div', {
							class: 'cskh-alert is-empty',
							text: t('No revision history found for this scope and period.'),
						})
					)
					return
				}
				bodyHost.appendChild(
					el('div', { class: 'cskh-table-scroll' }, [
						el('table', { class: 'cskh-table', id: 'p3-revisions-table' }, [
							el('thead', {}, [
								el('tr', {}, [
									el('th', { text: t('Run ID') }),
									el('th', { text: t('Snapshot revision') }),
									el('th', { text: t('Data confidence status') }),
									el('th', { text: t('Snapshot as-of') }),
									el('th', { text: t('Finished at') }),
									el('th', { text: t('Triggered by') }),
									el('th', { text: t('Delta / restatement note') }),
								]),
							]),
							el(
								'tbody',
								{},
								res.rows.map(function (r) {
									return el('tr', {}, [
										el('td', { class: 'cskh-mono', text: r.run_id || r.name || '—' }),
										el('td', { class: 'cskh-mono', text: String(r.revision || r.snapshot_revision || '1') }),
										el('td', {}, [
											el('span', {
												class: cskh.stateBadge(r.status || 'fresh'),
												text: confidenceLabel(r.status || 'fresh'),
											}),
										]),
										el('td', { text: r.as_of ? cskh.formatDate(r.as_of) : '—' }),
										el('td', { text: r.finished_at ? cskh.formatDate(r.finished_at) : '—' }),
										el('td', { text: r.triggered_by || r.owner || '—' }),
										el('td', { text: r.delta_note || r.reason || '—' }),
									])
								})
							),
						]),
					])
				)
			} catch (err) {
				cskh.clear(bodyHost)
				cskh.renderError(alertBox, err)
			}
		})()
	}

	function openWaiveModal(obligationRow, onCompleted) {
		const alertBox = el('div', {})
		const reasonInput = el('textarea', {
			class: 'cskh-textarea',
			id: 'p3-waive-reason',
		})
		const submitBtn = el('button', {
			type: 'button',
			id: 'p3-waive-submit',
			class: 'cskh-btn is-primary',
			text: t('Submit SLA waiver'),
		})
		const obId = obligationRow.obligation_id || obligationRow.name || ''

		submitBtn.addEventListener('click', async function () {
			cskh.clear(alertBox)
			const reason = reasonInput.value.trim()
			if (!reason) {
				cskh.alert(
					alertBox,
					'warning',
					t('Please enter an audit reason to waive this SLA obligation.')
				)
				return
			}
			submitBtn.disabled = true
			try {
				await window.cskhP3Api.waiveSlaObligation({
					obligation_id: obId,
					reason: reason,
				})
				cskh.toast('success', t('SLA obligation waived with audit trail.'))
				closeModal()
				if (typeof onCompleted === 'function') onCompleted()
			} catch (err) {
				cskh.renderError(alertBox, err)
			} finally {
				submitBtn.disabled = false
			}
		})

		mountModalShell(
			t('Waive SLA obligation'),
			obId + ' · ' + obligationTypeLabel(obligationRow.obligation_type),
			[
				alertBox,
				el('label', { class: 'cskh-field', style: 'margin-bottom: 12px;' }, [
					t('Waiver reason'),
					reasonInput,
				]),
				el('div', { class: 'cskh-actions' }, [submitBtn]),
			],
			true
		)
	}

	function bindHeaderActions(reportKey, getFiltersFn, onRefreshFn) {
		const btnExport = document.getElementById('p3-btn-export')
		if (btnExport) {
			btnExport.addEventListener('click', function () {
				openExportModal(reportKey, getFiltersFn())
			})
		}
		const btnRecompute = document.getElementById('p3-btn-recompute')
		if (btnRecompute) {
			btnRecompute.addEventListener('click', function () {
				openRecomputeModal(getFiltersFn(), onRefreshFn)
			})
		}
		const btnRevisions = document.getElementById('p3-btn-revisions')
		if (btnRevisions) {
			btnRevisions.addEventListener('click', function () {
				openRevisionsModal(getFiltersFn())
			})
		}
	}

	return {
		formatDecimal: formatDecimal,
		confidenceLabel: confidenceLabel,
		grainLabel: grainLabel,
		unitLabel: unitLabel,
		timeBasisLabel: timeBasisLabel,
		obligationTypeLabel: obligationTypeLabel,
		slaStatusLabel: slaStatusLabel,
		exportStatusLabel: exportStatusLabel,
		formatMetricDisplay: formatMetricDisplay,
		formatInlineRateCell: formatInlineRateCell,
		renderBanners: renderBanners,
		renderWatermarkBar: renderWatermarkBar,
		mountFilterBar: mountFilterBar,
		renderMetricCard: renderMetricCard,
		renderThreePillars: renderThreePillars,
		partitionScoreGroups: partitionScoreGroups,
		renderBarChart: renderBarChart,
		openDrilldownModal: openDrilldownModal,
		openExportModal: openExportModal,
		openRecomputeModal: openRecomputeModal,
		openRevisionsModal: openRevisionsModal,
		openWaiveModal: openWaiveModal,
		bindHeaderActions: bindHeaderActions,
	}
})()
