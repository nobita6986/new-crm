/* P2 API Contract Adapter (`window.cskhP2Api`).

   Aligned 100% with frozen `docs/api-contract-p2.md` (WP00-WP01, SHA `5cb1c1f`).

   Invariants:
     * Every write operation (`recordAttempt`, `scheduleFollowup`,
       `completeFollowup`, `closeWorkItem`, `reopenWorkItem`,
       `requestCorrection`, `reviewCorrection`, `saveOutcome`, `savePolicy`,
       `setLeadDnc`) executes ONE single POST call with CSRF and `idempotency_key`.
     * Never chains multiple write APIs on the client as a fake transaction.
*/

window.cskhP2Api = (function () {
	'use strict'

	const ENDPOINTS = {
		myCustomers: 'crm_cskh.api.customers.my_list',
		scopedCustomers: 'crm_cskh.api.ownership_ui.assigned_list',
		listWorkItems: 'crm_cskh.api.workflow.list_work_items',
		getWorkItem: 'crm_cskh.api.workflow.get_work_item',
		listAttempts: 'crm_cskh.api.workflow.list_attempts',
		listUnlinkedCallLogs: 'crm_cskh.api.workflow.list_unlinked_call_logs',
		getOutcomeCodes: 'crm_cskh.api.workflow.get_outcome_codes',
		recordAttempt: 'crm_cskh.api.workflow.record_attempt',
		scheduleFollowup: 'crm_cskh.api.workflow.schedule_followup',
		listFollowups: 'crm_cskh.api.workflow.list_followups',
		completeFollowup: 'crm_cskh.api.workflow.complete_followup',
		closeWorkItem: 'crm_cskh.api.workflow.close_work_item',
		reopenWorkItem: 'crm_cskh.api.workflow.reopen_work_item',
		requestCorrection: 'crm_cskh.api.workflow.request_correction',
		reviewCorrection: 'crm_cskh.api.workflow.review_correction',
		listCorrections: 'crm_cskh.api.workflow.list_corrections',
		progressSummary: 'crm_cskh.api.workflow.progress_summary',
		listPolicies: 'crm_cskh.api.settings.list_policies',
		savePolicy: 'crm_cskh.api.settings.save_policy',
		listOutcomes: 'crm_cskh.api.settings.list_outcomes',
		saveOutcome: 'crm_cskh.api.settings.save_outcome',
		setLeadDnc: 'crm_cskh.api.settings.set_lead_dnc',
		linkedActivities: 'crm.api.activities.get_activities',
	}

	function toBool(value, defaultValue) {
		if (value === undefined || value === null) return !!defaultValue
		if (typeof value === 'boolean') return value
		if (typeof value === 'number') return value !== 0
		const text = String(value).trim().toLowerCase()
		if (text === '1' || text === 'true' || text === 'yes') return true
		if (text === '0' || text === 'false' || text === 'no' || text === '') return false
		return !!defaultValue
	}

	function toArray(value) {
		if (Array.isArray(value)) return value
		if (!value) return []
		if (typeof value === 'string') {
			const trimmed = value.trim()
			if (!trimmed) return []
			if (trimmed.charAt(0) === '[') {
				try {
					const parsed = JSON.parse(trimmed)
					if (Array.isArray(parsed)) return parsed
				} catch (_err) {
					/* fall through */
				}
			}
			return trimmed
				.split(/[,\n]+/)
				.map(function (s) {
					return s.trim()
				})
				.filter(Boolean)
		}
		return []
	}

	function parseJsonObject(value) {
		if (!value) return {}
		if (typeof value === 'object') return value
		if (typeof value === 'string') {
			try {
				const parsed = JSON.parse(value)
				return parsed && typeof parsed === 'object' ? parsed : {}
			} catch (_err) {
				return {}
			}
		}
		return {}
	}

	function normalizeOutcome(raw) {
		if (!raw) return null
		const code = raw.code || raw.outcome_code || raw.name || ''
		const channels = toArray(raw.allowed_channels || raw.channels)
		const dnc = toBool(
			raw.is_do_not_contact !== undefined
				? raw.is_do_not_contact
				: raw.sets_do_not_contact !== undefined
					? raw.sets_do_not_contact
					: raw.do_not_contact,
			false
		)
		return {
			code: code,
			label: raw.label_vi || raw.title_vi || raw.label || raw.title || code,
			label_vi: raw.label_vi || raw.label || code,
			description: raw.description || '',
			result_group: raw.result_group || 'unreached',
			allowed_channels: channels.length
				? channels
				: ['phone_call', 'sms', 'zalo', 'facebook', 'other'],
			is_contact_attempt: toBool(raw.is_contact_attempt, true),
			is_connected: toBool(raw.is_connected, false),
			is_interested: toBool(raw.is_interested, false),
			is_terminal: toBool(raw.is_terminal, false),
			is_do_not_contact: dnc,
			sets_do_not_contact: dnc,
			requires_note: toBool(raw.requires_note, false),
			requires_followup: toBool(raw.requires_followup, false),
			allows_followup:
				raw.allows_followup === undefined
					? !toBool(raw.is_terminal, false)
					: toBool(raw.allows_followup, true),
			requires_completion_reason: toBool(raw.requires_completion_reason, false),
			suggested_next_status: raw.suggested_next_status || 'in_progress',
			version: Number(raw.version || 1),
			enabled: raw.enabled === undefined ? true : toBool(raw.enabled, true),
			sort_order: Number(raw.sort_order !== undefined ? raw.sort_order : 100),
		}
	}

	function normalizePolicy(raw) {
		if (!raw) return null
		const code = raw.policy_code || raw.name || ''
		const enabledChannels = toArray(raw.enabled_channels)
		return {
			policy_code: code,
			name: code,
			title: raw.title || code,
			scope_type: raw.scope_type || 'Global',
			team: raw.team || '',
			campaign: raw.campaign || '',
			enabled: raw.enabled === undefined ? true : toBool(raw.enabled, true),
			version: Number(raw.version || 1),
			allowed_backdate_minutes: Number(
				raw.allowed_backdate_minutes !== undefined ? raw.allowed_backdate_minutes : 120
			),
			max_open_followup_tasks_per_item: Number(
				raw.max_open_followup_tasks_per_item !== undefined
					? raw.max_open_followup_tasks_per_item
					: 1
			),
			require_attempt_before_close: toBool(
				raw.require_attempt_before_close !== undefined ? raw.require_attempt_before_close : true,
				true
			),
			allow_close_without_contact_for_invalid: toBool(
				raw.allow_close_without_contact_for_invalid !== undefined
					? raw.allow_close_without_contact_for_invalid
					: true,
				true
			),
			do_not_contact_behavior: raw.do_not_contact_behavior || 'block_and_auto_close',
			enabled_channels: enabledChannels.length
				? enabledChannels
				: ['phone_call', 'sms', 'zalo', 'facebook', 'other'],
			allow_leader_reopen: toBool(
				raw.allow_leader_reopen !== undefined ? raw.allow_leader_reopen : true,
				true
			),
			allow_leader_review_correction: toBool(
				raw.allow_leader_review_correction !== undefined
					? raw.allow_leader_review_correction
					: true,
				true
			),
		}
	}

	function normalizeWorkItemRow(raw) {
		if (!raw) return null
		const wiObj =
			raw.work_item && typeof raw.work_item === 'object' ? raw.work_item : raw
		const leadObj =
			raw.lead && typeof raw.lead === 'object' ? raw.lead : null
		const openFollowup = raw.open_followup || raw.open_callback || null
		const policy = normalizePolicy(raw.effective_policy || raw.policy || null)

		const wiId =
			(typeof raw.work_item === 'string' && raw.work_item) ||
			wiObj.work_item ||
			wiObj.work_item_id ||
			wiObj.name ||
			''
		const leadId = leadObj ? leadObj.name || '' : String(raw.lead || wiObj.lead || '')
		const displayName = leadObj
			? leadObj.display_name || ''
			: raw.display_name || wiObj.display_name || ''
		const phone = leadObj
			? leadObj.phone || null
			: raw.phone !== undefined
				? raw.phone
				: wiObj.phone || null
		const email = leadObj ? leadObj.email || '' : raw.email || ''
		const source = leadObj ? leadObj.source || '' : raw.source || wiObj.source || ''
		const wiStatus = wiObj.work_item_status || wiObj.status || 'assigned'
		const attemptCount = Number(
			wiObj.attempt_count !== undefined
				? wiObj.attempt_count
				: wiObj.effective_attempt_count !== undefined
					? wiObj.effective_attempt_count
					: 0
		)
		const connectedCount = Number(
			wiObj.connected_attempt_count !== undefined
				? wiObj.connected_attempt_count
				: wiObj.connected_count !== undefined
					? wiObj.connected_count
					: 0
		)
		const dnc = toBool(
			leadObj && leadObj.do_not_contact !== undefined
				? leadObj.do_not_contact
				: wiObj.do_not_contact !== undefined
					? wiObj.do_not_contact
					: raw.do_not_contact,
			false
		)
		const nextDue =
			wiObj.next_action_due ||
			wiObj.next_followup_at ||
			(openFollowup && (openFollowup.due_at || openFollowup.due_date)) ||
			null
		let followupState = wiObj.followup_state || ''
		if (!followupState) {
			if (wiObj.is_followup_overdue === true || (openFollowup && openFollowup.is_overdue === true)) {
				followupState = 'overdue'
			} else if (nextDue) {
				followupState = cskh.classifyDueAt(nextDue, 'Open')
			}
		}

		const lastAttempt = wiObj.last_attempt || {}
		const lastAttemptAt = wiObj.last_attempt_at || lastAttempt.occurred_at || null
		const lastOutcomeCode = wiObj.last_outcome_code || lastAttempt.outcome_code || null
		const lastOutcomeLabel =
			wiObj.last_outcome_label || lastAttempt.outcome_label || lastAttempt.outcome_title || null
		const lastChannel = wiObj.last_channel || lastAttempt.channel || null
		const hasConnected =
			wiObj.has_connected !== undefined
				? toBool(wiObj.has_connected, false)
				: connectedCount > 0 || Boolean(wiObj.last_connected_at)

		let derived = wiObj.derived_state || ''
		if (!derived) {
			if (dnc) derived = 'do_not_contact'
			else if (wiStatus === 'closed') derived = 'closed'
			else if (followupState === 'overdue') derived = 'followup_overdue'
			else if (followupState === 'due_today') derived = 'followup_due'
			else if (attemptCount === 0) derived = 'not_contacted'
			else if (hasConnected) derived = 'connected'
			else derived = 'attempted_not_connected'
		}

		const rawCaps = Object.assign({}, raw.capabilities || wiObj.capabilities || {})
		const assignedTo = wiObj.assigned_to || ''
		const isOwner = assignedTo ? assignedTo === cskh.currentUser() : false
		const globalCaps = cskh.capabilities() || {}
		const activeStatuses = { assigned: true, in_progress: true, on_hold: true }

		const caps = {
			can_record_attempt:
				rawCaps.can_record_attempt !== undefined
					? toBool(rawCaps.can_record_attempt, false)
					: !!(
							(isOwner || globalCaps.leader || globalCaps.admin) &&
							activeStatuses[wiStatus] &&
							!dnc
						),
			can_schedule_followup:
				rawCaps.can_schedule_followup !== undefined
					? toBool(rawCaps.can_schedule_followup, false)
					: !!(
							(isOwner || globalCaps.leader || globalCaps.admin) &&
							activeStatuses[wiStatus] &&
							!dnc
						),
			can_complete_followup:
				rawCaps.can_complete_followup !== undefined
					? toBool(rawCaps.can_complete_followup, false)
					: !!((isOwner || globalCaps.leader || globalCaps.admin) && activeStatuses[wiStatus]),
			can_close_work_item:
				rawCaps.can_close !== undefined
					? toBool(rawCaps.can_close, false)
					: rawCaps.can_close_work_item !== undefined
						? toBool(rawCaps.can_close_work_item, false)
						: !!((isOwner || globalCaps.leader || globalCaps.admin) && activeStatuses[wiStatus]),
			can_reopen_work_item:
				rawCaps.can_reopen !== undefined
					? toBool(rawCaps.can_reopen, false)
					: rawCaps.can_reopen_work_item !== undefined
						? toBool(rawCaps.can_reopen_work_item, false)
						: !!((globalCaps.admin || globalCaps.leader) && wiStatus === 'closed'),
			can_request_correction:
				rawCaps.can_request_correction !== undefined
					? toBool(rawCaps.can_request_correction, false)
					: !!(isOwner || globalCaps.leader || globalCaps.admin),
			can_review_correction:
				rawCaps.can_review_correction !== undefined
					? toBool(rawCaps.can_review_correction, false)
					: !!(globalCaps.admin || globalCaps.leader),
			can_reassign_or_revoke:
				rawCaps.can_reassign_or_revoke !== undefined
					? toBool(rawCaps.can_reassign_or_revoke, false)
					: !!globalCaps.admin,
		}

		return {
			raw: raw,
			work_item: wiId,
			work_item_id: wiId,
			lead: leadId,
			display_name: displayName,
			phone: phone,
			email: email,
			source: source,
			campaign: wiObj.campaign || '',
			campaign_title: wiObj.campaign_title || '',
			campaign_code: wiObj.campaign_code || '',
			campaign_status: wiObj.campaign_status || '',
			assigned_to: assignedTo,
			assigned_at: wiObj.assigned_at || null,
			first_attempt_at: wiObj.first_attempt_at || null,
			work_item_status: wiStatus,
			derived_state: derived,
			do_not_contact: dnc,
			dnc_reason: (leadObj && leadObj.dnc_reason) || wiObj.dnc_reason || '',
			dnc_at: (leadObj && leadObj.dnc_at) || wiObj.dnc_at || null,
			completion_reason: wiObj.completion_reason || wiObj.close_reason || '',
			closed_at: wiObj.closed_at || null,
			closed_by: wiObj.closed_by || '',
			reopened_at: wiObj.reopened_at || null,
			reopened_by: wiObj.reopened_by || '',
			reopen_reason: wiObj.reopen_reason || '',
			attempt_count: attemptCount,
			connected_count: connectedCount,
			has_connected: hasConnected,
			last_attempt_at: lastAttemptAt,
			last_connected_at: wiObj.last_connected_at || null,
			last_channel: lastChannel,
			last_outcome_code: lastOutcomeCode,
			last_outcome_label: lastOutcomeLabel,
			open_callback_task_id:
				wiObj.open_callback_task ||
				wiObj.open_callback_task_id ||
				(openFollowup && (openFollowup.task_id || openFollowup.name)) ||
				null,
			open_callback: openFollowup,
			next_action_due: nextDue,
			followup_state: followupState,
			revision: Number(wiObj.revision !== undefined ? wiObj.revision : 1),
			policy_version: Number(
				(policy && policy.version) ||
					(wiObj.policy_version !== undefined ? wiObj.policy_version : 1)
			),
			policy: policy,
			active_ownership: raw.active_ownership || null,
			capabilities: caps,
		}
	}

	function normalizeCorrection(c) {
		const fields = parseJsonObject(c.corrected_fields || c.corrected_fields_json)
		return {
			correction_id: c.correction_id || c.name || '',
			status: String(c.status || 'pending').toLowerCase(),
			action: String(c.action || c.correction_type || 'correct').toLowerCase(),
			corrected_fields: fields,
			proposed_outcome_code:
				fields.outcome_code || c.proposed_outcome_code || c.new_outcome_code || '',
			proposed_outcome_label:
				fields.outcome_label || c.proposed_outcome_label || c.new_outcome_label || '',
			proposed_channel: fields.channel || c.proposed_channel || '',
			proposed_occurred_at: fields.occurred_at || c.proposed_occurred_at || '',
			proposed_note: fields.note !== undefined ? fields.note : c.clarification_note || '',
			reason: c.reason || '',
			requested_by: c.requested_by || c.owner || '',
			requested_at: c.requested_at || c.creation || null,
			reviewed_by: c.reviewed_by || '',
			reviewed_at: c.reviewed_at || null,
			review_note: c.review_note || '',
			applied_revision: c.applied_revision,
		}
	}

	function normalizeAttempt(raw) {
		if (!raw) return null
		const eff = raw.effective && typeof raw.effective === 'object' ? raw.effective : raw
		const orig = raw.original && typeof raw.original === 'object' ? raw.original : null
		const corrections = (raw.corrections || []).map(normalizeCorrection)
		const isVoid = toBool(
			raw.is_voided !== undefined
				? raw.is_voided
				: raw.is_void !== undefined
					? raw.is_void
					: raw.voided,
			false
		)
		const vRaw = String(raw.verification_status || 'self_reported').toLowerCase()
		const verification = vRaw === 'verified' ? 'verified' : 'self_reported'

		return {
			kind: 'attempt',
			attempt_id: raw.attempt_id || raw.name || '',
			work_item_id: raw.work_item || raw.work_item_id || '',
			owner_at_attempt: raw.owner_at_attempt || '',
			performed_by: raw.performed_by || raw.actor || '',
			performed_by_name: raw.performed_by_name || raw.performed_by || '',
			occurred_at: eff.occurred_at || raw.occurred_at || raw.creation || null,
			recorded_at: raw.recorded_at || raw.creation || null,
			channel: eff.channel || raw.channel || 'phone_call',
			outcome_code: eff.outcome_code || raw.outcome_code || '',
			outcome_label: eff.outcome_label || raw.outcome_label || raw.outcome_label_snapshot || '',
			outcome_group: eff.outcome_group || raw.outcome_group_snapshot || '',
			is_contact_attempt: toBool(eff.is_contact_attempt, true),
			is_connected: toBool(eff.is_connected, false),
			is_interested: toBool(eff.is_interested, false),
			is_terminal: toBool(eff.is_terminal, false),
			is_do_not_contact: toBool(eff.is_do_not_contact, false),
			verification_status: verification,
			note: eff.note !== undefined ? eff.note || '' : raw.note || '',
			call_log_id: eff.call_log || raw.call_log || raw.call_log_id || '',
			callback_task_id: raw.callback_task || raw.callback_task_id || '',
			resolved_callback_task_id: raw.resolved_callback_task || '',
			policy_snapshot: raw.policy_snapshot || null,
			is_void: isVoid,
			has_correction:
				toBool(raw.has_approved_correction, false) ||
				toBool(raw.has_correction, false) ||
				corrections.some(function (c) {
					return c.status === 'approved'
				}),
			pending_correction_count: Number(
				raw.pending_correction_count !== undefined
					? raw.pending_correction_count
					: corrections.filter(function (c) {
							return c.status === 'pending'
						}).length
			),
			original_snapshot: orig,
			corrections: corrections,
		}
	}

	function normalizeCallLog(raw) {
		if (!raw) return null
		return {
			kind: 'call_log',
			call_log_id: raw.call_log_id || raw.id || raw.name || '',
			direction: raw.type || raw.direction || '',
			status: raw.status || '',
			duration: raw.duration,
			occurred_at: raw.start_time || raw.started_at || raw.creation || null,
			performed_by: raw.caller || raw.receiver || raw.owner || '',
			note: raw.note || raw.summary || '',
			linked_attempt_id: raw.linked_attempt_id || raw.attempt_id || '',
		}
	}

	function normalizeTask(raw) {
		if (!raw) return null
		const dueAt = raw.due_at || raw.due_date || null
		const status = raw.status || 'Todo'
		const wiId = raw.work_item || raw.work_item_id || raw.cskh_work_item || ''
		const isOverdue = raw.is_overdue === true
		let followupState = raw.followup_state || ''
		if (!followupState) {
			if (isOverdue && String(status).toLowerCase() !== 'done' && String(status).toLowerCase() !== 'canceled') {
				followupState = 'overdue'
			} else {
				followupState = cskh.classifyDueAt(dueAt, status)
			}
		}
		const globalCaps = cskh.capabilities() || {}
		const isAssignee = raw.assigned_to ? raw.assigned_to === cskh.currentUser() : false
		const isOpen = ['done', 'canceled', 'cancelled'].indexOf(String(status).toLowerCase()) < 0

		return {
			kind: 'task',
			task_id: String(raw.task_id || raw.name || ''),
			work_item_id: wiId,
			work_item: wiId,
			lead: raw.lead || raw.reference_docname || '',
			display_name: raw.display_name || '',
			phone: raw.phone || null,
			campaign: raw.campaign || '',
			campaign_title: raw.campaign_title || '',
			title: raw.title || '',
			description: raw.description || raw.note || '',
			due_at: dueAt,
			occurred_at: raw.modified || raw.creation || dueAt,
			status: status,
			is_cskh_followup:
				raw.cskh_is_followup !== undefined ? toBool(raw.cskh_is_followup, true) : Boolean(wiId),
			followup_state: followupState,
			assigned_to: raw.assigned_to || '',
			source_attempt: raw.source_attempt || raw.cskh_source_attempt || '',
			linked_attempt_id:
				raw.completed_attempt ||
				raw.cskh_completed_attempt ||
				raw.linked_attempt_id ||
				raw.attempt_id ||
				'',
			close_reason:
				raw.completion_reason ||
				raw.cskh_completion_reason ||
				raw.close_reason ||
				raw.cancel_reason ||
				raw.reason ||
				'',
			reschedule_count: Number(
				raw.reschedule_count !== undefined
					? raw.reschedule_count
					: raw.cskh_reschedule_count || 0
			),
			work_item_revision: Number(
				raw.work_item_revision !== undefined
					? raw.work_item_revision
					: raw.revision !== undefined
						? raw.revision
						: 1
			),
			can_complete:
				raw.can_complete !== undefined
					? toBool(raw.can_complete, false)
					: !!(isOpen && (isAssignee || globalCaps.leader || globalCaps.admin)),
			can_reschedule:
				raw.can_reschedule !== undefined
					? toBool(raw.can_reschedule, false)
					: !!(isOpen && (isAssignee || globalCaps.leader || globalCaps.admin)),
		}
	}

	function normalizeNote(raw) {
		if (!raw) return null
		return {
			kind: 'note',
			note_id: raw.note_id || raw.name || '',
			title: raw.title || '',
			content: raw.content || raw.note || '',
			created_by: raw.created_by || raw.owner || '',
			occurred_at: raw.created_at || raw.modified || raw.creation || null,
		}
	}

	function normalizeMilestone(raw) {
		if (!raw) return null
		return {
			kind: 'milestone',
			event_id: raw.event_id || raw.name || '',
			action: raw.action || '',
			from_user: raw.from_user || '',
			to_user: raw.to_user || '',
			actor: raw.actor || '',
			occurred_at: raw.event_at || raw.ownership_start || null,
			reason: raw.reason || '',
		}
	}

	/* ------------------------------------------------------------------ */
	/* Read operations (GET)                                              */
	/* ------------------------------------------------------------------ */

	async function listMyCustomers(params) {
		const data = await cskh.get(ENDPOINTS.myCustomers, params || {})
		const items = ((data && data.items) || []).map(normalizeWorkItemRow).filter(Boolean)
		return Object.assign({}, data || {}, { items: items })
	}

	async function listScopedCustomers(params) {
		const data = await cskh.get(ENDPOINTS.scopedCustomers, params || {})
		const items = ((data && data.items) || []).map(normalizeWorkItemRow).filter(Boolean)
		return Object.assign({}, data || {}, { items: items })
	}

	async function listWorkItems(params) {
		const data = await cskh.get(ENDPOINTS.listWorkItems, params || {})
		const items = ((data && data.items) || []).map(normalizeWorkItemRow).filter(Boolean)
		return Object.assign({}, data || {}, { items: items })
	}

	async function getWorkItem(params) {
		let workItemId = (params && (params.work_item || params.work_item_id)) || ''
		const leadId = (params && params.lead) || ''

		if (!workItemId && leadId) {
			const res = await listWorkItems({
				search: leadId,
				status: 'all',
				page: 1,
				page_size: 20,
			})
			const exact = (res.items || []).filter(function (row) {
				return row.lead === leadId
			})
			const active = exact.find(function (row) {
				return (
					row.work_item_status === 'assigned' ||
					row.work_item_status === 'in_progress' ||
					row.work_item_status === 'on_hold'
				)
			})
			const chosen = active || exact[0] || null
			if (!chosen || !chosen.work_item) {
				const err = new Error(cskh.errorText('NOT_FOUND') || 'NOT_FOUND')
				err.status = 404
				err.code = 'NOT_FOUND'
				throw err
			}
			workItemId = chosen.work_item
		}

		const data = await cskh.get(ENDPOINTS.getWorkItem, { work_item: workItemId })
		return normalizeWorkItemRow(data)
	}

	async function listTimeline(params) {
		const workItemId = params.work_item || params.work_item_id
		const leadId = params.lead || ''
		const page = params.page || 1
		const pageSize = params.page_size || 20

		const attemptsPromise = cskh.get(ENDPOINTS.listAttempts, {
			work_item: workItemId,
			page: page,
			page_size: pageSize,
			include_milestones: 1,
		})

		const followupsPromise = cskh
			.get(ENDPOINTS.listFollowups, {
				work_item: workItemId,
				status: 'all',
				page: 1,
				page_size: 50,
			})
			.catch(function () {
				return { items: [] }
			})

		const unlinkedCallsPromise = cskh
			.get(ENDPOINTS.listUnlinkedCallLogs, {
				work_item: workItemId,
			})
			.catch(function () {
				return { items: [] }
			})

		const linkedActivitiesPromise = leadId
			? cskh.get(ENDPOINTS.linkedActivities, { name: leadId }).catch(function () {
					return { calls: [], notes: [], tasks: [] }
				})
			: Promise.resolve({ calls: [], notes: [], tasks: [] })

		const results = await Promise.all([
			attemptsPromise,
			followupsPromise,
			unlinkedCallsPromise,
			linkedActivitiesPromise,
		])

		const attemptsData = results[0] || {}
		const followupsData = results[1] || {}
		const unlinkedData = results[2] || {}
		const rawAct = results[3] || {}
		const linkedAct = Array.isArray(rawAct)
			? {
					calls: rawAct[1] || [],
					notes: rawAct[2] || [],
					tasks: rawAct[3] || [],
				}
			: rawAct

		const attempts = ((attemptsData && (attemptsData.items || attemptsData.attempts)) || [])
			.map(normalizeAttempt)
			.filter(Boolean)

		const milestones = ((attemptsData && attemptsData.ownership_milestones) || [])
			.map(normalizeMilestone)
			.filter(Boolean)

		// Map linked call_log_id -> attempt_id
		const linkedByCallLog = {}
		attempts.forEach(function (att) {
			if (att.call_log_id) {
				linkedByCallLog[att.call_log_id] = att.attempt_id
			}
		})

		// Merge call logs from guarded CRM activities + workflow unlinked call logs
		const callMap = {}
		;((linkedAct && linkedAct.calls) || []).forEach(function (rawCall) {
			const cl = normalizeCallLog(rawCall)
			if (cl && cl.call_log_id) {
				if (linkedByCallLog[cl.call_log_id]) {
					cl.linked_attempt_id = linkedByCallLog[cl.call_log_id]
				}
				callMap[cl.call_log_id] = cl
			}
		})
		;((unlinkedData && (unlinkedData.items || unlinkedData.call_logs)) || []).forEach(function (
			rawCall
		) {
			const cl = normalizeCallLog(rawCall)
			if (cl && cl.call_log_id && !callMap[cl.call_log_id]) {
				if (linkedByCallLog[cl.call_log_id]) {
					cl.linked_attempt_id = linkedByCallLog[cl.call_log_id]
				}
				callMap[cl.call_log_id] = cl
			}
		})
		const callLogs = Object.keys(callMap).map(function (k) {
			return callMap[k]
		})

		// Merge follow-up tasks + general CRM tasks on Lead (deduped by task_id)
		const taskMap = {}
		;((followupsData && followupsData.items) || []).forEach(function (rawTask) {
			const t = normalizeTask(rawTask)
			if (t && t.task_id) taskMap[t.task_id] = t
		})
		;((linkedAct && linkedAct.tasks) || []).forEach(function (rawTask) {
			const t = normalizeTask(rawTask)
			if (t && t.task_id && !taskMap[t.task_id]) {
				taskMap[t.task_id] = t
			}
		})
		const tasks = Object.keys(taskMap).map(function (k) {
			return taskMap[k]
		})

		const notes = ((linkedAct && linkedAct.notes) || []).map(normalizeNote).filter(Boolean)

		return {
			total: Number(attemptsData.total !== undefined ? attemptsData.total : attempts.length),
			page: Number(attemptsData.page || page),
			page_size: Number(attemptsData.page_size || pageSize),
			attempts: attempts,
			call_logs: callLogs,
			tasks: tasks,
			notes: notes,
			milestones: milestones,
		}
	}

	async function getOutcomeCodes(params) {
		const data = await cskh.get(ENDPOINTS.getOutcomeCodes, params || {})
		const rawList = Array.isArray(data)
			? data
			: (data && (data.items || data.outcomes)) || []
		const policy = data && data.effective_policy ? normalizePolicy(data.effective_policy) : null
		return {
			items: rawList.map(normalizeOutcome).filter(Boolean),
			effective_policy: policy,
			policy_version: policy ? policy.version : null,
			can_manage: !!(cskh.capabilities() && cskh.capabilities().can_manage_settings),
		}
	}

	async function listFollowups(params) {
		const data = await cskh.get(ENDPOINTS.listFollowups, params || {})
		const items = ((data && data.items) || []).map(normalizeTask).filter(Boolean)
		return {
			scope: (data && data.scope) || 'self',
			total: Number((data && data.total) || 0),
			page: Number((data && data.page) || 1),
			page_size: Number((data && data.page_size) || 20),
			as_of: data && data.as_of,
			items: items,
		}
	}

	async function getProgressSummary(params) {
		return cskh.get(ENDPOINTS.progressSummary, params || {})
	}

	async function getSettingsSnapshot() {
		const caps = cskh.capabilities() || {}
		const canManage = !!caps.can_manage_settings
		const includeDisabled = caps.admin || caps.bod ? 1 : 0

		const results = await Promise.all([
			cskh.get(ENDPOINTS.listOutcomes, { include_disabled: includeDisabled }),
			cskh.get(ENDPOINTS.listPolicies, {}),
		])
		const outData = results[0] || {}
		const polData = results[1] || {}
		const rawOutcomes = Array.isArray(outData)
			? outData
			: outData.items || outData.outcomes || []
		const rawPolicies = Array.isArray(polData)
			? polData
			: polData.items || polData.policies || []

		return {
			outcomes: rawOutcomes.map(normalizeOutcome).filter(Boolean),
			policies: rawPolicies.map(normalizePolicy).filter(Boolean),
			can_manage: outData.can_manage !== undefined ? !!outData.can_manage : canManage,
		}
	}

	/* ------------------------------------------------------------------ */
	/* Atomic Write Operations (Single POST per user action + CSRF)       */
	/* ------------------------------------------------------------------ */

	function recordAttempt(payload) {
		return cskh.post(ENDPOINTS.recordAttempt, payload)
	}

	function scheduleFollowup(payload) {
		return cskh.post(ENDPOINTS.scheduleFollowup, payload)
	}

	function completeFollowup(payload) {
		return cskh.post(ENDPOINTS.completeFollowup, payload)
	}

	function closeWorkItem(payload) {
		return cskh.post(ENDPOINTS.closeWorkItem, payload)
	}

	function reopenWorkItem(payload) {
		return cskh.post(ENDPOINTS.reopenWorkItem, payload)
	}

	function requestCorrection(payload) {
		return cskh.post(ENDPOINTS.requestCorrection, payload)
	}

	function reviewCorrection(payload) {
		return cskh.post(ENDPOINTS.reviewCorrection, payload)
	}

	function saveOutcome(payload) {
		return cskh.post(ENDPOINTS.saveOutcome, payload)
	}

	function savePolicy(payload) {
		return cskh.post(ENDPOINTS.savePolicy, payload)
	}

	function setLeadDnc(payload) {
		return cskh.post(ENDPOINTS.setLeadDnc, payload)
	}

	return {
		ENDPOINTS: ENDPOINTS,
		normalizeOutcome: normalizeOutcome,
		normalizePolicy: normalizePolicy,
		normalizeWorkItemRow: normalizeWorkItemRow,
		normalizeAttempt: normalizeAttempt,
		normalizeCallLog: normalizeCallLog,
		normalizeTask: normalizeTask,
		normalizeNote: normalizeNote,
		listMyCustomers: listMyCustomers,
		listScopedCustomers: listScopedCustomers,
		listWorkItems: listWorkItems,
		getWorkItem: getWorkItem,
		listTimeline: listTimeline,
		getOutcomeCodes: getOutcomeCodes,
		listFollowups: listFollowups,
		getProgressSummary: getProgressSummary,
		getSettingsSnapshot: getSettingsSnapshot,
		recordAttempt: recordAttempt,
		scheduleFollowup: scheduleFollowup,
		completeFollowup: completeFollowup,
		closeWorkItem: closeWorkItem,
		reopenWorkItem: reopenWorkItem,
		requestCorrection: requestCorrection,
		reviewCorrection: reviewCorrection,
		saveOutcome: saveOutcome,
		savePolicy: savePolicy,
		setLeadDnc: setLeadDnc,
	}
})()
