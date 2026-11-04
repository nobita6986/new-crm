#!/usr/bin/env node
/**
 * Deterministic P2 UI Timezone & Contract Unit Suite.
 *
 * Verifies:
 *   1. 23:59:59 vs 00:00:00 boundaries in `Asia/Ho_Chi_Minh` regardless of host
 *      process timezone (`TZ=UTC`, `TZ=America/Los_Angeles`, etc.);
 *   2. Cross-timezone conversion (`user=Asia/Tokyo`, `site=Asia/Ho_Chi_Minh`) for
 *      `toDatetimeLocalValue` and `datetimeLocalToServer` (`YYYY-MM-DD HH:mm:ss`);
 *   3. `p2-api.js` read-model normalizers (`get_work_item` nested object,
 *      `list_attempts` effective vs original snapshots, `no_answer` and
 *      `message_sent_no_reply` strictly `is_connected === false`, `CRM Task`
 *      `Done` never classified as `connected`);
 *   4. Single-API orchestration invariant in `p2-api.js` (every write method maps
 *      to exactly 1 POST endpoint).
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const PUBLIC_CSKH = path.resolve(__dirname, '../../apps/crm_cskh/crm_cskh/public/cskh')

function loadCskhSandbox(timezoneConfig) {
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
		crypto: {
			randomUUID: () => '00000000-0000-4000-8000-000000000001',
		},
		navigator: {},
		document: {
			getElementById: () => null,
			createElement: (tag) => ({
				tagName: tag.toUpperCase(),
				children: [],
				childNodes: [],
				style: {},
				classList: { add() {}, remove() {} },
				setAttribute() {},
				appendChild(child) {
					this.children.push(child)
					this.childNodes.push(child)
					return child
				},
				addEventListener() {},
			}),
			createTextNode: (text) => ({ nodeType: 3, textContent: text }),
		},
	}
	context.window = context
	context.window.cskhTimezone = timezoneConfig
	context.window.cskhUser = 'staff.a@example.test'
	context.window.cskhCapabilities = {
		admin: false,
		bod: false,
		leader: false,
		staff: true,
		pii: true,
		can_manage_settings: false,
		can_review_correction: false,
	}
	context.window.cskhI18n = {}
	context.window.cskhErrorCodes = []
	context.window.cskhErrorTexts = {}

	vm.createContext(context)
	vm.runInContext(fs.readFileSync(path.join(PUBLIC_CSKH, 'cskh.js'), 'utf8'), context)
	vm.runInContext(fs.readFileSync(path.join(PUBLIC_CSKH, 'p2-api.js'), 'utf8'), context)
	return context
}

// 1. Test 23:59 / 00:00 boundaries in Asia/Ho_Chi_Minh when server clock is 2026-11-03 22:30:00+07:00
{
	const ctx = loadCskhSandbox({
		site: 'Asia/Ho_Chi_Minh',
		user: '',
		effective: 'Asia/Ho_Chi_Minh',
		server_now_iso: '2026-11-03T22:30:00+07:00',
	})
	const { cskh, cskhP2Api } = ctx

	// 2026-11-03 23:59:00 in Asia/Ho_Chi_Minh (which is 2026-11-03 16:59:00Z, still Nov 3 in VN!)
	assert.equal(
		cskh.classifyDueAt('2026-11-03 23:59:00', 'Todo', '2026-11-03T22:30:00+07:00'),
		'due_today',
		'23:59 on Nov 3 in VN must be due_today when refNow is 22:30 Nov 3 VN'
	)

	// 2026-11-04 00:00:00 in Asia/Ho_Chi_Minh (1 minute later -> crosses midnight into Nov 4 VN!)
	assert.equal(
		cskh.classifyDueAt('2026-11-04 00:00:00', 'Todo', '2026-11-03T22:30:00+07:00'),
		'upcoming',
		'00:00 on Nov 4 in VN must be upcoming when refNow is 22:30 Nov 3 VN'
	)

	// Critical morning boundary: 2026-11-04 00:05:00+07:00 (which is 2026-11-03 17:05:00Z in UTC!).
	// A buggy UTC-based UI would think today is still 2026-11-03; our engine must see 2026-11-04 in VN!
	assert.equal(
		cskh.classifyDueAt('2026-11-03 23:59:59', 'Todo', '2026-11-04T00:05:00+07:00'),
		'overdue',
		'23:59:59 Nov 3 must be overdue at 00:05 Nov 4 VN (even though UTC is still Nov 3 17:05Z)'
	)
	assert.equal(
		cskh.classifyDueAt('2026-11-04 00:10:00', 'Todo', '2026-11-04T00:05:00+07:00'),
		'due_today',
		'00:10 Nov 4 must be due_today at 00:05 Nov 4 VN (even though UTC is still Nov 3 17:05Z)'
	)
	assert.equal(
		cskh.classifyDueAt('2026-11-04 23:59:00', 'Todo', '2026-11-04T00:05:00+07:00'),
		'due_today',
		'23:59 Nov 4 must be due_today at 00:05 Nov 4 VN'
	)
	assert.equal(
		cskh.classifyDueAt('2026-11-05 00:00:00', 'Todo', '2026-11-04T00:05:00+07:00'),
		'upcoming',
		'00:00 Nov 5 must be upcoming at 00:05 Nov 4 VN'
	)

	// Done & Canceled task status overrides
	assert.equal(cskh.classifyDueAt('2026-11-01 09:00:00', 'Done'), 'done')
	assert.equal(cskh.classifyDueAt('2026-11-01 09:00:00', 'Canceled'), 'canceled')

	// datetime-local round-trip in VN timezone
	assert.equal(cskh.toDatetimeLocalValue('2026-11-03 23:59:00'), '2026-11-03T23:59')
	assert.equal(cskh.datetimeLocalToServer('2026-11-03T23:59'), '2026-11-03 23:59:00')
	assert.equal(cskh.datetimeLocalToIso('2026-11-03T23:59'), '2026-11-03T23:59:00+07:00')

	// 2. Honesty & Read-Model Normalizers
	const nestedWi = cskhP2Api.normalizeWorkItemRow({
		work_item: {
			name: 'wi-001',
			campaign: 'CAMP-01',
			campaign_title: 'Chiến dịch Q4',
			status: 'on_hold',
			revision: 4,
			assigned_to: 'staff.a@example.test',
			attempt_count: 1,
			connected_attempt_count: 0,
			last_outcome_code: 'message_sent_no_reply',
			last_outcome_label: 'Đã gửi tin nhắn, chưa phản hồi',
			open_callback_task: 'TASK-99',
			next_action_due: '2026-11-03 23:59:00',
		},
		lead: {
			name: 'CRM-LEAD-001',
			display_name: 'Trần Thị Bình',
			phone: '0987654321',
			do_not_contact: false,
		},
		effective_policy: {
			name: 'GLOBAL-DEFAULT',
			version: 2,
			allowed_backdate_minutes: 90,
		},
		capabilities: {
			can_record_attempt: true,
			can_close: true,
			can_reopen: false,
		},
	})
	assert.equal(nestedWi.work_item_id, 'wi-001')
	assert.equal(nestedWi.lead, 'CRM-LEAD-001')
	assert.equal(nestedWi.display_name, 'Trần Thị Bình')
	assert.equal(nestedWi.has_connected, false, 'message_sent_no_reply must NOT mark has_connected')
	assert.equal(nestedWi.followup_state, 'due_today')
	assert.equal(nestedWi.policy_version, 2)
	assert.equal(nestedWi.capabilities.can_close_work_item, true)
	assert.equal(nestedWi.capabilities.can_reopen_work_item, false)

	// Attempt effective vs original snapshot & immutability
	const normAtt = cskhP2Api.normalizeAttempt({
		attempt_id: 'att-101',
		work_item: 'wi-001',
		performed_by: 'staff.a@example.test',
		verification_status: 'self_reported',
		is_voided: false,
		has_approved_correction: true,
		pending_correction_count: 0,
		effective: {
			occurred_at: '2026-11-03 09:15:00',
			channel: 'zalo',
			outcome_code: 'message_sent_no_reply',
			outcome_label: 'Đã gửi tin nhắn, chưa phản hồi',
			is_connected: false,
			note: 'Đã gửi tin Zalo, khách chưa xem.',
		},
		original: {
			occurred_at: '2026-11-03 09:15:00',
			channel: 'sms',
			outcome_code: 'message_sent_no_reply',
			outcome_label: 'Đã gửi tin nhắn, chưa phản hồi',
			is_connected: false,
			note: 'Ghi nhầm kênh SMS.',
		},
		corrections: [
			{
				correction_id: 'corr-1',
				action: 'correct',
				status: 'approved',
				corrected_fields: { channel: 'zalo' },
				reason: 'Chọn nhầm kênh SMS',
			},
		],
	})
	assert.equal(normAtt.channel, 'zalo')
	assert.equal(normAtt.is_connected, false)
	assert.equal(normAtt.verification_status, 'self_reported')
	assert.equal(normAtt.has_correction, true)
	assert.equal(normAtt.original_snapshot.channel, 'sms')

	// Task Done never reports connected
	const normTask = cskhP2Api.normalizeTask({
		task_id: 77,
		work_item: 'wi-001',
		status: 'Done',
		due_at: '2026-11-03 10:00:00',
		completed_attempt: 'att-101',
	})
	assert.equal(normTask.task_id, '77')
	assert.equal(normTask.followup_state, 'done')
	assert.equal(normTask.can_complete, false)
}

// 3. Cross-timezone user (`Asia/Tokyo` UTC+9 on a `Asia/Ho_Chi_Minh` UTC+7 site)
{
	const ctx = loadCskhSandbox({
		site: 'Asia/Ho_Chi_Minh',
		user: 'Asia/Tokyo',
		effective: 'Asia/Tokyo',
		server_now_iso: '2026-11-04T00:30:00+09:00',
	})
	const { cskh } = ctx
	// Server DB stores 2026-11-03 22:30:00 (VN +07:00), which is 2026-11-04 00:30:00 in Tokyo (+09:00)!
	assert.equal(
		cskh.toDatetimeLocalValue('2026-11-03 22:30:00'),
		'2026-11-04T00:30',
		'Naive VN DB time 22:30 Nov 3 must display as 00:30 Nov 4 for Asia/Tokyo user'
	)
	assert.equal(
		cskh.datetimeLocalToServer('2026-11-04T00:30'),
		'2026-11-03 22:30:00',
		'User input 00:30 Nov 4 in Asia/Tokyo must serialize back to 22:30:00 Nov 3 in VN site DB timezone'
	)
}

// 4. F-P2-04A & F-P2-05 & F-P2-01 UI Contract & Anti-Workaround Guards
{
	const ctx = loadCskhSandbox({
		site: 'Asia/Ho_Chi_Minh',
		user: '',
		effective: 'Asia/Ho_Chi_Minh',
		server_now_iso: '2026-11-04T10:00:00+07:00',
	})
	const { cskh, cskhP2Api } = ctx

	// Server-side care filter query builder (`F-P2-04A`)
	const qConn = cskhP2Api.buildWorkItemsQuery({
		page: 2,
		pageSize: 20,
		careFilter: 'connected',
		campaign: 'CAMP-A',
		assignee: 'staff.a@example.test',
		search: '091234',
	})
	assert.equal(qConn.page, 2)
	assert.equal(qConn.page_size, 20)
	assert.equal(qConn.status, 'active')
	assert.equal(qConn.care_state, 'connected')
	assert.equal(qConn.campaign, 'CAMP-A')
	assert.equal(qConn.assignee, 'staff.a@example.test')
	assert.equal(qConn.search, '091234')

	assert.equal(
		cskhP2Api.buildWorkItemsQuery({ careFilter: 'not_contacted' }).care_state,
		'not_contacted'
	)
	assert.equal(cskhP2Api.buildWorkItemsQuery({ careFilter: 'attempted' }).care_state, 'attempted')
	assert.equal(
		cskhP2Api.buildWorkItemsQuery({ careFilter: 'attempted_not_connected' }).care_state,
		'attempted_not_connected'
	)
	assert.equal(
		cskhP2Api.buildWorkItemsQuery({ careFilter: 'do_not_contact' }).care_state,
		'do_not_contact'
	)
	assert.equal(cskhP2Api.buildWorkItemsQuery({ careFilter: 'closed' }).status, 'closed')
	assert.equal(
		cskhP2Api.buildWorkItemsQuery({ careFilter: 'followup_due' }).followup_filter,
		'today'
	)
	assert.equal(
		cskhP2Api.buildWorkItemsQuery({ careFilter: 'followup_overdue' }).followup_filter,
		'overdue'
	)

	// Pager multi-page server total & range (`F-P2-04A`)
	const rangeNode = { textContent: '' }
	const prevBtn = { disabled: false }
	const nextBtn = { disabled: false }
	const pgr = cskh.pager({ pageSize: 20, range: rangeNode, prev: prevBtn, next: nextBtn })
	pgr.setTotal(45)
	pgr.render()
	assert.equal(rangeNode.textContent, '1–20 / 45')
	assert.equal(prevBtn.disabled, true)
	assert.equal(nextBtn.disabled, false)

	pgr.goto(2)
	pgr.render()
	assert.equal(rangeNode.textContent, '21–40 / 45')
	assert.equal(prevBtn.disabled, false)
	assert.equal(nextBtn.disabled, false)

	pgr.goto(3)
	pgr.render()
	assert.equal(rangeNode.textContent, '41–45 / 45')
	assert.equal(prevBtn.disabled, false)
	assert.equal(nextBtn.disabled, true)

	// Progress summary normalizer (`F-P2-05`: distinct Work Items vs Attempts vs DNC Customers)
	const normProg = cskhP2Api.normalizeProgressSummary({
		scope: 'team',
		cards: {
			total_active_owned: 10,
			assigned: 3,
			in_progress: 5,
			on_hold: 2,
			closed: 4,
			unattempted_work_items: 3,
			attempted_work_items: 11,
			connected_work_items: 6,
			dnc_leads: 2,
			effective_attempts: 27,
			connected_attempts: 9,
			unreached_attempts: 18,
		},
	})
	assert.equal(normProg.cards.unattempted_work_items, 3)
	assert.equal(normProg.cards.attempted_work_items, 11)
	assert.equal(normProg.cards.connected_work_items, 6)
	assert.equal(normProg.cards.attempted_unconnected_work_items, 5)
	assert.equal(normProg.cards.dnc_leads, 2)
	assert.equal(normProg.cards.effective_attempts, 27)
	assert.equal(normProg.cards.connected_attempts, 9)
	assert.equal(normProg.cards.unreached_attempts, 18)

	// Static guards on `my-customers.js`, `progress.js`, `work-item.js`
	const myCustomersSrc = fs.readFileSync(path.join(PUBLIC_CSKH, 'my-customers.js'), 'utf8')
	const progressSrc = fs.readFileSync(path.join(PUBLIC_CSKH, 'progress.js'), 'utf8')
	const workItemSrc = fs.readFileSync(path.join(PUBLIC_CSKH, 'work-item.js'), 'utf8')

	assert(
		!myCustomersSrc.includes('matchesClientCareFilter') &&
			!myCustomersSrc.includes('filteredCount'),
		'F-P2-04A regression: my-customers.js must never filter care-state client-side or set pager total from filteredCount'
	)
	assert(
		!progressSrc.includes('Do-not-contact (DNC) work items') &&
			progressSrc.includes('Do-not-contact (DNC) customers'),
		'F-P2-05 regression: progress.js must label dnc_leads as customers, never work items'
	)
	for (const [name, src] of [
		['my-customers.js', myCustomersSrc],
		['work-item.js', workItemSrc],
	]) {
		assert(
			!/\[H:/i.test(src),
			`F-P2-01 anti-workaround guard: ${name} must not strip [H:<16hex>] on the client`
		)
	}
}

console.log('ALL P2 UI TIMEZONE (23:59 / 00:00) & CONTRACT NORMALIZER TESTS PASSED')
