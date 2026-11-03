#!/usr/bin/env node
/**
 * Real-HTTP + real-Chromium + real-RQ E2E verification for Round-4 final integration:
 *   1. L-29 real-HTTP lockdown on P1 DocTypes (`CSKH Campaign`, `CSKH Assignment Rule`,
 *      `CSKH Distribution Run`, `CSKH Work Item`) & `permlevel = 1` fields (`selection_json`,
 *      `preview_json`, `actual_json`, `state_json`, `progress`, `error`, `lease_expires_at`),
 *      direct DocType write denial (including `CRM-ADMIN`), filter-oracle denial (`403`),
 *      and `frappe.desk.search.search_link` lockdown.
 *   2. Full real-Chromium Leader (`Sales Manager` A & B) and BOD (`CSKH BOD Viewer`) UI
 *      verification across `/cskh`, `/cskh/pool`, `/cskh/campaigns`, `/cskh/my-customers`,
 *      `/cskh/dashboard`, and `/cskh/import` (`403`), plus `Staff A` (`/cskh`,
 *      `/cskh/my-customers` with zero background `403` console/network noise) and `CRM-ADMIN`
 *      — WITHOUT widening any backend DocType/field/row permission.
 *   3. Preservation of L-23 (cross-team pool isolation across `pool_ui.list`,
 *      `customers.customer_pool`, `customers.pool_list`), L-24 (shared-campaign run history
 *      & live polling isolation per actor), L-25 (mapped `name`/`source` + `batch=` filter),
 *      L-26 (`400 INVALID_REQUEST` across all 8 paginated/bounded endpoints + `assignees`),
 *      and direct ownership mutation eligibility (`ownership.assign` / `ownership.reassign`).
 *
 * Preserves all historical Round 1 / Round 2 / Round 3 artifacts and writes redacted
 * Round-4 evidence to `deploy/p1/evidence/round4-final-integration-e2e.json`.
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
const OUT_PATH = process.env.P1_R4_REPORT_OUT
	? path.resolve(process.env.P1_R4_REPORT_OUT)
	: path.join(EVIDENCE_DIR, 'round4-final-integration-e2e.json')

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
		throw new Error(`ROUND4_E2E_ASSERTION_FAILED: ${msg}`)
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

function startWorker() {
	execSync(
		`docker exec -d ${BENCH_CONTAINER} bash -lc "cd /workspace/frappe-bench && pgrep -f '[f]rappe.utils.bench_helper frappe worker' >/dev/null || nohup bench worker --queue short,default,long >/tmp/p1-worker.log 2>&1 </dev/null &"`,
		{ stdio: 'pipe' }
	)
}

function benchExecute(method, kwargs = null) {
	const args = [
		'exec',
		'-w',
		'/workspace/frappe-bench',
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
	await page.addInitScript(() => {
		window.__xssTriggered = false
		window.__xssHit = function () {
			window.__xssTriggered = true
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
		{ usr: email, pwd: PASSWORD }
	)
	assert(loginStatus === 200, `Login failed for ${email}: ${loginStatus}`)

	const telemetry = { consoleErrors: [], pageErrors: [], server500s: [], api403s: [] }
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
		if (res.status() === 403 && res.url().includes('/api/')) {
			telemetry.api403s.push(res.url())
		}
	})
	return { context, page, telemetry }
}

async function importBatchCsv(page, batchKey, rows) {
	const lines = ['so_dt,ho_ten,nguon_kh']
	rows.forEach((r) => {
		const safeName = r.name.includes(',') ? `"${r.name.replace(/"/g, '""')}"` : r.name
		lines.push(`${r.phone},${safeName},${r.source}`)
	})
	const b64 = Buffer.from(lines.join('\n') + '\n', 'utf-8').toString('base64')
	return await page.evaluate(
		async ({ b64Content, key }) => {
			const up = await window.cskh.post('crm_cskh.api.import_files.upload_import_file', {
				file_name: `${key}.csv`,
				content: b64Content,
			})
			const prev = await window.cskh.post('crm_cskh.api.imports.create_preview', {
				file_name: up.name,
				mapping: { phone: 'so_dt', name: 'ho_ten', source: 'nguon_kh' },
				idempotency_key: `idem-${key}`,
			})
			await window.cskh.post('crm_cskh.api.imports.confirm', { batch: prev.batch })
			let finalStatus = null
			for (let i = 0; i < 60; i += 1) {
				const st = await window.cskh.get('crm_cskh.api.imports.get_status', {
					batch: prev.batch,
				})
				finalStatus = st
				if (['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED'].includes(st.status)) {
					break
				}
				await new Promise((r) => setTimeout(r, 400))
			}
			return {
				batch: prev.batch,
				status: finalStatus && finalStatus.status,
				totals: finalStatus && finalStatus.totals,
			}
		},
		{ b64Content: b64, key: batchKey }
	)
}

async function waitDistComplete(page, runId) {
	for (let i = 0; i < 60; i += 1) {
		const r = await page.evaluate(
			async (id) => window.cskh.get('crm_cskh.api.distribution.get_run', { run_id: id }),
			runId
		)
		const st = String((r && r.status) || '').toLowerCase()
		if (['completed', 'completed_with_errors', 'failed'].includes(st)) {
			return r
		}
		await new Promise((res) => setTimeout(res, 400))
	}
	throw new Error(`Timed out waiting for distribution run ${runId}`)
}

async function main() {
	fs.mkdirSync(EVIDENCE_DIR, { recursive: true })
	const gitSha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: ROOT, encoding: 'utf-8' }).trim()
	const appsTreeSha = execFileSync('git', ['rev-parse', 'HEAD:apps/crm_cskh'], {
		cwd: ROOT,
		encoding: 'utf-8',
	}).trim()
	const parentUiSha = execFileSync(
		'git',
		['rev-parse', '--verify', '5c37bf3dc464a0d8e2b9bd94ba55df40da469559^{commit}'],
		{ cwd: ROOT, encoding: 'utf-8' }
	).trim()
	const parentBackendL26Sha = execFileSync(
		'git',
		['rev-parse', '--verify', '2235757b79bb9f1a7fb3ae2957b28deb088185b9^{commit}'],
		{ cwd: ROOT, encoding: 'utf-8' }
	).trim()
	const parentBackendL29Sha = execFileSync(
		'git',
		['rev-parse', '--verify', '77e07dac7948c156039bc6a9746fe5d451fc0d71^{commit}'],
		{ cwd: ROOT, encoding: 'utf-8' }
	).trim()
	const parentBackendPoolListL23Sha = execFileSync(
		'git',
		['rev-parse', '--verify', '6c457b327e59eead47cc9d0982c4ee1df4fe9e64^{commit}'],
		{ cwd: ROOT, encoding: 'utf-8' }
	).trim()
	const baselineSha = execFileSync(
		'git',
		['rev-parse', '--verify', '0d3f2bcbbf9715a4c0c783903ce750de8646583d^{commit}'],
		{ cwd: ROOT, encoding: 'utf-8' }
	).trim()

	execFileSync('git', ['merge-base', '--is-ancestor', parentUiSha, 'HEAD'], { cwd: ROOT })
	execFileSync('git', ['merge-base', '--is-ancestor', parentBackendL26Sha, 'HEAD'], { cwd: ROOT })
	execFileSync('git', ['merge-base', '--is-ancestor', parentBackendL29Sha, 'HEAD'], { cwd: ROOT })
	execFileSync('git', ['merge-base', '--is-ancestor', parentBackendPoolListL23Sha, 'HEAD'], { cwd: ROOT })

	const seed = benchExecute('crm_cskh.tools.e2e_seed.reset_and_seed')
	const users = seed.users
	const teamA = seed.team_a
	const teamB = seed.team_b
	startWorker()

	const checks = []
	const browser = await chromium.launch({ headless: true })

	try {
		const adminS = await createActorPage(browser, users.admin)
		const bodS = await createActorPage(browser, users.bod)
		const leaderAS = await createActorPage(browser, users.leader_a)
		const leaderBS = await createActorPage(browser, users.leader_b)
		const staffAS = await createActorPage(browser, users.staff_a)

		try {
			await adminS.page.goto(`${BASE_URL}/cskh/pool`, { waitUntil: 'networkidle' })
			await bodS.page.goto(`${BASE_URL}/cskh/pool`, { waitUntil: 'networkidle' })
			await leaderAS.page.goto(`${BASE_URL}/cskh/pool`, { waitUntil: 'networkidle' })
			await leaderBS.page.goto(`${BASE_URL}/cskh/pool`, { waitUntil: 'networkidle' })
			await staffAS.page.goto(`${BASE_URL}/cskh`, { waitUntil: 'networkidle' })

			// =================================================================
			// 1. Seed real data via controlled Admin APIs (Import + Campaigns +
			//    Rules + Async Distribution + Ownership Reassign/Revoke)
			// =================================================================
			const batchShared = await importBatchCsv(adminS.page, 'r4-batch-shared', [
				{
					phone: '0918840101',
					name: 'Nguyen R4 Khach A1 <img src=x onerror="window.__xssHit()">',
					source: 'R4SharedSrc',
				},
				{ phone: '0918840102', name: 'Tran R4 Khach A2', source: 'R4SharedSrc' },
				{ phone: '0918840103', name: 'Le R4 Khach B1', source: 'R4SharedSrc' },
				{ phone: '0918840104', name: 'Vu R4 Khach B2', source: 'R4SharedSrc' },
			])
			assert(
				batchShared.status === 'COMPLETED',
				`Expected batchShared COMPLETED, got ${batchShared.status}`
			)

			const batchTeamA = await importBatchCsv(adminS.page, 'r4-batch-ta', [
				{ phone: '0918840201', name: 'Pham R4 TeamA 1', source: 'R4TeamASrc' },
				{ phone: '0918840202', name: 'Doan R4 TeamA 2', source: 'R4TeamASrc' },
				{ phone: '0918840203', name: 'Hoang R4 TeamA 3', source: 'R4TeamASrc' },
			])
			assert(
				batchTeamA.status === 'COMPLETED',
				`Expected batchTeamA COMPLETED, got ${batchTeamA.status}`
			)

			const setupData = await adminS.page.evaluate(
				async ({ bSh, bTa, tA, tB, staffA, staffB, staffC }) => {
					const shRows = await window.cskh.get('crm_cskh.api.pool_ui.list', {
						batch: bSh,
						page_size: 20,
					})
					const taRows = await window.cskh.get('crm_cskh.api.pool_ui.list', {
						batch: bTa,
						page_size: 20,
					})
					const sharedLeads = shRows.items.map((x) => x.lead).sort()
					const taLeads = taRows.items.map((x) => x.lead).sort()

					const campA = await window.cskh.post('crm_cskh.api.campaigns.save', {
						code: `R4-TA-${Date.now()}`,
						title: 'Chiến dịch Đội A R4',
						status: 'Active',
						team_scope: tA,
						selection: { leads: taLeads, secret_marker: 'L29_SECRET_TA' },
					})
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign: campA.campaign,
						user: staffA,
						weight: 70,
						capacity_limit: 0,
						enabled: 1,
						team: tA,
						version: 1,
					})
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign: campA.campaign,
						user: staffB,
						weight: 30,
						capacity_limit: 5,
						enabled: 1,
						team: tA,
						version: 1,
					})

					const campShared = await window.cskh.post('crm_cskh.api.campaigns.save', {
						code: `R4-SH-${Date.now()}`,
						title: 'Chiến dịch Chung R4',
						status: 'Active',
						team_scope: null,
						selection: { leads: sharedLeads, secret_marker: 'L29_SECRET_SHARED' },
					})
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign: campShared.campaign,
						user: staffA,
						weight: 50,
						capacity_limit: 0,
						enabled: 1,
						team: tA,
						version: 1,
					})
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign: campShared.campaign,
						user: staffC,
						weight: 50,
						capacity_limit: 5,
						enabled: 1,
						team: tB,
						version: 1,
					})

					const prevA = await window.cskh.post('crm_cskh.api.distribution.preview', {
						campaign: campA.campaign,
						idempotency_key: `prev-r4-a-${Date.now()}`,
					})
					const execA = await window.cskh.post('crm_cskh.api.distribution.execute', {
						preview_id: prevA.preview_id,
						expected_rule_version: prevA.rule_version,
						idempotency_key: `exec-r4-a-${Date.now()}`,
					})

					const prevSh = await window.cskh.post('crm_cskh.api.distribution.preview', {
						campaign: campShared.campaign,
						idempotency_key: `prev-r4-sh-${Date.now()}`,
					})
					const execSh = await window.cskh.post('crm_cskh.api.distribution.execute', {
						preview_id: prevSh.preview_id,
						expected_rule_version: prevSh.rule_version,
						idempotency_key: `exec-r4-sh-${Date.now()}`,
					})

					return {
						campA: campA.campaign,
						campShared: campShared.campaign,
						runA: execA.run_id || prevA.preview_id,
						runShared: execSh.run_id || prevSh.preview_id,
					}
				},
				{
					bSh: batchShared.batch,
					bTa: batchTeamA.batch,
					tA: teamA,
					tB: teamB,
					staffA: users.staff_a,
					staffB: users.staff_b,
					staffC: users.staff_c,
				}
			)

			const doneA = await waitDistComplete(adminS.page, setupData.runA)
			const doneShared = await waitDistComplete(adminS.page, setupData.runShared)
			assert(doneA.status === 'completed', `runA expected completed, got ${doneA.status}`)
			assert(doneShared.status === 'completed', `runShared expected completed, got ${doneShared.status}`)

			// Reassign 1 Team A item (`wiKeepA`: `staffA -> staffB`) to generate >= 2 history events,
			// AND revoke 1 Team A item (`wiMoveToB`) -> distribute to `campB` (`staffC`) for L-23 check!
			// Also revoke 1 `campA` item with `return_to_pool: 1` (leaving a stale `queued` Work Item in `campA`)
			// and distribute BOTH `movedLead` and `retPoolLead` to `campB` (`staffC`) to verify `6c457b3` SQL `pool_list` L-23!
			const ownershipSetup = await adminS.page.evaluate(
				async ({ campA, campShared, tB, staffA, staffB, staffC }) => {
					const asgAll = await window.cskh.get('crm_cskh.api.ownership_ui.assigned_list', {
						campaign: campShared,
						page_size: 50,
					})
					const teamAItems = asgAll.items.filter((x) => x.assigned_to === staffA)
					const wiKeepA = teamAItems[0].work_item
					const wiMoveToB = teamAItems[1].work_item
					const movedLead = teamAItems[1].lead

					const asgCampA = await window.cskh.get('crm_cskh.api.ownership_ui.assigned_list', {
						campaign: campA,
						page_size: 50,
					})
					const wiRetPoolA =
						asgCampA.items.find((x) => x.assigned_to === staffB) ||
						asgCampA.items[asgCampA.items.length - 1]
					const retPoolLead = wiRetPoolA.lead

					await window.cskh.post('crm_cskh.api.ownership.reassign', {
						work_item: wiKeepA,
						expected_owner: staffA,
						target_user: staffB,
						reason: 'Chuyển giao Đội A kiểm thử R4',
						request_id: `r4-reasg-${Date.now()}`,
					})
					await window.cskh.post('crm_cskh.api.ownership.revoke', {
						work_item: wiMoveToB,
						expected_owner: staffA,
						reason: 'Thu hồi chuyển Đội B kiểm thử L23 R4',
						return_to_pool: 0,
						request_id: `r4-rev-${Date.now()}`,
					})
					await window.cskh.post('crm_cskh.api.ownership.revoke', {
						work_item: wiRetPoolA.work_item,
						expected_owner: wiRetPoolA.assigned_to,
						reason: 'Trả về kho Đội A rồi chuyển Đội B kiểm thử 6c457b3',
						return_to_pool: 1,
						request_id: `r4-retpool-${Date.now()}`,
					})

					const campB = await window.cskh.post('crm_cskh.api.campaigns.save', {
						code: `R4-TB-${Date.now()}`,
						title: 'Chiến dịch Đội B R4',
						status: 'Active',
						team_scope: tB,
						selection: { leads: [movedLead, retPoolLead], secret_marker: 'L29_SECRET_TB' },
					})
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign: campB.campaign,
						user: staffC,
						weight: 100,
						capacity_limit: 5,
						enabled: 1,
						team: tB,
						version: 1,
					})
					const prevB = await window.cskh.post('crm_cskh.api.distribution.preview', {
						campaign: campB.campaign,
						idempotency_key: `prev-r4-b-${Date.now()}`,
					})
					const execB = await window.cskh.post('crm_cskh.api.distribution.execute', {
						preview_id: prevB.preview_id,
						expected_rule_version: prevB.rule_version,
						idempotency_key: `exec-r4-b-${Date.now()}`,
					})
					return {
						campB: campB.campaign,
						wiKeepA,
						wiMoveToB,
						movedLead,
						retPoolLead,
						runB: execB.run_id || prevB.preview_id,
					}
				},
				{
					campA: setupData.campA,
					campShared: setupData.campShared,
					tB: teamB,
					staffA: users.staff_a,
					staffB: users.staff_b,
					staffC: users.staff_c,
				}
			)
			const doneB = await waitDistComplete(adminS.page, ownershipSetup.runB)
			assert(doneB.status === 'completed', `runB expected completed, got ${doneB.status}`)
			setupData.campB = ownershipSetup.campB

			// =================================================================
			// 2. Check L-29 Real-HTTP DocType / Document / Field (`permlevel=1`)
			//    & `search_link` Lockdown Across Actors
			// =================================================================
			const httpProbeHelper = async (page, url, method = 'GET', bodyObj = null) => {
				return await page.evaluate(
					async ({ u, m, b }) => {
						const headers = { Accept: 'application/json' }
						if (window.frappe && window.frappe.csrf_token) {
							headers['X-Frappe-CSRF-Token'] = window.frappe.csrf_token
						}
						const opts = { method: m, credentials: 'same-origin', headers }
						if (b !== null) {
							headers['Content-Type'] = 'application/json'
							opts.body = JSON.stringify(b)
						}
						const r = await fetch(u, opts)
						let data = null
						try {
							data = await r.json()
						} catch (_e) {
							data = null
						}
						return { status: r.status, data }
					},
					{ u: url, m: method, b: bodyObj }
				)
			}

			// 2a. Admin positive `permlevel = 1` read, but direct DocType POST/PUT denied (403)
			const adminCampRaw = await httpProbeHelper(
				adminS.page,
				`/api/resource/CSKH%20Campaign/${encodeURIComponent(setupData.campShared)}`
			)
			assert(adminCampRaw.status === 200, `Admin GET CSKH Campaign expected 200, got ${adminCampRaw.status}`)
			assert(
				String(adminCampRaw.data.data.selection_json || '').includes('L29_SECRET_SHARED'),
				'Admin must read permlevel=1 selection_json'
			)
			const adminDirectWrite = await httpProbeHelper(
				adminS.page,
				`/api/resource/CSKH%20Campaign/${encodeURIComponent(setupData.campShared)}`,
				'PUT',
				{ title: 'Tampered Directly' }
			)
			assert(
				adminDirectWrite.status === 403,
				`Admin direct PUT /api/resource/CSKH Campaign must be 403, got ${adminDirectWrite.status}`
			)

			// 2b. Leader A HTTP probes: foreign campaign/run 403, own/shared `permlevel=1` stripped, filter oracle 403, search_link scoped/denied
			const laForeignCamp = await httpProbeHelper(
				leaderAS.page,
				`/api/resource/CSKH%20Campaign/${encodeURIComponent(setupData.campB)}`
			)
			assert(laForeignCamp.status === 403, `Leader A foreign campB expected 403, got ${laForeignCamp.status}`)

			const laForeignRun = await httpProbeHelper(
				leaderAS.page,
				`/api/resource/CSKH%20Distribution%20Run/${encodeURIComponent(ownershipSetup.runB)}`
			)
			assert(laForeignRun.status === 403, `Leader A foreign runB expected 403, got ${laForeignRun.status}`)

			const laSharedCampRaw = await httpProbeHelper(
				leaderAS.page,
				`/api/resource/CSKH%20Campaign/${encodeURIComponent(setupData.campShared)}`
			)
			assert(laSharedCampRaw.status === 200, `Leader A shared camp expected 200, got ${laSharedCampRaw.status}`)
			assert(
				laSharedCampRaw.data.data.selection_json == null &&
					!JSON.stringify(laSharedCampRaw.data).includes('L29_SECRET_SHARED'),
				'Leader A must have selection_json stripped on /api/resource/CSKH Campaign/<name>'
			)

			const laSharedRunRaw = await httpProbeHelper(
				leaderAS.page,
				`/api/resource/CSKH%20Distribution%20Run/${encodeURIComponent(setupData.runShared)}`
			)
			assert(laSharedRunRaw.status === 200, `Leader A shared run expected 200, got ${laSharedRunRaw.status}`)
			for (const rf of ['preview_json', 'actual_json', 'state_json', 'error', 'lease_expires_at']) {
				assert(
					laSharedRunRaw.data.data[rf] == null,
					`Leader A raw run field ${rf} must be null/omitted, got ${laSharedRunRaw.data.data[rf]}`
				)
			}
			assert(
				!JSON.stringify(laSharedRunRaw.data).includes(users.staff_c),
				'Leader A raw /api/resource/CSKH Distribution Run must NOT leak Team B staff_c'
			)

			const laFilterOracle = await httpProbeHelper(
				leaderAS.page,
				`/api/resource/CSKH%20Distribution%20Run?filters=${encodeURIComponent(
					JSON.stringify([['actual_json', 'like', '%staff%']])
				)}`
			)
			assert(
				laFilterOracle.status === 403,
				`Leader A permlevel=1 filter oracle expected 403, got ${laFilterOracle.status}`
			)

			const laSearchIdentity = await httpProbeHelper(
				leaderAS.page,
				'/api/method/frappe.desk.search.search_link',
				'POST',
				{ doctype: 'CSKH Identity Index', txt: '' }
			)
			assert(
				laSearchIdentity.status === 403,
				`Leader A search_link on CSKH Identity Index expected 403, got ${laSearchIdentity.status}`
			)
			const laSearchLead = await httpProbeHelper(
				leaderAS.page,
				'/api/method/frappe.desk.search.search_link',
				'POST',
				{
					doctype: 'CRM Lead',
					txt: '',
					filters: { name: ['in', [ownershipSetup.movedLead]] },
					ignore_user_permissions: true,
				}
			)
			assert(
				laSearchLead.status === 200 && (laSearchLead.data.message || []).length === 0,
				`Leader A search_link on CRM Lead must exclude Team B movedLead: ${JSON.stringify(laSearchLead)}`
			)
			const bodSearchLead = await httpProbeHelper(
				bodS.page,
				'/api/method/frappe.desk.search.search_link',
				'POST',
				{ doctype: 'CRM Lead', txt: '' }
			)
			assert(
				bodSearchLead.status === 403,
				`BOD search_link on CRM Lead expected 403, got ${bodSearchLead.status}`
			)

			const laSearchCamp = await httpProbeHelper(
				leaderAS.page,
				'/api/method/frappe.desk.search.search_link',
				'POST',
				{
					doctype: 'CSKH Campaign',
					txt: '',
					filters: { name: ['in', [setupData.campA, setupData.campB, setupData.campShared]] },
					ignore_user_permissions: true,
				}
			)
			assert(laSearchCamp.status === 200, `Leader A search_link CSKH Campaign expected 200`)
			const laSearchNames = (laSearchCamp.data.message || []).map((r) => r.value)
			assert(
				laSearchNames.includes(setupData.campA) &&
					laSearchNames.includes(setupData.campShared) &&
					!laSearchNames.includes(setupData.campB),
				`Leader A search_link must scope campaigns strictly: ${JSON.stringify(laSearchNames)}`
			)

			// 2c. BOD HTTP probes: reads metadata across teams, `permlevel=1` stripped on raw API, filter oracle 403
			const bodRunListRaw = await httpProbeHelper(
				bodS.page,
				`/api/resource/CSKH%20Distribution%20Run?fields=${encodeURIComponent(
					JSON.stringify(['name', 'status', 'preview_json', 'actual_json', 'state_json', 'progress'])
				)}&filters=${encodeURIComponent(JSON.stringify([['name', '=', setupData.runShared]]))}`
			)
			assert(bodRunListRaw.status === 200, `BOD list runs expected 200, got ${bodRunListRaw.status}`)
			assert(
				bodRunListRaw.data.data.length === 1 &&
					!('actual_json' in bodRunListRaw.data.data[0]) &&
					!('preview_json' in bodRunListRaw.data.data[0]) &&
					!('progress' in bodRunListRaw.data.data[0]),
				'BOD /api/resource list must omit all permlevel=1 fields'
			)
			const bodFilterOracle = await httpProbeHelper(
				bodS.page,
				`/api/resource/CSKH%20Campaign?filters=${encodeURIComponent(
					JSON.stringify([['selection_json', 'like', '%L29%']])
				)}`
			)
			assert(
				bodFilterOracle.status === 403,
				`BOD permlevel=1 filter oracle expected 403, got ${bodFilterOracle.status}`
			)

			// 2d. Staff A HTTP probe: denied DocType read on CSKH Campaign (403)
			const staffCampRaw = await httpProbeHelper(
				staffAS.page,
				`/api/resource/CSKH%20Campaign/${encodeURIComponent(setupData.campA)}`
			)
			assert(staffCampRaw.status === 403, `Staff A CSKH Campaign read expected 403, got ${staffCampRaw.status}`)

			checks.push({
				id: 'L29-HTTP-DOCTYPE-PERMLEVEL-LOCKDOWN',
				passed: true,
				details: {
					admin_direct_put_status: adminDirectWrite.status,
					leader_a_foreign_camp_status: laForeignCamp.status,
					leader_a_foreign_run_status: laForeignRun.status,
					leader_a_filter_oracle_status: laFilterOracle.status,
					leader_a_search_link_identity_status: laSearchIdentity.status,
					bod_search_link_crm_lead_status: bodSearchLead.status,
					bod_filter_oracle_status: bodFilterOracle.status,
					staff_a_camp_read_status: staffCampRaw.status,
				},
			})

			// =================================================================
			// 3. Full Real-Chromium UI Audit for Leader A & Leader B
			// =================================================================
			// 3a. `/cskh` landing cards & `/cskh/import` 403
			await leaderAS.page.goto(`${BASE_URL}/cskh`, { waitUntil: 'networkidle' })
			const laLandingHrefs = await leaderAS.page.$$eval('main .cskh-card a.cskh-btn', (els) =>
				els.map((e) => e.getAttribute('href'))
			)
			assert(
				!laLandingHrefs.includes('/cskh/import') &&
					laLandingHrefs.includes('/cskh/pool') &&
					laLandingHrefs.includes('/cskh/campaigns') &&
					laLandingHrefs.includes('/cskh/my-customers') &&
					laLandingHrefs.includes('/cskh/dashboard'),
				`Leader A landing cards mismatch: ${JSON.stringify(laLandingHrefs)}`
			)
			const laImportResp = await leaderAS.page.goto(`${BASE_URL}/cskh/import`, {
				waitUntil: 'domcontentloaded',
			})
			assert(
				laImportResp.status() === 403,
				`Leader A /cskh/import expected 403, got ${laImportResp.status()}`
			)

			// 3b. `/cskh/pool` + L-23 verification across `pool_ui.list`, `customers.customer_pool`, `customers.pool_list`
			await leaderAS.page.goto(`${BASE_URL}/cskh/pool`, { waitUntil: 'networkidle' })
			await leaderAS.page.waitForSelector('#pool-table:not([hidden])')
			const laPoolDomText = await leaderAS.page.textContent('#pool-rows')
			assert(laPoolDomText.includes('***'), 'Leader A pool table must render masked phones')
			assert(
				!(await leaderAS.page.evaluate(() => window.__xssTriggered)),
				'XSS must NOT trigger on Leader A /cskh/pool'
			)

			const l23PoolCheck = await leaderAS.page.evaluate(
				async ({ movedLead, retPoolLead, campA }) => {
					const pUi = await window.cskh.get('crm_cskh.api.pool_ui.list', { page_size: 100 })
					const pCust = await window.cskh.get('crm_cskh.api.customers.customer_pool', {
						page_size: 100,
					})
					const pLegacy = await window.cskh.get('crm_cskh.api.customers.pool_list', {
						campaign: campA,
						page_size: 100,
					})
					const forbiddenSet = new Set([movedLead, retPoolLead])
					return {
						inUi: pUi.items.some((x) => forbiddenSet.has(x.lead)),
						inCust: pCust.items.some((x) => forbiddenSet.has(x.lead)),
						inLegacy: pLegacy.items.some((x) => forbiddenSet.has(x.lead)),
						legacyTotalMatchesItems: pLegacy.total === pLegacy.items.length,
						uiCount: pUi.total,
					}
				},
				{
					movedLead: ownershipSetup.movedLead,
					retPoolLead: ownershipSetup.retPoolLead,
					campA: setupData.campA,
				}
			)
			assert(
				!l23PoolCheck.inUi &&
					!l23PoolCheck.inCust &&
					!l23PoolCheck.inLegacy &&
					l23PoolCheck.legacyTotalMatchesItems,
				`Leader A must NOT see movedLead or retPoolLead (active in Team B) on any pool endpoint: ${JSON.stringify(l23PoolCheck)}`
			)
			const lbSeesMoved = await leaderBS.page.evaluate(
				async ({ movedLead, retPoolLead }) => {
					const pUi = await window.cskh.get('crm_cskh.api.pool_ui.list', { page_size: 100 })
					const pCust = await window.cskh.get('crm_cskh.api.customers.customer_pool', {
						page_size: 100,
					})
					return {
						inUiMoved: pUi.items.some((x) => x.lead === movedLead),
						inUiRetPool: pUi.items.some((x) => x.lead === retPoolLead),
						inCustMoved: pCust.items.some((x) => x.lead === movedLead),
						inCustRetPool: pCust.items.some((x) => x.lead === retPoolLead),
					}
				},
				{ movedLead: ownershipSetup.movedLead, retPoolLead: ownershipSetup.retPoolLead }
			)
			assert(
				lbSeesMoved.inUiMoved &&
					lbSeesMoved.inUiRetPool &&
					lbSeesMoved.inCustMoved &&
					lbSeesMoved.inCustRetPool,
				`Leader B positive control must see both movedLead and retPoolLead owned by Team B: ${JSON.stringify(lbSeesMoved)}`
			)

			// 3c. `/cskh/campaigns` for Leader A & Leader B (rules, hidden Admin/lookup controls, L-24 shared run history)
			await leaderAS.page.goto(`${BASE_URL}/cskh/campaigns`, { waitUntil: 'networkidle' })
			await leaderAS.page.waitForFunction(
				() => document.querySelectorAll('#rule-campaign option').length >= 2
			)
			const laCampControls = await leaderAS.page.evaluate(() => ({
				formHidden: document.getElementById('campaign-form-card').hidden,
				distHidden: document.getElementById('dist-card').hidden,
				lookupWrapHidden: document.getElementById('dist-run-id-wrap').hidden,
				lookupBtnHidden: document.getElementById('dist-run-lookup').hidden,
				options: Array.from(document.querySelectorAll('#rule-campaign option')).map((o) => o.value),
			}))
			assert(
				laCampControls.formHidden &&
					laCampControls.distHidden &&
					laCampControls.lookupWrapHidden &&
					laCampControls.lookupBtnHidden,
				`Leader A must have Admin & Run Lookup controls hidden: ${JSON.stringify(laCampControls)}`
			)
			assert(
				laCampControls.options.includes(setupData.campA) &&
					laCampControls.options.includes(setupData.campShared) &&
					!laCampControls.options.includes(setupData.campB),
				`Leader A campaign dropdown mismatch: ${JSON.stringify(laCampControls.options)}`
			)
			await leaderAS.page.selectOption('#rule-campaign', setupData.campShared)
			await leaderAS.page.waitForFunction(
				(u) => (document.getElementById('rule-list')?.textContent || '').includes(u),
				users.staff_a
			)
			const laRuleText = await leaderAS.page.textContent('#rule-list')
			assert(
				laRuleText.includes(users.staff_a) &&
					laRuleText.includes('Không giới hạn') &&
					!laRuleText.includes(users.staff_c),
				`Leader A shared campaign rules must show only staff_a with Không giới hạn: ${laRuleText}`
			)

			// Verify L-24 shared run history API + DOM for Leader A (no Team B `staff_c` leak, no lookup buttons)
			const laSharedRunHistory = await leaderAS.page.evaluate(
				async ({ campSh, runSh }) => {
					const res = await window.cskh.get('crm_cskh.api.run_history.runs', {
						campaign: campSh,
						run_id: runSh,
					})
					return {
						can_lookup: res.can_lookup,
						can_distribute: res.can_distribute,
						item: res.items[0],
						rowButtonsCount: document.querySelectorAll('#dist-runs-rows button').length,
						domText: document.getElementById('dist-runs-rows')?.textContent || '',
					}
				},
				{ campSh: setupData.campShared, runSh: setupData.runShared }
			)
			assert(
				!laSharedRunHistory.can_lookup &&
					!laSharedRunHistory.can_distribute &&
					laSharedRunHistory.rowButtonsCount === 0 &&
					laSharedRunHistory.domText.includes(setupData.runShared) &&
					!JSON.stringify(laSharedRunHistory.item).includes(users.staff_c) &&
					laSharedRunHistory.item.created === 2,
				`Leader A shared run_history L-24 check failed: ${JSON.stringify(laSharedRunHistory)}`
			)

			// Symmetric check for Leader B on `/cskh/campaigns`
			await leaderBS.page.goto(`${BASE_URL}/cskh/campaigns`, { waitUntil: 'networkidle' })
			await leaderBS.page.selectOption('#rule-campaign', setupData.campShared)
			await leaderBS.page.waitForFunction(
				(u) => (document.getElementById('rule-list')?.textContent || '').includes(u),
				users.staff_c
			)
			const lbRuleText = await leaderBS.page.textContent('#rule-list')
			assert(
				lbRuleText.includes(users.staff_c) && !lbRuleText.includes(users.staff_a),
				`Leader B shared campaign rules must show only staff_c: ${lbRuleText}`
			)

			// 3d. `/cskh/my-customers` for Leader A: team scope table + clicking `Chi tiết quyền phụ trách` -> `ownership.history`
			await leaderAS.page.goto(`${BASE_URL}/cskh/my-customers`, { waitUntil: 'networkidle' })
			await leaderAS.page.waitForSelector('#mine-scope-table:not([hidden])')
			const laScopeButtons = await leaderAS.page.$$eval('#mine-scope-rows button', (btns) =>
				btns.map((b) => b.textContent.trim())
			)
			assert(
				laScopeButtons.length >= 1 &&
					laScopeButtons.every((t) => t === 'Chi tiết quyền phụ trách'),
				`Leader A scope actions must be read-only 'Chi tiết quyền phụ trách', got: ${JSON.stringify(laScopeButtons)}`
			)
			await leaderAS.page.evaluate((targetOwner) => {
				const rows = Array.from(document.querySelectorAll('#mine-scope-rows tr'))
				const match = rows.find((tr) => (tr.textContent || '').includes(targetOwner))
				if (!match) throw new Error('Target row not found in Leader A scope table')
				match.querySelector('button').click()
			}, users.staff_b)
			await leaderAS.page.waitForSelector('#mine-detail-card:not([hidden])')
			await leaderAS.page.waitForFunction((wi) =>
				(document.getElementById('mine-detail')?.textContent || '').includes(wi),
				ownershipSetup.wiKeepA
			)
			const laDetailText = await leaderAS.page.textContent('#mine-detail')
			assert(
				laDetailText.includes(ownershipSetup.wiKeepA) &&
					laDetailText.includes('Chuyển người phụ trách'),
				`Leader A ownership detail card must render history events: ${laDetailText}`
			)

			// 3e. `/cskh/dashboard` for Leader A
			await leaderAS.page.goto(`${BASE_URL}/cskh/dashboard`, { waitUntil: 'networkidle' })
			await leaderAS.page.waitForFunction(
				() => document.querySelectorAll('#dash-cards .cskh-metric, #dash-cards .cskh-stat').length >= 3
			)
			await leaderAS.page.waitForFunction(
				(u) => (document.getElementById('dash-table-wrap')?.textContent || '').includes(u),
				users.staff_b
			)
			const laDashText = await leaderAS.page.textContent('#dash-table-wrap')
			assert(
				laDashText.includes(users.staff_b) && !laDashText.includes(users.staff_c),
				`Leader A dashboard must show Team A users and exclude Team B staff_c: ${laDashText}`
			)

			checks.push({
				id: 'LEADER-UI-FULL-SCREEN-AUDIT',
				passed: true,
				details: {
					landing_cards: laLandingHrefs,
					import_http_status: 403,
					campaigns_controls: laCampControls,
					l23_pool_check: l23PoolCheck,
					l24_shared_run_created: laSharedRunHistory.item.created,
					ownership_detail_verified: ownershipSetup.wiKeepA,
				},
			})

			// =================================================================
			// 4. Full Real-Chromium UI Audit for BOD (`CSKH BOD Viewer`)
			// =================================================================
			// 4a. `/cskh` & `/cskh/import` 403
			await bodS.page.goto(`${BASE_URL}/cskh`, { waitUntil: 'networkidle' })
			const bodLandingHrefs = await bodS.page.$$eval('main .cskh-card a.cskh-btn', (els) =>
				els.map((e) => e.getAttribute('href'))
			)
			assert(
				!bodLandingHrefs.includes('/cskh/import') && bodLandingHrefs.length === 4,
				`BOD landing cards mismatch: ${JSON.stringify(bodLandingHrefs)}`
			)
			const bodImportResp = await bodS.page.goto(`${BASE_URL}/cskh/import`, {
				waitUntil: 'domcontentloaded',
			})
			assert(
				bodImportResp.status() === 403,
				`BOD /cskh/import expected 403, got ${bodImportResp.status()}`
			)

			// 4b. `/cskh/pool` 0-PII (search hidden, lead IDs only, no names, no masked phone digits, no Open in CRM)
			await bodS.page.goto(`${BASE_URL}/cskh/pool`, { waitUntil: 'networkidle' })
			await bodS.page.waitForSelector('#pool-table:not([hidden])')
			const bodPoolAudit = await bodS.page.evaluate(() => ({
				searchHidden: document.getElementById('pool-search-wrap').hidden,
				rowsText: document.getElementById('pool-rows')?.textContent || '',
				openCrmLinks: document.querySelectorAll('#pool-rows a').length,
			}))
			assert(
				bodPoolAudit.searchHidden &&
					bodPoolAudit.openCrmLinks === 0 &&
					!bodPoolAudit.rowsText.includes('Nguyen R4') &&
					!bodPoolAudit.rowsText.includes('***'),
				`BOD pool must hide search, names, phones, and CRM links completely: ${JSON.stringify(bodPoolAudit)}`
			)

			// 4c. `/cskh/campaigns`: sees all campaigns, `Look up run` visible & works via `distribution.get_run`!
			await bodS.page.goto(`${BASE_URL}/cskh/campaigns`, { waitUntil: 'networkidle' })
			await bodS.page.waitForFunction(
				() => document.querySelectorAll('#rule-campaign option').length >= 3
			)
			const bodLookupVisible = await bodS.page.evaluate(() => ({
				formHidden: document.getElementById('campaign-form-card').hidden,
				distHidden: document.getElementById('dist-card').hidden,
				lookupWrapHidden: document.getElementById('dist-run-id-wrap').hidden,
				lookupBtnHidden: document.getElementById('dist-run-lookup').hidden,
			}))
			assert(
				bodLookupVisible.formHidden &&
					bodLookupVisible.distHidden &&
					!bodLookupVisible.lookupWrapHidden &&
					!bodLookupVisible.lookupBtnHidden,
				`BOD must have Admin forms hidden but Run Lookup visible: ${JSON.stringify(bodLookupVisible)}`
			)
			await bodS.page.fill('#dist-run-id', setupData.runShared)
			await bodS.page.click('#dist-run-lookup')
			await bodS.page.waitForFunction(
				(rid) => (document.getElementById('dist-run-lookup-result')?.textContent || '').includes(rid),
				setupData.runShared
			)
			const bodLookupText = await bodS.page.textContent('#dist-run-lookup-result')
			assert(
				bodLookupText.includes(users.staff_a) && bodLookupText.includes(users.staff_c),
				`BOD Run Lookup must render org-wide summary via controlled API: ${bodLookupText}`
			)

			// 4d. `/cskh/my-customers` for BOD: 0-PII scope list + `Chi tiết quyền phụ trách` works
			await bodS.page.goto(`${BASE_URL}/cskh/my-customers`, { waitUntil: 'networkidle' })
			await bodS.page.waitForSelector('#mine-scope-table:not([hidden])')
			const bodScopeText = await bodS.page.textContent('#mine-scope-rows')
			assert(
				!bodScopeText.includes('Nguyen R4') && !bodScopeText.includes('***'),
				`BOD my-customers scope table must be 0-PII: ${bodScopeText}`
			)
			await bodS.page.evaluate((targetOwner) => {
				const rows = Array.from(document.querySelectorAll('#mine-scope-rows tr'))
				const match = rows.find((tr) => (tr.textContent || '').includes(targetOwner))
				if (!match) throw new Error('Target row not found in BOD scope table')
				match.querySelector('button').click()
			}, users.staff_b)
			await bodS.page.waitForSelector('#mine-detail-card:not([hidden])')
			await bodS.page.waitForFunction((wi) =>
				(document.getElementById('mine-detail')?.textContent || '').includes(wi),
				ownershipSetup.wiKeepA
			)
			const bodDetailText = await bodS.page.textContent('#mine-detail')
			assert(
				bodDetailText.includes(ownershipSetup.wiKeepA) && !RAW_PHONE_RE.test(bodDetailText),
				'BOD ownership history detail must render cleanly with zero phone PII'
			)

			// 4e. `/cskh/dashboard` for BOD: org-wide totals + both Team A and Team B users
			await bodS.page.goto(`${BASE_URL}/cskh/dashboard`, { waitUntil: 'networkidle' })
			await bodS.page.waitForFunction(
				(u) => (document.getElementById('dash-table-wrap')?.textContent || '').includes(u),
				users.staff_c
			)
			const bodDashText = await bodS.page.textContent('#dash-table-wrap')
			assert(
				bodDashText.includes(users.staff_b) && bodDashText.includes(users.staff_c),
				`BOD dashboard must include both Team A and Team B owners: ${bodDashText}`
			)

			checks.push({
				id: 'BOD-UI-FULL-SCREEN-AUDIT-ZERO-PII',
				passed: true,
				details: {
					landing_cards: bodLandingHrefs,
					import_http_status: 403,
					lookup_visibility: bodLookupVisible,
					run_lookup_verified: setupData.runShared,
					ownership_detail_verified: ownershipSetup.wiKeepA,
				},
			})

			// =================================================================
			// 5. Staff A (`/cskh`, `/cskh/my-customers` with 0 background 403s)
			//    + L-26 HTTP 400 INVALID_REQUEST & Direct Mutation Checks
			// =================================================================
			await staffAS.page.goto(`${BASE_URL}/cskh`, { waitUntil: 'networkidle' })
			const staffLandingHrefs = await staffAS.page.$$eval('main .cskh-card a.cskh-btn', (els) =>
				els.map((e) => e.getAttribute('href'))
			)
			assert(
				staffLandingHrefs.length === 1 && staffLandingHrefs[0] === '/cskh/my-customers',
				`Staff A landing cards must contain only /cskh/my-customers, got: ${JSON.stringify(staffLandingHrefs)}`
			)

			staffAS.telemetry.api403s.length = 0
			await staffAS.page.goto(`${BASE_URL}/cskh/my-customers`, { waitUntil: 'networkidle' })
			await staffAS.page.waitForSelector('#mine-table:not([hidden])')
			assert(
				staffAS.telemetry.api403s.length === 0,
				`Staff A /cskh/my-customers must NOT fire any background 403 API calls, got: ${JSON.stringify(staffAS.telemetry.api403s)}`
			)
			const staffCampOptions = await staffAS.page.$$eval('#mine-campaign option', (opts) =>
				opts.map((o) => o.value).filter(Boolean)
			)
			assert(
				staffCampOptions.includes(setupData.campA),
				`Staff A campaign filter should populate from own assigned items: ${JSON.stringify(staffCampOptions)}`
			)

			// L-26 across all 8 endpoints + direct ownership mutation guard
			const l26AndMutation = await adminS.page.evaluate(
				async ({ wiKeepA, staffB, staffC, campA }) => {
					const endpoints = [
						['crm_cskh.api.pool_ui.list', { page: 'bad' }],
						['crm_cskh.api.customers.customer_pool', { page: 'bad' }],
						['crm_cskh.api.customers.pool_list', { page_size: 0 }],
						['crm_cskh.api.customers.my_list', { page: -1 }],
						['crm_cskh.api.ownership.history', { work_item: wiKeepA, limit: 'bad' }],
						['crm_cskh.api.ownership_ui.assigned_list', { page_size: 'bad' }],
						['crm_cskh.api.import_history.history', { page: 0 }],
						['crm_cskh.api.run_history.runs', { page_size: -5 }],
					]
					const l26Results = []
					for (const [ep, params] of endpoints) {
						const qs = new URLSearchParams(params).toString()
						const r = await fetch(`/api/method/${ep}?${qs}`, {
							credentials: 'same-origin',
							headers: { Accept: 'application/json' },
						})
						const body = await r.json()
						l26Results.push({
							endpoint: ep,
							status: r.status,
							p1_code: body.p1_code || (body.error && body.error.code),
						})
					}

					// Direct cross-team reassign rejection (`staffC` of Team B onto `campA` work item)
					const campAItems = await window.cskh.get('crm_cskh.api.ownership_ui.assigned_list', {
						campaign: campA,
						page_size: 20,
					})
					const wiCampA = campAItems.items[0]
					const mutRes = await fetch('/api/method/crm_cskh.api.ownership.reassign', {
						method: 'POST',
						credentials: 'same-origin',
						headers: {
							'Content-Type': 'application/json',
							Accept: 'application/json',
							'X-Frappe-CSRF-Token': window.frappe.csrf_token,
						},
						body: JSON.stringify({
							work_item: wiCampA.work_item,
							expected_owner: wiCampA.assigned_to,
							target_user: staffC,
							reason: 'Cross-team direct reassign attempt',
							request_id: `r4-bad-reasg-${Date.now()}`,
						}),
					})
					const mutBody = await mutRes.json()
					const asgDropdown = await window.cskh.get('crm_cskh.api.ownership_ui.assignees', {
						campaign: campA,
					})
					return {
						l26Results,
						mutStatus: mutRes.status,
						mutCode: mutBody.p1_code || (mutBody.error && mutBody.error.code),
						campAAssignees: asgDropdown.items.map((x) => x.user),
					}
				},
				{
					wiKeepA: ownershipSetup.wiKeepA,
					staffB: users.staff_b,
					staffC: users.staff_c,
					campA: setupData.campA,
				}
			)
			for (const row of l26AndMutation.l26Results) {
				assert(
					row.status === 400 && row.p1_code === 'INVALID_REQUEST',
					`L-26 expected 400 INVALID_REQUEST on ${row.endpoint}, got ${JSON.stringify(row)}`
				)
			}
			assert(
				l26AndMutation.mutStatus === 400 && l26AndMutation.mutCode === 'INVALID_REQUEST',
				`Direct cross-team reassign expected 400 INVALID_REQUEST, got ${l26AndMutation.mutStatus} ${l26AndMutation.mutCode}`
			)
			assert(
				!l26AndMutation.campAAssignees.includes(users.staff_c),
				`assignees(campA) must exclude Team B staff_c: ${JSON.stringify(l26AndMutation.campAAssignees)}`
			)

			checks.push({
				id: 'STAFF-ZERO-403-AND-L26-ALL-8-ENDPOINTS-AND-DIRECT-MUTATION',
				passed: true,
				details: {
					staff_landing_cards: staffLandingHrefs,
					staff_my_customers_background_403_count: staffAS.telemetry.api403s.length,
					l26_endpoints: l26AndMutation.l26Results,
					direct_cross_team_reassign: {
						status: l26AndMutation.mutStatus,
						code: l26AndMutation.mutCode,
					},
					camp_a_assignees: l26AndMutation.campAAssignees,
				},
			})

			// =================================================================
			// 6. Telemetry Gate Across All 5 Browser Contexts
			// =================================================================
			const allTelemetry = {
				admin: adminS.telemetry,
				bod: bodS.telemetry,
				leader_a: leaderAS.telemetry,
				leader_b: leaderBS.telemetry,
				staff_a: staffAS.telemetry,
			}
			for (const [actorKey, t] of Object.entries(allTelemetry)) {
				assert(
					t.consoleErrors.length === 0,
					`Console errors on ${actorKey}: ${JSON.stringify(t.consoleErrors)}`
				)
				assert(
					t.pageErrors.length === 0,
					`Page errors on ${actorKey}: ${JSON.stringify(t.pageErrors)}`
				)
				assert(
					t.server500s.length === 0,
					`HTTP 500s on ${actorKey}: ${JSON.stringify(t.server500s)}`
				)
			}
		} finally {
			await adminS.context.close()
			await bodS.context.close()
			await leaderAS.context.close()
			await leaderBS.context.close()
			await staffAS.context.close()
		}
	} finally {
		await browser.close()
		startWorker()
	}

	const report = redactObj({
		generated_at: new Date().toISOString(),
		round: 4,
		site: BASE_URL,
		git_sha: gitSha,
		apps_tree_sha: appsTreeSha,
		parent_ui_sha: parentUiSha,
		parent_backend_l26_sha: parentBackendL26Sha,
		parent_backend_l29_sha: parentBackendL29Sha,
		parent_backend_pool_list_l23_sha: parentBackendPoolListL23Sha,
		baseline_ui_sha: baselineSha,
		passed_checks: checks.filter((c) => c.passed).length,
		total_checks: checks.length,
		all_passed: checks.every((c) => c.passed),
		checks,
	})

	const serialized = JSON.stringify(report, null, 2) + '\n'
	assert(!RAW_PHONE_RE.test(serialized), 'Unredacted phone number leaked into Round-4 evidence!')
	fs.writeFileSync(OUT_PATH, serialized, 'utf-8')
	console.log(`ROUND 4 E2E PASSED (${report.passed_checks}/${report.total_checks}) -> ${OUT_PATH}`)
}

main().catch((err) => {
	console.error(err)
	process.exit(1)
})
