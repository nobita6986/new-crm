/* P3 Reporting, SLA, Drill-Down & Export API Adapter (`window.cskhP3Api`).

   Invariants enforced here (P3.md §0.13, §0.15, §5, §11, §12):
     * Frontend NEVER defines or computes KPI formulas, weights, or rates:
       every metric `value`, `numerator`, `denominator`, `status`, `grain`,
       `formula`, `exclusions`, `as_of`, `calculated_at`, `source_watermark`,
       and `version` comes strictly from the authoritative backend contract.
     * `denominator === 0` or `status === 'insufficient_data'` or `value === null`
       is flagged `is_na: true` so UI renders `N/A` (never `0%`).
     * Drill-down and tables pass pagination/filter/sort parameters to the server
       before `LIMIT/OFFSET` — never fetching full datasets to slice in browser.
     * Exports are queued and downloaded strictly through server-authorized jobs
       with `expires_at` and server-enforced permission checks.
*/

window.cskhP3Api = (function () {
	'use strict'

	const ENDPOINTS = {
		filterOptions: 'crm_cskh.api.reporting.get_filter_options',
		executiveOverview: 'crm_cskh.api.reporting.get_bod_executive_overview',
		teamComparison: 'crm_cskh.api.reporting.get_team_comparison',
		employeeQuality: 'crm_cskh.api.reporting.get_employee_quality_table',
		campaignQuality: 'crm_cskh.api.reporting.get_campaign_quality_report',
		slaOperations: 'crm_cskh.api.sla.list_obligations',
		waiveSlaObligation: 'crm_cskh.api.sla.waive_obligation',
		personalScorecard: 'crm_cskh.api.reporting.get_my_scorecard',
		drilldown: 'crm_cskh.api.reporting.drilldown_metric',
		requestRecompute: 'crm_cskh.api.reporting.request_recompute',
		listRevisions: 'crm_cskh.api.reporting.list_snapshot_revisions',
		createExport: 'crm_cskh.api.reporting.create_report_export',
		getExportStatus: 'crm_cskh.api.reporting.get_report_export_status',
		downloadExportPath: '/api/method/crm_cskh.api.reporting.download_report_export',
	}

	const METRIC_CATALOG_DEFAULTS = {
		process_compliance_score: {
			label: 'Process compliance score',
			pillar: 'compliance',
			unit: 'score',
			grain: 'ownership_interval',
			time_basis: 'deadline_cohort',
			formula: 'Weighted sum over valid components divided by active weight sum.',
			drillable: true,
		},
		first_touch_on_time_rate: {
			label: 'First-touch on-time rate',
			pillar: 'compliance',
			unit: 'percent',
			grain: 'sla_obligation',
			time_basis: 'deadline_cohort',
			formula:
				'On-time FIRST_ATTEMPT obligations divided by due eligible FIRST_ATTEMPT obligations in period.',
			drillable: true,
		},
		followup_on_time_rate: {
			label: 'Follow-up on-time rate',
			pillar: 'compliance',
			unit: 'percent',
			grain: 'sla_obligation',
			time_basis: 'deadline_cohort',
			formula:
				'On-time FOLLOWUP obligations divided by due eligible FOLLOWUP obligations in period.',
			drillable: true,
		},
		eligible_contact_coverage: {
			label: 'Eligible contact coverage',
			pillar: 'compliance',
			unit: 'percent',
			grain: 'ownership_interval',
			time_basis: 'event_time',
			formula:
				'Eligible ownership intervals with >= 1 effective attempt divided by mature eligible intervals.',
			drillable: true,
		},
		min_attempts_compliance_rate: {
			label: 'Minimum attempts compliance',
			pillar: 'compliance',
			unit: 'percent',
			grain: 'sla_obligation',
			time_basis: 'deadline_cohort',
			formula: 'Fulfilled MIN_ATTEMPTS obligations divided by due MIN_ATTEMPTS obligations.',
			drillable: true,
		},
		overdue_work_item_backlog: {
			label: 'Overdue work item backlog',
			pillar: 'compliance',
			unit: 'count',
			grain: 'distinct_work_item',
			time_basis: 'as_of_point_in_time',
			formula: 'Active distinct work items holding >= 1 open overdue SLA obligation at as_of.',
			drillable: true,
		},
		overdue_followup_count: {
			label: 'Overdue open follow-up obligations',
			pillar: 'compliance',
			unit: 'count',
			grain: 'sla_obligation',
			time_basis: 'as_of_point_in_time',
			formula: 'Open overdue FOLLOWUP SLA obligations at as_of.',
			drillable: true,
		},
		sla_breach_count: {
			label: 'SLA breach count',
			pillar: 'compliance',
			unit: 'count',
			grain: 'sla_obligation',
			time_basis: 'deadline_cohort',
			formula: 'Obligations in breached_open or fulfilled_late within period.',
			drillable: true,
		},
		sla_waived_count: {
			label: 'Valid SLA waiver count',
			pillar: 'compliance',
			unit: 'count',
			grain: 'sla_obligation',
			time_basis: 'deadline_cohort',
			formula: 'Approved waived SLA obligations within period.',
			drillable: true,
		},
		first_touch_latency_business: {
			label: 'First-touch latency (business minutes, p50 / p90)',
			pillar: 'compliance',
			unit: 'minutes_business',
			grain: 'ownership_interval',
			time_basis: 'event_time',
			formula: 'p50 / p90 business-hour minutes from interval start to first effective attempt.',
			drillable: false,
		},
		first_touch_latency_wall: {
			label: 'First-touch latency (wall minutes, p50 / p90)',
			pillar: 'compliance',
			unit: 'minutes_wall',
			grain: 'ownership_interval',
			time_basis: 'event_time',
			formula: 'p50 / p90 wall-clock minutes from interval start to first effective attempt.',
			drillable: false,
		},
		unique_assigned_work_items: {
			label: 'Unique assigned work items',
			pillar: 'connection',
			unit: 'count',
			grain: 'distinct_work_item',
			time_basis: 'event_time',
			formula: 'DISTINCT Work Items active in period (company/campaign grain).',
			drillable: true,
		},
		assigned_intervals: {
			label: 'Assigned intervals',
			pillar: 'connection',
			unit: 'count',
			grain: 'ownership_interval',
			time_basis: 'event_time',
			formula: 'Ownership intervals active in period (employee accountability grain).',
			drillable: true,
		},
		effective_attempts: {
			label: 'Effective attempts (activity reference)',
			pillar: 'connection',
			unit: 'count',
			grain: 'effective_attempt',
			time_basis: 'event_time',
			formula: 'Effective non-voided contact attempts in period (activity reference only).',
			drillable: false,
		},
		connected_work_items: {
			label: 'Connected work items',
			pillar: 'connection',
			unit: 'count',
			grain: 'distinct_work_item',
			time_basis: 'event_time',
			formula: 'Distinct work items with >= 1 effective connected attempt in period.',
			drillable: true,
		},
		connected_rate: {
			label: 'Connected rate',
			pillar: 'connection',
			unit: 'percent',
			grain: 'ownership_interval',
			time_basis: 'event_time',
			formula:
				'Eligible intervals with >= 1 connected attempt divided by eligible intervals with >= 1 attempt.',
			drillable: true,
		},
		first_connected_rate: {
			label: 'First-connected tracking (informational, non-penalizing)',
			pillar: 'connection',
			unit: 'percent',
			grain: 'sla_obligation',
			time_basis: 'deadline_cohort',
			formula: 'Informational FIRST_CONNECTED tracking rate (never penalizes Process Score).',
			drillable: false,
		},
		campaign_conversion_rate: {
			label: 'Campaign conversion rate',
			pillar: 'connection',
			unit: 'percent',
			grain: 'distinct_work_item',
			time_basis: 'event_time',
			formula: 'Distinct work items achieving campaign conversion divided by campaign eligible cohort.',
			drillable: true,
		},
		outcome_completeness_rate: {
			label: 'Outcome completeness rate',
			pillar: 'reliability',
			unit: 'percent',
			grain: 'effective_attempt',
			time_basis: 'event_time',
			formula:
				'Effective attempts satisfying required fields divided by total effective attempts in period.',
			drillable: true,
		},
		qa_pass_rate: {
			label: 'QA pass rate',
			pillar: 'reliability',
			unit: 'percent',
			grain: 'qa_review',
			time_basis: 'event_time',
			formula: 'Approved passing QA reviews divided by completed eligible QA reviews in cohort.',
			drillable: false,
		},
		verified_result_rate: {
			label: 'Verified result rate',
			pillar: 'reliability',
			unit: 'percent',
			grain: 'effective_attempt',
			time_basis: 'event_time',
			formula: 'Verified effective attempts divided by total effective attempts in period.',
			drillable: true,
		},
	}

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
		const meta = (raw && (raw.meta || raw.snapshot || raw.provenance)) || raw || {}
		return {
			scope: meta.scope || raw.scope || 'system',
			period_type: meta.period_type || raw.period_type || 'month',
			from_date: meta.from_date || meta.period_from || raw.from_date || '',
			to_date_exclusive:
				meta.to_date_exclusive || meta.period_to_exclusive || raw.to_date_exclusive || '',
			timezone: meta.timezone || raw.timezone || window.cskh.siteTimeZone(),
			as_of: meta.as_of || raw.as_of || '',
			calculated_at: meta.calculated_at || raw.calculated_at || '',
			source_watermark: meta.source_watermark || raw.source_watermark || '',
			freshness_status:
				meta.freshness_status || meta.status || raw.freshness_status || raw.status || 'fresh',
			snapshot_revision: meta.snapshot_revision || meta.revision || raw.snapshot_revision || '',
			metric_version: meta.metric_version || raw.metric_version || 'p3.v1',
			policy_version: meta.policy_version || raw.policy_version || 'DEV-DEFAULT',
			qa_enabled: toBool(
				meta.qa_enabled !== undefined ? meta.qa_enabled : raw.qa_enabled,
				window.cskh.capabilities().qa_enabled
			),
			is_dev_default_policy: toBool(
				meta.is_dev_default_policy !== undefined
					? meta.is_dev_default_policy
					: raw.is_dev_default_policy,
				true
			),
			has_unknown_historical_attribution: toBool(
				meta.has_unknown_historical_attribution !== undefined
					? meta.has_unknown_historical_attribution
					: raw.has_unknown_historical_attribution,
				false
			),
			has_multiple_policies: toBool(
				meta.has_multiple_policies !== undefined
					? meta.has_multiple_policies
					: raw.has_multiple_policies,
				false
			),
		}
	}

	/**
	 * Normalize a single KPI metric object WITHOUT computing any KPI math.
	 * Enforces `denominator === 0` / `value === null` -> `is_na: true`.
	 */
	function normalizeMetric(metricKey, rawMetric, envelopeMeta) {
		const defaults = METRIC_CATALOG_DEFAULTS[metricKey] || {}
		const env = envelopeMeta || {}
		const raw =
			rawMetric && typeof rawMetric === 'object' && !Array.isArray(rawMetric)
				? rawMetric
				: { value: rawMetric }

		const key = raw.metric_key || raw.key || metricKey || ''
		const unit = raw.unit || defaults.unit || 'percent'
		const numerator =
			raw.numerator !== undefined && raw.numerator !== null ? Number(raw.numerator) : null
		const denominator =
			raw.denominator !== undefined && raw.denominator !== null ? Number(raw.denominator) : null

		const status = String(
			raw.status || raw.confidence_status || env.freshness_status || 'fresh'
		).toLowerCase()

		const isRateOrScore = unit === 'percent' || unit === 'score'
		const zeroDenominator = denominator !== null && denominator === 0
		const missingValue = raw.value === undefined || raw.value === null || raw.value === 'N/A'
		const hasLatencyPair =
			(unit === 'minutes_business' || unit === 'minutes_wall') &&
			raw.p50 !== undefined &&
			raw.p50 !== null

		const isNa =
			toBool(raw.is_na, false) ||
			status === 'insufficient_data' ||
			(isRateOrScore && zeroDenominator) ||
			(missingValue && !hasLatencyPair)

		return {
			key: key,
			label: raw.label || defaults.label || key,
			pillar: raw.pillar || defaults.pillar || 'compliance',
			unit: unit,
			grain: raw.grain || defaults.grain || 'ownership_interval',
			value: isNa ? null : raw.value !== undefined ? raw.value : null,
			p50: raw.p50 !== undefined ? raw.p50 : null,
			p90: raw.p90 !== undefined ? raw.p90 : null,
			numerator: numerator,
			denominator: denominator,
			valid_weight_sum:
				raw.valid_weight_sum !== undefined ? Number(raw.valid_weight_sum) : null,
			is_na: isNa,
			na_reason:
				raw.na_reason ||
				(!env.qa_enabled && key === 'qa_pass_rate'
					? 'qa_disabled'
					: zeroDenominator
						? 'zero_denominator'
						: ''),
			formula: raw.formula || defaults.formula || '',
			period_label:
				raw.period_label ||
				(env.from_date && env.to_date_exclusive
					? '[' + env.from_date + ', ' + env.to_date_exclusive + ')'
					: ''),
			time_basis: raw.time_basis || defaults.time_basis || 'deadline_cohort',
			scope: raw.scope || env.scope || 'system',
			exclusions: toArray(raw.exclusions),
			excluded_count:
				raw.excluded_count !== undefined && raw.excluded_count !== null
					? Number(raw.excluded_count)
					: null,
			as_of: raw.as_of || env.as_of || '',
			calculated_at: raw.calculated_at || env.calculated_at || '',
			source_watermark: raw.source_watermark || env.source_watermark || '',
			metric_version: raw.metric_version || env.metric_version || 'p3.v1',
			policy_version: raw.policy_version || env.policy_version || 'DEV-DEFAULT',
			status: isNa && status === 'fresh' ? 'insufficient_data' : status,
			drillable:
				raw.drillable !== undefined ? toBool(raw.drillable, false) : !!defaults.drillable,
		}
	}

	function normalizeMetricsCollection(rawMetrics, envMeta) {
		const list = []
		const byKey = {}
		if (Array.isArray(rawMetrics)) {
			rawMetrics.forEach(function (item) {
				if (!item) return
				const m = normalizeMetric(item.metric_key || item.key, item, envMeta)
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
			s.period_type || 'month',
			s.anchor || '',
			s.custom_to || '',
			window.cskh.siteTimeZone()
		)
		const params = {
			period_type: bounds.periodType,
			from_date: bounds.fromDate,
			to_date_exclusive: bounds.toDateExclusive,
			period_from: bounds.fromServer,
			period_to_exclusive: bounds.toExclusiveServer,
		}
		if (s.campaign) params.campaign = s.campaign
		if (s.lead_source) params.lead_source = s.lead_source
		if (s.team) params.team = s.team
		if (s.employee) params.employee = s.employee
		if (s.snapshot_revision) params.snapshot_revision = s.snapshot_revision
		if (s.include_inactive !== undefined) params.include_inactive = s.include_inactive ? 1 : 0
		if (s.score_group) params.score_group = s.score_group
		if (s.obligation_type) params.obligation_type = s.obligation_type
		if (s.sla_status) params.sla_status = s.sla_status
		if (s.search) params.search = s.search
		if (s.sort_by) params.sort_by = s.sort_by
		if (s.sort_dir) params.sort_dir = s.sort_dir
		if (s.page) params.page = Number(s.page)
		if (s.page_size) params.page_size = Number(s.page_size)
		return params
	}

	return {
		ENDPOINTS: ENDPOINTS,
		METRIC_CATALOG_DEFAULTS: METRIC_CATALOG_DEFAULTS,
		extractEnvelopeMeta: extractEnvelopeMeta,
		normalizeMetric: normalizeMetric,
		normalizeMetricsCollection: normalizeMetricsCollection,
		buildPeriodParams: buildPeriodParams,

		getFilterOptions: async function (args) {
			const res = await window.cskh.get(ENDPOINTS.filterOptions, args || {})
			const data = res || {}
			return {
				campaigns: toArray(data.campaigns),
				teams: toArray(data.teams),
				employees: toArray(data.employees),
				lead_sources: toArray(data.lead_sources),
				revisions: toArray(data.revisions || data.snapshot_revisions),
			}
		},

		getExecutiveOverview: async function (filterState) {
			const res = await window.cskh.get(
				ENDPOINTS.executiveOverview,
				buildPeriodParams(filterState)
			)
			const raw = res || {}
			const meta = extractEnvelopeMeta(raw)
			const metrics = normalizeMetricsCollection(raw.metrics || raw.kpis || {}, meta)
			return {
				meta: meta,
				metrics: metrics.list,
				metricsByKey: metrics.byKey,
				trends: toArray(raw.trends || raw.trend_series),
				teams_summary: toArray(raw.teams_summary || raw.teams),
				campaigns_summary: toArray(raw.campaigns_summary || raw.campaigns),
				alerts: toArray(raw.alerts || raw.anomalies),
			}
		},

		getTeamComparison: async function (filterState) {
			const res = await window.cskh.get(ENDPOINTS.teamComparison, buildPeriodParams(filterState))
			const raw = res || {}
			const meta = extractEnvelopeMeta(raw)
			const summary = normalizeMetricsCollection(raw.summary_metrics || raw.metrics || {}, meta)
			return {
				meta: meta,
				summaryMetrics: summary.list,
				rows: toArray(raw.rows || raw.teams || raw.items),
				total: Number(raw.total !== undefined ? raw.total : toArray(raw.rows || raw.teams).length),
			}
		},

		getEmployeeQuality: async function (filterState) {
			const res = await window.cskh.get(
				ENDPOINTS.employeeQuality,
				buildPeriodParams(filterState)
			)
			const raw = res || {}
			const meta = extractEnvelopeMeta(raw)
			return {
				meta: meta,
				rows: toArray(raw.rows || raw.employees || raw.items),
				total: Number(
					raw.total !== undefined ? raw.total : toArray(raw.rows || raw.employees).length
				),
				group_counts: raw.group_counts || {},
			}
		},

		getCampaignQuality: async function (filterState) {
			const res = await window.cskh.get(
				ENDPOINTS.campaignQuality,
				buildPeriodParams(filterState)
			)
			const raw = res || {}
			const meta = extractEnvelopeMeta(raw)
			return {
				meta: meta,
				rows: toArray(raw.rows || raw.campaigns || raw.items),
				source_rows: toArray(raw.source_rows || raw.lead_sources),
				total: Number(
					raw.total !== undefined ? raw.total : toArray(raw.rows || raw.campaigns).length
				),
			}
		},

		getSlaOperations: async function (filterState) {
			const res = await window.cskh.get(ENDPOINTS.slaOperations, buildPeriodParams(filterState))
			const raw = res || {}
			const meta = extractEnvelopeMeta(raw)
			const summary = normalizeMetricsCollection(raw.summary_metrics || raw.metrics || {}, meta)
			return {
				meta: meta,
				summaryMetrics: summary.list,
				rows: toArray(raw.rows || raw.obligations || raw.items),
				total: Number(
					raw.total !== undefined ? raw.total : toArray(raw.rows || raw.obligations).length
				),
			}
		},

		waiveSlaObligation: async function (payload) {
			return window.cskh.post(ENDPOINTS.waiveSlaObligation, {
				obligation_id: payload.obligation_id,
				reason: payload.reason,
				idempotency_key: payload.idempotency_key || window.cskh.uuid(),
			})
		},

		getPersonalScorecard: async function (filterState) {
			const res = await window.cskh.get(
				ENDPOINTS.personalScorecard,
				buildPeriodParams(filterState)
			)
			const raw = res || {}
			const meta = extractEnvelopeMeta(raw)
			const metrics = normalizeMetricsCollection(raw.metrics || raw.kpis || {}, meta)
			return {
				meta: meta,
				score: raw.score
					? normalizeMetric('process_compliance_score', raw.score, meta)
					: metrics.byKey.process_compliance_score || null,
				components: toArray(raw.components || raw.score_components),
				metrics: metrics.list,
				metricsByKey: metrics.byKey,
				actionable_obligations: toArray(
					raw.actionable_obligations || raw.open_obligations || raw.obligations
				),
			}
		},

		drilldownMetric: async function (args) {
			const params = buildPeriodParams(args)
			params.metric_key = args.metric_key
			params.segment = args.segment || 'all'
			params.page = Number(args.page || 1)
			params.page_size = Number(args.page_size || 20)
			if (args.search) params.search = args.search
			if (args.sort_by) params.sort_by = args.sort_by
			if (args.sort_dir) params.sort_dir = args.sort_dir

			const res = await window.cskh.get(ENDPOINTS.drilldown, params)
			const raw = res || {}
			const meta = extractEnvelopeMeta(raw)
			return {
				meta: meta,
				metric: raw.metric
					? normalizeMetric(args.metric_key, raw.metric, meta)
					: normalizeMetric(args.metric_key, raw, meta),
				segment: raw.segment || args.segment || 'all',
				segment_counts: raw.segment_counts || {
					all: raw.cohort_total !== undefined ? Number(raw.cohort_total) : null,
					met: raw.met_count !== undefined ? Number(raw.met_count) : null,
					unmet: raw.unmet_count !== undefined ? Number(raw.unmet_count) : null,
					excluded: raw.excluded_count !== undefined ? Number(raw.excluded_count) : null,
				},
				rows: toArray(raw.rows || raw.items),
				total: Number(raw.total || 0),
				page: Number(raw.page || params.page),
				page_size: Number(raw.page_size || params.page_size),
			}
		},

		requestRecompute: async function (payload) {
			const params = buildPeriodParams(payload)
			params.reason = payload.reason || ''
			params.idempotency_key = payload.idempotency_key || window.cskh.uuid()
			return window.cskh.post(ENDPOINTS.requestRecompute, params)
		},

		listRevisions: async function (filterState) {
			const res = await window.cskh.get(ENDPOINTS.listRevisions, buildPeriodParams(filterState))
			const raw = res || {}
			return {
				rows: toArray(raw.rows || raw.revisions || raw.runs || raw),
			}
		},

		createExport: async function (payload) {
			const params = buildPeriodParams(payload.filters || {})
			params.report_key = payload.report_key || 'quality_overview'
			params.export_format = payload.export_format || 'csv'
			params.format = payload.export_format || 'csv'
			params.purpose = payload.purpose || ''
			params.idempotency_key = payload.idempotency_key || window.cskh.uuid()
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
			const status = String(raw.status || 'running').toLowerCase()
			const downloadUrl =
				raw.download_url ||
				(exportId
					? ENDPOINTS.downloadExportPath + '?export_id=' + encodeURIComponent(exportId)
					: '')
			return {
				export_id: exportId,
				report_key: raw.report_key || '',
				format: raw.export_format || raw.format || 'csv',
				status: status,
				row_count: raw.row_count !== undefined && raw.row_count !== null ? Number(raw.row_count) : null,
				expires_at: raw.expires_at || '',
				created_at: raw.created_at || raw.creation || '',
				error_message: raw.error_message || raw.error || '',
				download_url: downloadUrl,
			}
		},
	}
})()
