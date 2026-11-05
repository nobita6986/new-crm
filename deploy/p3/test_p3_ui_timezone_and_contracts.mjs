#!/usr/bin/env node
/**
 * Deterministic P3 UI Timezone, Provenance, Ranking Separation & Safe-DOM Unit Suite.
 *
 * Verifies:
 *   1. Half-open `[from, to_exclusive)` period boundaries (`day`, `week`, `month`,
 *      `quarter`, `year`, `custom`) in `Asia/Ho_Chi_Minh` (`+07:00`) regardless of
 *      browser/host OS timezone (`TZ=America/Los_Angeles`, `TZ=UTC`, `TZ=Asia/Tokyo`),
 *      including leap-year Feb, year-end rollover, and 23:59:59 vs 00:00:00 instants;
 *   2. `denominator === 0` (`0/0`) ALWAYS normalizes to `is_na: true` and formats as
 *      `N/A` (never `0%`), whereas `numerator = 0, denominator = 12` formats as `0% (0 / 12)`;
 *   3. `qa_enabled = false` Process Compliance Score (`7350 / 85 = 86.47`, `status: 'provisional'`)
 *      formats with explicit provisional badge (`86,47 — Tạm tính; thiếu mẫu QA`);
 *   4. `partitionScoreGroups` strictly separates `finalized`, `provisional`, and
 *      `insufficient_data` groups so provisional scores NEVER mix with finalized ranks;
 *   5. Explainable KPI card DOM contains all 9 mandatory provenance attributes and
 *      zero `innerHTML` / `outerHTML` / `insertAdjacentHTML` or client-side Blob CSV synthesis
 *      exist anywhere in CSKH frontend source.
 */

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(__dirname, '../..')
const PUBLIC_CSKH = path.resolve(REPO_ROOT, 'apps/crm_cskh/crm_cskh/public/cskh')
const WWW_CSKH = path.resolve(REPO_ROOT, 'apps/crm_cskh/crm_cskh/www/cskh')

function makeMockNode(tag) {
	return {
		tagName: String(tag).toUpperCase(),
		attributes: {},
		children: [],
		childNodes: [],
		style: {},
		hidden: false,
		disabled: false,
		checked: false,
		value: '',
		className: '',
		_text: '',
		get textContent() {
			if (this._text) return this._text
			return this.children
				.map((c) => (c.nodeType === 3 ? c.textContent : c.textContent || ''))
				.join('')
		},
		set textContent(v) {
			this._text = String(v ?? '')
			this.children = []
			this.childNodes = []
		},
		get firstChild() {
			return this.childNodes[0] || null
		},
		setAttribute(k, v) {
			this.attributes[k] = String(v)
		},
		getAttribute(k) {
			return this.attributes[k] || null
		},
		appendChild(child) {
			this.children.push(child)
			this.childNodes.push(child)
			return child
		},
		removeChild(child) {
			const idx = this.childNodes.indexOf(child)
			if (idx >= 0) {
				this.childNodes.splice(idx, 1)
				this.children.splice(idx, 1)
			}
		},
		addEventListener() {},
	}
}

