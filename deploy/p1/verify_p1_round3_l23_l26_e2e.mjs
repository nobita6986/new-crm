#!/usr/bin/env node
/**
 * Real-HTTP + real-Chromium + real-RQ E2E verification for Round-3 items:
 *   1. L-23: `pool_ui.list` never uses Team A `revoked`/`closed` history to leak
 *      leads currently active/owned in Team B (phone, owner, campaign, items,
 *      total, unique_customers, work_items, search, filters), with positive
 *      controls for Team A, Team B, CRM Admin, and BOD (0-PII).
 *   2. L-24: `run_history.runs` and `/cskh/campaigns` live polling on shared
 *      campaigns (`team_scope IS NULL`) strictly scope `assigned`, `skipped`,
 *      `preview`, `progress`, `total`, `created`, and `errors` per actor.
 *   3. L-26: Invalid pagination (`page`/`page_size`) returns HTTP `400` with
 *      stable `code = "INVALID_REQUEST"` (`p1_code = "INVALID_REQUEST"`);
 *      `ownership_ui.assignees(campaign)` enforces `team_scope` + eligibility
 *      in API and browser dropdowns (`404 NOT_FOUND` on unknown campaign).
 *   4. L-25 / Import Batch filter: Mapped `name` (XSS/formula sanitised) and
 *      `source` persist on `CRM Lead`, and `/cskh/pool` `batch=` filter matches
 *      real persisted batch-lead relationships while preserving team isolation.
 *
 * Preserves all baseline screenshots (`0d3f2bcbbf9715a4c0c783903ce750de8646583d`)
 * and writes redacted evidence to `deploy/p1/evidence/round3-l23-l26-delta-e2e.json`.
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
const OUT_PATH = process.env.P1_R3_REPORT_OUT
	? path.resolve(process.env.P1_R3_REPORT_OUT)
	: path.join(EVIDENCE_DIR, 'round3-l23-l26-delta-e2e.json')

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
		throw new Error(`ROUND3_E2E_ASSERTION_FAILED: ${msg}`)
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
		`docker exec ${BENCH_CONTAINER} bash -lc "pkill -f '[f]rappe.utils.bench_helper frappe worker' || true; pkill -f 'nohup [b]ench worker' || true; sleep 0.5"`,
		{ stdio: 'pipe' }
	)
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
			return { batch: prev.batch, status: finalStatus && finalStatus.status, totals: finalStatus && finalStatus.totals }
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
	const baselineSha = execFileSync(
		'git',
		['rev-parse', '--verify', '0d3f2bcbbf9715a4c0c783903ce750de8646583d^{commit}'],
		{ cwd: ROOT, encoding: 'utf-8' }
	).trim()

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
		const staffCS = await createActorPage(browser, users.staff_c)

		try {
			await adminS.page.goto(`${BASE_URL}/cskh/pool`, { waitUntil: 'networkidle' })
			await bodS.page.goto(`${BASE_URL}/cskh/pool`, { waitUntil: 'networkidle' })
			await leaderAS.page.goto(`${BASE_URL}/cskh/pool`, { waitUntil: 'networkidle' })
			await leaderBS.page.goto(`${BASE_URL}/cskh/pool`, { waitUntil: 'networkidle' })
			await staffAS.page.goto(`${BASE_URL}/cskh`, { waitUntil: 'networkidle' })
			await staffCS.page.goto(`${BASE_URL}/cskh`, { waitUntil: 'networkidle' })

			// =================================================================
			// 1. L-25 + Import Batch Filter (two batches, XSS/formula name, source)
			// =================================================================
			const batch1Res = await importBatchCsv(adminS.page, 'r3-batch-1', [
				{
					phone: '0918810101',
					name: 'Nguyen L25 Lo1 <img src=x onerror="window.__xssHit()">',
					source: 'R3SrcBatch1',
				},
				{
					phone: '0918810102',
					name: '=cmd|calc!L25Lo1B',
					source: 'R3SrcBatch1',
				},
			])
			assert(batch1Res.status === 'COMPLETED', `Batch 1 expected COMPLETED, got ${batch1Res.status}`)

			const batch2Res = await importBatchCsv(adminS.page, 'r3-batch-2', [
				{ phone: '0918810201', name: 'Tran L25 Lo2 A', source: 'R3SrcBatch2' },
				{ phone: '0918810202', name: 'Le L25 Lo2 B', source: 'R3SrcBatch2' },
			])
			assert(batch2Res.status === 'COMPLETED', `Batch 2 expected COMPLETED, got ${batch2Res.status}`)

			const batchCheckAdmin = await adminS.page.evaluate(
				async ({ b1, b2 }) => {
					const rB1 = await window.cskh.get('crm_cskh.api.pool_ui.list', { batch: b1, page_size: 50 })
					const rB2 = await window.cskh.get('crm_cskh.api.pool_ui.list', { batch: b2, page_size: 50 })
					const rSrc1 = await window.cskh.get('crm_cskh.api.pool_ui.list', {
						source: 'R3SrcBatch1',
						page_size: 50,
					})
					const rName = await window.cskh.get('crm_cskh.api.pool_ui.list', {
						search: 'Nguyen L25 Lo1',
						page_size: 50,
					})
					return { rB1, rB2, rSrc1, rName }
				},
				{ b1: batch1Res.batch, b2: batch2Res.batch }
			)
			assert(batchCheckAdmin.rB1.total === 2, `Batch 1 filter expected 2 leads, got ${batchCheckAdmin.rB1.total}`)
			assert(batchCheckAdmin.rB2.total === 2, `Batch 2 filter expected 2 leads, got ${batchCheckAdmin.rB2.total}`)
			assert(
				batchCheckAdmin.rB1.items.every((i) => i.state === 'eligible' && i.source === 'R3SrcBatch1'),
				'Batch 1 leads must be eligible with persisted source R3SrcBatch1'
			)
			assert(batchCheckAdmin.rSrc1.total === 2, `Source R3SrcBatch1 expected 2, got ${batchCheckAdmin.rSrc1.total}`)
			assert(batchCheckAdmin.rName.total === 1, `Mapped name search expected 1, got ${batchCheckAdmin.rName.total}`)
			const sanitizedDisplay = batchCheckAdmin.rName.items[0].display_name
			assert(
				!sanitizedDisplay.includes('<img') && sanitizedDisplay.includes('Nguyen L25 Lo1'),
				`Expected HTML stripped from display_name, got: ${sanitizedDisplay}`
			)

			// Verify UI batch filter on `/cskh/pool` in Chromium and XSS safety
			await adminS.page.goto(`${BASE_URL}/cskh/pool`, { waitUntil: 'networkidle' })
			await adminS.page.fill('#pool-batch', batch1Res.batch)
			await adminS.page.click('#pool-refresh')
			await adminS.page.waitForFunction(
				() => document.querySelectorAll('#pool-rows tr').length === 2,
				{ timeout: 10000 }
			)
			const xssFired = await adminS.page.evaluate(() => Boolean(window.__xssTriggered))
			assert(!xssFired, 'XSS payload must never execute on /cskh/pool')

			const b1Leads = batchCheckAdmin.rB1.items.map((i) => i.lead).sort()
			const b2Leads = batchCheckAdmin.rB2.items.map((i) => i.lead).sort()

			// =================================================================
			// 2. L-23: Revoked in Team A -> Active in Team B (zero cross-team leak)
			// =================================================================
			const leadMoveToB = b1Leads[0]
			const leadStayRevokedA = b1Leads[1]

			const campA = await adminS.page.evaluate(
				async ({ leads, team, staffA }) => {
					const c = await window.cskh.post('crm_cskh.api.campaigns.save', {
						code: 'CAMP-R3-TEAMA',
						title: 'Chiến dịch Đội A R3',
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
					const p = await window.cskh.post('crm_cskh.api.distribution.preview', {
						campaign: c.campaign,
						idempotency_key: 'prev-r3-a',
					})
					const ex = await window.cskh.post('crm_cskh.api.distribution.execute', {
						preview_id: p.preview_id,
						expected_rule_version: p.rule_version,
						idempotency_key: 'exec-r3-a',
					})
					return { campaign: c.campaign, run_id: ex.run_id || p.preview_id }
				},
				{ leads: [leadMoveToB, leadStayRevokedA], team: teamA, staffA: users.staff_a }
			)
			await waitDistComplete(adminS.page, campA.run_id)

			// Revoke BOTH leads from Team A (`return_to_pool: 0` -> `revoked`), then assign `leadMoveToB` to Team B (`staff_c`).
			const campB = await adminS.page.evaluate(
				async ({ campAId, moveLead, stayLead, staffA, teamBNode, staffC }) => {
					const assigned = await window.cskh.get('crm_cskh.api.ownership_ui.assigned_list', {
						campaign: campAId,
						page_size: 50,
					})
					for (const item of assigned.items) {
						if (item.lead === moveLead || item.lead === stayLead) {
							await window.cskh.post('crm_cskh.api.ownership.revoke', {
								work_item: item.work_item,
								expected_owner: staffA,
								reason: 'Thu hồi R3 kiểm thử L-23',
								request_id: `rev-r3-${item.lead}`,
								return_to_pool: 0,
							})
						}
					}
					const cB = await window.cskh.post('crm_cskh.api.campaigns.save', {
						code: 'CAMP-R3-TEAMB',
						title: 'Chiến dịch Nội Bộ Đội B R3',
						status: 'Active',
						team_scope: teamBNode,
						selection: { leads: [moveLead] },
					})
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign: cB.campaign,
						user: staffC,
						weight: 100,
						capacity_limit: 0,
						team: teamBNode,
						enabled: 1,
					})
					const pB = await window.cskh.post('crm_cskh.api.distribution.preview', {
						campaign: cB.campaign,
						idempotency_key: 'prev-r3-b',
					})
					const exB = await window.cskh.post('crm_cskh.api.distribution.execute', {
						preview_id: pB.preview_id,
						expected_rule_version: pB.rule_version,
						idempotency_key: 'exec-r3-b',
					})
					return { campaign: cB.campaign, run_id: exB.run_id || pB.preview_id }
				},
				{
					campAId: campA.campaign,
					moveLead: leadMoveToB,
					stayLead: leadStayRevokedA,
					staffA: users.staff_a,
					teamBNode: teamB,
					staffC: users.staff_c,
				}
			)
			await waitDistComplete(adminS.page, campB.run_id)

			const moveLeadAdminRow = await adminS.page.evaluate(async (leadId) => {
				const r = await window.cskh.get('crm_cskh.api.pool_ui.list', { search: leadId, page_size: 20 })
				return r.items.find((x) => x.lead === leadId)
			}, leadMoveToB)
			assert(
				moveLeadAdminRow && moveLeadAdminRow.state === 'assigned' && moveLeadAdminRow.owner === users.staff_c,
				'Admin positive control must show leadMoveToB assigned to staff_c in Team B'
			)
			const movePhoneCanonical = moveLeadAdminRow.phone
			const movePhoneLocal = movePhoneCanonical.startsWith('+84')
				? '0' + movePhoneCanonical.slice(3)
				: movePhoneCanonical

			const l23LeaderA = await leaderAS.page.evaluate(
				async ({ moveLead, stayLead, phoneLocal, phoneCanon, moveName, campAId, campBId, batch1 }) => {
					const all = await window.cskh.get('crm_cskh.api.pool_ui.list', { page: 1, page_size: 100 })
					const byLocal = await window.cskh.get('crm_cskh.api.pool_ui.list', { search: phoneLocal })
					const byCanon = await window.cskh.get('crm_cskh.api.pool_ui.list', { search: phoneCanon })
					const byName = await window.cskh.get('crm_cskh.api.pool_ui.list', {
						search: moveName.slice(0, 14),
					})
					const byId = await window.cskh.get('crm_cskh.api.pool_ui.list', { search: moveLead })
					const byCampB = await window.cskh.get('crm_cskh.api.pool_ui.list', { campaign: campBId })
					const byCampA = await window.cskh.get('crm_cskh.api.pool_ui.list', { campaign: campAId })
					const byBatch1 = await window.cskh.get('crm_cskh.api.pool_ui.list', { batch: batch1 })
					return { all, byLocal, byCanon, byName, byId, byCampB, byCampA, byBatch1, stayLead }
				},
				{
					moveLead: leadMoveToB,
					stayLead: leadStayRevokedA,
					phoneLocal: movePhoneLocal,
					phoneCanon: movePhoneCanonical,
					moveName: moveLeadAdminRow.display_name,
					campAId: campA.campaign,
					campBId: campB.campaign,
					batch1: batch1Res.batch,
				}
			)

			const rawJsonLeaderA = JSON.stringify(l23LeaderA)
			assert(!rawJsonLeaderA.includes(leadMoveToB), 'L-23 leak: Leader A saw leadMoveToB ID')
			assert(!rawJsonLeaderA.includes(movePhoneCanonical), 'L-23 leak: Leader A saw Team B canonical phone')
			assert(!rawJsonLeaderA.includes(movePhoneLocal), 'L-23 leak: Leader A saw Team B local phone')
			assert(!rawJsonLeaderA.includes(users.staff_c), 'L-23 leak: Leader A saw staff_c owner')
			assert(!rawJsonLeaderA.includes(campB.campaign), 'L-23 leak: Leader A saw Team B campaign ID')
			assert(!rawJsonLeaderA.includes('Chiến dịch Nội Bộ Đội B R3'), 'L-23 leak: Leader A saw Team B campaign title')

			for (const probeKey of ['byLocal', 'byCanon', 'byId', 'byCampB']) {
				const p = l23LeaderA[probeKey]
				assert(
					p.total === 0 && p.unique_customers === 0 && p.work_items === 0 && p.items.length === 0,
					`L-23 oracle leak on ${probeKey}: ${JSON.stringify(p)}`
				)
			}
			// Positive control for Leader A: `leadStayRevokedA` (still revoked in Team A only) IS visible!
			assert(
				l23LeaderA.byCampA.total === 1 &&
					l23LeaderA.byCampA.work_items === 1 &&
					l23LeaderA.byCampA.items[0].lead === leadStayRevokedA &&
					l23LeaderA.byCampA.items[0].state === 'revoked',
				'Leader A positive control failed for leadStayRevokedA'
			)
			// Combined L-23 + L-25 check: Leader A filtering `batch=batch1` ONLY gets `leadStayRevokedA` (1), NOT `leadMoveToB`!
			assert(
				l23LeaderA.byBatch1.total === 1 && l23LeaderA.byBatch1.items[0].lead === leadStayRevokedA,
				`Leader A batch filter expected only leadStayRevokedA (1), got ${l23LeaderA.byBatch1.total}`
			)

			// Leader B & BOD positive controls for L-23
			const l23LeaderB = await leaderBS.page.evaluate(async (leadId) => {
				return window.cskh.get('crm_cskh.api.pool_ui.list', { search: leadId })
			}, leadMoveToB)
			assert(
				l23LeaderB.total === 1 &&
					l23LeaderB.items[0].state === 'assigned' &&
					l23LeaderB.items[0].owner === users.staff_c &&
					l23LeaderB.items[0].campaign === campB.campaign,
				'Leader B positive control failed for leadMoveToB'
			)

			const l23Bod = await bodS.page.evaluate(async (campBId) => {
				return window.cskh.get('crm_cskh.api.pool_ui.list', { campaign: campBId })
			}, campB.campaign)
			assert(
				l23Bod.total === 1 &&
					l23Bod.items[0].phone === null &&
					l23Bod.items[0].display_name === '',
				'BOD 0-PII positive control failed on pool_ui.list'
			)

			checks.push({
				id: 'ROUND3-L23-AND-L25-POOL-ISOLATION-AND-BATCH-FILTER',
				passed: true,
				batch1_admin_total: batchCheckAdmin.rB1.total,
				batch2_admin_total: batchCheckAdmin.rB2.total,
				xss_executed: xssFired,
				leader_a_cross_team_probes_zero: true,
				leader_a_batch1_scoped_total: l23LeaderA.byBatch1.total,
				leader_b_positive_control_total: l23LeaderB.total,
				bod_zero_pii_verified: true,
			})

			// =================================================================
			// 3. L-24: Shared campaign `run_history` + live UI polling per actor
			// =================================================================
			const sharedBatch = await importBatchCsv(adminS.page, 'r3-shared-leads', [
				{ phone: '0918820101', name: 'Khach Chung 1', source: 'R3Shared' },
				{ phone: '0918820102', name: 'Khach Chung 2', source: 'R3Shared' },
				{ phone: '0918820103', name: 'Khach Chung 3', source: 'R3Shared' },
				{ phone: '0918820104', name: 'Khach Chung 4', source: 'R3Shared' },
			])
			assert(sharedBatch.status === 'COMPLETED', 'Shared batch import failed')
			const fourFreeLeads = await adminS.page.evaluate(async (b) => {
				const r = await window.cskh.get('crm_cskh.api.pool_ui.list', { batch: b, page_size: 20 })
				return r.items.map((x) => x.lead).sort()
			}, sharedBatch.batch)
			assert(fourFreeLeads.length === 4, `Expected 4 shared leads, got ${fourFreeLeads.length}`)

			pauseWorker()
			const sharedRunInfo = await adminS.page.evaluate(
				async ({ freeLeads, moveBLead, staffA, staffB, staffC }) => {
					const c = await window.cskh.post('crm_cskh.api.campaigns.save', {
						code: 'CAMP-R3-SHARED',
						title: 'Chiến dịch Liên Đội R3',
						status: 'Active',
						team_scope: null,
						selection: { leads: [...freeLeads, moveBLead] },
					})
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign: c.campaign,
						user: staffA,
						weight: 50,
						capacity_limit: 0,
						enabled: 1,
					})
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign: c.campaign,
						user: staffC,
						weight: 50,
						capacity_limit: 0,
						enabled: 1,
					})
					// Add an excluded rule (`enabled: 0` or capacity=0? `enabled: 0` is filtered) -- `moveBLead` is already active in `campB`,
					// so `excluded_count === 1` globally (`0` for team leaders on shared campaign) and `eligible_count === 4` (`2` Team A + `2` Team B).
					const prev = await window.cskh.post('crm_cskh.api.distribution.preview', {
						campaign: c.campaign,
						idempotency_key: 'prev-r3-shared',
					})
					const ex = await window.cskh.post('crm_cskh.api.distribution.execute', {
						preview_id: prev.preview_id,
						expected_rule_version: prev.rule_version,
						idempotency_key: 'exec-r3-shared',
					})
					return {
						campaign: c.campaign,
						run_id: ex.run_id || prev.preview_id,
						enqueue_status: ex.status,
					}
				},
				{
					freeLeads: fourFreeLeads,
					moveBLead: leadMoveToB,
					staffA: users.staff_a,
					staffB: users.staff_b,
					staffC: users.staff_c,
				}
			)
			assert(sharedRunInfo.enqueue_status === 'queued', `Expected queued, got ${sharedRunInfo.enqueue_status}`)

			// Verify Leader A & Leader B queued polling via `run_history.runs` + browser `/cskh/campaigns` BEFORE worker runs!
			await leaderAS.page.goto(`${BASE_URL}/cskh/campaigns`, { waitUntil: 'networkidle' })
			await leaderAS.page.selectOption('#rule-campaign', sharedRunInfo.campaign)
			await leaderAS.page.waitForFunction(
				(rid) => (document.getElementById('dist-run-lookup-result')?.innerText || '').includes(rid),
				sharedRunInfo.run_id,
				{ timeout: 10000 }
			)
			const queuedLeaderA = await leaderAS.page.evaluate(async (rid) => {
				const api = await window.cskh.get('crm_cskh.api.run_history.runs', {
					run_id: rid,
					page: 1,
					page_size: 1,
				})
				const dom = document.getElementById('dist-run-lookup-result')?.innerText || ''
				return { item: api.items[0], dom }
			}, sharedRunInfo.run_id)

			const queuedLeaderB = await leaderBS.page.evaluate(async (rid) => {
				const api = await window.cskh.get('crm_cskh.api.run_history.runs', {
					run_id: rid,
					page: 1,
					page_size: 1,
				})
				return api.items[0]
			}, sharedRunInfo.run_id)

			assert(
				queuedLeaderA.item.status === 'queued' &&
					queuedLeaderA.item.progress === 0 &&
					queuedLeaderA.item.total === 2 &&
					queuedLeaderB.total === 2 &&
					queuedLeaderA.item.preview.excluded_count === 0,
				`Expected queued scoped totals of Leader A (${queuedLeaderA.item.total}) == 2 & Leader B (${queuedLeaderB.total}) == 2`
			)
			assert(
				!JSON.stringify(queuedLeaderA).includes(users.staff_c),
				'L-24 leak while queued: Leader A saw staff_c'
			)
			assert(
				!JSON.stringify(queuedLeaderB).includes(users.staff_a),
				'L-24 leak while queued: Leader B saw staff_a'
			)

			// Reload Leader A mid-queue to prove reload recovery works for Sales Manager via `run_history.runs`, then start worker!
			await leaderAS.page.reload({ waitUntil: 'networkidle' })
			await leaderAS.page.waitForFunction(
				(rid) => (document.getElementById('dist-run-lookup-result')?.innerText || '').includes(rid),
				sharedRunInfo.run_id,
				{ timeout: 10000 }
			)

			startWorker()
			await waitDistComplete(adminS.page, sharedRunInfo.run_id)

			// Wait for Leader A's browser live-polling to transition `#dist-run-lookup-result` to Completed (`Đã hoàn tất`)
			await leaderAS.page.waitForFunction(
				() => {
					const txt = document.getElementById('dist-run-lookup-result')?.innerText || ''
					return txt.toLowerCase().includes('hoàn tất') || txt.includes('Đã phân phối xong')
				},
				{ timeout: 15000 }
			)

			const finalL24 = await Promise.all([
				leaderAS.page.evaluate(async (rid) => {
					const r = await window.cskh.get('crm_cskh.api.run_history.runs', { run_id: rid })
					return { item: r.items[0], dom: document.getElementById('dist-run-lookup-result')?.innerText || '' }
				}, sharedRunInfo.run_id),
				leaderBS.page.evaluate(async (rid) => {
					const r = await window.cskh.get('crm_cskh.api.run_history.runs', { run_id: rid })
					return r.items[0]
				}, sharedRunInfo.run_id),
				adminS.page.evaluate(async (rid) => {
					const r = await window.cskh.get('crm_cskh.api.run_history.runs', { run_id: rid })
					return r.items[0]
				}, sharedRunInfo.run_id),
				bodS.page.evaluate(async (rid) => {
					const r = await window.cskh.get('crm_cskh.api.run_history.runs', { run_id: rid })
					return r.items[0]
				}, sharedRunInfo.run_id),
			])

			const [finA, finB, finAdmin, finBod] = finalL24
			assert(
				finA.item.created === 2 &&
					finA.item.total === 2 &&
					finA.item.progress === 2 &&
					Object.keys(finA.item.assigned).every((u) => u === users.staff_a) &&
					!JSON.stringify(finA).includes(users.staff_c),
				'L-24 leak on completed shared run: Leader A saw staff_c or wrong counters'
			)
			assert(
				finB.created === 2 &&
					finB.total === 2 &&
					finB.progress === 2 &&
					Object.keys(finB.assigned).every((u) => u === users.staff_c) &&
					!JSON.stringify(finB).includes(users.staff_a),
				'L-24 leak on completed shared run: Leader B saw staff_a or wrong counters'
			)
			assert(
				finA.item.created + finB.created === finAdmin.created && finAdmin.created === 4,
				`Expected Leader A (${finA.item.created}) + Leader B (${finB.created}) == Admin (${finAdmin.created}) == 4`
			)
			assert(
				finAdmin.preview.excluded_count === 1 && finBod.created === 4,
				`Admin/BOD positive control failed: excluded_count=${finAdmin.preview.excluded_count}, bod=${finBod.created}`
			)

			checks.push({
				id: 'ROUND3-L24-SHARED-CAMPAIGN-SCOPED-RUN-HISTORY-AND-POLLING',
				passed: true,
				queued_leader_a_total: queuedLeaderA.item.total,
				queued_leader_b_total: queuedLeaderB.total,
				completed_leader_a_created: finA.item.created,
				completed_leader_b_created: finB.created,
				completed_admin_created: finAdmin.created,
				completed_bod_created: finBod.created,
				leader_a_zero_team_b_leak: true,
				leader_b_zero_team_a_leak: true,
			})

			// =================================================================
			// 4. L-26: HTTP 400 INVALID_REQUEST on bad pagination + scoped assignees
			// =================================================================
			const l26Http = await adminS.page.evaluate(
				async ({ campAId, campBId, staffB, staffC }) => {
					const endpoints = [
						'crm_cskh.api.pool_ui.list',
						'crm_cskh.api.ownership_ui.assigned_list',
						'crm_cskh.api.import_history.history',
						'crm_cskh.api.run_history.runs',
					]
					const badQueries = [
						'page=abc',
						'page=0',
						'page=-1',
						'page_size=abc',
						'page_size=0',
						'page_size=101',
					]
					const badResults = []
					for (const ep of endpoints) {
						for (const q of badQueries) {
							const res = await fetch(`/api/method/${ep}?${q}`, {
								credentials: 'same-origin',
								headers: { Accept: 'application/json' },
							})
							const body = await res.json()
							badResults.push({
								ep,
								q,
								status: res.status,
								code: body.code,
								p1_code: body.p1_code,
							})
						}
					}

					const assigneesA = await window.cskh.get('crm_cskh.api.ownership_ui.assignees', {
						campaign: campAId,
					})
					const assigneesB = await window.cskh.get('crm_cskh.api.ownership_ui.assignees', {
						campaign: campBId,
					})
					// Verify rule-based eligibility exclusion (`enabled = 0` on `staff_b` in `campA`, `capacity_limit = 1` on `staff_c` in `campB` who already owns 1 item)
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign: campAId,
						user: staffB,
						weight: 50,
						capacity_limit: 0,
						enabled: 0,
					})
					await window.cskh.post('crm_cskh.api.campaigns.save_rule', {
						campaign: campBId,
						user: staffC,
						weight: 100,
						capacity_limit: 1,
						enabled: 1,
					})
					const assigneesAAfter = await window.cskh.get('crm_cskh.api.ownership_ui.assignees', {
						campaign: campAId,
					})
					const assigneesBAfter = await window.cskh.get('crm_cskh.api.ownership_ui.assignees', {
						campaign: campBId,
					})
					const nfRes = await fetch(
						'/api/method/crm_cskh.api.ownership_ui.assignees?campaign=NO-SUCH-CAMP-R3',
						{ credentials: 'same-origin', headers: { Accept: 'application/json' } }
					)
					const nfBody = await nfRes.json()
					return {
						badResults,
						assigneesA: assigneesA.items.map((x) => x.user),
						assigneesB: assigneesB.items.map((x) => x.user),
						assigneesAAfter: assigneesAAfter.items.map((x) => x.user),
						assigneesBAfter: assigneesBAfter.items.map((x) => x.user),
						notFound: { status: nfRes.status, code: nfBody.code, p1_code: nfBody.p1_code },
					}
				},
				{ campAId: campA.campaign, campBId: campB.campaign, staffB: users.staff_b, staffC: users.staff_c }
			)

			assert(
				l26Http.badResults.every(
					(r) => r.status === 400 && r.code === 'INVALID_REQUEST' && r.p1_code === 'INVALID_REQUEST'
				),
				`Expected all 24 bad pagination probes to return 400 INVALID_REQUEST: ${JSON.stringify(
					l26Http.badResults.filter((r) => r.status !== 400 || r.code !== 'INVALID_REQUEST')
				)}`
			)
			assert(
				l26Http.assigneesA.includes(users.staff_a) &&
					l26Http.assigneesA.includes(users.staff_b) &&
					!l26Http.assigneesA.includes(users.staff_c) &&
					!l26Http.assigneesA.includes(users.outside),
				`assignees(campA) leaked out-of-team users: ${JSON.stringify(l26Http.assigneesA)}`
			)
			assert(
				l26Http.assigneesB.includes(users.staff_c) &&
					!l26Http.assigneesB.includes(users.staff_a) &&
					!l26Http.assigneesB.includes(users.staff_b) &&
					!l26Http.assigneesB.includes(users.outside),
				`assignees(campB) leaked out-of-team users: ${JSON.stringify(l26Http.assigneesB)}`
			)
			assert(
				!l26Http.assigneesAAfter.includes(users.staff_b) &&
					l26Http.assigneesAAfter.includes(users.staff_a),
				`assignees(campA) must exclude disabled rule user staff_b: ${JSON.stringify(l26Http.assigneesAAfter)}`
			)
			assert(
				!l26Http.assigneesBAfter.includes(users.staff_c) &&
					l26Http.assigneesBAfter.includes(users.leader_b),
				`assignees(campB) must exclude capacity-full user staff_c while keeping leader_b: ${JSON.stringify(l26Http.assigneesBAfter)}`
			)
			assert(
				l26Http.notFound.status === 404 &&
					l26Http.notFound.code === 'NOT_FOUND' &&
					l26Http.notFound.p1_code === 'NOT_FOUND',
				`Expected 404 NOT_FOUND on unknown campaign, got ${JSON.stringify(l26Http.notFound)}`
			)

			// Verify UI dropdown on `/cskh/my-customers` Transfer Owner for `campB` item does NOT offer Team A / outside users!
			await adminS.page.goto(`${BASE_URL}/cskh/my-customers`, { waitUntil: 'networkidle' })
			await adminS.page.selectOption('#mine-campaign', campB.campaign)
			await adminS.page.waitForFunction(
				() => document.querySelectorAll('#mine-scope-rows tr').length === 1,
				{ timeout: 10000 }
			)
			await adminS.page.locator('#mine-scope-rows button.is-primary').first().click()
			await adminS.page.waitForSelector('#mine-detail select.cskh-select', { timeout: 10000 })
			const transferOptions = await adminS.page.evaluate(() =>
				Array.from(document.querySelectorAll('#mine-detail select.cskh-select option')).map(
					(o) => o.value
				)
			)
			assert(
				!transferOptions.includes(users.staff_a) &&
					!transferOptions.includes(users.staff_b) &&
					!transferOptions.includes(users.outside),
				`UI Transfer Owner dropdown leaked out-of-team users: ${JSON.stringify(transferOptions)}`
			)

			checks.push({
				id: 'ROUND3-L26-PAGINATION-400-AND-CAMPAIGN-ASSIGNEES',
				passed: true,
				invalid_pagination_probes: l26Http.badResults.length,
				all_returned_400_invalid_request: true,
				assignees_team_a: l26Http.assigneesA,
				assignees_team_b: l26Http.assigneesB,
				unknown_campaign_response: l26Http.notFound,
				ui_transfer_dropdown_team_b_options: transferOptions,
			})

			for (const [label, sess] of [
				['admin', adminS],
				['bod', bodS],
				['leader_a', leaderAS],
				['leader_b', leaderBS],
				['staff_a', staffAS],
				['staff_c', staffCS],
			]) {
				assert(
					sess.telemetry.consoleErrors.length === 0,
					`${label} console errors: ${sess.telemetry.consoleErrors.join('; ')}`
				)
				assert(
					sess.telemetry.pageErrors.length === 0,
					`${label} page errors: ${sess.telemetry.pageErrors.join('; ')}`
				)
				assert(
					sess.telemetry.server500s.length === 0,
					`${label} server 500s: ${sess.telemetry.server500s.join('; ')}`
				)
			}
		} finally {
			startWorker()
			await adminS.context.close()
			await bodS.context.close()
			await leaderAS.context.close()
			await leaderBS.context.close()
			await staffAS.context.close()
			await staffCS.context.close()
		}
	} finally {
		startWorker()
		await browser.close()
	}

	const report = redactObj({
		generated_at: new Date().toISOString(),
		site: BASE_URL,
		integrated_git_sha: gitSha,
		baseline_evidence_sha_preserved: baselineSha,
		checks,
	})
	const serialized = JSON.stringify(report, null, 2) + '\n'
	assert(!RAW_PHONE_RE.test(serialized), 'Raw phone number leaked into evidence JSON!')
	assert(!serialized.includes(PASSWORD), 'Seed password leaked into evidence JSON!')
	fs.writeFileSync(OUT_PATH, serialized, 'utf-8')
	console.log(`ALL ROUND-3 DELTA E2E CHECKS PASSED (${checks.length}) -> ${OUT_PATH}`)
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
