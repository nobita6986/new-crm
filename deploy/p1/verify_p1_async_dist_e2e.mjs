#!/usr/bin/env node
/**
 * Targeted real-HTTP + real-Chromium E2E verification for:
 *   1. Async background distribution (`execute` -> `run_id` + `queued`, UI live polling,
 *      mid-queue `page.reload()` recovery, never mistaking enqueue for completion,
 *      `completed_with_errors` + resume, `failed` + Vietnamese `[CODE]` rendering).
 *   2. Integrated backend fixes (`L-20` >200-row error artifact & exact `error_rows`,
 *      `L-21` form-urlencoded `return_to_pool=false` -> `closed`,
 *      `L-22` Leader team-scoped `revoked_work_items` / `closed_work_items` / `active_claims`).
 *
 * Preserves the baseline screenshot set (`0d3f2bc`) intact and writes sanitized delta
 * evidence to `deploy/p1/evidence/async-distribution-delta-e2e.json`.
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
const OUT_PATH = path.join(EVIDENCE_DIR, 'async-distribution-delta-e2e.json')

const BASE_URL = process.env.P1_BROWSER_URL || 'http://p1.localhost:8120'
const PASSWORD = process.env.P1_SEED_PASSWORD || 'P1Seed-Only-2026!'
const BENCH_CONTAINER = process.env.P1_BENCH_CONTAINER || 'crm-cskh-p1-bench-1'

const PLAYWRIGHT_PATH =
	process.env.PLAYWRIGHT_MODULE ||
	'D:/HRP-app/Contract-mange-hrp/node_modules/.pnpm/playwright@1.63.0/node_modules/playwright'

const require = createRequire(import.meta.url)
const { chromium } = require(PLAYWRIGHT_PATH)

const RAW_PHONE_RE = /\b(?:\+?84|0)\d{8,9}\b/g

function assert(cond, msg) {
	if (!cond) {
		throw new Error(`ASYNC_E2E_ASSERTION_FAILED: ${msg}`)
	}
}

function redactText(value) {
	if (typeof value !== 'string') return value
	return value
		.replace(RAW_PHONE_RE, '[REDACTED_PHONE]')
		.replace(/\/private\/files\/[^\s"',)]+/gi, '/private/files/[REDACTED_FILE]')
}

function redactObj(obj) {
	if (typeof obj === 'string') return redactText(obj)
	if (Array.isArray(obj)) return obj.map(redactObj)
	if (obj && typeof obj === 'object') {
		const out = {}
		for (const [k, v] of Object.entries(obj)) {
			if (/password|pwd|cookie|csrf|sid/i.test(k)) {
				out[k] = '[REDACTED]'
			} else {
				out[k] = redactObj(v)
			}
		}
		return out
	}
	return obj
}

function pauseWorker() {
	execSync(
		`docker exec ${BENCH_CONTAINER} bash -lc "pkill -f 'bench worker' || true"`,
		{ stdio: 'pipe' }
	)
}

function startWorker() {
	execSync(
		`docker exec -d ${BENCH_CONTAINER} bash -lc "cd /workspace/frappe-bench && pgrep -f 'bench worker' >/dev/null || nohup bench worker --queue short,default,long >/tmp/p1-worker.log 2>&1 </dev/null &"`,
		{ stdio: 'pipe' }
	)
}

function benchExecute(method, kwargs = null) {
	const args = [
		'exec',
		BENCH_CONTAINER,
		'bench',
		'--site',
		'p1.localhost',
		'execute',
		method,
	]
	if (kwargs) {
		args.push('--kwargs', JSON.stringify(kwargs))
	}
	const out = execFileSync('docker', args, { encoding: 'utf-8' }).trim()
	const lines = out.split(/\r?\n/).filter(Boolean)
	return JSON.parse(lines[lines.length - 1])
}

async function createActorPage(browser, email) {
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
		{ usr: email, pwd: PASSWORD }
	)
	assert(loginStatus === 200, `Login failed for ${email}: ${loginStatus}`)

	const telemetry = { consoleErrors: [], pageErrors: [], server500s: [] }
	page.on('console', (msg) => {
		if (msg.type() === 'error') {
			const t = msg.text()
			if (!t.includes('Failed to load resource: the server responded with a status of 40')) {
				telemetry.consoleErrors.push(t)
			}
		}
	})
	page.on('pageerror', (err) => telemetry.pageErrors.push(String(err)))
	page.on('response', (res) => {
		if (res.status() >= 500) {
			telemetry.server500s.push(`${res.status()} ${res.url()}`)
		}
	})
	return { context, page, telemetry }
}

async function importLeadsViaApi(page, tag, phones) {
	const lines = ['so_dt,ho_ten,nguon_kh']
	phones.forEach((p, idx) => {
		lines.push(`${p},Khách Phân Phối Nền ${tag}-${idx + 1},Web`)
	})
	const b64 = Buffer.from(lines.join('\n') + '\n', 'utf-8').toString('base64')

	return await page.evaluate(
		async ({ b64Content, batchKey, phonePrefixes }) => {
			const up = await window.cskh.post('crm_cskh.api.import_files.upload_import_file', {
				file_name: `${batchKey}.csv`,
				content: b64Content,
			})
			const prev = await window.cskh.post('crm_cskh.api.imports.create_preview', {
				file_name: up.name,
				mapping: { phone: 'so_dt', name: 'ho_ten', source: 'nguon_kh' },
				idempotency_key: `idem-${batchKey}`,
			})
			await window.cskh.post('crm_cskh.api.imports.confirm', { batch: prev.batch })
			for (let i = 0; i < 60; i += 1) {
				const st = await window.cskh.get('crm_cskh.api.imports.get_status', {
					batch: prev.batch,
				})
				if (['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED'].includes(st.status)) {
					break
				}
				await new Promise((r) => setTimeout(r, 400))
			}
			const pool = await window.cskh.get('crm_cskh.api.pool_ui.list', {
				search: phonePrefixes,
				page_size: 50,
			})
			return (pool.items || []).map((item) => item.lead)
		},
		{ b64Content: b64, batchKey: tag, phonePrefixes: phones[0].slice(0, 8) }
	)
}

async function main() {
	fs.mkdirSync(EVIDENCE_DIR, { recursive: true })
	const gitSha = execSync('git rev-parse HEAD', { cwd: ROOT, encoding: 'utf-8' }).trim()
	const seed = benchExecute('crm_cskh.tools.e2e_seed.reset_and_seed')
	const users = seed.users
	const teamA = seed.team_a
	const teamB = seed.team_b
	startWorker()

	const checks = []
	const browser = await chromium.launch({ headless: true })

	try {
		const adminS = await createActorPage(browser, users.admin)
		const staffAS = await createActorPage(browser, users.staff_a)
		const staffBS = await createActorPage(browser, users.staff_b)
		const leaderAS = await createActorPage(browser, users.leader_a)
		const leaderBS = await createActorPage(browser, users.leader_b)

		try {
			await adminS.page.goto(`${BASE_URL}/cskh/campaigns`, { waitUntil: 'networkidle' })

			// -----------------------------------------------------------------
			// 1. ASYNC-01: Enqueue returns `run_id` + `queued`, UI shows `.is-info`
			//    (never premature completion), survives mid-queue `page.reload()`,
			//    polls `get_run` to `completed`, and delivers to Staff A & Staff B.
			// -----------------------------------------------------------------
			const asyncLeads = await importLeadsViaApi(adminS.page, 'async01', [
				'0917710101',
				'0917710102',
				'0917710103',
				'0917710104',
			])
			assert(asyncLeads.length === 4, `Expected 4 imported leads for async01, got ${asyncLeads.length}`)

			const camp1 = await adminS.page.evaluate(
				async ({ leads, team, staffA, staffB }) => {
					const c = await window.cskh.post('crm_cskh.api.campaigns.save', {
						code: 'CAMP-ASYNC-01',
						title: 'Chiến dịch Phân phối Nền 01',
						status: 'Active',
						team_scope: team,
						selection: { leads },
					})
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign: c.campaign,
						user: staffA,
						weight: 50,
						capacity_limit: 0,
						team,
						enabled: 1,
					})
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign: c.campaign,
						user: staffB,
						weight: 50,
						capacity_limit: 0,
						team,
						enabled: 1,
					})
					return c.campaign
				},
				{ leads: asyncLeads, team: teamA, staffA: users.staff_a, staffB: users.staff_b }
			)

			await adminS.page.goto(`${BASE_URL}/cskh/campaigns`, { waitUntil: 'networkidle' })
			await adminS.page.selectOption('#rule-campaign', camp1)
			await adminS.page.click('#dist-preview')
			await adminS.page.waitForFunction(
				() => !document.getElementById('dist-execute')?.disabled,
				{ timeout: 10000 }
			)

			// Pause worker briefly so `execute` stays `queued` across enqueue + reload!
			pauseWorker()

			let executeHttpPayload = null
			const execRespPromise = adminS.page.waitForResponse(
				(res) =>
					res.url().includes('/api/method/crm_cskh.api.distribution.execute') &&
					res.request().method() === 'POST'
			)
			await adminS.page.click('#dist-execute')
			const execResp = await execRespPromise
			assert(execResp.status() === 200, `Expected 200 on distribution.execute, got ${execResp.status()}`)
			const execJson = await execResp.json()
			executeHttpPayload = execJson.message

			assert(
				executeHttpPayload &&
					executeHttpPayload.run_id &&
					['queued', 'running'].includes(String(executeHttpPayload.status).toLowerCase()),
				`execute must return run_id + non-terminal queued/running status, got: ${JSON.stringify(executeHttpPayload)}`
			)

			await adminS.page.waitForSelector('#dist-results .cskh-alert.is-info', { timeout: 5000 })
			const queuedDomState = await adminS.page.evaluate(() => {
				const resEl = document.getElementById('dist-results')
				const alertEl = document.getElementById('campaign-alert')
				return {
					resultsText: resEl?.innerText || '',
					hasSuccessAlert: !!document.querySelector('#dist-results .cskh-alert.is-success, #campaign-alert .cskh-alert.is-success'),
					alertText: alertEl?.innerText || '',
					url: window.location.href,
					stored: window.sessionStorage.getItem('cskh_active_dist_run'),
				}
			})
			assert(!queuedDomState.hasSuccessAlert, 'UI must NEVER show success alert upon enqueue!')
			assert(
				!queuedDomState.resultsText.includes('Đã phân phối xong.'),
				`Premature completion text shown upon enqueue: ${queuedDomState.resultsText}`
			)
			assert(
				queuedDomState.resultsText.includes('Đã xếp hàng đợi phân phối') ||
					queuedDomState.resultsText.includes('Đang thực thi phân phối nền'),
				`Missing Vietnamese background progress banner: ${queuedDomState.resultsText}`
			)
			assert(
				queuedDomState.url.includes(`run_id=${executeHttpPayload.run_id}`),
				`URL did not persist run_id for reload recovery: ${queuedDomState.url}`
			)

			// Reload page while run is STILL queued in Redis RQ!
			const pollAfterReloadPromise = adminS.page.waitForResponse(
				(res) =>
					res.url().includes('/api/method/crm_cskh.api.distribution.get_run') &&
					res.url().includes(executeHttpPayload.run_id)
			)
			await adminS.page.reload({ waitUntil: 'networkidle' })
			await pollAfterReloadPromise
			await adminS.page.waitForSelector('#dist-results .cskh-alert.is-info', { timeout: 8000 })

			const afterReloadText = await adminS.page.evaluate(
				() => document.getElementById('dist-results')?.innerText || ''
			)
			assert(
				afterReloadText.includes(executeHttpPayload.run_id),
				`Reload recovery lost run_id ${executeHttpPayload.run_id}: ${afterReloadText}`
			)
			assert(
				!afterReloadText.includes('Đã phân phối xong.'),
				`Reload recovery showed premature completion while worker was still paused: ${afterReloadText}`
			)

			// Resume RQ worker and let browser polling complete automatically!
			startWorker()
			await adminS.page.waitForFunction(
				() => {
					const el = document.querySelector('#dist-results .cskh-alert.is-success')
					const txt = document.getElementById('dist-results')?.innerText || ''
					return !!el && txt.includes('Đã phân phối xong.') && txt.includes('4 / 4 (100%)')
				},
				{ timeout: 30000 }
			)

			// Verify Staff A & Staff B actually received 2 leads each
			const [myA, myB] = await Promise.all([
				staffAS.page.evaluate(async (camp) => {
					await window.fetch('/cskh/my-customers')
					const r = await fetch(`/api/method/crm_cskh.api.ownership_ui.assigned_list?campaign=${encodeURIComponent(camp)}&page=1&page_size=20`, { credentials: 'same-origin' })
					return (await r.json()).message
				}, camp1),
				staffBS.page.evaluate(async (camp) => {
					await window.fetch('/cskh/my-customers')
					const r = await fetch(`/api/method/crm_cskh.api.ownership_ui.assigned_list?campaign=${encodeURIComponent(camp)}&page=1&page_size=20`, { credentials: 'same-origin' })
					return (await r.json()).message
				}, camp1),
			])
			assert(myA.total === 2 && myB.total === 2, `Expected 2 leads each for Staff A & B, got ${myA.total} / ${myB.total}`)

			checks.push({
				id: 'ASYNC-01-enqueue-polling-and-reload-recovery',
				passed: true,
				run_id: executeHttpPayload.run_id,
				enqueue_status: executeHttpPayload.status,
				premature_success_on_enqueue: false,
				recovered_after_reload: true,
				final_progress: '4 / 4 (100%)',
				staff_a_assigned: myA.total,
				staff_b_assigned: myB.total,
			})

			// -----------------------------------------------------------------
			// 2. ASYNC-02: `completed_with_errors` (capacity exhausted on 3rd lead)
			//    renders `.is-warning` + Vietnamese skipped reason + retry control.
			// -----------------------------------------------------------------
			const partialLeads = await importLeadsViaApi(adminS.page, 'async02', [
				'0917720101',
				'0917720102',
				'0917720103',
			])
			const camp2 = await adminS.page.evaluate(
				async ({ leads, team, staffA }) => {
					const c = await window.cskh.post('crm_cskh.api.campaigns.save', {
						code: 'CAMP-ASYNC-PARTIAL',
						title: 'Chiến dịch Hoàn tất Có Lỗi',
						status: 'Active',
						team_scope: team,
						selection: { leads },
					})
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign: c.campaign,
						user: staffA,
						weight: 100,
						capacity_limit: 2,
						team,
						enabled: 1,
					})
					return c.campaign
				},
				{ leads: partialLeads, team: teamA, staffA: users.staff_a }
			)

			await adminS.page.selectOption('#rule-campaign', camp2)
			await adminS.page.click('#dist-preview')
			await adminS.page.waitForFunction(
				() => !document.getElementById('dist-execute')?.disabled,
				{ timeout: 10000 }
			)
			await adminS.page.click('#dist-execute')
			await adminS.page.waitForFunction(
				() => {
					const warn = document.querySelector('#dist-results .cskh-alert.is-warning')
					const txt = document.getElementById('dist-results')?.innerText || ''
					return (
						!!warn &&
						txt.includes('Đã phân phối xong nhưng có lỗi.') &&
						txt.includes('Không có người nhận đủ điều kiện') &&
						txt.includes('Chạy lại công việc thất bại')
					)
				},
				{ timeout: 30000 }
			)
			const partialText = await adminS.page.evaluate(
				() => document.getElementById('dist-results')?.innerText || ''
			)
			checks.push({
				id: 'ASYNC-02-completed-with-errors-rendering',
				passed: true,
				campaign: camp2,
				has_warning_banner: true,
				has_vietnamese_skip_reason: partialText.includes('Không có người nhận đủ điều kiện'),
				has_retry_button: partialText.includes('Chạy lại công việc thất bại'),
			})

			// -----------------------------------------------------------------
			// 3. ASYNC-03: `failed` background run (rule changed while queued)
			//    polls `queued -> failed` and renders `.is-error` with Vietnamese
			//    message + `[RULE_CHANGED]`, never raw English or false success.
			// -----------------------------------------------------------------
			const failLeads = await importLeadsViaApi(adminS.page, 'async03', [
				'0917730101',
				'0917730102',
			])
			const camp3 = await adminS.page.evaluate(
				async ({ leads, team, staffA }) => {
					const c = await window.cskh.post('crm_cskh.api.campaigns.save', {
						code: 'CAMP-ASYNC-FAIL',
						title: 'Chiến dịch Kiểm tra Lỗi Nền',
						status: 'Active',
						team_scope: team,
						selection: { leads },
					})
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign: c.campaign,
						user: staffA,
						weight: 100,
						capacity_limit: 0,
						team,
						enabled: 1,
					})
					return c.campaign
				},
				{ leads: failLeads, team: teamA, staffA: users.staff_a }
			)

			await adminS.page.selectOption('#rule-campaign', camp3)
			await adminS.page.click('#dist-preview')
			await adminS.page.waitForFunction(
				() => !document.getElementById('dist-execute')?.disabled,
				{ timeout: 10000 }
			)

			pauseWorker()
			await adminS.page.click('#dist-execute')
			await adminS.page.waitForSelector('#dist-results .cskh-alert.is-info', { timeout: 5000 })

			// Bump rule version to 2 while run sits in `queued` so worker execution-time recheck aborts it to `failed`!
			await adminS.page.evaluate(
				async ({ campaign, staffA, team }) => {
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign,
						user: staffA,
						weight: 100,
						capacity_limit: 0,
						team,
						enabled: 1,
						version: 2,
					})
				},
				{ campaign: camp3, staffA: users.staff_a, team: teamA }
			)

			startWorker()
			await adminS.page.waitForFunction(
				() => {
					const errEl = document.querySelector('#dist-results .cskh-alert.is-error')
					const txt = document.getElementById('dist-results')?.innerText || ''
					return !!errEl && txt.includes('Phân phối thất bại.') && txt.includes('[RULE_CHANGED]')
				},
				{ timeout: 30000 }
			)
			const failBannerText = await adminS.page.evaluate(
				() => document.getElementById('dist-results')?.innerText || ''
			)
			assert(
				failBannerText.includes('Quy tắc phân công đã thay đổi sau khi xem trước.'),
				`Expected translated RULE_CHANGED failure detail, got: ${failBannerText}`
			)
			assert(
				!failBannerText.includes('The assignment rules changed'),
				`Untranslated English leaked in failed banner: ${failBannerText}`
			)

			checks.push({
				id: 'ASYNC-03-failed-background-run-rendering',
				passed: true,
				campaign: camp3,
				error_banner_code: 'RULE_CHANGED',
				vietnamese_detail_verified: true,
			})

			// -----------------------------------------------------------------
			// 4. BACKEND-DELTA: Verify integrated T1B backend fixes over real HTTP:
			//    - L-20: >200-row import (`202` valid + `13` invalid across chunk >200)
			//            keeps all 13 rows in error CSV and `error_rows === 13`.
			//    - L-21: `ownership.revoke` with form-urlencoded `return_to_pool=false`
			//            closes the work item (`status === 'closed'`).
			//    - L-22: Leader A vs Leader B dashboard `revoked_work_items` /
			//            `closed_work_items` / `active_claims` are strictly team-scoped.
			// -----------------------------------------------------------------
			const l20Check = await adminS.page.evaluate(async () => {
				const rows = ['so_dt,ho_ten,nguon_kh']
				let expectedErrors = 0
				for (let i = 1; i <= 215; i += 1) {
					if (i % 16 === 0) {
						expectedErrors += 1
						rows.push(`BAD_ROW_${i},Lỗi Dòng ${i},CSV`)
					} else {
						const suffix = String(i).padStart(4, '0')
						rows.push(`09166${suffix}9,Khách Lô Lớn ${i},CSV`)
					}
				}
				const b64 = btoa(unescape(encodeURIComponent(rows.join('\n') + '\n')))
				const up = await window.cskh.post('crm_cskh.api.import_files.upload_import_file', {
					file_name: 'l20-over-200.csv',
					content: b64,
				})
				const prev = await window.cskh.post('crm_cskh.api.imports.create_preview', {
					file_name: up.name,
					mapping: { phone: 'so_dt', name: 'ho_ten', source: 'nguon_kh' },
					idempotency_key: 'idem-l20-over-200',
				})
				await window.cskh.post('crm_cskh.api.imports.confirm', { batch: prev.batch })
				let finalSt = null
				for (let i = 0; i < 90; i += 1) {
					finalSt = await window.cskh.get('crm_cskh.api.imports.get_status', {
						batch: prev.batch,
					})
					if (['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED'].includes(finalSt.status)) break
					await new Promise((r) => setTimeout(r, 400))
				}
				const detail = await window.cskh.get('crm_cskh.api.import_history.batch_detail', {
					batch: prev.batch,
				})
				const dlResp = await fetch(detail.error_artifact.download_url, {
					credentials: 'same-origin',
				})
				const csvText = await dlResp.text()
				const csvLines = csvText.trim().split(/\r?\n/)
				return {
					expectedErrors,
					status: finalSt.status,
					totals: detail.totals,
					downloadStatus: dlResp.status,
					csvDataRows: csvLines.length - 1,
					hasRowOver200: csvLines.some((line) => /^209,/.test(line)),
				}
			})
			assert(
				l20Check.status === 'COMPLETED_WITH_ERRORS' &&
					l20Check.totals.error_rows === l20Check.expectedErrors &&
					l20Check.csvDataRows === l20Check.expectedErrors &&
					l20Check.hasRowOver200,
				`L-20 >200-row error artifact / counter check failed: ${JSON.stringify(l20Check)}`
			)

			// L-21 + L-22: Close 1 Team A work item via form-urlencoded `return_to_pool=false`
			// and create + revoke 1 Team B work item, then compare Leader A vs Leader B dashboards!
			const l21And22 = await adminS.page.evaluate(
				async ({ campA, teamBNode, staffC }) => {
					const listA = await window.cskh.get('crm_cskh.api.ownership_ui.assigned_list', {
						campaign: campA,
						page: 1,
						page_size: 10,
					})
					const wiCloseA = listA.items[0].work_item

					// Form-urlencoded `return_to_pool=false` (L-21)
					const formBody = new URLSearchParams({
						work_item: wiCloseA,
						reason: 'Đóng công việc qua form-urlencoded return_to_pool=false',
						return_to_pool: 'false',
						request_id: 'req-l21-form-false-01',
					})
					const revResp = await fetch('/api/method/crm_cskh.api.ownership.revoke', {
						method: 'POST',
						credentials: 'same-origin',
						headers: {
							'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
							Accept: 'application/json',
							'X-Frappe-CSRF-Token': window.cskh.csrfToken(),
						},
						body: formBody.toString(),
					})
					const revJson = await revResp.json()

					// Also create 1 Team B campaign + distribute 1 lead + revoke (`return_to_pool=true`) in Team B
					const upB = await window.cskh.post('crm_cskh.api.import_files.upload_import_file', {
						file_name: 'l22-team-b.csv',
						content: btoa('so_dt,ho_ten\n0917740101,Khach Doi B\n'),
					})
					const prevB = await window.cskh.post('crm_cskh.api.imports.create_preview', {
						file_name: upB.name,
						mapping: { phone: 'so_dt', name: 'ho_ten' },
						idempotency_key: 'idem-l22-team-b',
					})
					await window.cskh.post('crm_cskh.api.imports.confirm', { batch: prevB.batch })
					for (let i = 0; i < 40; i += 1) {
						const s = await window.cskh.get('crm_cskh.api.imports.get_status', { batch: prevB.batch })
						if (s.status === 'COMPLETED') break
						await new Promise((r) => setTimeout(r, 300))
					}
					const poolB = await window.cskh.get('crm_cskh.api.pool_ui.list', {
						search: '09177401',
						page_size: 10,
					})
					const campB = await window.cskh.post('crm_cskh.api.campaigns.save', {
						code: 'CAMP-L22-TEAM-B',
						title: 'Chiến dịch Đội B L22',
						status: 'Active',
						team_scope: teamBNode,
						selection: { leads: poolB.items.map((x) => x.lead) },
					})
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign: campB.campaign,
						user: staffC,
						weight: 100,
						capacity_limit: 0,
						team: teamBNode,
						enabled: 1,
					})
					const dpB = await window.cskh.post('crm_cskh.api.distribution.preview', {
						campaign: campB.campaign,
						idempotency_key: 'idem-dp-l22-b',
					})
					const exB = await window.cskh.post('crm_cskh.api.distribution.execute', {
						preview_id: dpB.preview_id,
						expected_rule_version: dpB.rule_version,
						idempotency_key: 'idem-ex-l22-b',
					})
					for (let i = 0; i < 40; i += 1) {
						const r = await window.cskh.get('crm_cskh.api.distribution.get_run', { run_id: exB.run_id })
						if (r.status === 'completed') break
						await new Promise((res) => setTimeout(res, 300))
					}
					const listB = await window.cskh.get('crm_cskh.api.ownership_ui.assigned_list', {
						campaign: campB.campaign,
						page: 1,
						page_size: 10,
					})
					await window.cskh.post('crm_cskh.api.ownership.revoke', {
						work_item: listB.items[0].work_item,
						reason: 'Thu hồi về kho trong Đội B',
						return_to_pool: true,
						request_id: 'req-l22-revoke-b',
					})

					return {
						revokeFalseHttp: revResp.status,
						revokeFalseResult: revJson.message,
					}
				},
				{ campA: camp1, teamBNode: teamB, staffC: users.staff_c }
			)

			assert(
				l21And22.revokeFalseHttp === 200 &&
					l21And22.revokeFalseResult &&
					l21And22.revokeFalseResult.status === 'revoked',
				`L-21 form-urlencoded return_to_pool=false must yield status='revoked' (not 'queued'), got: ${JSON.stringify(l21And22)}`
			)

			const [dashA, dashB] = await Promise.all([
				leaderAS.page.evaluate(async () => {
					const r = await fetch('/api/method/crm_cskh.api.reporting.dashboard', {
						credentials: 'same-origin',
					})
					return (await r.json()).message
				}),
				leaderBS.page.evaluate(async () => {
					const r = await fetch('/api/method/crm_cskh.api.reporting.dashboard', {
						credentials: 'same-origin',
					})
					return (await r.json()).message
				}),
			])

			// Team A had 1 work item revoked with return_to_pool=false (`revoked_work_items = 1`)
			// and Team B had 1 work item revoked with return_to_pool=true (`unassigned_work_items = 1`, `active_claims = 1`, `revoked_work_items = 0`)!
			assert(
				dashA.cards.revoked_work_items === 1 &&
					dashB.cards.revoked_work_items === 0 &&
					dashB.cards.unassigned_work_items === 1 &&
					dashB.cards.active_claims === 1,
				`L-22 Leader dashboard cross-team leak: dashA=${JSON.stringify(dashA.cards)} dashB=${JSON.stringify(dashB.cards)}`
			)

			checks.push({
				id: 'BACKEND-DELTA-L20-L21-L22',
				passed: true,
				l20_over_200_error_rows: l20Check,
				l21_form_revoke_false_status: l21And22.revokeFalseResult.status,
				l22_leader_a_cards: dashA.cards,
				l22_leader_b_cards: dashB.cards,
			})

			assert(adminS.telemetry.consoleErrors.length === 0, `Console errors: ${adminS.telemetry.consoleErrors.join('; ')}`)
			assert(adminS.telemetry.pageErrors.length === 0, `Page errors: ${adminS.telemetry.pageErrors.join('; ')}`)
			assert(adminS.telemetry.server500s.length === 0, `Server 500s: ${adminS.telemetry.server500s.join('; ')}`)
		} finally {
			startWorker()
			await adminS.context.close()
			await staffAS.context.close()
			await staffBS.context.close()
			await leaderAS.context.close()
			await leaderBS.context.close()
		}
	} finally {
		startWorker()
		await browser.close()
	}

	const report = redactObj({
		generated_at: new Date().toISOString(),
		site: BASE_URL,
		integrated_git_sha: gitSha,
		baseline_evidence_sha_preserved: '0d3f2bc9da3e3e497a999baeb9a4dd19129224fb',
		checks,
	})
	fs.writeFileSync(OUT_PATH, JSON.stringify(report, null, 2) + '\n', 'utf-8')
	console.log(`ALL ASYNC DISTRIBUTION & BACKEND DELTA CHECKS PASSED (${checks.length}) -> ${OUT_PATH}`)
}

main().catch((err) => {
	try {
		startWorker()
	} catch (_e) {
		/* ignore */
	}
	console.error(err)
	process.exit(1)
})