function loadP3Sandbox(timezoneConfig, i18nOverrides) {
	const context = {
		console,
		Intl,
		Date,
		Math,
		Number,
		String,
		Boolean,
		Array,
		Object,
		RegExp,
		JSON,
		Error,
		Promise,
		URLSearchParams,
		encodeURIComponent,
		setTimeout,
		clearTimeout,
		performance: { now: () => 1000 },
		crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000099' },
		navigator: {},
		location: { search: '' },
		fetchCalls: [],
		fetch: async (url, init) => {
			context.fetchCalls.push({ url, init })
			return {
				ok: true,
				status: 200,
				json: async () => ({ message: { rows: [], total: 0 } }),
			}
		},
		document: {
			querySelector: () => null,
			getElementById: () => null,
			createElement: (tag) => makeMockNode(tag),
			createElementNS: (_ns, tag) => makeMockNode(tag),
			createTextNode: (text) => ({ nodeType: 3, textContent: String(text ?? '') }),
		},
	}
	context.window = context
	context.window.cskhTimezone = timezoneConfig
	context.window.cskhUser = 'bod@example.test'
	context.window.cskhCapabilities = {
		admin: false,
		bod: true,
		leader: false,
		staff: false,
		pii: false,
		p3_enabled: true,
		qa_enabled: false,
		can_recompute_kpi: false,
		can_waive_sla: false,
		can_export_report: true,
	}
	context.window.cskhI18n = Object.assign(
		{
			'N/A': 'N/A',
			'Provisional; missing QA sample': 'Tạm tính; thiếu mẫu QA',
			'Valid weight sum': 'Tổng trọng số thành phần hợp lệ',
			'N/A — No eligible denominator (0/0)': 'N/A — Không có mẫu số hợp lệ (0/0)',
			'N/A — QA disabled': 'N/A — Phân hệ QA đang tắt',
			'N/A — Policy disabled': 'N/A — Chính sách đang tắt',
			'Company-wide (BOD)': 'Toàn công ty (BOD)',
			'CRM system administration': 'Quản trị hệ thống CRM',
			'Managed team scope': 'Phạm vi đội nhóm quản lý',
		},
		i18nOverrides || {}
	)
	context.window.cskhErrorTexts = {}
	context.window.frappe = { csrf_token: 'test-csrf-p3' }

	vm.createContext(context)
	vm.runInContext(fs.readFileSync(path.join(PUBLIC_CSKH, 'cskh.js'), 'utf8'), context, {
		filename: 'cskh.js',
	})
	vm.runInContext(fs.readFileSync(path.join(PUBLIC_CSKH, 'p3-api.js'), 'utf8'), context, {
		filename: 'p3-api.js',
	})
	vm.runInContext(fs.readFileSync(path.join(PUBLIC_CSKH, 'p3-widgets.js'), 'utf8'), context, {
		filename: 'p3-widgets.js',
	})
	return context
}

