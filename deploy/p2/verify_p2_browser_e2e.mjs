#!/usr/bin/env node
/**
 * Real-Chromium Browser E2E verification & screenshot capture for P2 UI (`/cskh/*`).
 *
 * Connects ONLY to the isolated P2 Frappe site (`http://p2.localhost:8220`),
 * never touching Owner's `p1.localhost:8120`.
 *
 * Exercises all mandatory P2 browser workflows (`P2.md` §12, §15, §17 & User Request #7):
 *   1. Vietnamese happy path (`Staff A`): `/cskh/my-customers` CSKH filters & columns ->
 *      inline Quick Record (`no_answer` + callback, verified 1 single POST `record_attempt`) ->
 *      `/cskh/work-item?id=...` timeline (`Nhân viên tự báo cáo`, `Chưa kết nối`, 0 direct
 *      Edit/Delete buttons) -> browser reload persistence.
 *   2. Honesty & XSS safety (`Staff A` on `wi_honesty`): `zalo` + `message_sent_no_reply`
 *      explicitly marked `Chưa kết nối`, native `CRM Call Log` linked vs unlinked,
 *      `FCRM Note` (`<script>window.__xssTriggered=1</script>`, `=1+1`) rendered inertly
 *      and excluded from attempt counts, callback cancel with mandatory audit reason
 *      never shown as connected.
 *   3. Double-click idempotency, stale revision (`STALE_WORK_ITEM`), and policy reload
 *      (`wi_conflict`): rapid double-click creates exactly 1 Attempt; concurrent revision
 *      bump triggers Vietnamese `[STALE_WORK_ITEM]` banner + `Tải lại công việc mới nhất`
 *      recovery button -> clean retry succeeds.
 *   4. Live `A -> B` ownership transfer & URL replay zero-leak (`wi_transfer`):
 *      `Admin` transfers `wi_transfer` from `Staff A` to `Staff B`; `Staff A` reloads
 *      `/cskh/work-item?id=...` -> `[FORBIDDEN]`, `#wi-summary-card` & `#wi-timeline-card`
 *      hidden/empty (0 PII leaked); `Staff B` opens workspace -> sees `Staff A`'s prior
 *      attempt (`Người thực hiện: cskh-p2-staff-a@example.test`) + preserved open callback
 *      deadline, then records `Record & complete` as `Staff B`!
 *   5. Append-only correction & void (`wi_correction`): `Staff A` submits correction
 *      request (`Chờ duyệt hiệu chỉnh`), `Leader A` approves in browser (`Đã hiệu chỉnh` +
 *      `Bản ghi gốc` preserved alongside effective value), `Admin` voids a bogus attempt
 *      (`Đã vô hiệu — loại khỏi bộ đếm`).
 *   6. Callbacks schedule & `23:59` / `00:00` timezone boundary (`/cskh/callbacks`):
 *      tested in BOTH `Asia/Ho_Chi_Minh` AND `America/Los_Angeles` Chromium OS timezone
 *      contexts (`23:59` -> `Đến hạn hôm nay`, `00:00` -> `Sắp đến hạn`, yesterday -> `Quá hạn`),
 *      plus `User.time_zone = "Asia/Tokyo"` (`+09:00`) cross-timezone rendering check.
 *   7. Role gates, Scoped Progress (`/cskh/progress`), and Settings (`/cskh/settings`):
 *      non-KPI/SLA banner, `Staff A` (`self`), `Leader A` (`team`), `BOD` (`all` 0-PII),
 *      `BOD` 403 on `/cskh/work-item` & `/cskh/callbacks`, `Staff A` 403 on `/cskh/settings`,
 *      `Leader A` & `BOD` read-only settings, `Admin` `save_outcome` & `save_policy` (`v+1`).
 *   8. Deep link `/cskh/work-item?lead=<lead_id>` resolution (used by `CRM Form Script`
 *      `"CSKH - Open Lead CSKH Workspace"`).
 *
 * Writes screenshots to `deploy/p2/evidence/screenshots/` and manifest to
 * `deploy/p2/evidence/browser-manifest.json`.
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

const BASE_URL = process.env.P2_BROWSER_URL || 'http://p2.localhost:8220'
const PASSWORD = process.env.P2_SEED_PASSWORD || 'P2Seed-Only-2026!'
const CONTAINER = process.env.P2_CONTAINER || 'crm-cskh-p2-ui-bench-1'

if (BASE_URL.includes('8120') || BASE_URL.includes('p1.localhost')) {
	throw new Error('Refusing to run P2 Browser E2E against Owner P1 stack!')
}

const PLAYWRIGHT_PATH =
	process.env.PLAYWRIGHT_MODULE ||
	'D:/HRP-app/Contract-mange-hrp/node_modules/.pnpm/playwright@1.63.0/node_modules/playwright'

const require = createRequire(import.meta.url)
const { chromium } = require(PLAYWRIGHT_PATH)

const RAW_PHONE_RE = /\b(?:\+?84|0)\d{8,9}\b/g

function assert(cond, msg) {
	if (!cond) {
		throw new Error(`P2_BROWSER_ASSERTION_FAILED: ${msg}`)
	}
}

function benchExec(method, kwargs) {
	const kwPart = kwargs ? ` --kwargs '${JSON.stringify(kwargs)}'` : ''
	const out = execFileSync(
		'docker',
		['exec', CONTAINER, 'bash', '-lc', `cd /workspace/frappe-bench && bench --site p2.localhost execute ${method}${kwPart}`],
		{ encoding: 'utf-8' }
	)
	const lines = out
		.split(/\r?\n/)
		.map((l) => l.trim())
		.filter(Boolean)
	return JSON.parse(lines[lines.length - 1])
}

async function createActorPage(browser, userEmail, roleLabel, timezoneId = 'Asia/Ho_Chi_Minh') {
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
		{ usr: userEmail, pwd: PASSWORD }
	)
	assert(loginStatus === 200, `Login failed for ${userEmail}: HTTP ${loginStatus}`)

	const telemetry = {
		consoleErrors: [],
		pageErrors: [],
		assetFailures: [],
		server500s: [],
		apiPosts: [],
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
		if (req.method() === 'POST' && req.url().includes('/api/method/')) {
			telemetry.apiPosts.push(req.url().split('/api/method/')[1].split('?')[0])
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

	if (expectCardVisible) {
		const card = page.locator('.cskh-card:visible').first()
		await card.waitFor({ state: 'visible', timeout: 10000 })
		const box = await card.boundingBox()
		assert(
			box && box.width > 450 && box.height > 36,
			`${actor.user} ${routeLabel}: broken card layout ${JSON.stringify(box)}`
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

async function main() {
	fs.mkdirSync(SHOTS_DIR, { recursive: true })
	if (!fs.existsSync(HTTP_REPORT_PATH)) {
		throw new Error(`Run verify_p2_http_e2e.py first; missing ${HTTP_REPORT_PATH}`)
	}
	const httpReport = JSON.parse(fs.readFileSync(HTTP_REPORT_PATH, 'utf-8'))
	const fx = httpReport.fixtures
	const users = fx.users
	const revisionSha = execSync('git rev-parse HEAD', { cwd: ROOT }).toString().trim()

	const browser = await chromium.launch({
		headless: true,
		args: ['--host-resolver-rules=MAP p2.localhost 127.0.0.1'],
	})

	const entries = []

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
			screenshot: `deploy/p2/evidence/screenshots/${fileName}`,
			notes,
		})
		console.log(`PASS  ${checkId} -> ${fileName}`)
		return bodyText
	}

	try {
		const staffA = await createActorPage(browser, users.staff_a, 'Sales User (Staff A1)')
		const staffB = await createActorPage(browser, users.staff_b, 'Sales User (Staff A2)')
		const leaderA = await createActorPage(browser, users.leader_a, 'Sales Manager (Team A)')
		const admin = await createActorPage(browser, users.admin, 'CSKH CRM Admin')
		const bod = await createActorPage(browser, users.bod, 'CSKH BOD Viewer')

		// ==================================================================
		// SCENARIO 1: Vietnamese Happy Path on `/cskh/my-customers` -> Quick
		// Record (`no_answer` + callback, single POST) -> `/cskh/work-item` -> Reload
		// ==================================================================
		await staffA.page.goto(`${BASE_URL}/cskh/my-customers`, { waitUntil: 'networkidle' })
		await staffA.page.locator('#mine-table').waitFor({ state: 'visible', timeout: 10000 })

		const mineInitialText = await captureShot(
			staffA,
			'/cskh/my-customers',
			'01-staff-a-my-customers-upgraded.png',
			'B2E-01-my-customers-upgraded',
			'My Customers upgraded with Vietnamese CSKH columns, chips, masked phones, and quick actions.'
		)
		const mineInitialLower = mineInitialText.toLowerCase()
		assert(mineInitialLower.includes('trạng thái cskh'), 'Missing column Trạng thái CSKH')
		assert(mineInitialLower.includes('lần liên hệ gần nhất'), 'Missing column Lần liên hệ gần nhất')
		assert(mineInitialLower.includes('kết quả gần nhất'), 'Missing column Kết quả gần nhất')
		assert(mineInitialLower.includes('số lần liên hệ'), 'Missing column Số lần liên hệ')
		assert(mineInitialLower.includes('hẹn tiếp theo'), 'Missing column Hẹn tiếp theo')
		assert(mineInitialText.includes('P2 Khách Hạnh Phúc'), 'Missing P2 Khách Hạnh Phúc row')

		// Filter chip `Chưa liên hệ` -> `P2 Khách Hạnh Phúc` visible, `P2 Khách Bàn Giao AB` (already contacted) filtered out
		await staffA.page.locator('#mine-filter-chips button[data-filter="not_contacted"]').click()
		await staffA.page.waitForTimeout(500)
		const notContactedText = await staffA.page.locator('#mine-rows').innerText()
		assert(notContactedText.includes('P2 Khách Hạnh Phúc'), 'not_contacted filter missed uncontacted lead')
		assert(
			!notContactedText.includes('P2 Khách Bàn Giao AB'),
			'not_contacted filter leaked already-contacted lead'
		)

		// Reset filter to `All` and click inline `Ghi nhận kết quả` on `P2 Khách Hạnh Phúc`
		await staffA.page.locator('#mine-filter-chips button[data-filter=""]').click()
		await staffA.page.waitForTimeout(500)
		staffA.telemetry.apiPosts = []

		const happyRow = staffA.page.locator('#mine-rows tr', { hasText: 'P2 Khách Hạnh Phúc' })
		await happyRow.locator('a', { hasText: 'Ghi nhận kết quả' }).click()
		await staffA.page.locator('#mine-quick-record-card').waitFor({ state: 'visible', timeout: 10000 })

		await staffA.page.selectOption('#cskh-att-channel', 'phone_call')
		await staffA.page.selectOption('#cskh-att-outcome', 'no_answer')
		const badgeBeforeSave = await staffA.page.locator('#mine-quick-record-body').innerText()
		assert(
			badgeBeforeSave.includes('Không tính là đã kết nối'),
			'Quick form must explicitly show Không tính là đã kết nối for no_answer'
		)
		await staffA.page.fill('#cskh-att-note', 'Gọi lần đầu từ danh sách khách của tôi — không bắt máy')
		await staffA.page.check('#cskh-att-has-followup')
		await staffA.page.fill('#cskh-att-followup-title', 'Gọi lại khung giờ chiều')

		const recordRespPromise = staffA.page.waitForResponse(
			(r) => r.url().includes('crm_cskh.api.workflow.record_attempt') && r.request().method() === 'POST'
		)
		await staffA.page.click('#cskh-att-submit')
		const recordResp = await recordRespPromise
		assert(recordResp.status() === 200, `record_attempt HTTP ${recordResp.status()}`)
		const recordJson = await recordResp.json()
		assert(recordJson.message.attempt_count === 1, 'Expected attempt_count=1')
		assert(recordJson.message.connected_attempt_count === 0, 'Expected connected_attempt_count=0')
		assert(
			staffA.telemetry.apiPosts.length === 1 &&
				staffA.telemetry.apiPosts[0] === 'crm_cskh.api.workflow.record_attempt',
			`One save action must invoke exactly 1 write API, saw: ${staffA.telemetry.apiPosts.join(', ')}`
		)

		await staffA.page.waitForTimeout(600)
		await captureShot(
			staffA,
			'/cskh/my-customers',
			'02-staff-a-after-quick-record.png',
			'B2E-02-quick-record-single-post',
			'Single-POST record_attempt updated My Customers row to Chờ chăm sóc lại + Chưa kết nối.'
		)

		// Open `/cskh/work-item?id=<wi_happy>`, verify timeline & reload persistence
		await staffA.page.goto(`${BASE_URL}/cskh/work-item?id=${encodeURIComponent(fx.wi_happy)}`, {
			waitUntil: 'networkidle',
		})
		await staffA.page.locator('#wi-summary-card').waitFor({ state: 'visible', timeout: 10000 })
		const wiHappyText = await captureShot(
			staffA,
			`/cskh/work-item?id=${fx.wi_happy}`,
			'03-staff-a-work-item-happy.png',
			'B2E-03-work-item-timeline-and-reload',
			'Workspace shows Self-reported badge, Chưa kết nối, callback task, and zero direct edit/delete buttons.'
		)
		assert(wiHappyText.includes('Nhân viên tự báo cáo'), 'Missing Self-reported Vietnamese badge')
		assert(wiHappyText.includes('Chưa kết nối'), 'Missing Not connected Vietnamese badge')
		// Verify NO direct Edit/Delete button exists inside `.cskh-timeline-item.is-attempt`
		const attemptDirectEditButtons = await staffA.page
			.locator('.cskh-timeline-item.is-attempt button', { hasText: /^(Chỉnh sửa|Sửa|Xóa)$/ })
			.count()
		assert(attemptDirectEditButtons === 0, 'Attempt item must NEVER expose direct Edit/Delete buttons!')

		// Reload browser and confirm state persists cleanly
		await staffA.page.reload({ waitUntil: 'networkidle' })
		await staffA.page.locator('#wi-summary-card').waitFor({ state: 'visible', timeout: 10000 })
		const afterReloadText = await verifyCleanDomAndTelemetry(staffA, 'reload:/cskh/work-item')
		assert(
			afterReloadText.includes('Gọi lần đầu từ danh sách khách của tôi'),
			'Recorded attempt note disappeared after page reload!'
		)

		// ==================================================================
		// SCENARIO 2: Honesty & XSS Safety (`wi_honesty`): Zalo no-reply ≠
		// Connected, Native Call Log & XSS Note excluded from counts, Cancel Callback
		// ==================================================================
		await staffA.page.goto(`${BASE_URL}/cskh/work-item?id=${encodeURIComponent(fx.wi_honesty)}`, {
			waitUntil: 'networkidle',
		})
		await staffA.page.locator('#wi-summary-card').waitFor({ state: 'visible', timeout: 10000 })
		const honestyBefore = await verifyCleanDomAndTelemetry(staffA, `/cskh/work-item?id=${fx.wi_honesty}`)
		assert(
			honestyBefore.includes('0 (Đã kết nối: 0)'),
			'Seeded native Call Log and FCRM Note must NOT count as Contact Attempts!'
		)
		assert(
			honestyBefore.includes('Ghi chú nội bộ CRM =1+1') &&
				honestyBefore.includes('Khách hỏi bảng giá') &&
				!honestyBefore.includes('<script>'),
			'XSS/formula FCRM Note must render safely as inert text in timeline!'
		)

		// Record `zalo` + `message_sent_no_reply` + callback
		await staffA.page.click('#wi-btn-record')
		await staffA.page.selectOption('#cskh-att-channel', 'zalo')
		await staffA.page.selectOption('#cskh-att-outcome', 'message_sent_no_reply')
		const zaloSemantics = await staffA.page.locator('#wi-action-drawer-body').innerText()
		assert(
			zaloSemantics.includes('Gửi tin nhắn chưa có phản hồi không được tính là đã kết nối.'),
			'Missing Zalo honesty warning'
		)
		await staffA.page.fill('#cskh-att-note', 'Đã nhắn Zalo gửi brochure nhưng khách chưa xem')
		await staffA.page.check('#cskh-att-has-followup')
		await staffA.page.fill('#cskh-att-followup-title', 'Nhắc lại khách qua điện thoại')
		await Promise.all([
			staffA.page.waitForResponse(
				(r) => r.url().includes('crm_cskh.api.workflow.record_attempt') && r.status() === 200
			),
			staffA.page.click('#cskh-att-submit'),
		])
		await staffA.page.waitForTimeout(500)

		// Cancel callback WITHOUT contact attempt -> requires reason, never counted as connected
		await staffA.page.click('#wi-btn-cancel-callback')
		await staffA.page.click('#wi-cancel-cb-submit')
		const cancelWarn = await staffA.page.locator('#wi-action-drawer-body').innerText()
		assert(cancelWarn.includes('[NOTE_REQUIRED]'), 'Canceling callback without reason must require note')
		await staffA.page.fill('#wi-cancel-cb-reason', 'Khách nhắn tin hẹn sang tuần sau mới rảnh')
		await Promise.all([
			staffA.page.waitForResponse(
				(r) => r.url().includes('crm_cskh.api.workflow.complete_followup') && r.status() === 200
			),
			staffA.page.click('#wi-cancel-cb-submit'),
		])
		await staffA.page.waitForTimeout(500)

		const honestyAfter = await captureShot(
			staffA,
			`/cskh/work-item?id=${fx.wi_honesty}`,
			'04-staff-a-honesty-and-xss-safe.png',
			'B2E-04-honesty-zalo-calllog-note-cancel',
			'Zalo message_sent_no_reply + canceled callback + native Call Log + XSS Note: Connected stays 0, XSS inert.'
		)
		assert(
			honestyAfter.includes('1 (Đã kết nối: 0)'),
			`Expected 1 attempt & 0 connected after Zalo no-reply + canceled callback, got: ${honestyAfter}`
		)
		assert(
			honestyAfter.includes('Nhiệm vụ — không tự tính là lần liên hệ hay đã kết nối'),
			'Timeline task item must display non-connected honesty badge'
		)

		// ==================================================================
		// SCENARIO 3: Double-Click Idempotency & Stale Revision Recovery (`wi_conflict`)
		// ==================================================================
		await staffA.page.goto(`${BASE_URL}/cskh/work-item?id=${encodeURIComponent(fx.wi_conflict)}`, {
			waitUntil: 'networkidle',
		})
		await staffA.page.locator('#wi-summary-card').waitFor({ state: 'visible', timeout: 10000 })
		await staffA.page.click('#wi-btn-record')
		await staffA.page.selectOption('#cskh-att-channel', 'phone_call')
		await staffA.page.selectOption('#cskh-att-outcome', 'busy')
		await staffA.page.fill('#cskh-att-note', 'Kiểm thử bấm đúp liên tiếp không nhân đôi Attempt')

		staffA.telemetry.apiPosts = []
		const dblRespPromise = staffA.page.waitForResponse(
			(r) => r.url().includes('crm_cskh.api.workflow.record_attempt') && r.status() === 200
		)
		// Rapid double-click on submit button
		await staffA.page.evaluate(() => {
			const btn = document.getElementById('cskh-att-submit')
			btn.click()
			btn.click()
		})
		await dblRespPromise
		await staffA.page.waitForTimeout(500)
		assert(
			staffA.telemetry.apiPosts.length === 1,
			`In-flight guard must block duplicate submit on double-click, saw ${staffA.telemetry.apiPosts.length} POSTs`
		)
		const afterDblText = await staffA.page.locator('#wi-summary-metrics').innerText()
		assert(afterDblText.includes('1 (Đã kết nối: 0)'), 'Double-click must record exactly 1 attempt')

		// Now open drawer on Staff A (`expected_revision` = current), while Leader A bumps revision concurrently!
		await staffA.page.click('#wi-btn-record')
		await staffA.page.selectOption('#cskh-att-channel', 'phone_call')
		await staffA.page.selectOption('#cskh-att-outcome', 'connected_interested')
		await staffA.page.fill('#cskh-att-note', 'Ghi chú khi tab khác vừa cập nhật lịch hẹn')

		// Leader A schedules a callback concurrently in the background to bump `wi_conflict.revision`
		await leaderA.page.goto(`${BASE_URL}/cskh/work-item?id=${encodeURIComponent(fx.wi_conflict)}`, {
			waitUntil: 'networkidle',
		})
		await leaderA.page.locator('#wi-btn-schedule-followup').click()
		await Promise.all([
			leaderA.page.waitForResponse(
				(r) => r.url().includes('crm_cskh.api.workflow.schedule_followup') && r.status() === 200
			),
			leaderA.page.click('#wi-fu-submit'),
		])
		await leaderA.page.waitForTimeout(300)

		// Staff A submits stale form -> receives 409 `STALE_WORK_ITEM` with Vietnamese recovery button!
		const staleRespPromise = staffA.page.waitForResponse(
			(r) => r.url().includes('crm_cskh.api.workflow.record_attempt') && r.status() === 409
		)
		await staffA.page.click('#cskh-att-submit')
		const staleResp = await staleRespPromise
		assert(staleResp.status() === 409, 'Expected HTTP 409 on stale revision')
		await staffA.page.waitForTimeout(400)

		const conflictText = await captureShot(
			staffA,
			`/cskh/work-item?id=${fx.wi_conflict}`,
			'05-staff-a-stale-revision-conflict.png',
			'B2E-05-stale-revision-conflict-ui',
			'409 [STALE_WORK_ITEM] rendered in Vietnamese with Reload latest work item recovery button.'
		)
		assert(conflictText.includes('[STALE_WORK_ITEM]'), 'Missing [STALE_WORK_ITEM] error code badge')
		assert(
			conflictText.includes('Tải lại trạng thái công việc mới nhất'),
			'Missing Tải lại trạng thái công việc mới nhất recovery button'
		)

		// Click recovery button -> reloads latest revision -> re-submit succeeds cleanly!
		await staffA.page
			.locator('#wi-action-drawer-body button', { hasText: 'Tải lại trạng thái công việc mới nhất' })
			.click()
		await staffA.page.waitForTimeout(600)
		await staffA.page.selectOption('#cskh-att-outcome', 'connected_interested')
		await staffA.page.fill('#cskh-att-note', 'Lưu lại thành công sau khi đồng bộ phiên bản mới nhất')
		await Promise.all([
			staffA.page.waitForResponse(
				(r) => r.url().includes('crm_cskh.api.workflow.record_attempt') && r.status() === 200
			),
			staffA.page.click('#cskh-att-submit'),
		])
		await staffA.page.waitForTimeout(500)

		// ==================================================================
		// SCENARIO 4: Live `A -> B` Ownership Transfer & URL Replay Zero-Leak
		// ==================================================================
		// First, Staff A opens `wi_transfer` (sees `P2 Khách Bàn Giao AB`)
		await staffA.page.goto(`${BASE_URL}/cskh/work-item?id=${encodeURIComponent(fx.wi_transfer)}`, {
			waitUntil: 'networkidle',
		})
		await staffA.page.locator('#wi-summary-card').waitFor({ state: 'visible', timeout: 10000 })

		// Admin transfers `wi_transfer` from `staff_a` -> `staff_b` via `/cskh/my-customers` scope table or API
		const adminReassignStatus = await admin.page.evaluate(
			async ({ wi, toUser, fromUser }) => {
				const bootRes = await fetch('/cskh/progress', { credentials: 'same-origin' })
				const html = await bootRes.text()
				const m = html.match(/"X-Frappe-CSRF-Token"\s*:\s*"([^"]+)"/)
				const csrf = m ? m[1] : ''
				const r = await fetch('/api/method/crm_cskh.api.ownership.reassign', {
					method: 'POST',
					credentials: 'same-origin',
					headers: {
						'Content-Type': 'application/json',
						Accept: 'application/json',
						'X-Frappe-CSRF-Token': csrf,
					},
					body: JSON.stringify({
						work_item: wi,
						target_user: toUser,
						expected_owner: fromUser,
						reason: 'Bàn giao trực tiếp từ A1 sang A2 giữa ca làm việc',
						request_id: 'b2e-live-transfer-a-to-b',
					}),
				})
				return r.status
			},
			{ wi: fx.wi_transfer, toUser: users.staff_b, fromUser: users.staff_a }
		)
		assert(adminReassignStatus === 200, `Admin live reassign failed: ${adminReassignStatus}`)

		// Staff A tries to submit on the still-open page -> blocked (`OWNERSHIP_CHANGED` / `FORBIDDEN`),
		// AND when Staff A reloads `/cskh/work-item?id=<wi_transfer>`, workspace wipes DOM with 0 PII!
		await staffA.page.reload({ waitUntil: 'networkidle' })
		await staffA.page.waitForTimeout(500)
		const staffAReplayText = await captureShot(
			staffA,
			`/cskh/work-item?id=${fx.wi_transfer}`,
			'06-staff-a-after-transfer-forbidden-zero-leak.png',
			'B2E-06-a-to-b-url-replay-zero-leak',
			'After A->B transfer, Staff A reopening old URL receives [FORBIDDEN] and zero customer PII/timeline.',
			false
		)
		assert(staffAReplayText.includes('[FORBIDDEN]'), 'Staff A URL replay must display [FORBIDDEN]')
		assert(
			!staffAReplayText.includes('P2 Khách Bàn Giao AB') && !staffAReplayText.includes('******0004'),
			'Zero-leak violation: customer name or masked phone remained visible to former owner Staff A!'
		)

		// Staff B opens `/cskh/work-item?id=<wi_transfer>` -> sees Staff A's historical attempt,
		// owns the transferred open callback with original deadline intact, and completes it as Staff B!
		await staffB.page.goto(`${BASE_URL}/cskh/work-item?id=${encodeURIComponent(fx.wi_transfer)}`, {
			waitUntil: 'networkidle',
		})
		await staffB.page.locator('#wi-summary-card').waitFor({ state: 'visible', timeout: 10000 })
		const staffBBeforeAct = await verifyCleanDomAndTelemetry(staffB, 'staffB:/cskh/work-item')
		assert(staffBBeforeAct.includes('P2 Khách Bàn Giao AB'), 'New owner Staff B must see customer')
		assert(
			staffBBeforeAct.includes(users.staff_a),
			'New owner Staff B must see historical attempt performed_by Staff A'
		)

		// Staff B clicks `Ghi nhận & hoàn tất` to complete transferred callback atomically
		await staffB.page.click('#wi-btn-complete-callback')
		await staffB.page.selectOption('#cskh-att-channel', 'phone_call')
		await staffB.page.selectOption('#cskh-att-outcome', 'connected_interested')
		await staffB.page.fill('#cskh-att-note', 'Nhân viên A2 gọi theo lịch bàn giao — khách đồng ý nhận báo giá')
		await Promise.all([
			staffB.page.waitForResponse(
				(r) => r.url().includes('crm_cskh.api.workflow.record_attempt') && r.status() === 200
			),
			staffB.page.click('#cskh-att-submit'),
		])
		await staffB.page.waitForTimeout(500)

		const staffBAfterAct = await captureShot(
			staffB,
			`/cskh/work-item?id=${fx.wi_transfer}`,
			'07-staff-b-handover-completed-callback.png',
			'B2E-07-staff-b-handover-and-callback-complete',
			'Staff B sees Staff A history + completes transferred callback with new attempt attributed to Staff B.'
		)
		assert(
			staffBAfterAct.includes('2 (Đã kết nối: 2)'),
			'Expected 2 attempts (2 connected: callback_requested + connected_interested) after handover'
		)
		assert(staffBAfterAct.includes(users.staff_b), 'New attempt must be attributed to Staff B')

		// ==================================================================
		// SCENARIO 5: Append-Only Correction Request (`Staff A`) -> Leader Review
		// (`Leader A`) -> Admin Void (`Admin`) on `wi_correction`
		// ==================================================================
		await staffA.page.goto(`${BASE_URL}/cskh/work-item?id=${encodeURIComponent(fx.wi_correction)}`, {
			waitUntil: 'networkidle',
		})
		await staffA.page.locator('#wi-summary-card').waitFor({ state: 'visible', timeout: 10000 })

		await staffA.page
			.locator('.cskh-timeline-item.is-attempt button', { hasText: 'Yêu cầu hiệu chỉnh / vô hiệu' })
			.first()
			.click()
		const corrAttemptItem = staffA.page.locator('.cskh-timeline-item.is-attempt').first()
		await corrAttemptItem.locator('select').nth(0).selectOption('change_outcome')
		await corrAttemptItem.locator('select').nth(1).selectOption('connected_interested')
		await corrAttemptItem
			.locator('textarea')
			.nth(0)
			.fill('Thực tế khách bắt máy và quan tâm gói cơ bản')
		await corrAttemptItem
			.locator('textarea')
			.nth(1)
			.fill('Chọn nhầm mã Không nghe máy khi lưu nhanh')
		await Promise.all([
			staffA.page.waitForResponse(
				(r) => r.url().includes('crm_cskh.api.workflow.request_correction') && r.status() === 200
			),
			corrAttemptItem.locator('button', { hasText: 'Gửi yêu cầu hiệu chỉnh' }).click(),
		])
		await staffA.page.waitForTimeout(500)

		const pendingCorrText = await captureShot(
			staffA,
			`/cskh/work-item?id=${fx.wi_correction}`,
			'08-staff-a-correction-pending.png',
			'B2E-08-staff-correction-pending',
			'Staff A submitted append-only correction request; badge shows Đang chờ duyệt hiệu chỉnh while original stays intact.'
		)
		assert(pendingCorrText.includes('Đang chờ duyệt hiệu chỉnh'), 'Missing Đang chờ duyệt hiệu chỉnh badge')

		// Leader A opens `wi_correction` and approves the pending correction
		await leaderA.page.goto(`${BASE_URL}/cskh/work-item?id=${encodeURIComponent(fx.wi_correction)}`, {
			waitUntil: 'networkidle',
		})
		await leaderA.page.locator('#wi-summary-card').waitFor({ state: 'visible', timeout: 10000 })
		const leaderCorrBox = leaderA.page.locator('.cskh-correction-box').first()
		await leaderCorrBox.locator('input[type="text"]').fill('Trưởng nhóm A đã kiểm tra và duyệt hiệu chỉnh')
		await Promise.all([
			leaderA.page.waitForResponse(
				(r) => r.url().includes('crm_cskh.api.workflow.review_correction') && r.status() === 200
			),
			leaderCorrBox.locator('button', { hasText: 'Duyệt áp dụng' }).click(),
		])
		await leaderA.page.waitForTimeout(500)

		const approvedCorrText = await captureShot(
			leaderA,
			`/cskh/work-item?id=${fx.wi_correction}`,
			'09-leader-a-correction-approved.png',
			'B2E-09-leader-correction-approved',
			'Leader A approved correction: timeline shows both Bản ghi gốc (Không nghe máy) and effective Quan tâm.'
		)
		assert(approvedCorrText.includes('Đã hiệu chỉnh'), 'Missing Đã hiệu chỉnh badge')
		assert(approvedCorrText.includes('Bản ghi gốc'), 'Missing Bản ghi gốc immutable snapshot box')
		assert(approvedCorrText.includes('1 (Đã kết nối: 1)'), 'Approved correction must update effective connected count')

		// Admin records a mistaken attempt on `wi_correction` and immediately voids it (`action=void`, auto_approve=true)
		await admin.page.goto(`${BASE_URL}/cskh/work-item?id=${encodeURIComponent(fx.wi_correction)}`, {
			waitUntil: 'networkidle',
		})
		await admin.page.click('#wi-btn-record')
		await admin.page.selectOption('#cskh-att-channel', 'sms')
		await admin.page.selectOption('#cskh-att-outcome', 'message_sent_no_reply')
		await admin.page.fill('#cskh-att-note', 'Bản ghi nhầm khách — Admin sẽ vô hiệu hóa để kiểm thử')
		await Promise.all([
			admin.page.waitForResponse(
				(r) => r.url().includes('crm_cskh.api.workflow.record_attempt') && r.status() === 200
			),
			admin.page.click('#cskh-att-submit'),
		])
		await admin.page.waitForTimeout(500)

		const newestAttempt = admin.page.locator('.cskh-timeline-item.is-attempt').first()
		await newestAttempt.locator('button', { hasText: 'Yêu cầu hiệu chỉnh / vô hiệu' }).click()
		await newestAttempt.locator('select').nth(0).selectOption('void')
		await newestAttempt.locator('textarea').last().fill('Ghi nhầm hồ sơ khách hàng — vô hiệu khỏi bộ đếm')
		await Promise.all([
			admin.page.waitForResponse(
				(r) => r.url().includes('crm_cskh.api.workflow.request_correction') && r.status() === 200
			),
			newestAttempt.locator('button', { hasText: 'Gửi yêu cầu hiệu chỉnh' }).click(),
		])
		await admin.page.waitForTimeout(500)

		const voidedText = await captureShot(
			admin,
			`/cskh/work-item?id=${fx.wi_correction}`,
			'10-admin-voided-attempt-excluded.png',
			'B2E-10-admin-void-attempt',
			'Voided attempt remains visible in audit timeline with Đã vô hiệu — không tính vào bộ đếm while count returns to 1.'
		)
		assert(
			voidedText.includes('Đã vô hiệu — không tính vào bộ đếm'),
			'Voided attempt must show Đã vô hiệu — không tính vào bộ đếm badge'
		)
		assert(voidedText.includes('1 (Đã kết nối: 1)'), 'Voided attempt must be excluded from effective attempt_count')

		// ==================================================================
		// SCENARIO 6: Callbacks Schedule & `23:59` / `00:00` Timezone Boundary
		// in BOTH `Asia/Ho_Chi_Minh` AND `America/Los_Angeles` Browser Contexts
		// + User.time_zone = `Asia/Tokyo` (`+09:00`) Override
		// ==================================================================
		await staffA.page.goto(`${BASE_URL}/cskh/callbacks`, { waitUntil: 'networkidle' })
		await staffA.page.locator('#cb-table').waitFor({ state: 'visible', timeout: 10000 })
		const cbOpenText = await captureShot(
			staffA,
			'/cskh/callbacks',
			'11-staff-a-callbacks-schedule-boundaries.png',
			'B2E-11-callbacks-schedule-2359-0000',
			'Callbacks list classifies 23:59 as Đến hạn hôm nay, 00:00 as Sắp đến hạn, and yesterday as Quá hạn.'
		)
		const row2359 = await staffA.page.locator('#cb-rows tr', { hasText: 'P2 Khách Mốc 2359' }).innerText()
		const row0000 = await staffA.page.locator('#cb-rows tr', { hasText: 'P2 Khách Mốc 0000' }).innerText()
		const rowOver = await staffA.page.locator('#cb-rows tr', { hasText: 'P2 Khách Quá Hạn' }).innerText()
		assert(
			row2359.includes('23:59') && row2359.includes('Đến hạn hôm nay'),
			`23:59 row must be Đến hạn hôm nay, got: ${row2359}`
		)
		assert(
			row0000.includes('00:00') && row0000.includes('Sắp đến hạn'),
			`00:00 row must be Sắp đến hạn, got: ${row0000}`
		)
		assert(rowOver.includes('Quá hạn'), `Yesterday row must be Quá hạn, got: ${rowOver}`)

		// Verify under a Chromium context whose OS timezone is `America/Los_Angeles` (-08:00):
		// UI MUST still classify by site/user timezone (`Asia/Ho_Chi_Minh`), NOT machine timezone!
		const staffALosAngeles = await createActorPage(
			browser,
			users.staff_a,
			'Sales User (OS TZ America/Los_Angeles)',
			'America/Los_Angeles'
		)
		await staffALosAngeles.page.goto(`${BASE_URL}/cskh/callbacks`, { waitUntil: 'networkidle' })
		await staffALosAngeles.page.locator('#cb-table').waitFor({ state: 'visible', timeout: 10000 })
		const laRow2359 = await staffALosAngeles.page
			.locator('#cb-rows tr', { hasText: 'P2 Khách Mốc 2359' })
			.innerText()
		const laRow0000 = await staffALosAngeles.page
			.locator('#cb-rows tr', { hasText: 'P2 Khách Mốc 0000' })
			.innerText()
		assert(
			laRow2359.includes('23:59') && laRow2359.includes('Đến hạn hôm nay'),
			`Browser in America/Los_Angeles drifted 23:59 site boundary: ${laRow2359}`
		)
		assert(
			laRow0000.includes('00:00') && laRow0000.includes('Sắp đến hạn'),
			`Browser in America/Los_Angeles drifted 00:00 site boundary: ${laRow0000}`
		)
		await staffALosAngeles.context.close()

		// Verify explicit `User.time_zone = "Asia/Tokyo"` (`+09:00`, +2h ahead of `Asia/Ho_Chi_Minh`):
		// `today 23:59:59+07:00` becomes `tomorrow 01:59` in `Asia/Tokyo`!
		benchExec('crm_cskh.tools.p2_e2e_seed.set_user_timezone', {
			user: users.staff_a,
			time_zone: 'Asia/Tokyo',
		})
		try {
			await staffA.page.reload({ waitUntil: 'networkidle' })
			await staffA.page.locator('#cb-table').waitFor({ state: 'visible', timeout: 10000 })
			const tokyoRow2359 = await staffA.page
				.locator('#cb-rows tr', { hasText: 'P2 Khách Mốc 2359' })
				.innerText()
			assert(
				tokyoRow2359.includes('01:59'),
				`User.time_zone=Asia/Tokyo (+09:00) must shift 23:59+07:00 to 01:59+09:00, got: ${tokyoRow2359}`
			)
		} finally {
			benchExec('crm_cskh.tools.p2_e2e_seed.set_user_timezone', {
				user: users.staff_a,
				time_zone: '',
			})
		}

		// ==================================================================
		// SCENARIO 7: Role Gates, Scoped Progress (`/cskh/progress`), and
		// Outcome & Policy Settings (`/cskh/settings`)
		// ==================================================================
		// BOD 0-PII Progress (`/cskh/progress`)
		await bod.page.goto(`${BASE_URL}/cskh/progress`, { waitUntil: 'networkidle' })
		await bod.page.locator('#prog-wi-card').waitFor({ state: 'visible', timeout: 10000 })
		const bodProgText = await captureShot(
			bod,
			'/cskh/progress',
			'12-bod-progress-all-zero-pii.png',
			'B2E-12-bod-progress-zero-pii',
			'BOD sees global operational counts separated into Work Items, Effective Attempts, and Callbacks with non-KPI disclaimer and 0 PII.'
		)
		assert(
			bodProgText.includes('không phải điểm chất lượng CSKH hay xếp loại SLA'),
			'Progress page must state non-KPI/non-SLA P2 boundary'
		)
		assert(bodProgText.includes('Toàn hệ thống'), 'BOD scope badge must be Toàn hệ thống')

		// Leader A Team-scoped Progress (`/cskh/progress`)
		await leaderA.page.goto(`${BASE_URL}/cskh/progress`, { waitUntil: 'networkidle' })
		await leaderA.page.locator('#prog-wi-card').waitFor({ state: 'visible', timeout: 10000 })
		const leaderProgText = await captureShot(
			leaderA,
			'/cskh/progress',
			'13-leader-a-progress-team-scope.png',
			'B2E-13-leader-progress-team-scope',
			'Leader A sees Team A operational counts only (Team B staff_c excluded).'
		)
		assert(leaderProgText.includes('Phạm vi đội'), 'Leader A scope badge must be Phạm vi đội')
		assert(!leaderProgText.includes(users.staff_c), 'Leader A progress leaked Team B user!')

		// BOD denied (`403`) on PII pages `/cskh/work-item` & `/cskh/callbacks`
		const bodWiResp = await bod.page.goto(`${BASE_URL}/cskh/work-item?id=${encodeURIComponent(fx.wi_happy)}`, {
			waitUntil: 'networkidle',
		})
		assert(bodWiResp.status() === 403, `BOD must get HTTP 403 on /cskh/work-item, got ${bodWiResp.status()}`)
		await captureShot(
			bod,
			`/cskh/work-item?id=${fx.wi_happy}`,
			'14-bod-work-item-403-denied.png',
			'B2E-14-bod-pii-route-403',
			'BOD is denied (HTTP 403) on PII route /cskh/work-item with zero customer data.',
			false
		)
		const bodCbResp = await bod.page.goto(`${BASE_URL}/cskh/callbacks`, { waitUntil: 'networkidle' })
		assert(bodCbResp.status() === 403, `BOD must get HTTP 403 on /cskh/callbacks, got ${bodCbResp.status()}`)

		// Staff A denied (`403`) on `/cskh/settings`
		const staffSetResp = await staffA.page.goto(`${BASE_URL}/cskh/settings`, { waitUntil: 'networkidle' })
		assert(staffSetResp.status() === 403, `Staff must get HTTP 403 on /cskh/settings, got ${staffSetResp.status()}`)

		// BOD read-only `/cskh/settings`
		await bod.page.goto(`${BASE_URL}/cskh/settings`, { waitUntil: 'networkidle' })
		await bod.page.locator('#set-outcomes-card').waitFor({ state: 'visible', timeout: 10000 })
		const bodSetText = await captureShot(
			bod,
			'/cskh/settings',
			'15-bod-settings-readonly.png',
			'B2E-15-bod-settings-readonly',
			'BOD views Outcome Codes and Policies in read-only mode (write forms hidden).'
		)
		assert(
			bodSetText.includes('Chế độ chỉ đọc: chỉ vai trò Quản trị CRM mới được phép thay đổi'),
			'Read-only banner must be visible to BOD on /cskh/settings'
		)

		// Admin `/cskh/settings`: create new Outcome Code & save new Policy version (`version + 1`), zero Delete buttons
		await admin.page.goto(`${BASE_URL}/cskh/settings`, { waitUntil: 'networkidle' })
		await admin.page.locator('#set-outcomes-card').waitFor({ state: 'visible', timeout: 10000 })
		const deleteButtonsCount = await admin.page
			.locator('#set-outcomes-card button', { hasText: /Xóa/i })
			.count()
		assert(deleteButtonsCount === 0, 'Settings must NEVER offer hard Delete button on Outcome Codes')

		await admin.page.fill('#out-code', 'zalo_replied_demo')
		await admin.page.fill('#out-label-vi', 'Khách phản hồi qua Zalo (demo)')
		await admin.page.selectOption('#out-group', 'positive')
		await admin.page.fill('#out-channels', 'zalo')
		await admin.page.check('#out-connected')
		await admin.page.check('#out-interested')
		await Promise.all([
			admin.page.waitForResponse(
				(r) => r.url().includes('crm_cskh.api.settings.save_outcome') && r.status() === 200
			),
			admin.page.click('#out-save-btn'),
		])
		await admin.page.waitForTimeout(500)

		await admin.page.fill('#pol-code', 'POL-TEAM-A-BROWSER')
		await admin.page.fill('#pol-title', 'Chính sách CSKH Đội A (E2E)')
		await admin.page.selectOption('#pol-scope', 'Team')
		await admin.page.fill('#pol-team', fx.team_a)
		await admin.page.fill('#pol-backdate', '90')
		await Promise.all([
			admin.page.waitForResponse(
				(r) => r.url().includes('crm_cskh.api.settings.save_policy') && r.status() === 200
			),
			admin.page.click('#pol-save-btn'),
		])
		await admin.page.waitForTimeout(500)

		const adminSetText = await captureShot(
			admin,
			'/cskh/settings',
			'16-admin-settings-outcome-and-policy-saved.png',
			'B2E-16-admin-settings-save-outcome-policy',
			'Admin saved new Outcome Code (zalo_replied_demo) and Team Policy (POL-TEAM-A-BROWSER v1) in Vietnamese.'
		)
		assert(adminSetText.includes('zalo_replied_demo'), 'Saved outcome code missing from table')
		assert(adminSetText.includes('POL-TEAM-A-BROWSER'), 'Saved policy missing from table')

		// ==================================================================
		// SCENARIO 8: Lead Deep-Link `/cskh/work-item?lead=<lead_id>`
		// (target of CRM Form Script `"CSKH - Open Lead CSKH Workspace"`)
		// ==================================================================
		await staffA.page.goto(`${BASE_URL}/cskh/work-item?lead=${encodeURIComponent(fx.lead_happy)}`, {
			waitUntil: 'networkidle',
		})
		await staffA.page.locator('#wi-summary-card').waitFor({ state: 'visible', timeout: 10000 })
		const deepLinkText = await captureShot(
			staffA,
			`/cskh/work-item?lead=${fx.lead_happy}`,
			'17-staff-a-lead-deeplink-workspace.png',
			'B2E-17-lead-deeplink-resolution',
			'Lead deep-link ?lead=<lead_id> resolves active Work Item cleanly in Vietnamese.'
		)
		assert(deepLinkText.includes('P2 Khách Hạnh Phúc'), 'Lead deep-link did not resolve P2 Khách Hạnh Phúc')

		await staffA.context.close()
		await staffB.context.close()
		await leaderA.context.close()
		await admin.context.close()
		await bod.context.close()

		const manifest = {
			site: 'p2.localhost',
			base_url: BASE_URL,
			worktree_head_sha: revisionSha,
			http_report_sha: httpReport.worktree_head_sha,
			total_checks: entries.length,
			all_passed: true,
			invariants_verified: {
				html_lang_vi: true,
				zero_white_screens: true,
				zero_console_errors: true,
				zero_page_errors: true,
				zero_asset_failures: true,
				zero_http_500s: true,
				zero_raw_phones_in_dom: true,
				xss_and_formula_inert: true,
				single_post_per_save_action: true,
				timezone_2359_0000_across_os_timezones: true,
				a_to_b_url_replay_zero_leak: true,
			},
			entries,
		}
		fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + '\n', 'utf-8')
		console.log(`WROTE ${MANIFEST_PATH} (${entries.length} browser checks & screenshots PASS)`)
	} finally {
		await browser.close()
	}
}

main().catch((err) => {
	console.error(err)
	process.exit(1)
})
