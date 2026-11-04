/* P3 Reporting, SLA, Drill-Down & Export API Adapter (`window.cskhP3Api`).

   Aligned 100% with frozen `docs/p3/report-api-contract.md` and `docs/p3/kpi-dictionary.md`
   (committed SHA `a50390ae263190a3d27ea71ea9750c0d748407a6`, metric_version `cskh-metrics-v1`).

   Invariants enforced here:
     * Frontend NEVER defines or computes KPI formulas, weights, or rates:
       every metric `value`, `numerator`, `denominator`, `status`, `grain`,
       `formula`, `exclusions`, `as_of`, `source_watermark`, and `metric_version`
       comes strictly from the authoritative backend contract.
     * `denominator === 0` (`unit` in `percent` / `score_100`) or `status` in
       `('insufficient_data', 'disabled')` or `value === null` is flagged `is_na: true`
       so UI renders `N/A` (never `0%`), whereas `CONTACT_ATTEMPT_COUNT = 0` (`count`)
       or `0 / 12` (`0%`) renders `0` / `0%` truthfully.
     * Drill-down and tables pass pagination/filter/sort parameters to the server
       before `LIMIT/OFFSET` — never fetching full datasets to slice in browser.
     * Exports are queued and downloaded strictly through server-authorized jobs
       (`request_export` -> `get_export_status` -> `download_export`).
*/