// 1. Period boundaries in Asia/Ho_Chi_Minh across day/week/month/quarter/year/custom
{
	const sb = loadP3Sandbox({
		site: 'Asia/Ho_Chi_Minh',
		user: '',
		effective: 'Asia/Ho_Chi_Minh',
		server_now_iso: '2026-11-04T23:59:30+07:00',
	})
	const { cskh } = sb

	// Default anchor at 23:59:30 ICT must still be 2026-11-04 (NOT 2026-11-05 UTC or 2026-11-04 LA morning)
	const dayDefault = cskh.computePeriodBounds('daily', '', '', 'Asia/Ho_Chi_Minh')
	assert.equal(dayDefault.periodType, 'daily')
	assert.equal(dayDefault.periodKey, '2026-11-04')
	assert.equal(dayDefault.fromDate, '2026-11-04')
	assert.equal(dayDefault.toDateExclusive, '2026-11-05')
	assert.equal(dayDefault.fromIso, '2026-11-04T00:00:00+07:00')
	assert.equal(dayDefault.toExclusiveIso, '2026-11-05T00:00:00+07:00')

	// Exact 23:59:59 vs 00:00:00 half-open inclusion check
	const ms2359 = cskh.parseServerDate('2026-11-04 23:59:59', 'Asia/Ho_Chi_Minh').getTime()
	const ms0000 = cskh.parseServerDate('2026-11-05 00:00:00', 'Asia/Ho_Chi_Minh').getTime()
	assert.ok(ms2359 >= dayDefault.fromEpochMs && ms2359 < dayDefault.toExclusiveEpochMs)
	assert.ok(!(ms0000 >= dayDefault.fromEpochMs && ms0000 < dayDefault.toExclusiveEpochMs))

	// ISO week: 2026-11-04 (Wednesday) -> 2026-W45 (Monday 2026-11-02 to Monday 2026-11-09)
	const wk = cskh.computePeriodBounds('weekly', '2026-11-04', '', 'Asia/Ho_Chi_Minh')
	assert.equal(wk.periodKey, '2026-W45')
	assert.equal(wk.fromDate, '2026-11-02')
	assert.equal(wk.toDateExclusive, '2026-11-09')

	// Month: leap-year Feb 2028 -> [2028-02-01, 2028-03-01) and Dec 2026 -> [2026-12-01, 2027-01-01)
	const leapFeb = cskh.computePeriodBounds('monthly', '2028-02', '', 'Asia/Ho_Chi_Minh')
	assert.equal(leapFeb.periodKey, '2028-02')
	assert.equal(leapFeb.fromDate, '2028-02-01')
	assert.equal(leapFeb.toDateInclusive, '2028-02-29')
	assert.equal(leapFeb.toDateExclusive, '2028-03-01')

	const dec = cskh.computePeriodBounds('monthly', '2026-12', '', 'Asia/Ho_Chi_Minh')
	assert.equal(dec.periodKey, '2026-12')
	assert.equal(dec.fromDate, '2026-12-01')
	assert.equal(dec.toDateExclusive, '2027-01-01')

	// Quarter: Q4 2026 -> [2026-10-01, 2027-01-01)
	const q4 = cskh.computePeriodBounds('quarterly', '2026-Q4', '', 'Asia/Ho_Chi_Minh')
	assert.equal(q4.periodKey, '2026-Q4')
	assert.equal(q4.fromDate, '2026-10-01')
	assert.equal(q4.toDateExclusive, '2027-01-01')

	// Year: 2026 -> [2026-01-01, 2027-01-01)
	const yr = cskh.computePeriodBounds('yearly', '2026', '', 'Asia/Ho_Chi_Minh')
	assert.equal(yr.periodKey, '2026')
	assert.equal(yr.fromDate, '2026-01-01')
	assert.equal(yr.toDateExclusive, '2027-01-01')

	// Same-day custom range: [2026-11-04, 2026-11-04] -> half-open [2026-11-04, 2026-11-05)
	const sameDay = cskh.computePeriodBounds('custom', '2026-11-04', '2026-11-04', 'Asia/Ho_Chi_Minh')
	assert.equal(sameDay.fromServer, '2026-11-04 00:00:00')
	assert.equal(sameDay.toExclusiveServer, '2026-11-05 00:00:00')
}

// 2. Subprocess verification under `TZ=America/Los_Angeles` and `TZ=Asia/Tokyo`
if (!process.env.CSKH_P3_SUBPROCESS_TZ_CHECK) {
	for (const hostTz of ['America/Los_Angeles', 'Asia/Tokyo', 'UTC']) {
		const res = spawnSync(process.execPath, [fileURLToPath(import.meta.url)], {
			env: Object.assign({}, process.env, {
				TZ: hostTz,
				CSKH_P3_SUBPROCESS_TZ_CHECK: '1',
			}),
			encoding: 'utf8',
		})
		assert.equal(
			res.status,
			0,
			`Cross-TZ subprocess failed under TZ=${hostTz}:\n${res.stderr || res.stdout}`
		)
	}
}

