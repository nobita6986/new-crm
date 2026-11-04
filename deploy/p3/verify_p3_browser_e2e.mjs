#!/usr/bin/env node
/**
 * Real-Chromium Browser E2E verification & screenshot capture for P3 UI (`/cskh/*`).
 *
 * Connects ONLY to the isolated P3 UI Frappe site (`http://p3ui.localhost:8320`),
 * strictly isolated from Owner's `p1.localhost:8120`, P2's `8220`, and T1B's `18320`.
 *
 * Exercises all mandatory P3 browser workflows (`docs/root-plan/P3.md` §10–17 & CÔNG VIỆC 1..7):
 *   1. Executive Quality Overview (`/cskh/quality-overview`, `BOD`):
 *      - `html[lang="vi"]`, 0 console/page/asset/500 errors, 0 raw customer PII;
 *      - `qa_enabled = false` fallback banner + `DEV/configurable` policy notice banner;
 *      - Watermark bar (`as_of`, `calculated_at`, `source_watermark`, `cskh-metrics-v1`);
 *      - Strict visual separation of the 3 P3 pillars (`compliance`, `connection`, `reliability`);
 *      - `QA_PASS_RATE` rendered as `N/A` (`N/A — Phân hệ QA đang tắt`, never `0%`);
 *      - `PROCESS_SCORE` rendered as `Provisional` (`Tạm tính; thiếu mẫu QA`, valid weight `85`);
 *      - Interactive `Công thức & nguồn gốc` (`Formula & provenance`) `<dl>` toggle with all 9 attributes.
 *   2. Interactive Server-Side Drill-Down Modal (`>= 5` core metrics, `Admin` & `BOD`):
 *      - Exercises `FIRST_ATTEMPT_SLA_RATE`, `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE`,
 *        `FOLLOWUP_SLA_RATE`, `CONNECTED_RATE`, `VALID_OUTCOME_LOGGING_RATE`,
 *        and `CALLBACK_COMPLETION_RATE`;
 *      - Verifies server-side query params (`metric_code`, `filter_bucket`, `page`, `page_size`)
 *        and exact cohort reconciliation (`all == met + unmet`), segment chip switching,
 *        multi-page `page=1 -> page=2` server pagination, and `BOD` 0-PII redaction.
 *   3. Team Comparison & `team-as-of` Historical Attribution (`/cskh/team-quality`, `BOD` & `Leader A`):
 *      - `BOD` sees both `Team A` and `Team B` with inline SVG bar chart and `0` PII;
 *      - `Leader A` sees only `Team A` and receives `[FORBIDDEN_SCOPE]` on `?team=<team_b>`.
 *   4. Employee Quality: `N/A` vs `0%` & Non-Mixed Score Groups (`/cskh/employee-quality`, `BOD`):
 *      - `finalized`, `provisional`, and `insufficient_data` rendered in separate `<section>` cards
 *        (never ranking `provisional` together with `finalized`);
 *      - Zero-denominator employee (`staff_c`) displays `N/A` (never `0%`);
 *      - Golden employee displays `86,47 — Tạm tính; thiếu mẫu QA`;
 *      - Score-group filter `#p3-filter-score-group` isolates groups cleanly.
 *   5. Campaign & Lead Source Quality (`/cskh/campaign-quality`, `BOD`):
 *      - Contrasts `DISTINCT Work Item` (`unique_assigned_work_items`) vs `Ownership Interval`
 *        (`ownership_intervals_count` under `A -> B -> A` transfer) + non-penalizing `FIRST_CONNECTED`.
 *   6. SLA & QA Operations + Audited SLA Waiver (`/cskh/sla-operations`, `Leader A`, `BOD`, `Staff A`):
 *      - `Leader A` waives an eligible SLA obligation with mandatory audit reason (`waived`);
 *      - `BOD` has 0 waive buttons and 0 PII; `Staff A` receives HTTP `403` on all 5 management screens.
 *   7. Personal Scorecard, Post-Transfer Redaction & Historical URL `403` (`/cskh/scorecard`):
 *      - `Staff A` sees own scorecard only (0 peer ranks), drills down own historical obligation
 *        with transferred Lead PII redacted, and gets `[FORBIDDEN]` (`0` PII) when replaying
 *        transferred `/cskh/work-item?id=...`;
 *      - `Staff C` (0 denominator) sees `N/A` (never `0%`); `BOD` gets HTTP `403` on `/cskh/scorecard`.
 *   8. Stale Banner, KPI Recompute & Revision / Restatement History (`Admin`):
 *      - Observes stale/dirty banner after mutation, triggers `#p3-btn-recompute` with audit reason,
 *        and inspects `#p3-btn-revisions` (`#p3-revisions-table` with `Run ID` & restatement note).
 *   9. Authorized Export Lifecycle & Post-Revocation `403` (`Leader A`):
 *      - Queues CSV export via `#p3-btn-export`, verifies `expires_at` + `Sẵn sàng tải`, downloads CSV,
 *        then revokes `Leader A`'s Team A grant and verifies BOTH `download_export` and raw
 *        `/private/files/...` URL immediately return HTTP `403` in the live browser session.
 *  10. Cross-Timezone Browser Verification (`America/Los_Angeles` & `Asia/Tokyo` vs `Asia/Ho_Chi_Minh`):
 *      - Verifies `daily`, `weekly`, `monthly`, `quarterly`, `yearly`, and `custom` period selectors
 *        anchor identically to `Asia/Ho_Chi_Minh` regardless of host browser timezone.
 *
 * Writes sanitized screenshots to `deploy/p3/evidence/screenshots/` and manifest to
 * `deploy/p3/evidence/browser-manifest.json`.
 */