window.cskhP3Api = (function () {
	'use strict'

	const ENDPOINTS = {
		campaignsList: 'crm_cskh.api.campaigns.list_campaigns',
		executiveOverview: 'crm_cskh.api.p3_reporting.get_executive_overview',
		teamComparison: 'crm_cskh.api.p3_reporting.get_team_comparison',
		employeeQuality: 'crm_cskh.api.p3_reporting.get_employee_quality',
		campaignQuality: 'crm_cskh.api.p3_reporting.get_campaign_quality',
		personalScorecard: 'crm_cskh.api.p3_reporting.get_my_scorecard',
		drilldown: 'crm_cskh.api.p3_reporting.get_metric_drilldown',
		requestRecompute: 'crm_cskh.api.p3_reporting.request_recompute',
		getRunStatus: 'crm_cskh.api.p3_reporting.get_run_status',
		createExport: 'crm_cskh.api.p3_reporting.request_export',
		getExportStatus: 'crm_cskh.api.p3_reporting.get_export_status',
		downloadExportPath: '/api/method/crm_cskh.api.p3_reporting.download_export',
		slaListObligations: 'crm_cskh.api.sla.list_obligations',
		slaGetObligationDetail: 'crm_cskh.api.sla.get_obligation_detail',
		waiveSlaObligation: 'crm_cskh.api.sla.waive_obligation',
		pauseSlaObligation: 'crm_cskh.api.sla.pause_obligation',
		resumeSlaObligation: 'crm_cskh.api.sla.resume_obligation',
	}

	const METRIC_CATALOG_DEFAULTS = {
		PROCESS_SCORE: {
			label: 'Process compliance score',
			pillar: 'compliance',
			unit: 'score_100',
			grain: 'ownership_interval',
			time_basis: 'deadline_cohort',
			formula: 'Weighted sum over valid components divided by active weight sum.',
			drillable: false,
		},
		WORK_ITEM_FIRST_ATTEMPT_SLA_RATE: {
			label: 'First-touch on-time rate (Work Item)',
			pillar: 'compliance',
			unit: 'percent',
			grain: 'distinct_work_item',
			time_basis: 'deadline_cohort',
			formula:
				'On-time FIRST_ATTEMPT obligations divided by due eligible FIRST_ATTEMPT obligations in period.',
			drillable: true,
		},
		FIRST_ATTEMPT_SLA_RATE: {
			label: 'First-touch on-time rate',
			pillar: 'compliance',
			unit: 'percent',
			grain: 'sla_obligation',
			time_basis: 'deadline_cohort',
			formula:
				'On-time FIRST_ATTEMPT obligations divided by due eligible FIRST_ATTEMPT obligations in period.',
			drillable: true,
		},
		FOLLOWUP_SLA_RATE: {
			label: 'Follow-up on-time rate',
			pillar: 'compliance',
			unit: 'percent',
			grain: 'sla_obligation',
			time_basis: 'deadline_cohort',
			formula:
				'On-time FOLLOWUP obligations divided by due eligible FOLLOWUP obligations in period.',
			drillable: true,
		},
		MIN_ATTEMPTS_COMPLIANCE_RATE: {
			label: 'Minimum attempts compliance',
			pillar: 'compliance',
			unit: 'percent',
			grain: 'sla_obligation',
			time_basis: 'deadline_cohort',
			formula: 'Fulfilled MIN_ATTEMPTS obligations divided by due MIN_ATTEMPTS obligations.',
			drillable: false,
		},
		CALLBACK_COMPLETION_RATE: {
			label: 'Callback completion rate',
			pillar: 'compliance',
			unit: 'percent',
			grain: 'sla_obligation',
			time_basis: 'deadline_cohort',
			formula:
				'Callbacks resolved by an effective contact attempt divided by due callbacks in period.',
			drillable: true,
		},
		RESCHEDULE_RATE: {
			label: 'Callback reschedule rate',
			pillar: 'compliance',
			unit: 'percent',
			grain: 'sla_obligation',
			time_basis: 'event_time',
			formula: 'Reschedule events divided by active or newly created callbacks in period.',
			drillable: false,
		},
		DNC_COMPLIANCE_RATE: {
			label: 'DNC & closure compliance rate',
			pillar: 'compliance',
			unit: 'percent',
			grain: 'ownership_interval',
			time_basis: 'event_time',
			formula:
				'Ownership intervals respecting DNC and valid closure rules divided by evaluated intervals.',
			drillable: true,
		},
		WORK_ITEMS_TOUCHED: {
			label: 'Unique touched work items',
			pillar: 'connection',
			unit: 'count',
			grain: 'distinct_work_item',
			time_basis: 'event_time',
			formula: 'DISTINCT Work Items with >= 1 effective contact attempt in period.',
			drillable: false,
		},
		CONTACT_ATTEMPT_COUNT: {
			label: 'Effective attempts (activity reference)',
			pillar: 'connection',
			unit: 'count',
			grain: 'effective_attempt',
			time_basis: 'event_time',
			formula: 'Effective non-voided contact attempts in period (activity reference only).',
			drillable: true,
		},
		CONNECTED_ATTEMPT_COUNT: {
			label: 'Connected attempts',
			pillar: 'connection',
			unit: 'count',
			grain: 'effective_attempt',
			time_basis: 'event_time',
			formula: 'Effective contact attempts with effective_is_connected = 1 in period.',
			drillable: false,
		},
		CONNECTED_RATE: {
			label: 'Connected rate',
			pillar: 'connection',
			unit: 'percent',
			grain: 'effective_attempt',
			time_basis: 'event_time',
			formula: 'Connected effective attempts divided by total effective attempts in period.',
			drillable: true,
		},
		FIRST_CONNECTED_TRACKING_RATE: {
			label: 'First-connected tracking (informational, non-penalizing)',
			pillar: 'connection',
			unit: 'percent',
			grain: 'sla_obligation',
			time_basis: 'deadline_cohort',
			formula: 'Informational FIRST_CONNECTED tracking rate (never penalizes Process Score).',
			drillable: true,
		},
		VALID_OUTCOME_LOGGING_RATE: {
			label: 'Outcome completeness rate',
			pillar: 'reliability',
			unit: 'percent',
			grain: 'effective_attempt',
			time_basis: 'event_time',
			formula:
				'Effective attempts satisfying required fields without error correction/void divided by raw attempts.',
			drillable: true,
		},
		CORRECTION_RATE: {
			label: 'Correction / void rate',
			pillar: 'reliability',
			unit: 'percent',
			grain: 'effective_attempt',
			time_basis: 'event_time',
			formula: 'Approved corrected or voided attempts divided by raw attempts in period.',
			drillable: true,
		},
		VOID_ATTEMPT_RATE: {
			label: 'Void attempt rate',
			pillar: 'reliability',
			unit: 'percent',
			grain: 'effective_attempt',
			time_basis: 'event_time',
			formula: 'Approved voided attempts divided by raw attempts in period.',
			drillable: false,
		},
		QA_PASS_RATE: {
			label: 'QA pass rate',
			pillar: 'reliability',
			unit: 'percent',
			grain: 'qa_review',
			time_basis: 'event_time',
			formula: 'Approved passing QA reviews divided by completed eligible QA reviews in cohort.',
			drillable: false,
		},
		C4_CALLBACK_DNC_DISCIPLINE: {
			label: 'Callback completion & DNC discipline',
			pillar: 'compliance',
			unit: 'percent',
			grain: 'ownership_interval',
			time_basis: 'event_time',
			formula: 'Ownership intervals respecting DNC and valid closure rules divided by evaluated intervals.',
			drillable: false,
		},
	}

	const SCORE_COMPONENT_METRIC_MAP = {
		C1_FIRST_ATTEMPT_SLA: 'FIRST_ATTEMPT_SLA_RATE',
		C2_FOLLOWUP_SLA: 'FOLLOWUP_SLA_RATE',
		C3_DATA_HYGIENE: 'VALID_OUTCOME_LOGGING_RATE',
		C4_CALLBACK_DNC_DISCIPLINE: 'C4_CALLBACK_DNC_DISCIPLINE',
		C5_QA_QUALITY: 'QA_PASS_RATE',
	}

	// Lowercase compatibility aliases
	METRIC_CATALOG_DEFAULTS.process_compliance_score = METRIC_CATALOG_DEFAULTS.PROCESS_SCORE
	METRIC_CATALOG_DEFAULTS.first_touch_on_time_rate = METRIC_CATALOG_DEFAULTS.FIRST_ATTEMPT_SLA_RATE
	METRIC_CATALOG_DEFAULTS.followup_on_time_rate = METRIC_CATALOG_DEFAULTS.FOLLOWUP_SLA_RATE
	METRIC_CATALOG_DEFAULTS.connected_rate = METRIC_CATALOG_DEFAULTS.CONNECTED_RATE
	METRIC_CATALOG_DEFAULTS.outcome_completeness_rate =
		METRIC_CATALOG_DEFAULTS.VALID_OUTCOME_LOGGING_RATE
	METRIC_CATALOG_DEFAULTS.qa_pass_rate = METRIC_CATALOG_DEFAULTS.QA_PASS_RATE

	function toArray(val) {
		if (Array.isArray(val)) return val
		if (!val) return []
		if (typeof val === 'string') {
			const trimmed = val.trim()
			if (!trimmed) return []
			if (trimmed.charAt(0) === '[') {
				try {
					const parsed = JSON.parse(trimmed)
					if (Array.isArray(parsed)) return parsed
				} catch (_err) {
					/* ignore */
				}
			}
			return trimmed
				.split(',')
				.map(function (s) {
					return s.trim()
				})
				.filter(Boolean)
		}
		return []
	}

	function toBool(val, fallback) {
		if (val === undefined || val === null) return !!fallback
		if (typeof val === 'boolean') return val
		if (typeof val === 'number') return val !== 0
		const s = String(val).trim().toLowerCase()
		if (s === '1' || s === 'true' || s === 'yes') return true
		if (s === '0' || s === 'false' || s === 'no' || s === '') return false
		return !!fallback
	}

	function extractEnvelopeMeta(raw) {
		const r = raw || {}
		const actor = r.actor_scope || {}
		const period = r.period || {}
		const fresh = r.freshness || r.meta || r.snapshot || {}
		const policy = r.policy_notice || {}
		const excl = r.exclusion_counts || {}

		let freshnessStatus =
			fresh.snapshot_status || fresh.freshness_status || fresh.status || r.freshness_status || 'fresh'
		if (fresh.is_restated) {
			freshnessStatus = 'restated'
		}

		const exclSummary = []
		if (excl.waived_obligations) exclSummary.push('waived: ' + excl.waived_obligations)
		if (excl.canceled_validly_dnc) exclSummary.push('canceled/DNC: ' + excl.canceled_validly_dnc)
		if (excl.voided_attempts) exclSummary.push('voided: ' + excl.voided_attempts)
		if (excl.historical_attribution_unknown) {
			exclSummary.push('unknown_team: ' + excl.historical_attribution_unknown)
		}

		return {
			scope: actor.role_profile || fresh.scope || r.scope || 'system',
			pii_allowed: toBool(
				actor.pii_allowed !== undefined ? actor.pii_allowed : window.cskh.capabilities().pii,
				false
			),
			period_type: period.period_type || r.period_type || 'monthly',
			period_key: period.period_key || r.period_key || '',
			from_date: period.from_date || fresh.from_date || r.from_date || '',
			to_date: period.to_date || r.to_date || '',
			to_date_exclusive: period.to_date_exclusive || r.to_date_exclusive || '',
			timezone: period.timezone || r.timezone || window.cskh.siteTimeZone(),
			run_id: fresh.run_id || '',
			run_mode: fresh.run_mode || '',
			restated_from_run: fresh.restated_from_run || '',
			dirty_pending: toBool(fresh.dirty_pending, false),
			as_of: fresh.as_of || r.as_of || '',
			calculated_at: fresh.calculated_at || fresh.as_of || r.calculated_at || '',
			source_watermark: fresh.source_watermark || r.source_watermark || '',
			freshness_status: freshnessStatus,
			metric_version: fresh.metric_version || r.metric_version || 'cskh-metrics-v1',
			policy_version:
				policy.policy_version || fresh.policy_version || policy.config_stage || 'DEV_CONFIGURABLE',
			policy_message_vi: policy.message_vi || '',
			qa_enabled: toBool(
				actor.qa_enabled !== undefined
					? actor.qa_enabled
					: fresh.qa_enabled !== undefined
						? fresh.qa_enabled
						: r.qa_enabled,
				window.cskh.capabilities().qa_enabled
			),
			is_dev_default_policy:
				policy.production_approved !== undefined
					? !policy.production_approved
					: toBool(r.is_dev_default_policy, true),
			exclusion_counts: excl,
			exclusion_summary: exclSummary,
			has_unknown_historical_attribution:
				Number(excl.historical_attribution_unknown || 0) > 0 ||
				toBool(r.has_unknown_historical_attribution, false),
			has_multiple_policies: toBool(r.has_multiple_policies, false),
		}
	}

	/**
	 * Normalize a single `MetricValue` object (`docs/p3/report-api-contract.md` §1.4)
	 * WITHOUT computing any KPI math.
	 */
	function normalizeMetric(metricKey, rawMetric, envelopeMeta) {
		const env = envelopeMeta || {}
		const raw =
			rawMetric && typeof rawMetric === 'object' && !Array.isArray(rawMetric)
				? rawMetric
				: { value: rawMetric }

		const key = raw.metric_code || raw.metric_key || raw.key || metricKey || ''
		const defaults =
			METRIC_CATALOG_DEFAULTS[key] || METRIC_CATALOG_DEFAULTS[String(key).toUpperCase()] || {}

		const rawUnit = raw.unit || defaults.unit || 'percent'
		const unit = rawUnit === 'score_100' ? 'score' : rawUnit

		const numerator =
			raw.numerator !== undefined && raw.numerator !== null ? Number(raw.numerator) : null
		const denominator =
			raw.denominator !== undefined && raw.denominator !== null ? Number(raw.denominator) : null

		const rawStatus = String(
			raw.status || raw.confidence_status || env.freshness_status || 'ok'
		).toLowerCase()

		let normalizedStatus = rawStatus
		if (rawStatus === 'ok') normalizedStatus = env.freshness_status || 'fresh'
		if (rawStatus === 'final') normalizedStatus = 'finalized'

		const isRateOrScore = unit === 'percent' || unit === 'score'
		const zeroDenominator = denominator !== null && denominator === 0
		const missingValue = raw.value === undefined || raw.value === null || raw.value === 'N/A'
		const hasLatencyPair =
			(unit === 'minutes_business' || unit === 'minutes_wall') &&
			raw.p50 !== undefined &&
			raw.p50 !== null

		const isNa =
			toBool(raw.is_na, false) ||
			rawStatus === 'insufficient_data' ||
			rawStatus === 'disabled' ||
			(isRateOrScore && zeroDenominator) ||
			(missingValue && !hasLatencyPair)

		const validWeight =
			raw.valid_weight !== undefined && raw.valid_weight !== null
				? Number(raw.valid_weight)
				: raw.valid_weight_sum !== undefined && raw.valid_weight_sum !== null
					? Number(raw.valid_weight_sum)
					: null

		let naReason = raw.reason || raw.na_reason || ''
		if (!naReason && isNa) {
			if (
				rawStatus === 'disabled' ||
				(!env.qa_enabled && (key === 'QA_PASS_RATE' || key === 'qa_pass_rate'))
			) {
				naReason = 'qa_disabled'
			} else if (zeroDenominator) {
				naReason = 'zero_denominator'
			}
		} else if (naReason === 'qa_disabled_by_policy') {
			naReason = 'qa_disabled'
		} else if (naReason === 'no_eligible_records') {
			naReason = 'zero_denominator'
		}

		const exclusions = toArray(raw.exclusions).length
			? toArray(raw.exclusions)
			: env.exclusion_summary || []

		return {
			key: key,
			label: defaults.label || raw.label || raw.label_vi || key,
			label_vi: raw.label_vi || '',
			pillar: raw.pillar || defaults.pillar || 'compliance',
			unit: unit,
			grain: raw.grain || defaults.grain || 'ownership_interval',
			value: isNa ? null : raw.value !== undefined ? raw.value : null,
			p50: raw.p50 !== undefined ? raw.p50 : null,
			p90: raw.p90 !== undefined ? raw.p90 : null,
			numerator: numerator,
			denominator: denominator,
			valid_weight_sum: validWeight,
			missing_components: toArray(raw.missing_components),
			attempt_breakdown: raw.attempt_breakdown || null,
			informational_only: toBool(raw.informational_only, false),
			is_na: isNa,
			na_reason: naReason,
			formula: raw.formula || defaults.formula || '',
			period_label:
				raw.period_label ||
				(env.from_date && (env.to_date || env.to_date_exclusive)
					? env.to_date_exclusive
						? '[' + env.from_date + ', ' + env.to_date_exclusive + ')'
						: env.from_date + ' -> ' + env.to_date
					: env.period_key || ''),
			time_basis: raw.time_basis || defaults.time_basis || 'deadline_cohort',
			scope: raw.scope || env.scope || 'system',
			exclusions: exclusions,
			excluded_count:
				raw.excluded_count !== undefined && raw.excluded_count !== null
					? Number(raw.excluded_count)
					: null,
			as_of: raw.as_of || env.as_of || '',
			calculated_at: raw.calculated_at || env.calculated_at || env.as_of || '',
			source_watermark: raw.source_watermark || env.source_watermark || '',
			metric_version: raw.metric_version || env.metric_version || 'cskh-metrics-v1',
			policy_version: raw.policy_version || env.policy_version || 'DEV_CONFIGURABLE',
			status:
				isNa && (normalizedStatus === 'fresh' || normalizedStatus === 'ok')
					? rawStatus === 'disabled'
						? 'disabled'
						: 'insufficient_data'
					: normalizedStatus,
			drillable:
				raw.drilldown_supported !== undefined
					? toBool(raw.drilldown_supported, false)
					: raw.drillable !== undefined
						? toBool(raw.drillable, false)
						: !!defaults.drillable,
		}
	}

	function normalizeMetricsCollection(rawMetrics, envMeta) {
		const list = []
		const byKey = {}
		if (Array.isArray(rawMetrics)) {
			rawMetrics.forEach(function (item) {
				if (!item) return
				const m = normalizeMetric(item.metric_code || item.metric_key || item.key, item, envMeta)
				list.push(m)
				byKey[m.key] = m
			})
		} else if (rawMetrics && typeof rawMetrics === 'object') {
			Object.keys(rawMetrics).forEach(function (k) {
				const m = normalizeMetric(k, rawMetrics[k], envMeta)
				list.push(m)
				byKey[m.key] = m
			})
		}
		return { list: list, byKey: byKey }
	}

	function buildPeriodParams(filterState) {
		const s = filterState || {}
		const bounds = window.cskh.computePeriodBounds(
			s.period_type || 'monthly',
			s.anchor || '',
			s.custom_to || '',
			window.cskh.siteTimeZone()
		)
		const params = {
			period_type: bounds.periodType,
		}
		if (bounds.periodType === 'custom') {
			params.from_date = bounds.fromDate
			params.to_date = bounds.toDateInclusive
		} else {
			params.period_key = bounds.periodKey
		}
		if (s.campaign) params.campaign = s.campaign
		if (s.team) params.team = s.team
		if (s.employee) params.employee = s.employee
		if (s.include_inactive) params.include_inactive = 1
		if (s.sort_by) params.sort_by = s.sort_by
		if (s.sort_dir) params.sort_dir = s.sort_dir
		if (s.page) params.page = Number(s.page)
		if (s.page_size) params.page_size = Number(s.page_size)
		return params
	}

	function resolveScopePair(filterState) {
		const s = filterState || {}
		if (s.scope_type) {
			return { scope_type: s.scope_type, scope_id: s.scope_id || '' }
		}
		if (s.employee) return { scope_type: 'employee', scope_id: s.employee }
		if (s.team) return { scope_type: 'team', scope_id: s.team }
		if (s.campaign) return { scope_type: 'campaign', scope_id: s.campaign }
		const caps = window.cskh.capabilities()
		if (!caps.admin && !caps.bod && !caps.leader && window.cskh.currentUser()) {
			return { scope_type: 'employee', scope_id: window.cskh.currentUser() }
		}
		return { scope_type: 'company', scope_id: '' }
	}

	function mapSegmentToBucket(seg) {
		const map = {
			all: 'all_denominator',
			all_denominator: 'all_denominator',
			met: 'numerator_only',
			numerator_only: 'numerator_only',
			unmet: 'breached_only',
			breached_only: 'breached_only',
			excluded: 'excluded',
		}
		return map[seg] || 'all_denominator'
	}

	return {
		ENDPOINTS: ENDPOINTS,
		METRIC_CATALOG_DEFAULTS: METRIC_CATALOG_DEFAULTS,
		extractEnvelopeMeta: extractEnvelopeMeta,
		normalizeMetric: normalizeMetric,
		normalizeMetricsCollection: normalizeMetricsCollection,
		buildPeriodParams: buildPeriodParams,

		getFilterOptions: async function () {
			const caps = window.cskh.capabilities()
			let campaigns = []
			if (caps.admin || caps.bod || caps.leader) {
				try {
					const res = await window.cskh.get(ENDPOINTS.campaignsList, { page: 1, page_size: 100 })
					const rows = toArray(res && (res.items || res.rows || res.campaigns || res))
					campaigns = rows.map(function (c) {
						return {
							value: c.name || c.campaign_id || '',
							label: c.campaign_name || c.title || c.name || '',
						}
					})
				} catch (_err) {
					campaigns = []
				}
			}
			return {
				campaigns: campaigns,
				teams: [],
				employees: [],
				lead_sources: [],
				revisions: [],
			}
		},

		getExecutiveOverview: async function (filterState) {
			const res = await window.cskh.get(
				ENDPOINTS.executiveOverview,
				buildPeriodParams(filterState)
			)
			const raw = res || {}
			const meta = extractEnvelopeMeta(raw)
			const metrics = normalizeMetricsCollection(raw.metrics || {}, meta)
			return {
				meta: meta,
				metrics: metrics.list,
				metricsByKey: metrics.byKey,
				trends: toArray(raw.trend_series || raw.trends),
				teams_summary: toArray(raw.teams_summary || raw.rows),
				alerts: toArray(raw.alerts || raw.anomalies),
			}
		},

		getTeamComparison: async function (filterState) {
			const res = await window.cskh.get(ENDPOINTS.teamComparison, buildPeriodParams(filterState))
			const raw = res || {}
			const meta = extractEnvelopeMeta(raw)
			const summary = normalizeMetricsCollection(raw.metrics || raw.summary_metrics || {}, meta)
			const rows = toArray(raw.rows || raw.teams).map(function (r) {
				const rowMetrics = r.metrics || {}
				const hasUnattr =
					r.is_unattributed_bucket || r.team_id === '__unattributed_historical_team__'
				if (hasUnattr) meta.has_unknown_historical_attribution = true
				return {
					team: hasUnattr ? 'historical_attribution_unknown' : r.team_id || r.team || '',
					team_label: hasUnattr ? '' : r.team_name || r.team_label || r.team_id || '',
					is_unattributed_bucket: !!hasUnattr,
					eligible_staff_count:
						r.active_staff_count !== undefined ? r.active_staff_count : r.eligible_staff_count,
					assigned_intervals: r.intervals_count !== undefined ? r.intervals_count : r.assigned_intervals,
					first_touch_on_time_rate:
						rowMetrics.FIRST_ATTEMPT_SLA_RATE || r.first_touch_on_time_rate,
					followup_on_time_rate: rowMetrics.FOLLOWUP_SLA_RATE || r.followup_on_time_rate,
					connected_rate: rowMetrics.CONNECTED_RATE || r.connected_rate,
					outcome_completeness_rate:
						rowMetrics.VALID_OUTCOME_LOGGING_RATE || r.outcome_completeness_rate,
					callback_completion_rate: rowMetrics.CALLBACK_COMPLETION_RATE,
					dnc_compliance_rate: rowMetrics.DNC_COMPLIANCE_RATE,
					process_compliance_score: rowMetrics.PROCESS_SCORE || r.process_compliance_score,
					score_status:
						(rowMetrics.PROCESS_SCORE && rowMetrics.PROCESS_SCORE.status) ||
						r.score_status ||
						'provisional',
				}
			})
			const pag = raw.pagination || {}
			return {
				meta: meta,
				summaryMetrics: summary.list,
				rows: rows,
				total: Number(pag.total !== undefined ? pag.total : rows.length),
			}
		},

		getEmployeeQuality: async function (filterState) {
			const res = await window.cskh.get(
				ENDPOINTS.employeeQuality,
				buildPeriodParams(filterState)
			)
			const raw = res || {}
			const meta = extractEnvelopeMeta(raw)
			const rows = toArray(raw.rows || raw.employees).map(function (r) {
				const rowMetrics = r.metrics || {}
				const pScore = rowMetrics.PROCESS_SCORE || r.process_compliance_score || {}
				const rawSt = String((pScore && pScore.status) || r.score_status || 'provisional').toLowerCase()
				const mappedSt = rawSt === 'final' || rawSt === 'ok' ? 'finalized' : rawSt
				const attBrk = r.attempt_breakdown || {}
				return {
					employee: r.employee_id || r.employee || '',
					employee_name: r.employee_name || r.full_name || r.employee_id || '',
					team: r.team_id || r.team || '',
					team_label: r.team_name || r.team_label || r.team_id || '',
					rank_in_group: r.rank_in_group !== undefined ? r.rank_in_group : null,
					score_status: mappedSt,
					process_compliance_score: pScore,
					first_touch_on_time_rate:
						rowMetrics.FIRST_ATTEMPT_SLA_RATE || r.first_touch_on_time_rate,
					followup_on_time_rate: rowMetrics.FOLLOWUP_SLA_RATE || r.followup_on_time_rate,
					callback_completion_rate:
						rowMetrics.CALLBACK_COMPLETION_RATE || r.eligible_contact_coverage,
					assigned_intervals:
						r.intervals_count !== undefined ? r.intervals_count : r.assigned_intervals,
					work_items_touched: r.work_items_touched,
					effective_attempts:
						attBrk.attempted !== undefined
							? attBrk.attempted
							: rowMetrics.CONTACT_ATTEMPT_COUNT || r.effective_attempts,
					connected_rate: rowMetrics.CONNECTED_RATE || r.connected_rate,
					outcome_completeness_rate:
						rowMetrics.VALID_OUTCOME_LOGGING_RATE || r.outcome_completeness_rate,
					dnc_compliance_rate: rowMetrics.DNC_COMPLIANCE_RATE,
					verified_count: attBrk.verified !== undefined ? attBrk.verified : 0,
				}
			})
			const pag = raw.pagination || {}
			return {
				meta: meta,
				rows: rows,
				total: Number(pag.total !== undefined ? pag.total : rows.length),
			}
		},

		getCampaignQuality: async function (filterState) {
			const res = await window.cskh.get(
				ENDPOINTS.campaignQuality,
				buildPeriodParams(filterState)
			)
			const raw = res || {}
			const meta = extractEnvelopeMeta(raw)
			const rows = toArray(raw.rows || raw.campaigns).map(function (r) {
				const rowMetrics = r.metrics || {}
				return {
					campaign: r.campaign_id || r.campaign || '',
					campaign_name: r.campaign_name || r.campaign_id || '',
					lead_source: r.lead_source || '',
					policy_version: r.policy_version || meta.policy_version,
					unique_assigned_work_items:
						r.distinct_work_items !== undefined
							? r.distinct_work_items
							: r.unique_assigned_work_items,
					ownership_intervals_count:
						r.ownership_intervals_count !== undefined ? r.ownership_intervals_count : null,
					dnc_compliance_rate: rowMetrics.DNC_COMPLIANCE_RATE || r.invalid_or_dnc_rate,
					work_item_first_touch_rate:
						rowMetrics.WORK_ITEM_FIRST_ATTEMPT_SLA_RATE || r.first_touch_on_time_rate,
					first_touch_on_time_rate:
						rowMetrics.FIRST_ATTEMPT_SLA_RATE || r.first_touch_on_time_rate,
					followup_on_time_rate: rowMetrics.FOLLOWUP_SLA_RATE || r.followup_on_time_rate,
					connected_rate: rowMetrics.CONNECTED_RATE || r.connected_rate,
					first_connected_rate:
						rowMetrics.FIRST_CONNECTED_TRACKING_RATE || r.campaign_conversion_rate,
				}
			})
			const pag = raw.pagination || {}
			return {
				meta: meta,
				rows: rows,
				total: Number(pag.total !== undefined ? pag.total : rows.length),
			}
		},

		getSlaOperations: async function (filterState) {
			const caps = window.cskh.capabilities()
			const s = filterState || {}

			// Per `report-api-contract.md` §2: `sla.list_obligations` denies BOD (`403`)
			// to prevent customer profile exposure; BOD views 0-PII aggregate SLA + redacted drilldown.
			if (caps.bod && !caps.admin) {
				const [ovRaw, ddRaw] = await Promise.all([
					window.cskh.get(ENDPOINTS.executiveOverview, buildPeriodParams(s)),
					this.drilldownMetric(
						Object.assign({}, s, {
							metric_key:
								s.obligation_type === 'FOLLOWUP'
									? 'FOLLOWUP_SLA_RATE'
									: 'FIRST_ATTEMPT_SLA_RATE',
							segment: s.sla_status === 'fulfilled_on_time' ? 'met' : 'unmet',
							page: s.page || 1,
							page_size: s.page_size || 20,
						})
					),
				])
				const meta = extractEnvelopeMeta(ovRaw || {})
				const ovMetrics = normalizeMetricsCollection((ovRaw && ovRaw.metrics) || {}, meta)
				const slaMetrics = ovMetrics.list.filter(function (m) {
					return m.pillar === 'compliance'
				})
				return {
					meta: meta,
					summaryMetrics: slaMetrics,
					rows: ddRaw.rows,
					total: ddRaw.total,
				}
			}

			const obParams = {
				page: Number(s.page || 1),
				page_size: Number(s.page_size || 20),
			}
			if (s.sla_status) obParams.status = s.sla_status
			if (s.obligation_type) obParams.obligation_type = s.obligation_type
			if (s.search) obParams.work_item = s.search

			const [obRaw, ovRaw] = await Promise.all([
				window.cskh.get(ENDPOINTS.slaListObligations, obParams),
				window.cskh.get(ENDPOINTS.executiveOverview, buildPeriodParams(s)),
			])
			const meta = extractEnvelopeMeta(ovRaw || obRaw || {})
			const ovMetrics = normalizeMetricsCollection((ovRaw && ovRaw.metrics) || {}, meta)
			const slaMetrics = ovMetrics.list.filter(function (m) {
				return m.pillar === 'compliance'
			})
			const rawRows = toArray((obRaw && (obRaw.rows || obRaw.obligations || obRaw.items)) || [])
			const pag = (obRaw && obRaw.pagination) || {}
			return {
				meta: meta,
				summaryMetrics: slaMetrics,
				rows: rawRows.map(normalizeObligationRow),
				total: Number(obRaw && obRaw.total !== undefined ? obRaw.total : pag.total !== undefined ? pag.total : rawRows.length),
			}
		},

		waiveSlaObligation: async function (payload) {
			return window.cskh.post(ENDPOINTS.waiveSlaObligation, {
				obligation_id: payload.obligation_id,
				reason: payload.reason,
			})
		},

		getPersonalScorecard: async function (filterState) {
			const res = await window.cskh.get(
				ENDPOINTS.personalScorecard,
				buildPeriodParams(filterState)
			)
			const raw = res || {}
			const meta = extractEnvelopeMeta(raw)
			meta.scope = 'self'
			const metrics = normalizeMetricsCollection(raw.metrics || {}, meta)
			const rawComps = toArray(raw.score_components || raw.components).map(function (c) {
				const code = c.component_code || c.metric_key || c.key || ''
				const mappedMetricKey = SCORE_COMPONENT_METRIC_MAP[code] || code
				const linked = metrics.byKey[mappedMetricKey] || null
				return {
					key: code,
					metric_key: mappedMetricKey,
					value: c.rate_value !== undefined ? c.rate_value : c.value,
					unit: 'percent',
					numerator:
						c.numerator !== undefined
							? c.numerator
							: linked
								? linked.numerator
								: null,
					denominator:
						c.denominator !== undefined
							? c.denominator
							: linked
								? linked.denominator
								: null,
					weight:
						c.configured_weight !== undefined
							? c.configured_weight
							: c.weight,
					weighted_contribution:
						c.weighted_points !== undefined
							? c.weighted_points
							: c.weighted_contribution,
					status: c.status,
				}
			})
			const activeObs = toArray(
				raw.active_obligations || raw.actionable_obligations || raw.obligations
			).map(normalizeObligationRow)
			return {
				meta: meta,
				score:
					metrics.byKey.PROCESS_SCORE ||
					(raw.score ? normalizeMetric('PROCESS_SCORE', raw.score, meta) : null),
				components: rawComps,
				metrics: metrics.list,
				metricsByKey: metrics.byKey,
				actionable_obligations: activeObs,
			}
		},

		drilldownMetric: async function (args) {
			const params = buildPeriodParams(args)
			const scope = resolveScopePair(args)
			params.metric_code = args.metric_key || args.metric_code
			params.scope_type = args.scope_type || scope.scope_type
			if (args.scope_id || scope.scope_id) {
				params.scope_id = args.scope_id || scope.scope_id
			}
			params.filter_bucket = mapSegmentToBucket(args.segment || args.filter_bucket || 'all')
			params.page = Number(args.page || 1)
			params.page_size = Number(args.page_size || 20)

			const res = await window.cskh.get(ENDPOINTS.drilldown, params)
			const raw = res || {}
			const meta = extractEnvelopeMeta(raw)
			const pag = raw.pagination || {}
			const bucketCounts = raw.bucket_counts || raw.segment_counts || {}
			const metricObj = raw.metric
				? normalizeMetric(params.metric_code, raw.metric, meta)
				: normalizeMetric(params.metric_code, raw, meta)

			return {
				meta: meta,
				metric: metricObj,
				segment: args.segment || 'all',
				segment_counts: {
					all:
						bucketCounts.all_denominator !== undefined
							? Number(bucketCounts.all_denominator)
							: metricObj.denominator,
					met:
						bucketCounts.numerator_only !== undefined
							? Number(bucketCounts.numerator_only)
							: metricObj.numerator,
					unmet:
						bucketCounts.breached_only !== undefined
							? Number(bucketCounts.breached_only)
							: metricObj.denominator !== null && metricObj.numerator !== null
								? Math.max(0, metricObj.denominator - metricObj.numerator)
								: null,
					excluded:
						bucketCounts.excluded !== undefined ? Number(bucketCounts.excluded) : null,
				},
				rows: toArray(raw.rows || raw.items).map(normalizeObligationRow),
				total: Number(raw.total !== undefined ? raw.total : pag.total || 0),
				page: Number(pag.page || params.page),
				page_size: Number(pag.page_size || params.page_size),
			}
		},

		requestRecompute: async function (payload) {
			const params = buildPeriodParams(payload)
			const scope = resolveScopePair(payload)
			params.scope_type = payload.scope_type || scope.scope_type
			if (payload.scope_id || scope.scope_id) {
				params.scope_id = payload.scope_id || scope.scope_id
			}
			params.reason = payload.reason || ''
			return window.cskh.post(ENDPOINTS.requestRecompute, params)
		},

		listRevisions: async function (filterState, currentRunId) {
			const t = window.cskh ? window.cskh.t : function (s) { return s }
			function normalizeRunRow(r, idx) {
				const isRestated = toBool(r.is_restated, false) || r.run_mode === 'restated'
				const modeLabel =
					r.run_mode === 'restated'
						? t('Restated after prior-period adjustment')
						: r.run_mode === 'backfill'
							? t('Historical backfill')
							: t('Incremental recompute')
				return {
					run_id: r.run_id || r.name || '—',
					revision: r.revision || (idx !== undefined ? 'v' + (idx + 1) : r.run_mode || '1'),
					status: isRestated
						? 'restated'
						: r.status === 'completed' || r.status === 'ok'
							? 'fresh'
							: r.status || 'fresh',
					as_of: r.as_of || null,
					finished_at: r.completed_at || r.finished_at || r.as_of || null,
					triggered_by: r.requested_by || r.triggered_by || t('System / Automated'),
					delta_note: r.restated_from_run
						? t('Restated from run') + ': ' + r.restated_from_run
						: r.delta_note || modeLabel,
				}
			}

			let seedRunId = currentRunId || ''
			let fallbackFreshness = null
			if (!seedRunId) {
				try {
					const ov = await window.cskh.get(
						ENDPOINTS.executiveOverview,
						buildPeriodParams(filterState)
					)
					fallbackFreshness = (ov && ov.freshness) || null
					if (fallbackFreshness && fallbackFreshness.run_id) {
						seedRunId = fallbackFreshness.run_id
					}
				} catch (_err) {
					/* ignore */
				}
			}

			if (seedRunId) {
				const chain = []
				const seen = {}
				let nextId = seedRunId
				while (nextId && !seen[nextId] && chain.length < 5) {
					seen[nextId] = true
					try {
						const runInfo = await window.cskh.get(ENDPOINTS.getRunStatus, {
							run_id: nextId,
						})
						if (!runInfo) break
						if (Array.isArray(runInfo.rows || runInfo.history)) {
							const arr = runInfo.rows || runInfo.history
							return { rows: arr.map(normalizeRunRow) }
						}
						chain.push(runInfo)
						nextId = runInfo.restated_from_run || ''
					} catch (_err) {
						break
					}
				}
				if (chain.length) {
					return {
						rows: chain.map(function (r, idx) {
							return normalizeRunRow(r, chain.length - 1 - idx)
						}),
					}
				}
			}

			if (fallbackFreshness && fallbackFreshness.run_id) {
				return {
					rows: [
						normalizeRunRow(
							{
								run_id: fallbackFreshness.run_id,
								run_mode: fallbackFreshness.run_mode,
								is_restated: fallbackFreshness.is_restated,
								status: fallbackFreshness.snapshot_status || 'fresh',
								as_of: fallbackFreshness.as_of,
								completed_at: fallbackFreshness.as_of,
								restated_from_run: fallbackFreshness.restated_from_run,
							},
							0
						),
					],
				}
			}
			return { rows: [] }
		},

		createExport: async function (payload) {
			const params = buildPeriodParams(payload.filters || {})
			const scope = resolveScopePair(payload.filters || {})
			const reportTypeMap = {
				quality_overview: 'executive_overview',
				executive_overview: 'executive_overview',
				team_quality: 'team_comparison',
				team_comparison: 'team_comparison',
				employee_quality: 'employee_quality',
				campaign_quality: 'campaign_quality',
				sla_operations: 'metric_drilldown',
				personal_scorecard: 'my_scorecard',
				my_scorecard: 'my_scorecard',
			}
			params.report_type = reportTypeMap[payload.report_key] || payload.report_key || 'executive_overview'
			params.scope_type = scope.scope_type
			if (scope.scope_id) params.scope_id = scope.scope_id
			if (params.report_type === 'metric_drilldown') {
				params.metric_code = payload.metric_code || 'FIRST_ATTEMPT_SLA_RATE'
			}
			params.format = payload.export_format || 'csv'
			params.purpose = payload.purpose || ''
			const res = await window.cskh.post(ENDPOINTS.createExport, params)
			return this.normalizeExportJob(res)
		},

		getExportStatus: async function (exportId) {
			const res = await window.cskh.get(ENDPOINTS.getExportStatus, { export_id: exportId })
			return this.normalizeExportJob(res)
		},

		normalizeExportJob: function (raw) {
			if (!raw) return null
			const exportId = raw.export_id || raw.name || raw.id || ''
			const status = String(raw.status || 'queued').toLowerCase()
			const downloadUrl =
				raw.download_endpoint ||
				raw.download_url ||
				(exportId
					? ENDPOINTS.downloadExportPath + '?export_id=' + encodeURIComponent(exportId)
					: '')
			return {
				export_id: exportId,
				report_key: raw.report_type || raw.report_key || '',
				format: raw.format || raw.export_format || 'csv',
				status: status,
				row_count:
					raw.row_count !== undefined && raw.row_count !== null ? Number(raw.row_count) : null,
				expires_at: raw.expires_at || '',
				expires_in_seconds: raw.expires_in_seconds || null,
				created_at: raw.created_at || raw.creation || '',
				error_message: raw.error_message || raw.error || '',
				download_url: downloadUrl,
			}
		},
	}

	function normalizeObligationRow(r) {
		const row = r || {}
		const piiRedacted = !!(
			row.pii_redacted ||
			row.pii_redacted_due_to_transfer ||
			row.lead_ref === 'REDACTED'
		)
		const teamVal =
			row.attribution_status === 'historical_attribution_unknown' ||
			row.team_at_obligation === '__unattributed_historical_team__' ||
			row.team_at_event === 'historical_attribution_unknown'
				? 'historical_attribution_unknown'
				: row.team_name || row.team_at_obligation || row.team_at_event || row.team || ''
		return {
			obligation_id: row.obligation_id || row.name || row.attempt_id || '',
			work_item: row.work_item || row.work_item_id || '',
			obligation_type: row.obligation_type || '',
			responsible_user: row.responsible_user || row.employee || row.owner_user || '',
			team_at_event: teamVal,
			pii_redacted: piiRedacted,
			customer_ref: row.customer_name || row.lead_ref || row.lead || row.customer_ref || '',
			original_due_at: row.original_due_at || '',
			effective_due_at: row.effective_due_at || row.due_at || row.attempt_at || '',
			fulfilled_at: row.fulfilled_at || '',
			breached_at: row.breached_at || '',
			sla_status: row.status || row.sla_status || '',
			waiver_reason:
				row.waive_reason || row.waiver_reason || row.exclusion_reason || row.reason || '',
		}
	}
})()