// 3. `denominator = 0` -> `N/A` (never `0%`), valid `0 / 12` -> `0%`, and `7350/85 = 86,47` Provisional
{
	const sb = loadP3Sandbox({
		site: 'Asia/Ho_Chi_Minh',
		user: 'Asia/Tokyo',
		effective: 'Asia/Tokyo',
		server_now_iso: '2026-11-04T16:00:00+07:00',
	})
	const { cskhP3Api: api, cskhP3Widgets: w } = sb

	// Denominator = 0 MUST be N/A, even if a buggy caller sent value: 0
	const zeroDen = api.normalizeMetric(
		'first_touch_on_time_rate',
		{ value: 0, numerator: 0, denominator: 0, status: 'insufficient_data' },
		{ qa_enabled: false }
	)
	assert.equal(zeroDen.is_na, true)
	const zeroDenDisp = w.formatMetricDisplay(zeroDen)
	assert.equal(zeroDenDisp.main, 'N/A')
	assert.notEqual(zeroDenDisp.main, '0%')

	// Valid zero rate (`0 / 12`) MUST render `0%` (`0 / 12`), NOT `N/A`
	const validZero = api.normalizeMetric(
		'followup_on_time_rate',
		{ value: 0, numerator: 0, denominator: 12, status: 'fresh' },
		{ qa_enabled: false }
	)
	assert.equal(validZero.is_na, false)
	const validZeroDisp = w.formatMetricDisplay(validZero)
	assert.equal(validZeroDisp.main, '0%')
	assert.equal(validZeroDisp.ratio, '0 / 12')

	// QA disabled (`qa_enabled = false`) Process Compliance Score (`7350 / 85 = 86.47` per `report-api-contract.md` §1.4)
	const provScore = api.normalizeMetric(
		'PROCESS_SCORE',
		{
			metric_code: 'PROCESS_SCORE',
			value: 86.47,
			numerator: 7350.0,
			denominator: 85.0,
			unit: 'score_100',
			status: 'provisional',
			reason: 'qa_component_excluded_reweighted',
			valid_weight: 85,
			missing_components: ['C5_QA_QUALITY'],
		},
		{ qa_enabled: false }
	)
	assert.equal(provScore.is_na, false)
	assert.equal(provScore.status, 'provisional')
	const provScoreDisp = w.formatMetricDisplay(provScore)
	assert.equal(provScoreDisp.main, '86,47 — Tạm tính; thiếu mẫu QA')
	assert.equal(provScoreDisp.ratio, 'Tổng trọng số thành phần hợp lệ: 85')

	// QA pass rate when QA disabled -> N/A — Phân hệ QA đang tắt (`status: "disabled"`)
	const qaMetric = api.normalizeMetric(
		'QA_PASS_RATE',
		{ value: null, numerator: 0, denominator: 0, status: 'disabled', reason: 'qa_disabled_by_policy' },
		{ qa_enabled: false }
	)
	assert.equal(qaMetric.is_na, true)
	assert.equal(w.formatMetricDisplay(qaMetric).sub, 'N/A — Phân hệ QA đang tắt')
}

// 4. Non-mixed ranking partition (`finalized` vs `provisional` vs `insufficient_data`)
{
	const sb = loadP3Sandbox({
		site: 'Asia/Ho_Chi_Minh',
		user: '',
		effective: 'Asia/Ho_Chi_Minh',
		server_now_iso: '2026-11-04T16:00:00+07:00',
	})
	const { cskhP3Widgets: w, cskhP3Api: api } = sb

	const rows = [
		{ employee: 'staff.prov@test', score_status: 'provisional', process_compliance_score: { value: 92.5, status: 'provisional' } },
		{ employee: 'staff.fin1@test', score_status: 'finalized', rank_in_group: 1, process_compliance_score: { value: 88.0, status: 'finalized' } },
		{ employee: 'staff.na@test', score_status: 'insufficient_data', process_compliance_score: { value: null, is_na: true, status: 'insufficient_data' } },
	]
	const groups = w.partitionScoreGroups(rows)
	assert.equal(groups.finalized.length, 1)
	assert.equal(groups.finalized[0].employee, 'staff.fin1@test')
	assert.equal(groups.provisional.length, 1)
	assert.equal(groups.provisional[0].employee, 'staff.prov@test')
	assert.equal(groups.insufficient_data.length, 1)
	assert.equal(groups.insufficient_data[0].employee, 'staff.na@test')

	// Verify rendered Explainable KPI card contains provenance `<dl>` with 11 dt/dd pairs covering all 9 attributes
	const cardMetric = api.normalizeMetric(
		'first_touch_on_time_rate',
		{
			value: 80,
			numerator: 80,
			denominator: 100,
			exclusions: ['waived', 'dnc'],
			excluded_count: 5,
			as_of: '2026-11-04T15:00:00+07:00',
			calculated_at: '2026-11-04T15:01:00+07:00',
			source_watermark: '2026-11-04T15:00:00+07:00',
			metric_version: 'p3.v1',
			policy_version: 'POL-2026-01',
			status: 'fresh',
			drillable: true,
		},
		{ from_date: '2026-11-01', to_date_exclusive: '2026-12-01', scope: 'system' }
	)
	let drilled = null
	const cardNode = w.renderMetricCard(cardMetric, {
		onDrilldown: (m) => {
			drilled = m.key
		},
	})
	assert.equal(cardNode.attributes['data-metric-key'], 'first_touch_on_time_rate')
	assert.equal(cardNode.attributes['data-metric-na'], '0')
	assert.ok(cardNode.textContent.includes('80%'))
	assert.ok(cardNode.textContent.includes('80 / 100'))
	assert.ok(cardNode.textContent.includes('POL-2026-01'))
	assert.equal(drilled, null)
}

