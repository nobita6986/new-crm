#!/usr/bin/env node
/**
 * Real-Chromium Browser E2E verification & screenshot capture for Integrated Recruitment UI + Backend (`T1A` + `32b53fb`).
 *
 * Verifies with `Nhân viên` (`Sales User`) and `Quản trị CRM` (`CSKH CRM Admin`) on isolated
 * `http://recruit-ui.localhost:18620`:
 *   1. `/crm` entry points (`/crm/leads`, `/crm/leads/<id>`, `/crm/deals` with `"Giao dịch CRM"`)
 *      and `/cskh` menu/home orientation, plus safe inert rendering of the salary & work shift
 *      note (`"Người liên hệ hỏi mức lương và ca làm việc"`) with XSS payload neutralized.
 *   2. End-to-end outreach flow:
 *      `Nhập danh sách liên hệ -> Kho hồ sơ liên hệ -> Phân công -> Hồ sơ được giao -> Ghi nhận liên hệ -> Lịch hẹn liên hệ lại`,
 *      including `"Hoàn tất mục tiêu liên hệ chiến dịch"` preserving true contact-outcome semantics.
 *   3. P3 Report Metric Consistency (`Cards === Detail/Comparison Tables === Drill-down Modal === Server CSV Export`)
 *      across `/cskh/quality-overview`, `/cskh/team-quality`, `/cskh/employee-quality`,
 *      `/cskh/campaign-quality`, and `/cskh/scorecard`.
 */

import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const SCRIPTS_DIR = path.dirname(__filename)
const BUNDLE_DIR = path.resolve(SCRIPTS_DIR, '..')
const REPO_ROOT = path.resolve(BUNDLE_DIR, '..', '..', '..', '..')
const SCREENSHOTS_DIR = path.join(BUNDLE_DIR, 'screenshots')
const SEED_PATH =
	process.env.RECRUIT_UI_SEED_JSON || path.join(os.tmpdir(), 'cskh_recruit_ui_seeded_sample.json')
const REPORT_PATH =
	process.env.RECRUIT_UI_REPORT_JSON || path.join(BUNDLE_DIR, 'browser_e2e_report.json')

const BASE_URL = process.env.RECRUIT_UI_BASE_URL || 'http://recruit-ui.localhost:18620'
if (
	BASE_URL.includes('8120') ||
	BASE_URL.includes('8220') ||
	BASE_URL.includes('8320') ||
	BASE_URL.includes('p1.localhost') ||
	BASE_URL.includes('p2.localhost') ||
	BASE_URL.includes('p3ui.localhost')
) {
	throw new Error(`Refusing to run recruitment UI browser E2E against protected URL: ${BASE_URL}`)
}

const require = createRequire(import.meta.url)
function loadPlaywright() {
	const candidates = [
		process.env.PLAYWRIGHT_MODULE,
		'playwright',
		'D:/HRP-app/Contract-mange-hrp/node_modules/.pnpm/playwright@1.63.0/node_modules/playwright',
	].filter(Boolean)
	for (const modPath of candidates) {
		try {
			const pw = require(modPath)
			let pwVersion = 'unknown'
			try {
				const pkgPath =
					modPath === 'playwright'
						? require.resolve('playwright/package.json')
						: path.join(modPath, 'package.json')
				pwVersion = JSON.parse(fs.readFileSync(pkgPath, 'utf-8')).version
			} catch (_err) {
				/* ignore */
			}
			return { chromium: pw.chromium, playwrightVersion: pwVersion }
		} catch (_err) {
			/* try next candidate */
		}
	}
	throw new Error(
		'Could not resolve playwright module. Install playwright or set PLAYWRIGHT_MODULE=/path/to/playwright.'
	)
}
const { chromium, playwrightVersion } = loadPlaywright()
fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true })

const seed = JSON.parse(fs.readFileSync(SEED_PATH, 'utf-8').replace(/^\uFEFF/, ''))

function assert(cond, msg) {
	if (!cond) {
		throw new Error(`RECRUIT_UI_ASSERTION_FAILED: ${msg}`)
	}
}

function parseCsvRows(csvText) {
	const rows = []
	let row = []
	let cur = ''
	let inQuotes = false
	for (let i = 0; i < csvText.length; i += 1) {
		const ch = csvText[i]
		if (inQuotes) {
			if (ch === '"') {
				if (csvText[i + 1] === '"') {
					cur += '"'
					i += 1
				} else {
					inQuotes = false
				}
			} else {
				cur += ch
			}
		} else if (ch === '"') {
			inQuotes = true
		} else if (ch === ',') {
			row.push(cur)
			cur = ''
		} else if (ch === '\n') {
			row.push(cur.replace(/\r$/, ''))
			cur = ''
			if (row.length > 1 || row[0] !== '') rows.push(row)
			row = []
		} else {
			cur += ch
		}
	}
	if (cur !== '' || row.length > 0) {
		row.push(cur.replace(/\r$/, ''))
		if (row.length > 1 || row[0] !== '') rows.push(row)
	}
	return rows
}

