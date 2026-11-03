#!/usr/bin/env node
/**
 * Real-Chromium browser E2E verification & screenshot capture for P1 UI (`/cskh/*`).
 *
 * Connects directly to the isolated P1 Frappe site (`http://p1.localhost:8120`),
 * logs in with real business accounts (`Admin`, `Leader A`, `Leader B`, `Staff A`,
 * `Staff B`, `BOD`), verifies:
 *   - 100% Vietnamese labels (`lang="vi"`, "Không giới hạn" for `capacity=0`),
 *   - safe DOM rendering (`window.__xssTriggered === undefined`, `<script>` / `<img onerror>` / `=1+1` inert),
 *   - zero `console.error`, zero `pageerror`, zero failed asset loads, zero white screens,
 *   - zero raw unmasked phone numbers in DOM text,
 *   - CSRF verification button ("Kiểm tra phiên đăng nhập và CSRF"),
 *   - backend errors rendered visibly with `[CODE]` (never turned into empty lists or `0`),
 * and writes sanitized screenshots + `deploy/p1/evidence/browser-manifest.json`.
 */

import fs from 'node:fs'
import path from 'node:path'
import { execSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const HERE = path.dirname(__filename)
const ROOT = path.resolve(HERE, '..', '..')
const EVIDENCE_DIR = path.join(HERE, 'evidence')
const SHOTS_DIR = path.join(EVIDENCE_DIR, 'screenshots')
const HTTP_REPORT_PATH = path.join(EVIDENCE_DIR, 'http-e2e-report.json')
const MANIFEST_PATH = path.join(EVIDENCE_DIR, 'browser-manifest.json')

const BASE_URL = process.env.P1_BROWSER_URL || 'http://p1.localhost:8120'
const PASSWORD = process.env.P1_SEED_PASSWORD || 'P1Seed-Only-2026!'

const PLAYWRIGHT_PATH =
	process.env.PLAYWRIGHT_MODULE ||
	'D:/HRP-app/Contract-mange-hrp/node_modules/.pnpm/playwright@1.63.0/node_modules/playwright'

const require = createRequire(import.meta.url)
const { chromium } = require(PLAYWRIGHT_PATH)

const ACTORS = {
	admin: { user: 'cskh-p1-admin@example.test', role: 'CSKH CRM Admin' },
	bod: { user: 'cskh-p1-bod@example.test', role: 'CSKH BOD Viewer' },
	leader_a: { user: 'cskh-p1-leader-a@example.test', role: 'Sales Manager (Team A)' },
	leader_b: { user: 'cskh-p1-leader-b@example.test', role: 'Sales Manager (Team B)' },
	staff_a: { user: 'cskh-p1-staff-a@example.test', role: 'Sales User (Staff A1)' },
	staff_b: { user: 'cskh-p1-staff-b@example.test', role: 'Sales User (Staff A2)' },
}

const RAW_PHONE_RE = /\b(?:\+?84|0)\d{8,9}\b/g

function assert(cond, msg) {
	if (!cond) {
		throw new Error(`BROWSER_ASSERTION_FAILED: ${msg}`)
	}
}

async function createActorPage(browser, actorKey) {
	const actor = ACTORS[actorKey]
	const context = await browser.newContext({
		viewport: { width: 1440, height: 960 },
		locale: 'vi-VN',
		timezoneId: 'Asia/Ho_Chi_Minh',
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
		{ usr: actor.user, pwd: PASSWORD }
	)
	assert(loginStatus === 200, `Login failed for ${actor.user}: ${loginStatus}`)

	const telemetry = {
		consoleErrors: [],
		pageErrors: [],
		assetFailures: [],
		server500s: [],
	}

	page.on('console', (msg) => {
		if (msg.type() === 'error') {
			const text = msg.text()
			// Expected browser network log on intentional 403/404/409 negative tests is tracked via HTTP status,
			// but we still inspect so no JS runtime or asset error slips through.
			if (!text.includes('Failed to load resource: the server responded with a status of 40')) {
				telemetry.consoleErrors.push(text)
			}
		}
	})
	page.on('pageerror', (err) => {
		telemetry.pageErrors.push(String(err))
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

	return { context, page, actor, telemetry }
}

async function verifyNoWhiteScreenAndSafeDom(page, expectAllowed = true) {
	const xss = await page.evaluate(() => window.__xssTriggered)
	assert(xss === undefined, 'XSS payload executed in page context!')

	const lang = await page.evaluate(() => document.documentElement.lang)
	assert(lang === 'vi', `Expected html[lang="vi"], got ${lang}`)

	const bodyText = await page.evaluate(() => document.body?.innerText || '')
	assert(bodyText.trim().length > 30, 'White/blank screen detected!')

	const rawPhones = bodyText.match(RAW_PHONE_RE) || []
	assert(rawPhones.length === 0, `Unmasked phone number rendered in DOM: ${rawPhones.join(', ')}`)

	if (expectAllowed) {
		const card = page.locator('.cskh-card').first()
		await card.waitFor({ state: 'visible', timeout: 10000 })
		const box = await card.boundingBox()
		assert(box && box.width > 500 && box.height > 40, `Broken card layout: ${JSON.stringify(box)}`)
	}
	return bodyText
}

async function main() {
	fs.mkdirSync(SHOTS_DIR, { recursive: true })
	if (!fs.existsSync(HTTP_REPORT_PATH)) {
		throw new Error(`Run verify_p1_http_e2e.py first; missing ${HTTP_REPORT_PATH}`)
	}
	const httpReport = JSON.parse(fs.readFileSync(HTTP_REPORT_PATH, 'utf-8'))
	const revisionSha = execSync('git rev-parse HEAD', { cwd: ROOT }).toString().trim()

	const browser = await chromium.launch({
		headless: true,
		args: ['--host-resolver-rules=MAP p1.localhost 127.0.0.1'],
	})

	const entries = []

	async function snap(session, route, fileName, linkedCheckId, extraAssertions, expectAllowed = true) {
		const resp = await session.page.goto(`${BASE_URL}${route}`, { waitUntil: 'networkidle' })
		const httpStatus = resp ? resp.status() : 0
		await session.page.waitForTimeout(450)
		const bodyText = await verifyNoWhiteScreenAndSafeDom(session.page, expectAllowed)
		if (extraAssertions) {
			await extraAssertions(session.page, bodyText, httpStatus)
		}
		assert(
			session.telemetry.consoleErrors.length === 0,
			`${session.actor.user} ${route} console.error: ${session.telemetry.consoleErrors.join(' | ')}`
		)
		assert(
			session.telemetry.pageErrors.length === 0,
			`${session.actor.user} ${route} pageerror: ${session.telemetry.pageErrors.join(' | ')}`
		)
		assert(
			session.telemetry.assetFailures.length === 0,
			`${session.actor.user} ${route} asset failure: ${session.telemetry.assetFailures.join(' | ')}`
		)
		assert(
			session.telemetry.server500s.length === 0,
			`${session.actor.user} ${route} HTTP 500: ${session.telemetry.server500s.join(' | ')}`
		)

		const shotPath = path.join(SHOTS_DIR, fileName)
		await session.page.screenshot({ path: shotPath, fullPage: true })
		entries.push({
			screenshot: `deploy/p1/evidence/screenshots/${fileName}`,
			actor: session.actor.user,
			role: session.actor.role,
			url: `${BASE_URL}${route}`,
			http_status: httpStatus,
			backend_baseline_sha: httpReport.backend_baseline_sha,
			worktree_revision_sha: revisionSha,
			linked_http_evidence: `deploy/p1/evidence/http-e2e-report.json#${linkedCheckId}`,
			console_errors: 0,
			page_errors: 0,
			asset_failures: 0,
			xss_triggered: false,
			unmasked_phones: 0,
		})
		console.log(`PASS  [BROWSER] ${fileName} (${session.actor.user} -> ${route} HTTP ${httpStatus})`)
	}

	try {
		// 1. Admin session: Shell CSRF check, Pool, Import (live browser upload->preview->confirm->Step 3 + error link), Campaigns (Unlimited + preview + 404 lookup), My Customers (transfer history)
		const adminS = await createActorPage(browser, 'admin')
		try {
			// Verify `/cskh` shell & CSRF enforcement (valid token succeeds, forged token rejected)
			await adminS.page.goto(`${BASE_URL}/cskh`, { waitUntil: 'networkidle' })
			const csrfCheck = await adminS.page.evaluate(async () => {
				const tok = window.cskh && window.cskh.csrfToken ? window.cskh.csrfToken() : ''
				const hdrTok = window.cskhCsrfHeader ? window.cskhCsrfHeader['X-Frappe-CSRF-Token'] : ''
				const okRes = await window.cskh.post('crm_cskh.api.health.ping', {})
				let badStatus = 0
				let badCode = ''
				try {
					const r = await fetch('/api/method/crm_cskh.api.health.ping', {
						method: 'POST',
						credentials: 'same-origin',
						headers: {
							'Content-Type': 'application/json',
							Accept: 'application/json',
							'X-Frappe-CSRF-Token': 'forged-csrf-token',
						},
						body: '{}',
					})
					badStatus = r.status
					const b = await r.json()
					badCode = b.exc_type || ''
				} catch (_e) {
					/* ignore */
				}
				return { tokLen: tok.length, sameTok: tok === hdrTok, okRes, badStatus, badCode }
			})
			assert(
				csrfCheck.tokLen > 16 &&
					csrfCheck.sameTok &&
					csrfCheck.okRes?.status === 'ok' &&
					csrfCheck.badStatus === 400 &&
					csrfCheck.badCode === 'CSRFTokenError',
				`Browser CSRF check failed: ${JSON.stringify(csrfCheck)}`
			)

			// Screen 1: Pool (Admin)
			await snap(
				adminS,
				'/cskh/pool',
				'01-pool-admin.png',
				'E2E-04-campaign-rules-stale-preview-and-execute',
				async (page, text, status) => {
					assert(status === 200, `Expected 200 on /cskh/pool, got ${status}`)
					assert(text.includes('Kho khách hàng'), 'Missing Vietnamese title Kho khách hàng')
					assert(text.includes('Số khách hàng duy nhất'), 'Missing Unique customers counter')
					assert(text.includes('Số công việc'), 'Missing Work items counter')
					assert(text.includes('Đã giao'), 'Missing Assigned state badge')
					assert(text.includes('Xung đột'), 'Missing Conflict state badge')
					assert(text.includes('Lỗi dữ liệu'), 'Missing Data error state badge')
					assert(text.includes('<img src="x">'), 'Escaped HTML tag should render literally as text')
					const injectedImgs = await page.locator('#pool-rows img').count()
					assert(injectedImgs === 0, 'Untrusted string must never create an <img> DOM element')
				}
			)
			fs.copyFileSync(path.join(SHOTS_DIR, '01-pool-admin.png'), path.join(SHOTS_DIR, '01-pool.png'))

			// Screen 2: Import (Admin) — live browser file upload + mapping + preview + confirm + Step 3 + download error link
			await snap(
				adminS,
				'/cskh/import',
				'02-import-admin-step3-and-history.png',
				'E2E-02-csv-import-and-error-download-policy',
				async (page, _initialText, status) => {
					assert(status === 200, `Expected 200 on /cskh/import, got ${status}`)
					const tmpCsv = path.join(EVIDENCE_DIR, '_tmp_browser_import.csv')
					// 1 duplicate-in-CRM + 1 invalid formula cell -> adds 0 new leads while exercising full Step 1->2->3 + error CSV in browser!
					fs.writeFileSync(
						tmpCsv,
						'cot_sdt,cot_ten,cot_nguon\n0918880101,<script>window.__xssTriggered=1</script>Trùng CRM,Web\n=1+1,Lỗi Công Thức,Excel\n',
						'utf-8'
					)
					try {
						await page.setInputFiles('#import-file', tmpCsv)
						await page.fill('#import-phone-column', 'cot_sdt')
						await page.fill('#import-name-column', 'cot_ten')
						await page.fill('#import-source-column', 'cot_nguon')
						await page.click('#import-preview')
						await page.waitForSelector('#step-preview:not([hidden])', { timeout: 10000 })

						const previewText = await page.evaluate(() => document.getElementById('step-preview')?.innerText || '')
						assert(previewText.includes('Trùng trong CRM'), 'Preview missing Vietnamese duplicate_in_crm label')
						assert(previewText.includes('Không hợp lệ'), 'Preview missing Vietnamese invalid label')

						await page.click('#import-confirm')
						await page.waitForFunction(
							() => {
								const el = document.getElementById('import-progress')
								return el && el.innerText.includes('Đã hoàn tất nhưng có lỗi') && el.innerText.includes('Tải tệp lỗi')
							},
							{ timeout: 25000 }
						)
						await page.waitForSelector('#import-history-table:not([hidden])', { timeout: 10000 })
						await verifyNoWhiteScreenAndSafeDom(page, true)
						const fullText = await page.evaluate(() => document.body.innerText)
						assert(fullText.includes('Số dòng tạo mới'), 'Missing Created rows label')
						assert(fullText.includes('Số dòng dùng lại'), 'Missing Reused rows label')
						assert(fullText.includes('Số dòng bỏ qua'), 'Missing Skipped rows label')
						assert(fullText.includes('Tải tệp lỗi'), 'Missing Download error file button')
						assert(fullText.includes('Chạy lại công việc thất bại'), 'Missing Retry failed job button')
					} finally {
						if (fs.existsSync(tmpCsv)) fs.unlinkSync(tmpCsv)
					}
				}
			)
			fs.copyFileSync(
				path.join(SHOTS_DIR, '02-import-admin-step3-and-history.png'),
				path.join(SHOTS_DIR, '02-import.png')
			)

			// Screen 3: Campaigns & Rules (Admin) — `Không giới hạn` (`capacity=0`), normalized %, run history
			await snap(
				adminS,
				'/cskh/campaigns',
				'03-campaigns-admin.png',
				'E2E-04-campaign-rules-stale-preview-and-execute',
				async (page, _text, status) => {
					assert(status === 200, `Expected 200 on /cskh/campaigns, got ${status}`)
					await page.selectOption('#rule-campaign', httpReport.final_ids.campaign_a)
					await page.waitForTimeout(500)
					const bodyText = await page.evaluate(() => document.body.innerText)
					assert(bodyText.includes('Chiến dịch và quy tắc phân phối'), 'Missing Vietnamese title')
					assert(bodyText.includes('Không giới hạn'), 'capacity=0 must display "Không giới hạn"')
					assert(bodyText.includes('60%'), 'Missing normalized 60% share')
					assert(bodyText.includes('40%'), 'Missing normalized 40% share')
					assert(bodyText.includes('Sửa chiến dịch') || bodyText.includes('Đóng chiến dịch'), 'Missing campaign edit/close actions')
					assert(bodyText.includes('Chạy lại công việc thất bại'), 'Missing failed run retry button')
					assert(bodyText.includes('Thao tác lỗi tạm thời. Hãy thử lại.'), 'Expected Vietnamese failed run message')
					assert(!bodyText.includes('The operation failed temporarily.'), 'Untranslated English error in run table!')
				}
			)
			fs.copyFileSync(path.join(SHOTS_DIR, '03-campaigns-admin.png'), path.join(SHOTS_DIR, '03-campaigns.png'))

			// Screen 3b: Campaigns negative error rendering (`[NOT_FOUND]`, never empty list/0)
			await snap(
				adminS,
				'/cskh/campaigns',
				'04-campaigns-error-not-found.png',
				'E2E-06-team-isolation-bod-pii-csrf-and-404',
				async (page) => {
					await page.fill('#dist-run-id', 'RUN-DOES-NOT-EXIST')
					await page.click('#dist-run-lookup')
					await page.waitForFunction(
						() => (document.getElementById('dist-run-lookup-result')?.innerText || '').includes('[NOT_FOUND]'),
						{ timeout: 8000 }
					)
					const errText = await page.evaluate(
						() => document.getElementById('dist-run-lookup-result')?.innerText || ''
					)
					assert(errText.includes('Dữ liệu yêu cầu không tồn tại.'), `Expected Vietnamese 404 text, got: ${errText}`)
				}
			)

			// Screen 4: My Customers (Admin — ownership detail & `[assign, reassign]` history + Transfer/Revoke controls)
			await snap(
				adminS,
				'/cskh/my-customers',
				'05-my-customers-admin-ownership-history.png',
				'E2E-05-reassign-revoke-conflict-and-reconciliation',
				async (page, _text, status) => {
					assert(status === 200, `Expected 200 on /cskh/my-customers, got ${status}`)
					await page.waitForSelector('#mine-scope-table:not([hidden])', { timeout: 10000 })
					// Open detail of the reassigned work item
					const rows = page.locator('#mine-scope-rows tr')
					const count = await rows.count()
					assert(count >= 10, `Expected >= 10 assigned items in Admin scope, got ${count}`)
					for (let i = 0; i < count; i += 1) {
						const rowText = await rows.nth(i).innerText()
						if (rowText.includes('cskh-p1-staff-b@example.test')) {
							await rows.nth(i).locator('button').first().click()
							await page.waitForFunction(
								() => (document.getElementById('mine-detail')?.innerText || '').includes('Lịch sử phụ trách'),
								{ timeout: 8000 }
							)
							const histText = await page.evaluate(() => document.getElementById('mine-detail')?.innerText || '')
							if (histText.includes('Điều phối tải ca sáng')) break
						}
					}
					const detailText = await page.evaluate(() => document.getElementById('mine-detail-card')?.innerText || '')
					assert(detailText.includes('Lịch sử phụ trách'), 'Missing Ownership history heading')
					assert(detailText.includes('Điều phối tải ca sáng'), 'Expected reassign reason in history')
					assert(detailText.includes('Chuyển người phụ trách'), 'Expected Vietnamese reassign action label')
					assert(detailText.includes('Đợt phân phối '), 'Expected Vietnamese distribution run reason')
					assert(!detailText.includes('Distribution run '), 'Untranslated English Distribution run reason!')
				}
			)
		} finally {
			await adminS.context.close()
		}

		// 2. Staff A session: `/cskh/my-customers` (own 6 items + detail button, no admin transfer/revoke)
		const staffAS = await createActorPage(browser, 'staff_a')
		try {
			await snap(
				staffAS,
				'/cskh/my-customers',
				'06-my-customers-staff-a.png',
				'E2E-05-reassign-revoke-conflict-and-reconciliation',
				async (page, _text, status) => {
					assert(status === 200, `Expected 200, got ${status}`)
					await page.waitForSelector('#mine-table:not([hidden])', { timeout: 10000 })
					const rowCount = await page.locator('#mine-rows tr').count()
					assert(rowCount === 6, `Expected Staff A to have 6 items after reassign, got ${rowCount}`)
					const scopeHidden = await page.evaluate(() => document.getElementById('mine-scope-card')?.hidden)
					assert(scopeHidden === true, 'Staff A must not see mine-scope-card')
					await page.locator('#mine-rows tr').first().locator('button').click()
					await page.waitForFunction(
						() => (document.getElementById('mine-detail')?.innerText || '').includes('Lịch sử phụ trách'),
						{ timeout: 8000 }
					)
				}
			)
			fs.copyFileSync(
				path.join(SHOTS_DIR, '06-my-customers-staff-a.png'),
				path.join(SHOTS_DIR, '04-my-customers.png')
			)
		} finally {
			await staffAS.context.close()
		}

		// 3. Staff B session: `/cskh/import` -> HTTP 403 Vietnamese permission denied
		const staffBS = await createActorPage(browser, 'staff_b')
		try {
			await snap(
				staffBS,
				'/cskh/import',
				'07-forbidden-staff-b-import.png',
				'E2E-01-page-role-matrix',
				async (_page, text, status) => {
					assert(status === 403, `Expected 403 for Staff B on /cskh/import, got ${status}`)
					assert(
						text.includes('Bạn không có quyền truy cập trang này.'),
						`Expected Vietnamese 403 message, got: ${text.slice(0, 200)}`
					)
				},
				false
			)
		} finally {
			await staffBS.context.close()
		}

		// 4. Leader A session: `/cskh/my-customers` (Team A scope = 10 items, read-only detail, zero Team B)
		const leaderAS = await createActorPage(browser, 'leader_a')
		try {
			await snap(
				leaderAS,
				'/cskh/my-customers',
				'08-my-customers-leader-a-team-scope.png',
				'E2E-06-team-isolation-bod-pii-csrf-and-404',
				async (page, _text, status) => {
					assert(status === 200, `Expected 200, got ${status}`)
					await page.waitForSelector('#mine-scope-table:not([hidden])', { timeout: 10000 })
					const scopeText = await page.evaluate(() => document.getElementById('mine-scope-card')?.innerText || '')
					assert(scopeText.includes('cskh-p1-staff-a@example.test'), 'Leader A missing staff_a')
					assert(scopeText.includes('cskh-p1-staff-b@example.test'), 'Leader A missing staff_b')
					assert(!scopeText.includes('cskh-p1-staff-c@example.test'), 'Cross-team leak: Leader A saw staff_c!')
					assert(!scopeText.includes('Chuyển người phụ trách'), 'Leader A must not see Transfer button')
				}
			)
		} finally {
			await leaderAS.context.close()
		}

		// 5. Leader B session: `/cskh/dashboard` (Team B scope = 2 items on staff_c only, zero Team A leak)
		const leaderBS = await createActorPage(browser, 'leader_b')
		try {
			await snap(
				leaderBS,
				'/cskh/dashboard',
				'09-dashboard-leader-b-team-isolation.png',
				'E2E-06-team-isolation-bod-pii-csrf-and-404',
				async (page, _text, status) => {
					assert(status === 200, `Expected 200, got ${status}`)
					await page.waitForFunction(
						() => (document.getElementById('dash-table-wrap')?.innerText || '').includes('cskh-p1-staff-c@example.test'),
						{ timeout: 10000 }
					)
					const dashText = await page.evaluate(() => document.body.innerText)
					assert(dashText.includes('Phạm vi đội'), 'Leader B missing Team scope label')
					assert(!dashText.includes('cskh-p1-staff-a@example.test'), 'Cross-team leak: Leader B saw staff_a!')
					assert(!dashText.includes('Chiến dịch Chăm sóc Đội A'), 'Cross-team leak: Leader B saw Team A campaign!')
				}
			)
		} finally {
			await leaderBS.context.close()
		}

		// 6. BOD session: `/cskh/dashboard` (system-wide live vs audit events + Không giới hạn) & `/cskh/pool` (zero PII)
		const bodS = await createActorPage(browser, 'bod')
		try {
			await snap(
				bodS,
				'/cskh/dashboard',
				'10-dashboard-bod-live-vs-events.png',
				'E2E-06-team-isolation-bod-pii-csrf-and-404',
				async (page, _text, status) => {
					assert(status === 200, `Expected 200, got ${status}`)
					await page.waitForFunction(
						() => (document.getElementById('dash-table-wrap')?.innerText || '').includes('cskh-p1-staff-a@example.test'),
						{ timeout: 10000 }
					)
					const dashText = await page.evaluate(() => document.body.innerText)
					assert(dashText.includes('Quyền phụ trách hiện tại (công việc đang hoạt động)'), 'Missing live ownership section')
					assert(dashText.includes('Sự kiện phân công (nhật ký kiểm toán)'), 'Missing audit events section')
					assert(dashText.includes('Không giới hạn'), 'Missing Unlimited label on dashboard')
					assert(dashText.includes('Toàn hệ thống'), 'Missing Whole installation scope label')
				}
			)
			fs.copyFileSync(
				path.join(SHOTS_DIR, '10-dashboard-bod-live-vs-events.png'),
				path.join(SHOTS_DIR, '05-dashboard.png')
			)

			await snap(
				bodS,
				'/cskh/pool',
				'11-pool-bod-zero-pii.png',
				'E2E-06-team-isolation-bod-pii-csrf-and-404',
				async (page, _text, status) => {
					assert(status === 200, `Expected 200, got ${status}`)
					await page.waitForSelector('#pool-table:not([hidden])', { timeout: 10000 })
					const searchHidden = await page.evaluate(() => document.getElementById('pool-search-wrap')?.hidden)
					assert(searchHidden === true, 'BOD must not see phone/name search box')
					const tableText = await page.evaluate(() => document.getElementById('pool-table')?.innerText || '')
					assert(!tableText.includes('******'), 'BOD pool must omit phone completely (not even last-4 digits)')
					assert(!tableText.includes('Mở trong CRM'), 'BOD pool must not show Open in CRM link')
				}
			)
		} finally {
			await bodS.context.close()
		}
	} finally {
		await browser.close()
	}

	const manifest = {
		generated_at: new Date().toISOString(),
		site: 'p1.localhost',
		base_url: BASE_URL,
		backend_baseline_sha: httpReport.backend_baseline_sha,
		worktree_revision_sha: revisionSha,
		linked_http_report: 'deploy/p1/evidence/http-e2e-report.json',
		total_screenshots: entries.length,
		screenshots: entries,
	}
	fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + '\n', 'utf-8')
	console.log(`ALL ${entries.length} BROWSER E2E CHECKS & SCREENSHOTS PASSED -> ${MANIFEST_PATH}`)
}

main().catch((err) => {
	console.error(err)
	process.exit(1)
})