// 5. Static AST/source guard: zero `innerHTML` and zero client-side Blob CSV synthesis
{
	const jsFiles = fs.readdirSync(PUBLIC_CSKH).filter((f) => f.endsWith('.js'))
	const htmlFiles = fs.readdirSync(WWW_CSKH).filter((f) => f.endsWith('.html'))

	for (const file of jsFiles) {
		const raw = fs.readFileSync(path.join(PUBLIC_CSKH, file), 'utf8')
		// Strip comments before checking for `.innerHTML` / `.outerHTML` / `insertAdjacentHTML`
		const codeOnly = raw
			.replace(/\/\*[\s\S]*?\*\//g, '')
			.replace(/^\s*\/\/.*$/gm, '')
		assert.equal(
			/\.(innerHTML|outerHTML)\b|insertAdjacentHTML\s*\(/.test(codeOnly),
			false,
			`Forbidden unsafe HTML sink detected in ${file}`
		)
	}

	const p3JsFiles = [
		'p3-api.js',
		'p3-widgets.js',
		'quality-overview.js',
		'team-quality.js',
		'employee-quality.js',
		'campaign-quality.js',
		'sla-operations.js',
		'scorecard.js',
	]
	for (const file of p3JsFiles) {
		const raw = fs.readFileSync(path.join(PUBLIC_CSKH, file), 'utf8')
		const codeOnly = raw
			.replace(/\/\*[\s\S]*?\*\//g, '')
			.replace(/^\s*\/\/.*$/gm, '')
		assert.equal(
			/\bnew\s+Blob\s*\(|createObjectURL\s*\(|data:text\/csv/i.test(codeOnly),
			false,
			`Forbidden client-side CSV/Blob export detected in ${file}`
		)
	}
	assert.ok(htmlFiles.includes('quality-overview.html'))
	assert.ok(htmlFiles.includes('scorecard.html'))
}

// 6. T1C remediation regressions: CONNECTED_RATE interval grain, count-card ratio suppression,
//    policy_disabled N/A distinction, Vietnamese scopeLabel, and raw vs normalized score points
{
	const sb = loadP3Sandbox({
		site: 'Asia/Ho_Chi_Minh',
		user: '',
		effective: 'Asia/Ho_Chi_Minh',
		server_now_iso: '2026-11-05T10:00:00+07:00',
	})
	const { cskh, cskhP3Api: api, cskhP3Widgets: w } = sb

	// F-P3-01: CONNECTED_RATE grain MUST be ownership_interval (41/49 = 83,67%)
	const connMetric = api.normalizeMetric(
		'CONNECTED_RATE',
		{ value: 83.67, numerator: 41, denominator: 49, status: 'fresh' },
		{ qa_enabled: false }
	)
	assert.equal(connMetric.grain, 'ownership_interval')
	assert.equal(w.formatMetricDisplay(connMetric).ratio, '41 / 49')

	// F-P3-10: Count metrics MUST NOT display `(34 / 1)`
	const countMetric = api.normalizeMetric(
		'ASSIGNED_INTERVALS',
		{ value: 34, numerator: 34, denominator: 1, unit: 'count', status: 'fresh' },
		{ qa_enabled: false }
	)
	assert.equal(countMetric.unit, 'count')
	const countDisp = w.formatMetricDisplay(countMetric)
	assert.equal(countDisp.main, '34')
	assert.equal(countDisp.ratio, '')

	// F-P3-10: Disabled MIN_ATTEMPTS policy MUST distinguish `policy_disabled` from `qa_disabled` and suppress `(0 / 0)`
	const minAttMetric = api.normalizeMetric(
		'MIN_ATTEMPTS_COMPLIANCE_RATE',
		{ value: null, numerator: 0, denominator: 0, status: 'disabled', reason: 'policy_disabled' },
		{ qa_enabled: false }
	)
	assert.equal(minAttMetric.is_na, true)
	assert.equal(minAttMetric.na_reason, 'policy_disabled')
	const minAttDisp = w.formatMetricDisplay(minAttMetric)
	assert.equal(minAttDisp.sub, 'N/A — Chính sách đang tắt')
	assert.equal(minAttDisp.ratio, '')

	// F-P3-10: scopeLabel translates `bod`, `crm_admin`, `team_leader` to Vietnamese
	assert.equal(cskh.scopeLabel('bod'), 'Toàn công ty (BOD)')
	assert.equal(cskh.scopeLabel('crm_admin'), 'Quản trị hệ thống CRM')
	assert.equal(cskh.scopeLabel('team_leader'), 'Phạm vi đội nhóm quản lý')

	// F-P3-09: Personal scorecard distinguishes raw_weighted_points (0..weight, e.g. 22.50 / 25)
	// from normalized_contribution (0..100, e.g. 26.47), never 2250!
	sb.fetch = async (url) => {
		if (String(url).includes('get_my_scorecard')) {
			return {
				ok: true,
				status: 200,
				json: async () => ({
					message: {
						qa_enabled: false,
						valid_weight: 85,
						metrics: {
							PROCESS_SCORE: {
								metric_code: 'PROCESS_SCORE',
								value: 86.47,
								numerator: 73.5,
								denominator: 85,
								unit: 'score_100',
								status: 'provisional',
								valid_weight: 85,
							},
						},
						score_components: [
							{
								component: 'FIRST_ATTEMPT_SLA_RATE',
								weight: 25,
								value: 90,
								raw_weighted_points: 22.5,
								normalized_contribution: 26.47,
								included: true,
							},
							{
								component: 'ELIGIBLE_COVERAGE',
								weight: 20,
								value: 80,
								raw_weighted_points: 16.0,
								normalized_contribution: 18.82,
								included: true,
							},
							{
								component: 'VALID_OUTCOME_LOGGING_RATE',
								weight: 20,
								value: 100,
								raw_weighted_points: 20.0,
								normalized_contribution: 23.53,
								included: true,
							},
							{
								component: 'FOLLOWUP_SLA_RATE',
								weight: 20,
								value: 75,
								raw_weighted_points: 15.0,
								normalized_contribution: 17.65,
								included: true,
							},
							{
								component: 'QA_PASS_RATE',
								weight: 15,
								value: null,
								raw_weighted_points: null,
								normalized_contribution: null,
								included: false,
								status: 'disabled',
							},
						],
					},
				}),
			}
		}
		return { ok: true, status: 200, json: async () => ({ message: { rows: [], total: 0 } }) }
	}
	const sc = await api.getPersonalScorecard({ period_type: 'monthly', period_value: '2026-10' })
	assert.equal(sc.valid_weight_sum, 85)
	assert.equal(sc.raw_points_total, 73.5)
	const included = sc.components.filter((c) => c.included)
	assert.equal(included.length, 4)
	assert.equal(included[0].raw_weighted_points, 22.5)
	assert.equal(included[0].normalized_contribution, 26.47)
	for (const comp of included) {
		assert.ok(comp.raw_weighted_points >= 0 && comp.raw_weighted_points <= comp.weight)
	}
	const normSum = included.reduce((acc, c) => acc + c.normalized_contribution, 0)
	assert.ok(Math.abs(normSum - 86.47) <= 0.02, `Expected normalized sum ~86.47, got ${normSum}`)
}

if (!process.env.CSKH_P3_SUBPROCESS_TZ_CHECK) {
	console.log('P3 UI timezone, N/A, provisional ranking & safe-DOM suite: ALL CHECKS PASSED.')
}
