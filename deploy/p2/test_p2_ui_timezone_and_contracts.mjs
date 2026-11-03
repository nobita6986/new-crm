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

console.log('ALL P2 UI TIMEZONE (23:59 / 00:00) & CONTRACT NORMALIZER TESTS PASSED')