async function loginActor(browser, userEmail, roleLabel) {
	const context = await browser.newContext({
		viewport: { width: 1440, height: 920 },
		locale: 'vi-VN',
		timezoneId: 'Asia/Ho_Chi_Minh',
	})
	const page = await context.newPage()

	const telemetry = {
		role: roleLabel,
		user: userEmail,
		consoleErrors: [],
		pageErrors: [],
		assetFailures: [],
		server500s: [],
		apiPosts: [],
	}

	page.on('console', (msg) => {
		if (msg.type() === 'error') {
			const text = msg.text()
			const locUrl = (msg.location && msg.location().url) || ''
			const isSocketIoPolling =
				locUrl.includes(':9000/socket.io/') || text.includes('WebSocket connection to')
			if (
				!text.includes('Failed to load resource: the server responded with a status of 40') &&
				!isSocketIoPolling
			) {
				telemetry.consoleErrors.push(`${text}${locUrl ? ` @ ${locUrl}` : ''}`)
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
	page.on('response', (resp) => {
		if (resp.url().includes('/assets/') && resp.status() >= 400) {
			telemetry.assetFailures.push(`${resp.url()} -> HTTP ${resp.status()}`)
		}
		if (resp.status() >= 500) {
			telemetry.server500s.push(`${resp.url()} -> HTTP ${resp.status()}`)
		}
	})

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
		{ usr: userEmail, pwd: seed.password }
	)
	assert(loginStatus === 200, `Login failed for ${userEmail}: HTTP ${loginStatus}`)

	return { context, page, telemetry }
}

async function dismissCrmOnboardingDrawer(page) {
	await page.evaluate(() => {
		for (const headingText of ['Bắt đầu sử dụng', 'Trung tâm trợ giúp']) {
			const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
			let node
			while ((node = walker.nextNode())) {
				if (node.textContent && node.textContent.trim() === headingText) {
					const headerRow = node.parentElement?.parentElement
					const buttons = headerRow ? headerRow.querySelectorAll('button') : []
					if (buttons.length) {
						buttons[buttons.length - 1].click()
					}
				}
			}
		}
	})
	await page.waitForTimeout(300)
}

async function saveShot(page, filename) {
	const outPath = path.join(SCREENSHOTS_DIR, filename)
	await page.screenshot({ path: outPath, fullPage: true })
	return path.relative(REPO_ROOT, outPath).split(path.sep).join('/')
}

async function collectMetricCardTitles(page) {
	return page.evaluate(() => {
		const out = {}
		for (const card of document.querySelectorAll('[data-metric-key]')) {
			const key = card.getAttribute('data-metric-key')
			const titleNode = card.querySelector('.cskh-kpi-label')
			if (key && titleNode) {
				out[key] = titleNode.textContent.trim()
			}
		}
		return out
	})
}

async function closeP3Modal(page) {
	const closeBtn = page.locator('.cskh-modal-head button')
	if ((await closeBtn.count()) > 0) {
		await closeBtn.first().click()
		await page.locator('.cskh-modal').waitFor({ state: 'hidden', timeout: 5000 })
	}
	await page.waitForTimeout(150)
}

async function triggerAndDownloadCsvExport(page, purposeText) {
	await page.click('#p3-btn-export')
	await page.locator('#p3-export-purpose').waitFor({ state: 'visible', timeout: 10000 })
	await page.fill('#p3-export-purpose', purposeText)
	await page.click('#p3-export-submit')
	await page.waitForFunction(
		() => {
			const t = document.getElementById('p3-export-jobs-table')
			return t && t.innerText.includes('Sẵn sàng tải')
		},
		{ timeout: 25000 }
	)
	const dlHref = await page
		.locator('#p3-export-jobs-table a[href*="download_export"]')
		.first()
		.getAttribute('href')
	assert(dlHref && dlHref.includes('download_export'), `Missing download_export href: ${dlHref}`)
	const csvContent = await page.evaluate(async (href) => {
		const resp = await fetch(href, { credentials: 'same-origin' })
		if (!resp.ok) throw new Error(`Export download failed: HTTP ${resp.status}`)
		return resp.text()
	}, dlHref)
	await closeP3Modal(page)
	return parseCsvRows(csvContent)
}

async function main() {
	const browser = await chromium.launch({
		headless: true,
		args: ['--host-resolver-rules=MAP recruit-ui.localhost 127.0.0.1'],
	})
	const checks = []
	const screenshots = []

	try {
		// =========================================================================
		// PART 1: `/crm` ENTRY POINTS, XSS SALARY/SHIFT NOTE & STAFF ORIENTATION
		// =========================================================================
		const staff = await loginActor(browser, seed.users.staff_a, 'Nhân viên (Sales User)')

		// 1A. `/crm/leads` list (`Hồ sơ liên hệ` + entry `"Không gian tuyển dụng"`)
		await staff.page.goto(`${BASE_URL}/crm/leads`, { waitUntil: 'networkidle' })
		await staff.page.getByText('Nguyễn Văn Minh (Mẫu)').first().waitFor({ timeout: 20000 })
		await staff.page.getByRole('button', { name: /Không gian tuyển dụng/i }).first().waitFor({
			timeout: 15000,
		})
		await dismissCrmOnboardingDrawer(staff.page)
		const crmLeadsText = await staff.page.locator('body').innerText()
		assert(crmLeadsText.includes('Hồ sơ liên hệ'), '/crm/leads must display "Hồ sơ liên hệ"')
		assert(crmLeadsText.includes('Giao dịch CRM'), '/crm sidebar must display "Giao dịch CRM"')
		assert(
			!crmLeadsText.includes('Khách hàng tiềm năng'),
			'/crm/leads must not retain legacy "Khách hàng tiềm năng"'
		)
		screenshots.push({
			id: '01-crm-leads-list-staff.png',
			role: 'Nhân viên',
			screen: 'Danh sách Hồ sơ liên hệ (/crm/leads) & nút Không gian tuyển dụng',
			path: await saveShot(staff.page, '01-crm-leads-list-staff.png'),
		})

		await Promise.all([
			staff.page.waitForURL(/\/cskh(\/?|\?.*)$/, { timeout: 15000 }),
			staff.page.getByRole('button', { name: /Không gian tuyển dụng/i }).first().click(),
		])
		checks.push(
			'PASS [Staff]: Nút "Không gian tuyển dụng" trên /crm/leads điều hướng trực tiếp sang /cskh'
		)

		// 1B. `/crm/leads/<lead_1>` detail -> `/cskh/work-item?lead=<lead_1>` + XSS salary/shift note check
		await staff.page.goto(`${BASE_URL}/crm/leads/${seed.leads.lead_1}`, {
			waitUntil: 'networkidle',
		})
		await staff.page.getByText('Nguyễn Văn Minh (Mẫu)').first().waitFor({ timeout: 20000 })
		await staff.page
			.getByRole('button', { name: /Mở trong Không gian tuyển dụng/i })
			.first()
			.waitFor({ timeout: 15000 })
		await dismissCrmOnboardingDrawer(staff.page)
		const crmLeadDetailText = await staff.page.locator('body').innerText()
		assert(
			crmLeadDetailText.includes('Chuyển thành Giao dịch CRM'),
			'/crm/leads/<id> must show "Chuyển thành Giao dịch CRM"'
		)
		assert(
			crmLeadDetailText.includes('Mở trong Không gian tuyển dụng'),
			'/crm/leads/<id> must show "Mở trong Không gian tuyển dụng"'
		)
		screenshots.push({
			id: '02-crm-lead-detail-staff.png',
			role: 'Nhân viên',
			screen: 'Chi tiết Hồ sơ liên hệ (/crm/leads/<id>): Chuyển thành Giao dịch CRM & Mở trong Không gian tuyển dụng',
			path: await saveShot(staff.page, '02-crm-lead-detail-staff.png'),
		})

		await Promise.all([
			staff.page.waitForURL(new RegExp(`/cskh/work-item\\?lead=${seed.leads.lead_1}`), {
				timeout: 15000,
			}),
			staff.page
				.getByRole('button', { name: /Mở trong Không gian tuyển dụng/i })
				.first()
				.click(),
		])
		await staff.page.locator('#wi-summary-card').waitFor({ state: 'visible', timeout: 15000 })
		await staff.page.waitForFunction(
			() => {
				const tl = document.getElementById('wi-timeline-list')
				return tl && tl.innerText.includes('Người liên hệ hỏi mức lương và ca làm việc')
			},
			{ timeout: 15000 }
		)
		const wiLead1Text = await staff.page.locator('body').innerText()
		const xssTriggered = await staff.page.evaluate(() => window.__xssTriggered)
		assert(
			wiLead1Text.includes('Nguyễn Văn Minh (Mẫu)') &&
				wiLead1Text.includes('Ghi chú nội bộ CRM =1+1') &&
				wiLead1Text.includes('Người liên hệ hỏi mức lương và ca làm việc') &&
				!wiLead1Text.includes('<script>') &&
				xssTriggered === undefined,
			'Salary & work shift FCRM Note must render safely as inert text with zero XSS execution!'
		)
		checks.push(
			'PASS [Staff - Anti-XSS & Ngữ cảnh tuyển dụng]: Ghi chú "Người liên hệ hỏi mức lương và ca làm việc" hiển thị an toàn dạng văn bản thuần trên dòng thời gian, payload <script>/<img onerror> bị vô hiệu hoàn toàn (window.__xssTriggered === undefined)'
		)

		// 1C. `/crm/deals` list (`Giao dịch CRM` native screen + `"Không gian tuyển dụng"` entry button)
		await staff.page.goto(`${BASE_URL}/crm/deals`, { waitUntil: 'networkidle' })
		await staff.page.getByText('Giao dịch CRM').first().waitFor({ timeout: 20000 })
		await staff.page.getByRole('button', { name: /Không gian tuyển dụng/i }).first().waitFor({
			timeout: 15000,
		})
		await dismissCrmOnboardingDrawer(staff.page)
		const crmDealsText = await staff.page.locator('body').innerText()
		assert(crmDealsText.includes('Giao dịch CRM'), '/crm/deals must display "Giao dịch CRM"')
		assert(!crmDealsText.includes('Cơ hội'), '/crm/deals must not retain legacy "Cơ hội"')
		screenshots.push({
			id: '03-crm-deals-list-staff.png',
			role: 'Nhân viên',
			screen: 'Danh sách Giao dịch CRM gốc (/crm/deals) & lối vào Không gian tuyển dụng',
			path: await saveShot(staff.page, '03-crm-deals-list-staff.png'),
		})
		await staff.page.getByRole('button', { name: /Tạo mới/i }).first().click()
		await staff.page.getByText('Tạo Giao dịch CRM').first().waitFor({ timeout: 10000 })
		await staff.page.keyboard.press('Escape')
		await staff.page.waitForTimeout(300)
		checks.push(
			'PASS [Staff]: Màn Giao dịch CRM (/crm/deals) giữ nguyên tính năng Deal gốc và hiển thị rõ "Giao dịch CRM"'
		)

		// 1D. `/cskh` (`Không gian tuyển dụng` home for `Nhân viên`)
		await staff.page.goto(`${BASE_URL}/cskh`, { waitUntil: 'networkidle' })
		const staffNavLinks = await staff.page.locator('.cskh-nav a').allInnerTexts()
		assert(
			staffNavLinks[0] === 'Không gian tuyển dụng' &&
				staffNavLinks[1] === 'Hồ sơ được giao' &&
				staffNavLinks[2] === 'Lịch hẹn liên hệ lại' &&
				staffNavLinks[3] === 'Tiến độ liên hệ',
			`Unexpected Staff nav order: ${JSON.stringify(staffNavLinks)}`
		)
		screenshots.push({
			id: '04-cskh-workspace-home-staff.png',
			role: 'Nhân viên',
			screen: 'Trang chủ & Menu Không gian tuyển dụng (/cskh) — Vai trò Nhân viên',
			path: await saveShot(staff.page, '04-cskh-workspace-home-staff.png'),
		})

		// =========================================================================
		// PART 2: CONNECTED OUTREACH FLOW (`Hồ sơ liên hệ -> Phân công -> Ghi nhận liên hệ -> Lịch hẹn liên hệ lại`)
		// =========================================================================
		const admin = await loginActor(browser, seed.users.admin, 'Quản trị CRM (CSKH CRM Admin)')

		await admin.page.goto(`${BASE_URL}/cskh`, { waitUntil: 'networkidle' })
		const adminNavLinks = await admin.page.locator('.cskh-nav a').allInnerTexts()
		assert(
			adminNavLinks[0] === 'Không gian tuyển dụng' &&
				adminNavLinks[1] === 'Nhập danh sách liên hệ' &&
				adminNavLinks[2] === 'Kho hồ sơ liên hệ' &&
				adminNavLinks[3] === 'Phân công & chiến dịch' &&
				adminNavLinks[4] === 'Hồ sơ được giao' &&
				adminNavLinks[5] === 'Lịch hẹn liên hệ lại',
			`CRM Admin nav must follow exact 6-step order, got: ${JSON.stringify(adminNavLinks)}`
		)
		screenshots.push({
			id: '05-cskh-workspace-home-admin.png',
			role: 'Quản trị CRM',
			screen: 'Trang chủ & Menu 6 bước Không gian tuyển dụng (/cskh) — Vai trò Quản trị CRM',
			path: await saveShot(admin.page, '05-cskh-workspace-home-admin.png'),
		})

		// `/cskh/settings`: verify `"Hoàn tất mục tiêu liên hệ chiến dịch"` in catalog table & edit form
		await admin.page.goto(`${BASE_URL}/cskh/settings`, { waitUntil: 'networkidle' })
		await admin.page.locator('#set-outcomes-rows tr').first().waitFor({ state: 'visible', timeout: 15000 })
		const convRow = admin.page.locator('#set-outcomes-rows tr', { hasText: 'converted' }).first()
		const convRowText = await convRow.innerText()
		assert(
			convRowText.includes('Hoàn tất mục tiêu liên hệ chiến dịch'),
			`Expected "Hoàn tất mục tiêu liên hệ chiến dịch" in /cskh/settings table, got: ${convRowText}`
		)
		await convRow.getByRole('button', { name: 'Chỉnh sửa' }).click()
		const convFormVal = await admin.page.inputValue('#out-label-vi')
		assert(
			convFormVal === 'Hoàn tất mục tiêu liên hệ chiến dịch',
			`Expected #out-label-vi === "Hoàn tất mục tiêu liên hệ chiến dịch", got: ${convFormVal}`
		)
		checks.push(
			'PASS [CRM Admin - Mã kết quả converted]: Bảng và biểu mẫu /cskh/settings đồng bộ "Hoàn tất mục tiêu liên hệ chiến dịch"'
		)

		// STEP 1: `Nhập danh sách liên hệ` (`/cskh/import`)
		await admin.page.goto(`${BASE_URL}/cskh/import`, { waitUntil: 'networkidle' })
		const sampleCsvPath = path.join(SCRIPTS_DIR, 'sample_import_contacts.csv')
		await admin.page.setInputFiles('#import-file', sampleCsvPath)
		await admin.page.fill('#import-phone-column', 'so_dien_thoai')
		await admin.page.fill('#import-name-column', 'ho_ten')
		await admin.page.fill('#import-source-column', 'nguon')
		await admin.page.click('#import-preview')
		await admin.page.locator('#step-preview').waitFor({ state: 'visible', timeout: 15000 })
		screenshots.push({
			id: '06-step1-import-preview-admin.png',
			role: 'Quản trị CRM',
			screen: 'Bước 1 — Nhập danh sách liên hệ: Xem trước & kiểm trùng/lỗi (/cskh/import)',
			path: await saveShot(admin.page, '06-step1-import-preview-admin.png'),
		})
		await admin.page.click('#import-confirm')
		await admin.page.waitForFunction(
			() => {
				const el = document.getElementById('import-progress')
				return (
					el &&
					(el.innerText.includes('Đã hoàn tất nhưng có lỗi') ||
						el.innerText.includes('Đã hoàn tất')) &&
					el.innerText.includes('Xem lô này trong Kho hồ sơ liên hệ')
				)
			},
			{ timeout: 25000 }
		)
		screenshots.push({
			id: '07-step1-import-completed-admin.png',
			role: 'Quản trị CRM',
			screen: 'Bước 1 — Nhập danh sách liên hệ: Hoàn tất lô nhập & nút sang Kho hồ sơ (/cskh/import)',
			path: await saveShot(admin.page, '07-step1-import-completed-admin.png'),
		})

		// STEP 2: `Kho hồ sơ liên hệ` (`/cskh/pool?batch=...`)
		await Promise.all([
			admin.page.waitForURL(/\/cskh\/pool\?batch=/, { timeout: 15000 }),
			admin.page.getByRole('link', { name: 'Xem lô này trong Kho hồ sơ liên hệ' }).click(),
		])
		await admin.page.locator('#pool-rows tr').first().waitFor({ state: 'visible', timeout: 15000 })
		screenshots.push({
			id: '08-step2-pool-batch-admin.png',
			role: 'Quản trị CRM',
			screen: 'Bước 2 — Kho hồ sơ liên hệ: Trạng thái Chưa phân phối & nút Phân công (/cskh/pool)',
			path: await saveShot(admin.page, '08-step2-pool-batch-admin.png'),
		})

		await Promise.all([
			admin.page.waitForURL(/\/cskh\/campaigns/, { timeout: 15000 }),
			admin.page.locator('#pool-rows a[href="/cskh/campaigns"]').first().click(),
		])

		// STEP 3: `Phân công` (`/cskh/campaigns`)
		await admin.page.waitForFunction(
			() => document.querySelectorAll('#rule-campaign option').length >= 1,
			{ timeout: 15000 }
		)
		await admin.page.selectOption('#rule-campaign', seed.campaign)
		await admin.page.waitForTimeout(400)
		await admin.page.click('#dist-preview')
		await admin.page.waitForFunction(
			() => {
				const s = document.getElementById('dist-summary')
				return s && s.innerText.includes('Hồ sơ đủ điều kiện phân công')
			},
			{ timeout: 15000 }
		)
		screenshots.push({
			id: '09-step3-campaigns-preview-admin.png',
			role: 'Quản trị CRM',
			screen: 'Bước 3 — Phân công: Xem trước phân phối hồ sơ liên hệ (/cskh/campaigns)',
			path: await saveShot(admin.page, '09-step3-campaigns-preview-admin.png'),
		})
		await admin.page.click('#dist-execute')
		await admin.page.waitForFunction(
			() => {
				const r = document.getElementById('dist-results')
				return r && r.innerText.includes('Đã phân phối xong.')
			},
			{ timeout: 25000 }
		)
		screenshots.push({
			id: '10-step3-campaigns-distributed-admin.png',
			role: 'Quản trị CRM',
			screen: 'Bước 3 — Phân công: Đã phân phối xong & nút Xem Hồ sơ được giao (/cskh/campaigns)',
			path: await saveShot(admin.page, '10-step3-campaigns-distributed-admin.png'),
		})
		checks.push(
			'PASS [Steps 1→3]: Nhập danh sách liên hệ → Kho hồ sơ liên hệ → Phân công & xác nhận phân phối hoạt động xuyên suốt'
		)

		// STEP 4: `Hồ sơ được giao` (`/cskh/my-customers`, `Nhân viên`)
		await staff.page.goto(`${BASE_URL}/cskh/my-customers`, { waitUntil: 'networkidle' })
		await staff.page.locator('#mine-rows tr').first().waitFor({ state: 'visible', timeout: 15000 })

		// Check `Hoàn tất xử lý liên hệ` (`closed`) filter shows `wi_3` with `"Hoàn tất mục tiêu liên hệ chiến dịch"`
		await staff.page.locator('#mine-filter-chips button[data-filter="closed"]').click()
		await staff.page.locator('#mine-rows tr', { hasText: 'Lê Hoàng Nam (Mẫu)' }).waitFor({
			state: 'visible',
			timeout: 10000,
		})
		const closedRowText = await staff.page
			.locator('#mine-rows tr', { hasText: 'Lê Hoàng Nam (Mẫu)' })
			.innerText()
		assert(
			closedRowText.includes('Hoàn tất xử lý liên hệ') &&
				closedRowText.includes('Hoàn tất mục tiêu liên hệ chiến dịch'),
			`Closed work item must display "Hoàn tất mục tiêu liên hệ chiến dịch", got: ${closedRowText}`
		)
		await staff.page.locator('#mine-filter-chips button[data-filter=""]').click()
		await staff.page.locator('#mine-rows tr').first().waitFor({ state: 'visible', timeout: 10000 })

		const mineTableText = await staff.page.locator('#mine-table').innerText()
		const importedForStaffA = mineTableText.includes('Hoàng Minh Tuấn (Luồng 6 Bước)')
			? 'Hoàng Minh Tuấn (Luồng 6 Bước)'
			: mineTableText.includes('Bùi Thanh Thảo (Luồng 6 Bước)')
				? 'Bùi Thanh Thảo (Luồng 6 Bước)'
				: null
		assert(importedForStaffA !== null, 'Expected distributed profile in Staff A list')
		screenshots.push({
			id: '11-step4-assigned-profiles-staff.png',
			role: 'Nhân viên',
			screen: `Bước 4 — Hồ sơ được giao: Nhận hồ sơ "${importedForStaffA}" vừa phân phối (/cskh/my-customers)`,
			path: await saveShot(staff.page, '11-step4-assigned-profiles-staff.png'),
		})

		// STEP 5: `Ghi nhận liên hệ` (Quick Record + `/cskh/work-item`, `Nhân viên`)
		const targetRow = staff.page.locator('#mine-rows tr', { hasText: importedForStaffA }).first()
		const quickHref = await targetRow.locator('a[href^="#quick-record-"]').getAttribute('href')
		const distributedWorkItemId = decodeURIComponent(quickHref.replace('#quick-record-', ''))
		await targetRow.locator('a[href^="#quick-record-"]').click()
		await staff.page.locator('#cskh-att-outcome').waitFor({ state: 'visible', timeout: 10000 })

		const outcomeOptions = await staff.page.locator('#cskh-att-outcome option').allInnerTexts()
		for (const expectedLabel of [
			'Đã trao đổi — Cần liên hệ tiếp',
			'Đã trao đổi — Có quan tâm',
			'Đã hẹn gọi / liên hệ lại',
			'Đã trao đổi — Không nhu cầu',
			'Hoàn tất mục tiêu liên hệ chiến dịch',
		]) {
			assert(
				outcomeOptions.some((t) => t.trim() === expectedLabel),
				`Missing synchronized outcome "${expectedLabel}" in ${JSON.stringify(outcomeOptions)}`
			)
		}

		// Select `converted` (`Hoàn tất mục tiêu liên hệ chiến dịch`) and verify true contact-outcome semantics disclaimer
		await staff.page.selectOption('#cskh-att-outcome', 'converted')
		const convertedCardText = await staff.page.locator('#mine-quick-record-card').innerText()
		assert(
			convertedCardText.includes(
				'Lưu ý: Hoàn tất mục tiêu liên hệ chiến dịch chỉ đóng bước liên hệ đầu vào — không tự xác nhận đã trúng tuyển, bố trí hay nhận việc.'
			),
			`Missing campaign goal completion honesty disclaimer: ${convertedCardText}`
		)
		screenshots.push({
			id: '12-step5-record-attempt-staff.png',
			role: 'Nhân viên',
			screen: 'Bước 5 — Ghi nhận liên hệ: Kết quả "Hoàn tất mục tiêu liên hệ chiến dịch" & lưu ý đúng nghĩa kết quả liên hệ',
			path: await saveShot(staff.page, '12-step5-record-attempt-staff.png'),
		})

		// Now switch to `connected_needs_followup`, fill note + callback, and submit
		await staff.page.selectOption('#cskh-att-outcome', 'connected_needs_followup')
		await staff.page.fill(
			'#cskh-att-note',
			'Đã gọi trao đổi mức lương và ca làm việc: ứng viên đồng ý ca xoay 3 ca, hẹn chiều gửi CCCD và xác nhận lịch phỏng vấn.'
		)
		if (!(await staff.page.isChecked('#cskh-att-has-followup'))) {
			await staff.page.check('#cskh-att-has-followup')
		}
		await staff.page.fill(
			'#cskh-att-followup-title',
			`Gọi lại xác nhận lịch phỏng vấn — ${importedForStaffA}`
		)
		await staff.page.click('#cskh-att-submit')
		await staff.page.locator('#mine-quick-record-card').waitFor({ state: 'hidden', timeout: 15000 })

		await staff.page.goto(
			`${BASE_URL}/cskh/work-item?id=${encodeURIComponent(distributedWorkItemId)}`,
			{ waitUntil: 'networkidle' }
		)
		await staff.page.locator('#wi-summary-card').waitFor({ state: 'visible', timeout: 15000 })
		await staff.page.waitForFunction(
			() => {
				const tl = document.getElementById('wi-timeline-list')
				return tl && tl.innerText.includes('Đã trao đổi — Cần liên hệ tiếp')
			},
			{ timeout: 15000 }
		)
		screenshots.push({
			id: '13-step5-profile-detail-staff.png',
			role: 'Nhân viên',
			screen: `Bước 5 — Chi tiết hồ sơ liên hệ & Nhật ký hợp nhất (/cskh/work-item?id=${distributedWorkItemId})`,
			path: await saveShot(staff.page, '13-step5-profile-detail-staff.png'),
		})

		// STEP 6: `Lịch hẹn liên hệ lại` (`/cskh/callbacks`, `Nhân viên`)
		await staff.page.goto(`${BASE_URL}/cskh/callbacks`, { waitUntil: 'networkidle' })
		await staff.page.locator('#cb-rows tr').first().waitFor({ state: 'visible', timeout: 15000 })
		const cbOpenText = await staff.page.locator('#cb-table').innerText()
		assert(
			cbOpenText.includes(importedForStaffA) && cbOpenText.includes('Ghi nhận liên hệ & đóng hẹn'),
			`Callbacks table missing scheduled callback for ${importedForStaffA}`
		)
		screenshots.push({
			id: '14-step6-callbacks-staff.png',
			role: 'Nhân viên',
			screen: `Bước 6 — Lịch hẹn liên hệ lại: Theo dõi & xử lý lịch hẹn của "${importedForStaffA}" (/cskh/callbacks)`,
			path: await saveShot(staff.page, '14-step6-callbacks-staff.png'),
		})
		const cbRow = staff.page.locator('#cb-rows tr', { hasText: importedForStaffA }).first()
		await cbRow.getByRole('button', { name: 'Ghi nhận liên hệ & đóng hẹn' }).click()
		await staff.page.locator('#cskh-att-outcome').waitFor({ state: 'visible', timeout: 10000 })
		await staff.page.selectOption('#cskh-att-outcome', 'connected_interested')
		await staff.page.fill(
			'#cskh-att-note',
			'Đã gọi lại theo lịch hẹn: ứng viên xác nhận tham gia phỏng vấn sáng thứ Năm.'
		)
		await staff.page.click('#cskh-att-submit')
		await staff.page.locator('#cb-action-card').waitFor({ state: 'hidden', timeout: 15000 })
		checks.push(
			`PASS [Steps 4→6]: Hồ sơ được giao → Ghi nhận liên hệ ("Hoàn tất mục tiêu liên hệ chiến dịch" giữ đúng nghĩa kết quả liên hệ) → Lịch hẹn liên hệ lại hoàn tất xuyên suốt`
		)

		// =========================================================================
		// PART 3: P3 REPORT METRIC CONSISTENCY (CARDS === TABLES === DRILL-DOWN === CSV EXPORT)
		// =========================================================================
		// 3A. `/cskh/quality-overview` (`Quản trị CRM`)
		await admin.page.goto(`${BASE_URL}/cskh/quality-overview`, { waitUntil: 'networkidle' })
		await admin.page.locator('[data-metric-key="PROCESS_SCORE"]').waitFor({
			state: 'visible',
			timeout: 15000,
		})
		const overviewCardTitles = await collectMetricCardTitles(admin.page)
		assert(
			overviewCardTitles.CONVERSION_RATE === 'Tỷ lệ hoàn tất mục tiêu liên hệ chiến dịch',
			`Expected CONVERSION_RATE card === "Tỷ lệ hoàn tất mục tiêu liên hệ chiến dịch", got: ${overviewCardTitles.CONVERSION_RATE}`
		)

		// Verify Teams summary table headers on `/cskh/quality-overview` match card titles 100%
		const ovTeamHeaders = (
			await admin.page.locator('#p3-overview-teams-card thead th').allTextContents()
		).map((s) => s.trim())
		for (const mKey of [
			'ASSIGNED_INTERVALS',
			'FIRST_ATTEMPT_SLA_RATE',
			'FOLLOWUP_SLA_RATE',
			'CONNECTED_RATE',
			'OVERDUE_BACKLOG',
			'PROCESS_SCORE',
		]) {
			assert(
				ovTeamHeaders.includes(overviewCardTitles[mKey]),
				`Overview Teams table missing exact card metric "${overviewCardTitles[mKey]}" (${mKey}); headers=${JSON.stringify(ovTeamHeaders)}`
			)
		}

		// Verify Drill-down modal title matches card title 100%
		const firstSlaCard = admin.page.locator('[data-metric-key="FIRST_ATTEMPT_SLA_RATE"]')
		await firstSlaCard.getByRole('button', { name: 'Truy vết' }).click()
		await admin.page.locator('.cskh-modal').waitFor({ state: 'visible', timeout: 10000 })
		const modalText = await admin.page.locator('.cskh-modal').innerText()
		assert(
			modalText.includes(overviewCardTitles.FIRST_ATTEMPT_SLA_RATE),
			`Drill-down modal title must match card "${overviewCardTitles.FIRST_ATTEMPT_SLA_RATE}", got: ${modalText.slice(0, 200)}`
		)
		await closeP3Modal(admin.page)

		screenshots.push({
			id: '15-p3-quality-overview-admin.png',
			role: 'Quản trị CRM',
			screen: 'Báo cáo P3 — Tổng quan chất lượng (/cskh/quality-overview): Nhất quán tên chỉ số trên Thẻ KPI & Bảng đối chiếu đội nhóm',
			path: await saveShot(admin.page, '15-p3-quality-overview-admin.png'),
		})

		// Trigger & download real server CSV export for `executive_overview` and assert 100% `label_vi` parity!
		const execCsvRows = await triggerAndDownloadCsvExport(
			admin.page,
			'Đối chiếu tính nhất quán tên chỉ số giữa thẻ KPI, bảng chi tiết và bản xuất CSV'
		)
		assert(
			execCsvRows.length >= 15 &&
				execCsvRows[0][0] === 'metric_code' &&
				execCsvRows[0][1] === 'label_vi',
			`Unexpected executive_overview CSV header: ${JSON.stringify(execCsvRows[0])}`
		)
		const execCsvByCode = Object.fromEntries(execCsvRows.slice(1).map((r) => [r[0], r[1]]))
		for (const [mKey, cardTitle] of Object.entries(overviewCardTitles)) {
			if (execCsvByCode[mKey] !== undefined) {
				assert(
					execCsvByCode[mKey] === cardTitle,
					`CSV label_vi mismatch for ${mKey}: CSV="${execCsvByCode[mKey]}" vs Card="${cardTitle}"`
				)
			}
		}

		// 3B. `/cskh/team-quality`, `/cskh/employee-quality`, `/cskh/campaign-quality` detail/comparison tables
		await admin.page.goto(`${BASE_URL}/cskh/team-quality`, { waitUntil: 'networkidle' })
		await admin.page.locator('#p3-team-table').waitFor({ state: 'visible', timeout: 15000 })
		const teamQualHeaders = (
			await admin.page.locator('#p3-team-table thead th').allTextContents()
		).map((s) => s.trim())
		for (const mKey of [
			'ASSIGNED_INTERVALS',
			'FIRST_ATTEMPT_SLA_RATE',
			'FOLLOWUP_SLA_RATE',
			'CONNECTED_RATE',
			'VALID_OUTCOME_LOGGING_RATE',
			'OVERDUE_BACKLOG',
			'PROCESS_SCORE',
		]) {
			const expectedTitle = overviewCardTitles[mKey] || execCsvByCode[mKey]
			assert(
				expectedTitle && teamQualHeaders.includes(expectedTitle),
				`/cskh/team-quality table header mismatch for ${mKey}: expected "${expectedTitle}", got ${JSON.stringify(teamQualHeaders)}`
			)
		}

		await admin.page.goto(`${BASE_URL}/cskh/employee-quality`, { waitUntil: 'networkidle' })
		await admin.page.locator('.cskh-score-group table').first().waitFor({
			state: 'visible',
			timeout: 15000,
		})
		const empQualHeaders = (
			await admin.page
				.locator('.cskh-score-group table')
				.first()
				.locator('thead th')
				.allTextContents()
		).map((s) => s.trim())
		for (const mKey of [
			'PROCESS_SCORE',
			'FIRST_ATTEMPT_SLA_RATE',
			'FOLLOWUP_SLA_RATE',
			'ELIGIBLE_COVERAGE',
			'CALLBACK_COMPLETION_RATE',
			'ASSIGNED_INTERVALS',
			'CONTACT_ATTEMPT_COUNT',
			'CONNECTED_RATE',
			'VALID_OUTCOME_LOGGING_RATE',
			'DNC_COMPLIANCE_RATE',
			'OVERDUE_BACKLOG',
		]) {
			const expectedTitle = overviewCardTitles[mKey] || execCsvByCode[mKey]
			assert(
				expectedTitle && empQualHeaders.includes(expectedTitle),
				`/cskh/employee-quality table header mismatch for ${mKey}: expected "${expectedTitle}", got ${JSON.stringify(empQualHeaders)}`
			)
		}

		await admin.page.goto(`${BASE_URL}/cskh/campaign-quality`, { waitUntil: 'networkidle' })
		await admin.page.locator('#p3-campaign-table').waitFor({ state: 'visible', timeout: 15000 })
		const campQualCardTitles = await collectMetricCardTitles(admin.page)
		const campQualHeaders = (
			await admin.page.locator('#p3-campaign-table thead th').allTextContents()
		).map((s) => s.trim())
		for (const mKey of [
			'UNIQUE_ASSIGNED_ITEMS',
			'ASSIGNED_INTERVALS',
			'WORK_ITEM_FIRST_ATTEMPT_SLA_RATE',
			'FIRST_ATTEMPT_SLA_RATE',
			'FOLLOWUP_SLA_RATE',
			'CONNECTED_RATE',
			'CONVERSION_RATE',
			'FIRST_CONNECTED_TRACKING_RATE',
			'DNC_COMPLIANCE_RATE',
		]) {
			const expectedTitle = campQualCardTitles[mKey] || execCsvByCode[mKey] || overviewCardTitles[mKey]
			assert(
				campQualHeaders.includes(expectedTitle),
				`/cskh/campaign-quality table header mismatch for ${mKey}: expected "${expectedTitle}", got ${JSON.stringify(campQualHeaders)}`
			)
		}
		screenshots.push({
			id: '16-p3-campaign-quality-admin.png',
			role: 'Quản trị CRM',
			screen: 'Báo cáo P3 — Hiệu quả chiến dịch & nguồn liên hệ (/cskh/campaign-quality): Nhất quán Thẻ & Bảng chi tiết ("Tỷ lệ hoàn tất mục tiêu liên hệ chiến dịch")',
			path: await saveShot(admin.page, '16-p3-campaign-quality-admin.png'),
		})
		checks.push(
			`PASS [CRM Admin - Báo cáo P3 & CSV Export]: Đối chiếu ${Object.keys(overviewCardTitles).length} chỉ số trên Thẻ KPI === Bảng chi tiết (/quality-overview, /team-quality, /employee-quality, /campaign-quality) === Modal Drill-down === Cột label_vi tệp xuất CSV (${execCsvRows.length - 1} dòng)`
		)

		// 3C. `/cskh/scorecard` (`Nhân viên`) + `my_scorecard` CSV export parity
		await staff.page.goto(`${BASE_URL}/cskh/scorecard`, { waitUntil: 'networkidle' })
		await staff.page.locator('#p3-scorecard-summary').waitFor({ state: 'visible', timeout: 15000 })
		const staffScorecardTitles = await collectMetricCardTitles(staff.page)
		const myScoreCsvRows = await triggerAndDownloadCsvExport(
			staff.page,
			'Nhân viên tải bảng điểm cá nhân đối chiếu tên chỉ số'
		)
		assert(
			myScoreCsvRows.length >= 10 &&
				myScoreCsvRows[0][1] === 'metric_code' &&
				myScoreCsvRows[0][2] === 'label_vi',
			`Unexpected my_scorecard CSV header: ${JSON.stringify(myScoreCsvRows[0])}`
		)
		const myScoreCsvByCode = Object.fromEntries(myScoreCsvRows.slice(1).map((r) => [r[1], r[2]]))
		for (const [mKey, cardTitle] of Object.entries(staffScorecardTitles)) {
			if (myScoreCsvByCode[mKey] !== undefined) {
				assert(
					myScoreCsvByCode[mKey] === cardTitle,
					`Staff scorecard CSV label_vi mismatch for ${mKey}: CSV="${myScoreCsvByCode[mKey]}" vs Card="${cardTitle}"`
				)
			}
		}
		screenshots.push({
			id: '17-p3-personal-scorecard-staff.png',
			role: 'Nhân viên',
			screen: 'Báo cáo P3 — Bảng điểm chất lượng cá nhân (/cskh/scorecard): Nhất quán Thẻ KPI, Điểm thành phần & Bản xuất CSV cá nhân',
			path: await saveShot(staff.page, '17-p3-personal-scorecard-staff.png'),
		})
		checks.push(
			`PASS [Staff - Bảng điểm cá nhân P3 & CSV Export]: Tất cả chỉ số trên /cskh/scorecard khớp 100% với cột label_vi của tệp CSV my_scorecard (${myScoreCsvRows.length - 1} dòng)`
		)

		// Telemetry cleanliness assertions
		for (const actor of [staff, admin]) {
			assert(
				actor.telemetry.pageErrors.length === 0,
				`${actor.telemetry.role} JS pageErrors: ${JSON.stringify(actor.telemetry.pageErrors)}`
			)
			assert(
				actor.telemetry.assetFailures.length === 0,
				`${actor.telemetry.role} assetFailures: ${JSON.stringify(actor.telemetry.assetFailures)}`
			)
			assert(
				actor.telemetry.server500s.length === 0,
				`${actor.telemetry.role} server500s: ${JSON.stringify(actor.telemetry.server500s)}`
			)
			assert(
				actor.telemetry.consoleErrors.length === 0,
				`${actor.telemetry.role} consoleErrors: ${JSON.stringify(actor.telemetry.consoleErrors)}`
			)
		}

		const browserVersion = browser.version()
		await staff.context.close()
		await admin.context.close()

		const report = {
			status: 'PASS',
			command:
				'node deploy/dev/evidence/recruitment-workspace-ui-20261107/scripts/verify_recruitment_ui_browser.mjs',
			integratedSha: seed.integratedSha || null,
			runtimeRevision: seed.runtimeRevision || null,
			browser: {
				engine: 'chromium',
				version: browserVersion,
				playwrightVersion,
				nodeVersion: process.version,
				headless: true,
				timezoneId: 'Asia/Ho_Chi_Minh',
				locale: 'vi-VN',
				viewport: { width: 1440, height: 920 },
			},
			site: seed.site,
			baseUrl: BASE_URL,
			executedAt: new Date().toISOString(),
			endToEndProfile: importedForStaffA,
			endToEndWorkItem: distributedWorkItemId,
			p3ExecutiveCsvRows: execCsvRows.length - 1,
			p3StaffScorecardCsvRows: myScoreCsvRows.length - 1,
			checks,
			screenshots,
			telemetry: {
				staff: staff.telemetry,
				admin: admin.telemetry,
			},
		}
		fs.writeFileSync(REPORT_PATH, JSON.stringify(report, null, 2), 'utf-8')
		console.log('ALL INTEGRATED RECRUITMENT UI + P3 REPORT CONSISTENCY CHECKS PASSED!')
		for (const c of checks) console.log('  - ' + c)
	} finally {
		await browser.close()
	}
}

main().catch((err) => {
	console.error(err)
	process.exit(1)
})