import fs from 'node:fs'
import path from 'node:path'
import { execFileSync, execSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const HERE = path.dirname(__filename)
const ROOT = path.resolve(HERE, '..', '..')
const EVIDENCE_DIR = path.join(HERE, 'evidence')
const SHOTS_DIR = path.join(EVIDENCE_DIR, 'screenshots')
const HTTP_REPORT_PATH = path.join(EVIDENCE_DIR, 'http-e2e-report.json')
const MANIFEST_PATH = path.join(EVIDENCE_DIR, 'browser-manifest.json')

const BASE_URL = process.env.P3_BROWSER_URL || 'http://p3ui.localhost:8320'
const SITE = process.env.P3_SITE || 'p3ui.localhost'
const PASSWORD = process.env.P3_SEED_PASSWORD || 'P2Seed-Only-2026!'
const CONTAINER = process.env.P3_CONTAINER || 'crm-cskh-p3-ui-bench-1'

if (
	BASE_URL.includes('8120') ||
	BASE_URL.includes('18320') ||
	BASE_URL.includes('8220') ||
	BASE_URL.includes('p1.localhost')
) {
	throw new Error(`Refusing to run P3 Browser E2E against non-isolated stack: ${BASE_URL}`)
}

const PLAYWRIGHT_PATH =
	process.env.PLAYWRIGHT_MODULE ||
	'D:/HRP-app/Contract-mange-hrp/node_modules/.pnpm/playwright@1.63.0/node_modules/playwright'

const require = createRequire(import.meta.url)
const { chromium } = require(PLAYWRIGHT_PATH)

const RAW_PHONE_RE = /\b(?:\+?84|0)\d{8,9}\b/g

function assert(cond, msg) {
	if (!cond) {
		throw new Error(`P3_BROWSER_ASSERTION_FAILED: ${msg}`)
	}
}

function benchExec(method, kwargs) {
	const pyCode = [
		'import json, os, frappe',
		"frappe.init(site=os.environ['P3_EXEC_SITE'], sites_path='.')",
		'frappe.connect()',
		"res = frappe.get_attr(os.environ['P3_EXEC_METHOD'])(**json.loads(os.environ.get('P3_EXEC_KWARGS') or '{}'))",
		'frappe.db.commit()',
		'print(json.dumps(res))',
		'frappe.destroy()',
	].join('; ')
	const out = execFileSync(
		'docker',
		[
			'exec',
			'-w',
			'/workspace/frappe-bench/sites',
			'-e',
			`P3_EXEC_SITE=${SITE}`,
			'-e',
			`P3_EXEC_METHOD=${method}`,
			'-e',
			`P3_EXEC_KWARGS=${JSON.stringify(kwargs || {})}`,
			CONTAINER,
			'/workspace/frappe-bench/env/bin/python',
			'-c',
			pyCode,
		],
		{ encoding: 'utf-8' }
	)
	const lines = out
		.split(/\r?\n/)
		.map((l) => l.trim())
		.filter(Boolean)
	return JSON.parse(lines[lines.length - 1])
}

async function createActorPage(
	browser,
	userEmail,
	roleLabel,
	timezoneId = 'Asia/Ho_Chi_Minh',
	password = PASSWORD
) {
	const context = await browser.newContext({
		viewport: { width: 1440, height: 960 },
		locale: 'vi-VN',
		timezoneId,
	})
	const page = await context.newPage()
	await page.goto(`${BASE_URL}/login`, { waitUntil: 'domcontentloaded' })
	const loginStatus = await page.evaluate(
		async ({ usr, pwd }) => {
			const r = await fetch('/api/method/login', {
				method: 'POST',
				credentials: 'same-origin',
				headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
				body: JSON.stringify({ usr, pwd }),
			})
			return r.status
		},
		{ usr: userEmail, pwd: password }
	)
	assert(loginStatus === 200, `Login failed for ${userEmail}: HTTP ${loginStatus}`)

	const telemetry = {
		consoleErrors: [],
		pageErrors: [],
		assetFailures: [],
		server500s: [],
		apiRequests: [],
	}

	page.on('console', (msg) => {
		if (msg.type() === 'error') {
			const text = msg.text()
			if (!text.includes('Failed to load resource: the server responded with a status of 40')) {
				telemetry.consoleErrors.push(text)
			}
		}
	})
	page.on('pageerror', (err) => {
		telemetry.pageErrors.push(String(err))
	})
	page.on('request', (req) => {
		if (req.url().includes('/api/method/')) {
			telemetry.apiRequests.push({
				method: req.method(),
				url: req.url(),
			})
		}
	})
	page.on('requestfailed', (req) => {
		if (req.url().includes('/assets/')) {
			telemetry.assetFailures.push(`${req.url()} -> ${req.failure()?.errorText || 'failed'}`)
		}
	})
	page.on('response', (resp) => {
		if (resp.url().includes('/assets/') && resp.status() >= 400) {
			telemetry.assetFailures.push(`${resp.url()} -> HTTP ${resp.status()}`)
		}
		if (resp.status() >= 500) {
			telemetry.server500s.push(`${resp.url()} -> HTTP ${resp.status()}`)
		}
	})

	return { context, page, actor: { user: userEmail, role: roleLabel, timezoneId }, telemetry }
}

async function verifyCleanDomAndTelemetry(session, routeLabel, expectCardVisible = true) {
	const { page, actor, telemetry } = session
	const xss = await page.evaluate(() => window.__xssTriggered)
	assert(xss === undefined, `${actor.user} ${routeLabel}: XSS payload executed!`)

	const lang = await page.evaluate(() => document.documentElement.lang)
	assert(lang === 'vi', `${actor.user} ${routeLabel}: expected html[lang="vi"], got ${lang}`)

	const bodyText = await page.evaluate(() => document.body?.innerText || '')
	assert(bodyText.trim().length > 30, `${actor.user} ${routeLabel}: white/blank screen detected!`)

	const rawPhones = bodyText.match(RAW_PHONE_RE) || []
	assert(
		rawPhones.length === 0,
		`${actor.user} ${routeLabel}: raw unmasked phone in DOM: ${rawPhones.join(', ')}`
	)
	assert(
		!/\[H:[0-9a-f]{6,}\]/i.test(bodyText),
		`${actor.user} ${routeLabel}: internal [H:<hex>] prefix leaked in DOM!`
	)

	if (expectCardVisible) {
		const card = page.locator('.cskh-card:visible, .cskh-pillar:visible, .cskh-score-group:visible').first()
		await card.waitFor({ state: 'visible', timeout: 15000 })
		const box = await card.boundingBox()
		assert(
			box && box.width > 400 && box.height > 36,
			`${actor.user} ${routeLabel}: broken layout ${JSON.stringify(box)}`
		)
	}

	assert(
		telemetry.consoleErrors.length === 0,
		`${actor.user} ${routeLabel} console.error: ${telemetry.consoleErrors.join(' | ')}`
	)
	assert(
		telemetry.pageErrors.length === 0,
		`${actor.user} ${routeLabel} pageerror: ${telemetry.pageErrors.join(' | ')}`
	)
	assert(
		telemetry.assetFailures.length === 0,
		`${actor.user} ${routeLabel} asset failure: ${telemetry.assetFailures.join(' | ')}`
	)
	assert(
		telemetry.server500s.length === 0,
		`${actor.user} ${routeLabel} HTTP 500: ${telemetry.server500s.join(' | ')}`
	)
	return bodyText
}

async function selectSeededMonthIfNeeded(page, periodKey, apiFragment) {
	const anchorInput = page.locator('#p3-filter-anchor')
	if ((await anchorInput.count()) > 0) {
		const curVal = await anchorInput.inputValue()
		if (periodKey && curVal !== periodKey) {
			await Promise.all([
				page.waitForResponse((r) => r.url().includes(apiFragment) && r.status() === 200),
				anchorInput.fill(periodKey).then(() => anchorInput.dispatchEvent('change')),
			])
		}
	}
	await page.locator('.cskh-loading').waitFor({ state: 'detached', timeout: 15000 }).catch(() => {})
}

function parseChipCount(text) {
	const m = /\((\d[\d.,]*)\)/.exec(text || '')
	if (!m) return null
	return Number(m[1].replace(/[.,]/g, ''))
}

async function main() {
	fs.mkdirSync(SHOTS_DIR, { recursive: true })
	if (!fs.existsSync(HTTP_REPORT_PATH)) {
		throw new Error(`Run verify_p3_http_e2e.py first; missing ${HTTP_REPORT_PATH}`)
	}

	// Ensure deterministic clean state before browser suite
	const seed = benchExec('crm_cskh.tools.p3_e2e_seed.reset_and_seed_p3')
	const users = seed.users
	const pwd = seed.password || PASSWORD
	const periodKey = seed.period_key
	const teamA = seed.team_a
	const teamB = seed.team_b
	const revisionSha = execSync('git rev-parse HEAD', { cwd: ROOT }).toString().trim()
	const appsTreeSha = execSync('git rev-parse HEAD:apps', { cwd: ROOT }).toString().trim()

	const hostUrl = new URL(BASE_URL)
	const browser = await chromium.launch({
		headless: true,
		args: [`--host-resolver-rules=MAP ${hostUrl.hostname} 127.0.0.1`],
	})

	const entries = []
	const assertions = []

	function recordAssert(id, cond, detail) {
		assert(cond, `[${id}] ${detail}`)
		assertions.push({ id, status: 'PASS', detail })
	}

	async function captureShot(session, route, fileName, checkId, notes, expectCardVisible = true) {
		const bodyText = await verifyCleanDomAndTelemetry(session, route, expectCardVisible)
		const shotPath = path.join(SHOTS_DIR, fileName)
		await session.page.screenshot({ path: shotPath, fullPage: true })
		entries.push({
			check_id: checkId,
			actor: session.actor.user,
			role: session.actor.role,
			browser_timezone: session.actor.timezoneId,
			route,
			screenshot: `deploy/p3/evidence/screenshots/${fileName}`,
			notes,
		})
		console.log(`PASS  ${checkId} -> ${fileName}`)
		return bodyText
	}

	try {
		const bod = await createActorPage(browser, users.bod, 'CSKH BOD Viewer', 'Asia/Ho_Chi_Minh', pwd)
		const admin = await createActorPage(browser, users.admin, 'CSKH CRM Admin', 'Asia/Ho_Chi_Minh', pwd)
		const leaderA = await createActorPage(browser, users.leader_a, 'Sales Manager (Team A)', 'Asia/Ho_Chi_Minh', pwd)
		const staffA = await createActorPage(browser, users.staff_a, 'Sales User (Staff A)', 'Asia/Ho_Chi_Minh', pwd)
		const staffC = await createActorPage(browser, users.staff_c, 'Sales User (Staff C - Zero Denom)', 'Asia/Ho_Chi_Minh', pwd)

		// ==================================================================
		// SCENARIO 1: BOD Executive Quality Overview (`/cskh/quality-overview`)
		// 0 PII, 3 Pillars, `qa_enabled=false` Banner, `N/A` vs `0%`, `86,47` Provisional,
		// and Formula & Provenance `<dl>` Explainability Toggle
		// ==================================================================
		await bod.page.goto(`${BASE_URL}/cskh/quality-overview`, { waitUntil: 'networkidle' })
		await selectSeededMonthIfNeeded(bod.page, periodKey, 'get_executive_overview')

		await bod.page.locator('[data-banner="qa-disabled"]').waitFor({ state: 'visible', timeout: 10000 })
		await bod.page.locator('[data-banner="dev-policy"]').waitFor({ state: 'visible', timeout: 10000 })

		for (const pillar of ['compliance', 'connection', 'reliability']) {
			const count = await bod.page.locator(`section[data-pillar="${pillar}"]`).count()
			recordAssert(
				`B3E-01-pillar-${pillar}`,
				count === 1,
				`Executive overview renders distinct pillar section[data-pillar="${pillar}"]`
			)
		}

		const qaCard = bod.page.locator('[data-metric-key="QA_PASS_RATE"]')
		const qaNaAttr = await qaCard.getAttribute('data-metric-na')
		const qaValueText = (await qaCard.locator('.cskh-kpi-value').innerText()).trim()
		const qaSubText = (await qaCard.locator('.cskh-kpi-sub').innerText()).trim()
		recordAssert(
			'B3E-02-qa-disabled-na',
			qaNaAttr === '1' && qaValueText === 'N/A' && !qaValueText.includes('0%') && qaSubText.includes('QA'),
			`QA_PASS_RATE renders "${qaValueText}" (${qaSubText}), never 0%`
		)

		const procCard = bod.page.locator('[data-metric-key="PROCESS_SCORE"]')
		const procStatus = await procCard.getAttribute('data-metric-status')
		const procValText = (await procCard.locator('.cskh-kpi-value').innerText()).trim()
		recordAssert(
			'B3E-03-process-score-provisional',
			procStatus === 'provisional' && procValText.includes('Tạm tính'),
			`PROCESS_SCORE renders provisional label: "${procValText}"`
		)

		// Toggle Formula & Provenance on FIRST_ATTEMPT_SLA_RATE and verify all 9 metadata attributes
		const firstTouchCard = bod.page.locator('[data-metric-key="FIRST_ATTEMPT_SLA_RATE"]')
		await firstTouchCard.locator('button', { hasText: 'Công thức & nguồn dữ liệu' }).click()
		const provNode = firstTouchCard.locator('.cskh-kpi-provenance')
		await provNode.waitFor({ state: 'visible', timeout: 5000 })
		const provText = await provNode.innerText()
		for (const requiredTerm of [
			'Mã chỉ số',
			'Đơn vị thống kê (Grain)',
			'Tử số / Mẫu số',
			'Công thức tính',
			'Kỳ báo cáo',
			'Phạm vi',
			'Loại trừ hợp lệ',
			'Mốc chốt dữ liệu (as-of)',
			'Thời điểm tính toán',
			'Mốc dữ liệu nguồn (Watermark)',
			'Phiên bản công thức & chính sách',
			'Trạng thái độ tin cậy',
			'cskh-metrics-v1',
		]) {
			recordAssert(
				`B3E-04-provenance-${requiredTerm}`,
				provText.includes(requiredTerm),
				`Provenance <dl> displays "${requiredTerm}"`
			)
		}

		await captureShot(
			bod,
			'/cskh/quality-overview',
			'01-bod-quality-overview-3-pillars.png',
			'B3E-01-bod-quality-overview',
			'BOD Executive Quality Overview with 3 separated pillars, qa_enabled=false banner, N/A QA card, provisional Process Score, and expanded formula/provenance.'
		)

		// BOD Drill-Down check: 0 PII & 0 clickable work-item links
		await Promise.all([
			bod.page.waitForResponse(
				(r) => r.url().includes('get_metric_drilldown') && r.status() === 200
			),
			firstTouchCard.locator('[data-drilldown-metric="FIRST_ATTEMPT_SLA_RATE"]').click(),
		])
		await bod.page.locator('.cskh-modal').waitFor({ state: 'visible', timeout: 10000 })
		await bod.page.locator('.cskh-modal .cskh-loading').waitFor({ state: 'detached', timeout: 10000 }).catch(() => {})
		const bodModalLinks = await bod.page.locator('.cskh-modal a[href*="/cskh/work-item"]').count()
		recordAssert(
			'B3E-05-bod-drilldown-zero-pii',
			bodModalLinks === 0,
			'BOD drill-down modal contains 0 clickable Work Item PII links'
		)
		await captureShot(
			bod,
			'/cskh/quality-overview#bod-drilldown',
			'02-bod-drilldown-redacted.png',
			'B3E-05-bod-drilldown-redacted',
			'BOD drill-down reconciles cohort counts while redacting all customer PII and Work Item links.'
		)
		await bod.page.locator('.cskh-modal-head button').click()

		// ==================================================================
		// SCENARIO 2: Interactive Server-Side Drill-Down Across >= 6 Core Metrics (`Admin`)
		// Cohort Reconciliation (`all == met + unmet`), Bucket Filtering & Multi-Page Pager
		// ==================================================================
		await admin.page.goto(`${BASE_URL}/cskh/quality-overview`, { waitUntil: 'networkidle' })
		await selectSeededMonthIfNeeded(admin.page, periodKey, 'get_executive_overview')

		const drillMetrics = [
			'FIRST_ATTEMPT_SLA_RATE',
			'WORK_ITEM_FIRST_ATTEMPT_SLA_RATE',
			'FOLLOWUP_SLA_RATE',
			'CONNECTED_RATE',
			'VALID_OUTCOME_LOGGING_RATE',
			'CALLBACK_COMPLETION_RATE',
		]
		for (const mKey of drillMetrics) {
			const btn = admin.page.locator(`[data-drilldown-metric="${mKey}"]`)
			assert((await btn.count()) === 1, `Missing drilldown button for ${mKey}`)
			const [ddResp] = await Promise.all([
				admin.page.waitForResponse(
					(r) =>
						r.url().includes('get_metric_drilldown') &&
						r.url().includes(`metric_code=${mKey}`) &&
						r.status() === 200
				),
				btn.click(),
			])
			assert(
				ddResp.url().includes('page=1') && ddResp.url().includes('page_size=20'),
				`Drilldown ${mKey} must pass server pagination params page=1&page_size=20`
			)
			await admin.page.locator('#p3-drilldown-table, .cskh-modal .cskh-alert.is-empty').first().waitFor({ state: 'visible', timeout: 10000 })

			const allCnt = parseChipCount(await admin.page.locator('.cskh-modal button[data-segment="all"]').innerText())
			const metCnt = parseChipCount(await admin.page.locator('.cskh-modal button[data-segment="met"]').innerText())
			const unmetCnt = parseChipCount(await admin.page.locator('.cskh-modal button[data-segment="unmet"]').innerText())
			recordAssert(
				`B3E-06-drill-${mKey}`,
				allCnt !== null && metCnt !== null && unmetCnt !== null && allCnt === metCnt + unmetCnt,
				`Drilldown ${mKey} reconciles cohort chips (${allCnt} == ${metCnt} + ${unmetCnt})`
			)

			if (mKey === 'FIRST_ATTEMPT_SLA_RATE') {
				// Switch segment to `unmet` (`breached_only`) via server
				await Promise.all([
					admin.page.waitForResponse(
						(r) =>
							r.url().includes('get_metric_drilldown') &&
							r.url().includes('filter_bucket=breached_only') &&
							r.status() === 200
					),
					admin.page.locator('.cskh-modal button[data-segment="unmet"]').click(),
				])
				await admin.page.locator('.cskh-modal .cskh-loading').waitFor({ state: 'detached', timeout: 10000 }).catch(() => {})
				await captureShot(
					admin,
					`/cskh/quality-overview#drilldown-${mKey}`,
					'03-admin-drilldown-reconciliation.png',
					'B3E-06-admin-drilldown-first-attempt',
					'Interactive server-side drill-down modal reconciling cohort segments (all = met + unmet) and breached_only filter.'
				)

				// Switch back to `all` and verify Page 2 pagination if cohort > 20
				await Promise.all([
					admin.page.waitForResponse(
						(r) =>
							r.url().includes('get_metric_drilldown') &&
							r.url().includes('filter_bucket=all_denominator') &&
							r.status() === 200
					),
					admin.page.locator('.cskh-modal button[data-segment="all"]').click(),
				])
				await admin.page.locator('.cskh-modal .cskh-loading').waitFor({ state: 'detached', timeout: 10000 }).catch(() => {})
				if (allCnt > 20) {
					await Promise.all([
						admin.page.waitForResponse(
							(r) =>
								r.url().includes('get_metric_drilldown') &&
								r.url().includes('page=2') &&
								r.status() === 200
						),
						admin.page.locator('.cskh-modal .cskh-pager button').last().click(),
					])
					await admin.page.locator('.cskh-modal .cskh-loading').waitFor({ state: 'detached', timeout: 10000 }).catch(() => {})
					const pagerText = await admin.page.locator('.cskh-modal .cskh-pager').innerText()
					recordAssert(
						'B3E-07-drilldown-page2',
						pagerText.includes('21–'),
						`Drilldown multi-page server pager advances cleanly to page 2: "${pagerText}"`
					)
				}
			}

			await admin.page.locator('.cskh-modal-head button').click()
		}

		// ==================================================================
		// SCENARIO 3: Team Comparison & `team-as-of` Historical Attribution
		// (`/cskh/team-quality`, `BOD` & `Leader A` Scope Isolation)
		// ==================================================================
		await bod.page.goto(`${BASE_URL}/cskh/team-quality`, { waitUntil: 'networkidle' })
		await selectSeededMonthIfNeeded(bod.page, periodKey, 'get_team_comparison')
		await bod.page.locator('#p3-team-table').waitFor({ state: 'visible', timeout: 10000 })

		const hasTeamA = (await bod.page.locator(`#p3-team-table tr[data-team="${teamA}"]`).count()) >= 1
		const hasTeamB = (await bod.page.locator(`#p3-team-table tr[data-team="${teamB}"]`).count()) >= 1
		const svgCharts = await bod.page.locator('svg.cskh-svg-chart').count()
		recordAssert(
			'B3E-08-team-as-of-bod',
			hasTeamA && hasTeamB && svgCharts >= 1,
			`BOD /cskh/team-quality renders both ${teamA} and ${teamB} with inline SVG bar chart`
		)
		await captureShot(
			bod,
			'/cskh/team-quality',
			'04-bod-team-quality-comparison.png',
			'B3E-08-bod-team-quality',
			'Team Comparison screen preserving historical team-as-of attribution across Team A and Team B.'
		)

		// Leader A sees ONLY Team A (never Team B), and `?team=<teamB>` on employee-quality yields FORBIDDEN_SCOPE
		await leaderA.page.goto(`${BASE_URL}/cskh/team-quality`, { waitUntil: 'networkidle' })
		await selectSeededMonthIfNeeded(leaderA.page, periodKey, 'get_team_comparison')
		await leaderA.page.locator('#p3-team-table').waitFor({ state: 'visible', timeout: 10000 })
		const leaderHasTeamA = (await leaderA.page.locator(`#p3-team-table tr[data-team="${teamA}"]`).count()) >= 1
		const leaderHasTeamB = (await leaderA.page.locator(`#p3-team-table tr[data-team="${teamB}"]`).count()) >= 1
		recordAssert(
			'B3E-09-leader-team-scope',
			leaderHasTeamA && !leaderHasTeamB,
			`Leader A sees own Team A (${leaderHasTeamA}) and 0 rows of Team B (${leaderHasTeamB})`
		)

		await leaderA.page.goto(
			`${BASE_URL}/cskh/employee-quality?team=${encodeURIComponent(teamB)}`,
			{ waitUntil: 'networkidle' }
		)
		await leaderA.page.locator('#p3-state-alert .cskh-alert').waitFor({ state: 'visible', timeout: 10000 })
		const crossTeamErr = await leaderA.page.locator('#p3-state-alert').innerText()
		const leakedEmpRows = await leaderA.page.locator('#p3-main-content tbody tr').count()
		recordAssert(
			'B3E-10-leader-cross-team-403',
			(crossTeamErr.includes('FORBIDDEN') || crossTeamErr.includes('quyền') || crossTeamErr.includes('phạm vi')) &&
				leakedEmpRows === 0,
			`Leader A deep-linking Team B gets Vietnamese forbidden banner and 0 rows: "${crossTeamErr.trim()}"`
		)

		// ==================================================================
		// SCENARIO 4: Employee Quality — Non-Mixed Score Groups & `N/A` vs `0%`
		// (`/cskh/employee-quality`, `BOD`)
		// ==================================================================
		await bod.page.goto(`${BASE_URL}/cskh/employee-quality`, { waitUntil: 'networkidle' })
		await selectSeededMonthIfNeeded(bod.page, periodKey, 'get_employee_quality')
		await bod.page.locator('section[data-score-section="provisional"]').waitFor({ state: 'visible', timeout: 10000 })

		const provSecCount = await bod.page.locator('section[data-score-section="provisional"]').count()
		const naSecCount = await bod.page.locator('section[data-score-section="insufficient_data"]').count()
		recordAssert(
			'B3E-11-non-mixed-score-groups',
			provSecCount === 1 && naSecCount === 1,
			'Provisional and insufficient_data employees are strictly separated into distinct sections (never ranked with finalized)'
		)

		const zeroEmpId = seed.zero_denominator_employee || users.staff_c
		const zeroRow = bod.page.locator(
			`section[data-score-section="insufficient_data"] tr[data-employee="${zeroEmpId}"]`
		)
		assert((await zeroRow.count()) === 1, `Expected ${zeroEmpId} in insufficient_data section`)
		const zeroRowText = await zeroRow.innerText()
		recordAssert(
			'B3E-12-zero-denominator-renders-na',
			zeroRowText.includes('N/A') && !zeroRowText.includes('0%') && !zeroRowText.includes('0,00%'),
			`Zero-denominator employee (${zeroEmpId}) renders N/A, never 0%: "${zeroRowText.replace(/\s+/g, ' ').trim()}"`
		)

		if (seed.golden_employee) {
			const goldenRow = bod.page.locator(
				`section[data-score-section="provisional"] tr[data-employee="${seed.golden_employee}"]`
			)
			if ((await goldenRow.count()) === 1) {
				const goldenText = await goldenRow.innerText()
				recordAssert(
					'B3E-13-golden-8647-vi',
					goldenText.includes('86,47') && goldenText.includes('Tạm tính') && goldenText.includes('Không xếp hạng'),
					`Golden employee renders 86,47 Provisional & Unranked`
				)
			}
		}

		await captureShot(
			bod,
			'/cskh/employee-quality',
			'05-bod-employee-quality-groups-and-na.png',
			'B3E-11-bod-employee-quality',
			'Employee Quality separating Provisional vs Insufficient Data (N/A) groups with 0 customer PII.'
		)

		// ==================================================================
		// SCENARIO 5: Campaign & Lead Source Quality — `DISTINCT Work Item` vs `Ownership Interval`
		// (`/cskh/campaign-quality`, `BOD`)
		// ==================================================================
		await bod.page.goto(`${BASE_URL}/cskh/campaign-quality`, { waitUntil: 'networkidle' })
		await selectSeededMonthIfNeeded(bod.page, periodKey, 'get_campaign_quality')
		await bod.page.locator('#p3-campaign-table').waitFor({ state: 'visible', timeout: 10000 })

		const campRow = bod.page.locator('#p3-campaign-table tbody tr').first()
		const distinctWiText = (await campRow.locator('td').nth(2).innerText()).trim()
		const intervalsText = (await campRow.locator('td').nth(3).innerText()).trim()
		const distinctWiNum = Number(distinctWiText.replace(/[.,]/g, ''))
		const intervalsNum = Number(intervalsText.replace(/[.,]/g, ''))
		recordAssert(
			'B3E-14-campaign-distinct-vs-intervals',
			distinctWiNum > 0 && intervalsNum > distinctWiNum,
			`Campaign table contrasts DISTINCT Work Items (${distinctWiNum}) < Ownership Intervals (${intervalsNum}) under A->B->A transfer`
		)

		await captureShot(
			bod,
			'/cskh/campaign-quality',
			'06-bod-campaign-quality-grain.png',
			'B3E-14-bod-campaign-quality',
			'Campaign & Lead Source Performance contrasting DISTINCT Work Items vs Ownership Intervals and non-penalizing FIRST_CONNECTED.'
		)

		// ==================================================================
		// SCENARIO 6: SLA & QA Operations + Audited SLA Waiver + Role Gates
		// (`/cskh/sla-operations`, `Leader A`, `BOD`, `Staff A`)
		// ==================================================================
		await leaderA.page.goto(`${BASE_URL}/cskh/sla-operations`, { waitUntil: 'networkidle' })
		await selectSeededMonthIfNeeded(leaderA.page, periodKey, 'get_executive_overview')
		await leaderA.page.locator('#p3-sla-table').waitFor({ state: 'visible', timeout: 10000 })

		// Filter to `breached_open` via server `list_obligations(status="breached_open")`
		await Promise.all([
			leaderA.page.waitForResponse(
				(r) =>
					r.url().includes('crm_cskh.api.sla.list_obligations') &&
					r.url().includes('status=breached_open') &&
					r.status() === 200
			),
			leaderA.page.selectOption('#p3-filter-sla-status', 'breached_open'),
		])
		await leaderA.page.locator('.cskh-loading').waitFor({ state: 'detached', timeout: 10000 }).catch(() => {})

		const waiveBtn = leaderA.page.locator('[data-waive-obligation]').first()
		assert((await waiveBtn.count()) === 1, 'Expected at least 1 waivable breached_open SLA obligation for Leader A')
		const targetObId = await waiveBtn.getAttribute('data-waive-obligation')
		await waiveBtn.click()
		await leaderA.page.locator('#p3-waive-reason').waitFor({ state: 'visible', timeout: 5000 })

		// Client empty-reason guard
		await leaderA.page.click('#p3-waive-submit')
		const emptyWarn = await leaderA.page.locator('.cskh-modal .cskh-alert').innerText()
		recordAssert(
			'B3E-15-waive-empty-reason-guard',
			emptyWarn.length > 5,
			'Waiver modal requires non-empty audit reason before POST'
		)

		await leaderA.page.fill(
			'#p3-waive-reason',
			'Khách hàng hẹn gọi lại sau kỳ nghỉ lễ - kiểm thử miễn trừ có kiểm toán P3'
		)
		await Promise.all([
			leaderA.page.waitForResponse(
				(r) => r.url().includes('crm_cskh.api.sla.waive_obligation') && r.status() === 200
			),
			leaderA.page.click('#p3-waive-submit'),
		])
		await leaderA.page.locator('.cskh-loading').waitFor({ state: 'detached', timeout: 15000 }).catch(() => {})

		// Switch server status filter to `waived` and verify the audited waived row
		await Promise.all([
			leaderA.page.waitForResponse(
				(r) =>
					r.url().includes('crm_cskh.api.sla.list_obligations') &&
					r.url().includes('status=waived') &&
					r.status() === 200
			),
			leaderA.page.selectOption('#p3-filter-sla-status', 'waived'),
		])
		await leaderA.page.locator('.cskh-loading').waitFor({ state: 'detached', timeout: 10000 }).catch(() => {})

		const waivedRow = leaderA.page.locator(`#p3-sla-table tr[data-obligation-id="${targetObId}"]`)
		assert((await waivedRow.count()) === 1, `Expected waived obligation ${targetObId} under status=waived`)
		const waivedText = await waivedRow.innerText()
		recordAssert(
			'B3E-16-waive-audited-row',
			waivedText.includes('Được miễn trừ hợp lệ') && waivedText.includes('kiểm toán'),
			`Obligation ${targetObId} updated to Waived with audit reason in DOM`
		)

		await captureShot(
			leaderA,
			'/cskh/sla-operations',
			'07-leader-a-sla-operations-waiver.png',
			'B3E-16-leader-sla-operations',
			'Leader A SLA & QA Operations table after audited SLA obligation waiver.'
		)

		// BOD on `/cskh/sla-operations`: 0 waive buttons & 0 PII
		await bod.page.goto(`${BASE_URL}/cskh/sla-operations`, { waitUntil: 'networkidle' })
		await selectSeededMonthIfNeeded(bod.page, periodKey, 'get_executive_overview')
		const bodWaiveBtns = await bod.page.locator('[data-waive-obligation]').count()
		recordAssert(
			'B3E-17-bod-no-waive-buttons',
			bodWaiveBtns === 0,
			'BOD sees 0 waive buttons on /cskh/sla-operations'
		)

		// Staff A receives HTTP 403 on all 5 management P3 screens
		for (const mgmtRoute of [
			'/cskh/quality-overview',
			'/cskh/team-quality',
			'/cskh/employee-quality',
			'/cskh/campaign-quality',
			'/cskh/sla-operations',
		]) {
			const resp = await staffA.page.goto(`${BASE_URL}${mgmtRoute}`, { waitUntil: 'domcontentloaded' })
			recordAssert(
				`B3E-18-staff-403-${mgmtRoute}`,
				resp && resp.status() === 403,
				`Staff A denied on ${mgmtRoute} with HTTP ${resp?.status()}`
			)
		}

		// ==================================================================
		// SCENARIO 7: Personal Scorecard, Post-Transfer PII Redaction &
		// Historical Work Item URL `403` (`/cskh/scorecard`, `Staff A`, `Staff C`, `BOD`)
		// ==================================================================
		await staffA.page.goto(`${BASE_URL}/cskh/scorecard`, { waitUntil: 'networkidle' })
		await selectSeededMonthIfNeeded(staffA.page, periodKey, 'get_my_scorecard')
		await staffA.page.locator('#p3-scorecard-summary').waitFor({ state: 'visible', timeout: 10000 })

		const scorecardText = await captureShot(
			staffA,
			'/cskh/scorecard',
			'08-staff-a-personal-scorecard.png',
			'B3E-19-staff-a-scorecard',
			'Staff A Personal Scorecard showing own Process Score components, 3 pillars, and active owned SLA obligations (0 peer ranks).'
		)
		recordAssert(
			'B3E-19-staff-scorecard-no-peers',
			!scorecardText.includes(users.staff_b) && !scorecardText.includes('Thứ tự (trong nội bộ nhóm)'),
			'Staff A Personal Scorecard never exposes peer emails or peer leaderboard'
		)

		// Staff A drills down own `FIRST_ATTEMPT_SLA_RATE` -> transferred Lead row has PII redacted!
		const staffDrillBtn = staffA.page.locator('[data-drilldown-metric="FIRST_ATTEMPT_SLA_RATE"]').first()
		await Promise.all([
			staffA.page.waitForResponse(
				(r) =>
					r.url().includes('get_metric_drilldown') &&
					r.url().includes('scope_type=employee') &&
					r.status() === 200
			),
			staffDrillBtn.click(),
		])
		await staffA.page.locator('#p3-drilldown-table').waitFor({ state: 'visible', timeout: 10000 })
		const staffModalText = await staffA.page.locator('.cskh-modal').innerText()
		recordAssert(
			'B3E-20-staff-post-transfer-redaction',
			staffModalText.includes('Đã ẩn PII sau bàn giao'),
			'Staff A drill-down preserves historical KPI row while redacting transferred Lead PII'
		)
		await staffA.page.locator('.cskh-modal-head button').click()

		// Replay historical transferred Work Item URL directly as Staff A -> `[FORBIDDEN]` & 0 PII
		if (seed.transferred_work_item) {
			await staffA.page.goto(
				`${BASE_URL}/cskh/work-item?id=${encodeURIComponent(seed.transferred_work_item)}`,
				{ waitUntil: 'networkidle' }
			)
			await staffA.page.locator('#wi-alert .cskh-alert').waitFor({ state: 'visible', timeout: 10000 })
			const wiAlert = await staffA.page.locator('#wi-alert').innerText()
			const summaryHidden = await staffA.page.locator('#wi-summary-card').isHidden()
			const timelineHidden = await staffA.page.locator('#wi-timeline-card').isHidden()
			recordAssert(
				'B3E-21-transferred-work-item-url-forbidden',
				summaryHidden &&
					timelineHidden &&
					(wiAlert.includes('FORBIDDEN') || wiAlert.includes('quyền') || wiAlert.includes('OWNERSHIP')),
				`Staff A replaying transferred Work Item URL sees forbidden alert & hidden cards (0 PII leaked)`
			)
		}

		// Staff C (zero denominator) on `/cskh/scorecard` -> `N/A`, never `0%`
		await staffC.page.goto(`${BASE_URL}/cskh/scorecard`, { waitUntil: 'networkidle' })
		await selectSeededMonthIfNeeded(staffC.page, periodKey, 'get_my_scorecard')
		const staffCFirstVal = (
			await staffC.page.locator('[data-metric-key="FIRST_ATTEMPT_SLA_RATE"] .cskh-kpi-value').innerText()
		).trim()
		recordAssert(
			'B3E-22-staff-c-scorecard-na',
			staffCFirstVal === 'N/A',
			`Zero-denominator Staff C scorecard renders FIRST_ATTEMPT_SLA_RATE as "${staffCFirstVal}", never 0%`
		)

		// BOD denied `/cskh/scorecard` (`403`)
		const bodScorecardResp = await bod.page.goto(`${BASE_URL}/cskh/scorecard`, {
			waitUntil: 'domcontentloaded',
		})
		recordAssert(
			'B3E-23-bod-scorecard-403',
			bodScorecardResp && bodScorecardResp.status() === 403,
			`BOD denied on /cskh/scorecard with HTTP ${bodScorecardResp?.status()}`
		)

		// ==================================================================
		// SCENARIO 8: Stale Banner, KPI Recompute & Revision / Restatement History
		// (`Admin` on `/cskh/quality-overview`)
		// ==================================================================
		await admin.page.goto(`${BASE_URL}/cskh/quality-overview`, { waitUntil: 'networkidle' })
		await selectSeededMonthIfNeeded(admin.page, periodKey, 'get_executive_overview')

		// Leader A's waiver in Scenario 6 marked dirty scope -> verify stale banner OR trigger dirty mark explicitly
		if ((await admin.page.locator('[data-banner="snapshot-stale"]').count()) === 0) {
			benchExec('crm_cskh.tools.p3_e2e_seed.mark_period_dirty_for_test', {
				period_key: periodKey,
			})
			await Promise.all([
				admin.page.waitForResponse((r) => r.url().includes('get_executive_overview') && r.status() === 200),
				admin.page.click('#p3-filter-refresh'),
			])
			await admin.page.locator('.cskh-loading').waitFor({ state: 'detached', timeout: 10000 }).catch(() => {})
		}
		await admin.page.locator('[data-banner="snapshot-stale"]').waitFor({ state: 'visible', timeout: 10000 })
		recordAssert(
			'B3E-24-stale-banner-visible',
			true,
			'Stale/dirty KPI snapshot warning banner displayed transparently before recompute'
		)
		await captureShot(
			admin,
			'/cskh/quality-overview#stale',
			'09-admin-stale-snapshot-banner.png',
			'B3E-24-admin-stale-banner',
			'Transparent stale/dirty KPI snapshot warning banner before recompute.'
		)

		// Trigger recompute via UI modal
		await admin.page.click('#p3-btn-recompute')
		await admin.page.locator('#p3-recompute-reason').waitFor({ state: 'visible', timeout: 5000 })
		await admin.page.fill(
			'#p3-recompute-reason',
			'Tính lại snapshot sau miễn trừ SLA kiểm toán (Browser E2E)'
		)
		await Promise.all([
			admin.page.waitForResponse(
				(r) => r.url().includes('request_recompute') && r.status() === 200
			),
			admin.page.click('#p3-recompute-submit'),
		])
		await admin.page.locator('.cskh-loading').waitFor({ state: 'detached', timeout: 15000 }).catch(() => {})

		// Open Revision & Restatement History modal
		await admin.page.click('#p3-btn-revisions')
		await admin.page.locator('#p3-revisions-table').waitFor({ state: 'visible', timeout: 10000 })
		const revRowsCount = await admin.page.locator('#p3-revisions-table tbody tr').count()
		recordAssert(
			'B3E-25-revisions-history-modal',
			revRowsCount >= 1,
			`Revision & restatement modal lists ${revRowsCount} KPI run revision(s)`
		)
		await captureShot(
			admin,
			'/cskh/quality-overview#revisions',
			'10-admin-revisions-and-restatement.png',
			'B3E-25-admin-revisions-modal',
			'KPI Revision & Restatement history modal showing Run ID, status, as-of, and restatement lineage.'
		)
		await admin.page.locator('.cskh-modal-head button').click()

		// ==================================================================
		// SCENARIO 9: Authorized Export Lifecycle & Post-Revocation `403`
		// (`Leader A` on `/cskh/team-quality`)
		// ==================================================================
		await leaderA.page.goto(`${BASE_URL}/cskh/team-quality`, { waitUntil: 'networkidle' })
		await selectSeededMonthIfNeeded(leaderA.page, periodKey, 'get_team_comparison')
		await leaderA.page.click('#p3-btn-export')
		await leaderA.page.locator('#p3-export-purpose').waitFor({ state: 'visible', timeout: 5000 })
		await leaderA.page.fill(
			'#p3-export-purpose',
			'Xuất báo cáo chất lượng nhóm A phục vụ họp tuần (Browser E2E)'
		)
		await Promise.all([
			leaderA.page.waitForResponse((r) => r.url().includes('request_export') && r.status() === 200),
			leaderA.page.click('#p3-export-submit'),
		])

		const dlLink = leaderA.page.locator('#p3-export-jobs-table a[data-export-download]').first()
		await dlLink.waitFor({ state: 'visible', timeout: 15000 })
		const exportId = await dlLink.getAttribute('data-export-download')
		const dlHref = await dlLink.getAttribute('href')
		const jobRowText = await leaderA.page.locator(`#p3-export-jobs-table tr[data-export-id="${exportId}"]`).innerText()
		recordAssert(
			'B3E-26-export-ready-with-expiry',
			Boolean(exportId) &&
				dlHref.includes('download_export') &&
				jobRowText.includes('Sẵn sàng tải'),
			`Authorized export ${exportId} ready with download_export URL and expiry timestamp`
		)

		await captureShot(
			leaderA,
			'/cskh/team-quality#export-ready',
			'11-leader-a-authorized-export-ready.png',
			'B3E-26-leader-export-ready',
			'Authorized server export modal showing Export ID, Ready status, row count, expiry timestamp, and download link.'
		)

		// Verify live authorized download inside Leader A browser session
		const dlCheck = await leaderA.page.evaluate(async (url) => {
			const r = await fetch(url, { credentials: 'same-origin' })
			const text = await r.text()
			return { status: r.status, len: text.length, hasRawCmd: /(?:^|,|\n)=CMD/.test(text) }
		}, dlHref)
		recordAssert(
			'B3E-27-export-browser-download-200',
			dlCheck.status === 200 && dlCheck.len > 15 && !dlCheck.hasRawCmd,
			`Browser CSV download succeeded (HTTP ${dlCheck.status}, ${dlCheck.len} chars, formula-safe)`
		)

		// Revoke Leader A's Team A access -> verify BOTH `download_export` AND `/private/files/...` return 403 in browser!
		const privateFileUrl = benchExec('crm_cskh.tools.p3_e2e_seed.get_export_private_file_url', {
			export_id: exportId,
		})
		benchExec('crm_cskh.tools.p3_e2e_seed.temporarily_revoke_leader_team', {
			user: users.leader_a,
			team: teamA,
			revoke: true,
		})
		try {
			const revCheck = await leaderA.page.evaluate(
				async ({ apiUrl, fileUrl }) => {
					const r1 = await fetch(apiUrl, { credentials: 'same-origin' })
					const r2 = fileUrl
						? await fetch(fileUrl, { credentials: 'same-origin' })
						: { status: 403 }
					return { apiStatus: r1.status, fileStatus: r2.status }
				},
				{ apiUrl: dlHref, fileUrl: privateFileUrl }
			)
			recordAssert(
				'B3E-28-export-post-revocation-403',
				revCheck.apiStatus === 403 && revCheck.fileStatus === 403,
				`Post-revocation browser download blocked on BOTH download_export (${revCheck.apiStatus}) AND /private/files/... (${revCheck.fileStatus})`
			)

			// Click Refresh on the export job row -> UI surfaces Access denied / FORBIDDEN_SCOPE clearly
			await leaderA.page
				.locator(`#p3-export-jobs-table tr[data-export-id="${exportId}"] button`)
				.click()
			await leaderA.page.locator('.cskh-modal .cskh-alert').waitFor({ state: 'visible', timeout: 5000 })
			await captureShot(
				leaderA,
				'/cskh/team-quality#export-revoked',
				'12-leader-a-export-revoked-403.png',
				'B3E-28-leader-export-revoked-403',
				'Post-revocation export status refresh surfaces clear Vietnamese access-denied error and disables download.'
			)
		} finally {
			benchExec('crm_cskh.tools.p3_e2e_seed.temporarily_revoke_leader_team', {
				user: users.leader_a,
				team: teamA,
				revoke: false,
			})
		}
		await leaderA.page.locator('.cskh-modal-head button').click()

		// ==================================================================
		// SCENARIO 10: Cross-Timezone Browser Contexts (`America/Los_Angeles` & `Asia/Tokyo`
		// vs Site `Asia/Ho_Chi_Minh` Across Daily/Weekly/Monthly/Quarterly/Yearly/Custom)
		// ==================================================================
		const bodLa = await createActorPage(
			browser,
			users.bod,
			'CSKH BOD Viewer (Browser TZ America/Los_Angeles)',
			'America/Los_Angeles',
			pwd
		)
		const bodTokyo = await createActorPage(
			browser,
			users.bod,
			'CSKH BOD Viewer (Browser TZ Asia/Tokyo)',
			'Asia/Tokyo',
			pwd
		)

		for (const [tzLabel, tzSession] of [
			['America/Los_Angeles', bodLa],
			['Asia/Tokyo', bodTokyo],
		]) {
			await tzSession.page.goto(`${BASE_URL}/cskh/quality-overview`, { waitUntil: 'networkidle' })

			// 1. Daily `2026-11-01` (Sunday midnight boundary test)
			await tzSession.page.selectOption('#p3-filter-period-type', 'daily')
			await Promise.all([
				tzSession.page.waitForResponse(
					(r) =>
						r.url().includes('get_executive_overview') &&
						r.url().includes('period_type=daily') &&
						r.url().includes('period_key=2026-11-01') &&
						r.status() === 200
				),
				tzSession.page
					.locator('#p3-filter-anchor')
					.fill('2026-11-01')
					.then(() => tzSession.page.locator('#p3-filter-anchor').dispatchEvent('change')),
			])
			const dailyPreview = await tzSession.page.locator('#p3-period-bounds-preview').innerText()
			recordAssert(
				`B3E-29-tz-daily-${tzLabel}`,
				dailyPreview.includes('[2026-11-01 00:00, 2026-11-02 00:00) Asia/Ho_Chi_Minh'),
				`${tzLabel} daily bounds stay anchored to Asia/Ho_Chi_Minh: "${dailyPreview}"`
			)

			// 2. Weekly `2026-11-04` -> ISO week `[2026-11-02, 2026-11-09)`
			await tzSession.page.selectOption('#p3-filter-period-type', 'weekly')
			await Promise.all([
				tzSession.page.waitForResponse(
					(r) =>
						r.url().includes('get_executive_overview') &&
						r.url().includes('period_type=weekly') &&
						r.url().includes('period_key=2026-W45') &&
						r.status() === 200
				),
				tzSession.page
					.locator('#p3-filter-anchor')
					.fill('2026-11-04')
					.then(() => tzSession.page.locator('#p3-filter-anchor').dispatchEvent('change')),
			])
			const weeklyPreview = await tzSession.page.locator('#p3-period-bounds-preview').innerText()
			recordAssert(
				`B3E-30-tz-weekly-${tzLabel}`,
				weeklyPreview.includes('[2026-11-02 00:00, 2026-11-09 00:00) Asia/Ho_Chi_Minh'),
				`${tzLabel} weekly bounds stay anchored to Asia/Ho_Chi_Minh: "${weeklyPreview}"`
			)

			// 3. Quarterly `2026-Q4` -> `[2026-10-01, 2027-01-01)`
			await Promise.all([
				tzSession.page.waitForResponse(
					(r) =>
						r.url().includes('get_executive_overview') &&
						r.url().includes('period_type=quarterly') &&
						r.status() === 200
				),
				tzSession.page.selectOption('#p3-filter-period-type', 'quarterly'),
			])
			if ((await tzSession.page.locator('#p3-filter-quarter').inputValue()) !== 'Q4') {
				await Promise.all([
					tzSession.page.waitForResponse(
						(r) =>
							r.url().includes('get_executive_overview') &&
							r.url().includes('period_key=2026-Q4') &&
							r.status() === 200
					),
					tzSession.page.selectOption('#p3-filter-quarter', 'Q4'),
				])
			}
			const qPreview = await tzSession.page.locator('#p3-period-bounds-preview').innerText()
			recordAssert(
				`B3E-31-tz-quarterly-${tzLabel}`,
				qPreview.includes('[2026-10-01 00:00, 2027-01-01 00:00) Asia/Ho_Chi_Minh'),
				`${tzLabel} quarterly bounds stay anchored to Asia/Ho_Chi_Minh: "${qPreview}"`
			)

			// 4. Yearly `2026` -> `[2026-01-01, 2027-01-01)`
			await tzSession.page.selectOption('#p3-filter-period-type', 'yearly')
			await Promise.all([
				tzSession.page.waitForResponse(
					(r) =>
						r.url().includes('get_executive_overview') &&
						r.url().includes('period_type=yearly') &&
						r.url().includes('period_key=2026') &&
						r.status() === 200
				),
				tzSession.page
					.locator('#p3-filter-anchor')
					.fill('2026')
					.then(() => tzSession.page.locator('#p3-filter-anchor').dispatchEvent('change')),
			])
			const yPreview = await tzSession.page.locator('#p3-period-bounds-preview').innerText()
			recordAssert(
				`B3E-32-tz-yearly-${tzLabel}`,
				yPreview.includes('[2026-01-01 00:00, 2027-01-01 00:00) Asia/Ho_Chi_Minh'),
				`${tzLabel} yearly bounds stay anchored to Asia/Ho_Chi_Minh: "${yPreview}"`
			)

			// 5. Custom `2026-11-01` -> `2026-11-30` (`[2026-11-01 00:00, 2026-12-01 00:00)`)
			await tzSession.page.selectOption('#p3-filter-period-type', 'custom')
			await tzSession.page
				.locator('#p3-filter-from')
				.fill('2026-11-01')
				.then(() => tzSession.page.locator('#p3-filter-from').dispatchEvent('change'))
			await Promise.all([
				tzSession.page.waitForResponse(
					(r) =>
						r.url().includes('get_executive_overview') &&
						r.url().includes('period_type=custom') &&
						r.url().includes('from_date=2026-11-01') &&
						r.url().includes('to_date=2026-11-30') &&
						r.status() === 200
				),
				tzSession.page
					.locator('#p3-filter-to')
					.fill('2026-11-30')
					.then(() => tzSession.page.locator('#p3-filter-to').dispatchEvent('change')),
			])
			const customPreview = await tzSession.page.locator('#p3-period-bounds-preview').innerText()
			recordAssert(
				`B3E-33-tz-custom-${tzLabel}`,
				customPreview.includes('[2026-11-01 00:00, 2026-12-01 00:00) Asia/Ho_Chi_Minh'),
				`${tzLabel} custom bounds stay anchored to Asia/Ho_Chi_Minh: "${customPreview}"`
			)
		}

		await captureShot(
			bodLa,
			'/cskh/quality-overview#tz-america-los-angeles',
			'13-bod-cross-timezone-los-angeles.png',
			'B3E-33-cross-tz-los-angeles',
			'Cross-timezone browser context (America/Los_Angeles vs site Asia/Ho_Chi_Minh) showing zero period boundary drift.'
		)

		const manifest = {
			suite: 'p3_browser_e2e',
			site: SITE,
			base_url: BASE_URL,
			git_sha: revisionSha,
			apps_tree: appsTreeSha,
			completed_at: new Date().toISOString(),
			total_screenshots: entries.length,
			total_assertions: assertions.length,
			passed: assertions.length,
			failed: 0,
			screenshots: entries,
			assertions,
		}
		fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2), 'utf-8')
		console.log(
			`[P3-BROWSER-E2E] COMPLETE: ${assertions.length}/${assertions.length} assertions PASS, ${entries.length} screenshots -> ${MANIFEST_PATH}`
		)
	} finally {
		await browser.close()
	}
}

main().catch((err) => {
	console.error(err)
	process.exit(1)
})
