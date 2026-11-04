/* Customer Care Workspace (`/cskh/work-item`, P2 WP07–WP08).

   Responsibilities:
     * Loads `get_work_item`, `list_attempts` (+ native Call Logs, Tasks, Notes),
       and `get_outcome_codes` for `?id=<work_item>` or `?lead=<lead_id>`.
     * Clears ALL workspace DOM immediately on `FORBIDDEN` / `NOT_FOUND` /
       `OWNERSHIP_CHANGED` so URL replay after `A -> B` transfer leaks 0 PII.
     * Exposes atomic single-API write actions gated by server `capabilities`:
       - Quick Record Attempt (`workflow.record_attempt`)
       - Schedule / Reschedule Callback (`workflow.schedule_followup`)
       - Cancel Callback with audit reason (`workflow.complete_followup`)
       - Close Work Item (`workflow.close_work_item`)
       - Reopen Work Item (`workflow.reopen_work_item`)
       - Request Append-Only Correction / Void / Restore (`workflow.request_correction`)
       - Review Pending Correction (`workflow.review_correction`)
     * Renders Unified Timeline with strict honesty invariants:
       - Zero direct Edit or Delete controls on `CSKH Contact Attempt`
       - `Self-reported` vs `Verified` badge on every Attempt
       - `message_sent_no_reply` and `no_answer` never shown as `Connected`
       - `CRM Task` (`Done` or `Open`) and `FCRM Note` explicitly marked as
         excluded from attempt counts and never shown as `Connected`.
*/
(function () {
	'use strict'

	const PAGE_SIZE = 20
	const caps = cskh.capabilities() || {}
	const allowPii = !!caps.pii

	const pager = cskh.pager({
		pageSize: PAGE_SIZE,
		range: document.getElementById('wi-range'),
		prev: document.getElementById('wi-prev'),
		next: document.getElementById('wi-next'),
	})
	const reads = cskh.sequencer()

	let currentWi = null
	let currentOutcomes = []
	let currentTimeline = {
		attempts: [],
		call_logs: [],
		tasks: [],
		notes: [],
		milestones: [],
	}
	let timelineFilter = 'all'

	function queryParams() {
		const sp = new URLSearchParams(window.location.search || '')
		return {
			work_item: (sp.get('id') || sp.get('work_item') || '').trim(),
			lead: (sp.get('lead') || '').trim(),
		}
	}

	function wipeWorkspaceDom() {
		currentWi = null
		currentTimeline = { attempts: [], call_logs: [], tasks: [], notes: [], milestones: [] }
		const summaryCard = document.getElementById('wi-summary-card')
		const drawerCard = document.getElementById('wi-action-drawer')
		const timelineCard = document.getElementById('wi-timeline-card')
		if (summaryCard) {
			summaryCard.hidden = true
			cskh.clear(document.getElementById('wi-summary-header'))
			cskh.clear(document.getElementById('wi-summary-metrics'))
			cskh.clear(document.getElementById('wi-summary-actions'))
		}
		if (drawerCard) {
			drawerCard.hidden = true
			cskh.clear(document.getElementById('wi-action-drawer-body'))
		}
		if (timelineCard) {
			timelineCard.hidden = true
			cskh.clear(document.getElementById('wi-timeline-list'))
		}
	}

	function metric(label, valueNodeOrText) {
		const valEl =
			valueNodeOrText && typeof valueNodeOrText === 'object' && valueNodeOrText.nodeType
				? cskh.el('div', { class: 'value', style: 'font-size: 16px;' }, [valueNodeOrText])
				: cskh.el('div', {
						class: 'value',
						style: 'font-size: 16px;',
						text:
							valueNodeOrText === undefined || valueNodeOrText === null || valueNodeOrText === ''
								? '—'
								: String(valueNodeOrText),
					})
		return cskh.el('div', { class: 'cskh-metric' }, [
			cskh.el('div', { class: 'label', text: label }),
			valEl,
		])
	}

	/* ------------------------------------------------------------------ */
	/* Header Summary & Action Bar                                        */
	/* ------------------------------------------------------------------ */

	function renderSummary() {
		const card = document.getElementById('wi-summary-card')
		const header = document.getElementById('wi-summary-header')
		const metrics = document.getElementById('wi-summary-metrics')
		const actions = document.getElementById('wi-summary-actions')
		cskh.clear(header)
		cskh.clear(metrics)
		cskh.clear(actions)
		if (!currentWi) {
			card.hidden = true
			return
		}

		const wi = currentWi
		const wiCaps = wi.capabilities || {}

		const badges = [
			cskh.el('span', {
				class: cskh.stateBadge(wi.work_item_status),
				text: cskh.workItemStateLabel(wi.work_item_status),
			}),
		]
		if (wi.do_not_contact) {
			badges.push(
				cskh.el('span', {
					class: cskh.stateBadge('dnc'),
					text: cskh.t('Do not contact'),
				})
			)
		}
		if (wi.followup_state && wi.next_action_due) {
			badges.push(
				cskh.el('span', {
					class: cskh.stateBadge(wi.followup_state),
					text: cskh.dueStateLabel(wi.followup_state),
				})
			)
		}

		header.appendChild(
			cskh.el(
				'div',
				{
					class: 'cskh-actions',
					style: 'justify-content: space-between; align-items: flex-start;',
				},
				[
					cskh.el('div', {}, [
						cskh.el('h2', {
							style: 'margin: 0 0 6px;',
							text: (allowPii && wi.display_name) || wi.lead || wi.work_item,
						}),
						cskh.el('div', { class: 'cskh-badge-stack' }, badges),
					]),
					cskh.el('div', { class: 'cskh-muted cskh-mono', style: 'text-align: right;' }, [
						cskh.el('div', { text: cskh.t('Work item') + ': ' + wi.work_item }),
						cskh.el('div', {
							text:
								cskh.t('Revision') +
								': v' +
								wi.revision +
								(wi.policy
									? ' · ' +
										cskh.t('Effective policy') +
										': ' +
										wi.policy.name +
										' (v' +
										wi.policy.version +
										')'
									: ''),
						}),
					]),
				]
			)
		)

		const phoneNode = cskh.el('div', { class: 'cskh-actions', style: 'margin: 0; gap: 6px;' }, [
			cskh.el('span', {
				class: 'cskh-mono',
				text: allowPii && wi.phone ? cskh.maskPhone(wi.phone) : '—',
			}),
			allowPii && wi.phone
				? cskh.el('button', {
						class: 'cskh-btn is-small',
						id: 'wi-btn-copy-phone',
						type: 'button',
						text: cskh.t('Copy phone number'),
						onclick: function () {
							cskh.copyPhone(wi.phone)
						},
					})
				: null,
		])

		metrics.appendChild(metric(cskh.t('Phone number'), phoneNode))
		metrics.appendChild(
			metric(cskh.t('Campaign'), wi.campaign_title || wi.campaign_code || wi.campaign)
		)
		metrics.appendChild(metric(cskh.t('Current owner'), wi.assigned_to || '—'))
		metrics.appendChild(
			metric(
				cskh.t('Contact count'),
				cskh.formatNumber(wi.attempt_count) +
					' (' +
					cskh.t('Connected') +
					': ' +
					cskh.formatNumber(wi.connected_count) +
					')'
			)
		)
		metrics.appendChild(
			metric(
				cskh.t('Last outcome'),
				wi.last_outcome_code || wi.last_outcome_label
					? cskh.outcomeLabel(wi.last_outcome_code, wi.last_outcome_label)
					: '—'
			)
		)
		metrics.appendChild(
			metric(
				cskh.t('Open callback'),
				wi.next_action_due ? cskh.formatDate(wi.next_action_due) : cskh.t('No open callback.')
			)
		)

		if (wiCaps.can_record_attempt) {
			actions.appendChild(
				cskh.el('button', {
					class: 'cskh-btn is-primary',
					id: 'wi-btn-record',
					type: 'button',
					text: cskh.t('Record result'),
					onclick: function () {
						openAttemptDrawer(false)
					},
				})
			)
		}
		if (wiCaps.can_complete_followup && wi.open_callback_task_id && wiCaps.can_record_attempt) {
			actions.appendChild(
				cskh.el('button', {
					class: 'cskh-btn is-primary',
					id: 'wi-btn-complete-callback',
					type: 'button',
					text: cskh.t('Record & complete'),
					onclick: function () {
						openAttemptDrawer(true)
					},
				})
			)
		}
		if (wiCaps.can_schedule_followup) {
			actions.appendChild(
				cskh.el('button', {
					class: 'cskh-btn',
					id: 'wi-btn-schedule-followup',
					type: 'button',
					text: wi.open_callback_task_id
						? cskh.t('Reschedule')
						: cskh.t('Schedule / update callback'),
					onclick: openScheduleDrawer,
				})
			)
		}
		if (wiCaps.can_complete_followup && wi.open_callback_task_id) {
			actions.appendChild(
				cskh.el('button', {
					class: 'cskh-btn',
					id: 'wi-btn-cancel-callback',
					type: 'button',
					text: cskh.t('Cancel callback'),
					onclick: openCancelCallbackDrawer,
				})
			)
		}
		if (wiCaps.can_close_work_item) {
			actions.appendChild(
				cskh.el('button', {
					class: 'cskh-btn',
					id: 'wi-btn-close-wi',
					type: 'button',
					text: cskh.t('Close work item'),
					onclick: openCloseWorkItemDrawer,
				})
			)
		}
		if (wiCaps.can_reopen_work_item) {
			actions.appendChild(
				cskh.el('button', {
					class: 'cskh-btn is-primary',
					id: 'wi-btn-reopen-wi',
					type: 'button',
					text: cskh.t('Reopen work item'),
					onclick: openReopenWorkItemDrawer,
				})
			)
		}
		if (allowPii && wi.lead) {
			actions.appendChild(
				cskh.el('a', {
					class: 'cskh-btn',
					href: '/crm/leads/' + encodeURIComponent(wi.lead),
					text: cskh.t('Open in CRM'),
				})
			)
		}

		card.hidden = false
	}

	/* ------------------------------------------------------------------ */
	/* Action Drawers (Single-API Orchestration Forms)                     */
	/* ------------------------------------------------------------------ */

	function closeDrawer() {
		const card = document.getElementById('wi-action-drawer')
		const body = document.getElementById('wi-action-drawer-body')
		if (card) card.hidden = true
		cskh.clear(body)
	}

	function openAttemptDrawer(completeCallback) {
		if (!currentWi) return
		const card = document.getElementById('wi-action-drawer')
		const body = document.getElementById('wi-action-drawer-body')
		card.hidden = false
		cskhAttemptForm.mount(body, {
			workItem: currentWi,
			outcomes: currentOutcomes,
			callLogs: currentTimeline.call_logs || [],
			completeOpenFollowup: !!completeCallback,
			callbackTaskId: completeCallback ? currentWi.open_callback_task_id : '',
			onCancel: closeDrawer,
			onSaved: async function () {
				closeDrawer()
				await loadAll()
			},
			onReloadWorkItem: async function () {
				await loadAll()
				return currentWi
			},
			onReloadPolicy: async function () {
				await loadAll()
				return { outcomes: currentOutcomes, workItem: currentWi }
			},
		})
		card.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
	}

	function openScheduleDrawer() {
		if (!currentWi) return
		const wi = currentWi
		const isReschedule = Boolean(wi.open_callback_task_id)
		const card = document.getElementById('wi-action-drawer')
		const body = document.getElementById('wi-action-drawer-body')
		cskh.clear(body)
		card.hidden = false

		let reqKey = 'ui-fu-sched-' + cskh.uuid()
		let inFlight = false
		const bumpKey = function () {
			if (!inFlight) reqKey = 'ui-fu-sched-' + cskh.uuid()
		}

		const alertBox = cskh.el('div', { 'aria-live': 'polite' })
		const defaultDue = wi.next_action_due
			? cskh.toDatetimeLocalValue(wi.next_action_due)
			: cskh.toDatetimeLocalValue(new Date(cskh.serverNow().getTime() + 24 * 3600 * 1000))

		const dueInput = cskh.el('input', {
			class: 'cskh-input',
			id: 'wi-fu-due-at',
			type: 'datetime-local',
			value: defaultDue,
		})
		const titleInput = cskh.el('input', {
			class: 'cskh-input',
			id: 'wi-fu-title',
			type: 'text',
			value: (wi.open_callback && wi.open_callback.title) || '',
			placeholder: cskh.t('Callback title / note'),
		})
		const reasonInput = cskh.el('input', {
			class: 'cskh-input',
			id: 'wi-fu-reason',
			type: 'text',
			placeholder: cskh.t('Reschedule reason'),
		})
		const noteInput = cskh.el('textarea', {
			class: 'cskh-textarea',
			id: 'wi-fu-note',
			placeholder: cskh.t('Callback title / note'),
		})
		if (wi.open_callback && wi.open_callback.description) {
			noteInput.value = wi.open_callback.description
		}

		;[dueInput, titleInput, reasonInput, noteInput].forEach(function (el) {
			el.addEventListener('input', bumpKey)
			el.addEventListener('change', bumpKey)
		})

		const saveBtn = cskh.el('button', {
			class: 'cskh-btn is-primary',
			id: 'wi-fu-submit',
			type: 'button',
			text: cskh.t('Save callback'),
		})

		async function submitSchedule() {
			if (inFlight) return
			cskh.clear(alertBox)
			const dueLocal = dueInput.value
			const reason = reasonInput.value.trim()
			if (!dueLocal) {
				cskh.alert(
					alertBox,
					'warning',
					cskh.t('A follow-up callback time is required for the selected outcome.')
				)
				return
			}
			if (isReschedule && !reason) {
				cskh.alert(
					alertBox,
					'warning',
					cskh.t('Please enter a reason to cancel or reschedule the callback.'),
					'[NOTE_REQUIRED]'
				)
				return
			}
			inFlight = true
			saveBtn.disabled = true
			try {
				await cskhP2Api.scheduleFollowup({
					work_item: wi.work_item_id,
					due_at: cskh.datetimeLocalToServer(dueLocal),
					title:
						titleInput.value.trim() ||
						cskh.t('Callbacks schedule') + (wi.display_name ? ' — ' + wi.display_name : ''),
					note: noteInput.value.trim(),
					reason: reason || null,
					expected_revision: wi.revision,
					idempotency_key: reqKey,
				})
				inFlight = false
				cskh.toast('success', cskh.t('Callback scheduled successfully.'))
				closeDrawer()
				await loadAll()
			} catch (err) {
				inFlight = false
				cskh.renderError(alertBox, err, {
					showBackLink: true,
					onSafeRetry: submitSchedule,
					onReloadWorkItem: async function () {
						await loadAll()
						openScheduleDrawer()
					},
				})
			} finally {
				saveBtn.disabled = false
			}
		}

		saveBtn.addEventListener('click', submitSchedule)

		body.appendChild(
			cskh.el('div', { class: 'cskh-panel' }, [
				cskh.el('h3', {
					style: 'margin-top: 0;',
					text: isReschedule ? cskh.t('Reschedule') : cskh.t('Schedule / update callback'),
				}),
				alertBox,
				cskh.el('div', { class: 'cskh-form-grid' }, [
					cskh.el('label', { class: 'cskh-field' }, [
						cskh.el('span', {
							text: cskh.t('Callback due at') + ' (' + cskh.effectiveTimeZone() + ') *',
						}),
						dueInput,
					]),
					cskh.el('label', { class: 'cskh-field' }, [
						cskh.el('span', { text: cskh.t('Callback title / note') }),
						titleInput,
					]),
					isReschedule
						? cskh.el('label', { class: 'cskh-field' }, [
								cskh.el('span', { text: cskh.t('Reschedule reason') + ' *' }),
								reasonInput,
							])
						: null,
				]),
				cskh.el('label', { class: 'cskh-field', style: 'margin-top: 8px;' }, [
					cskh.el('span', { text: cskh.t('Contact note') }),
					noteInput,
				]),
				cskh.el('div', { class: 'cskh-actions', style: 'margin-top: 12px;' }, [
					saveBtn,
					cskh.el('button', {
						class: 'cskh-btn',
						type: 'button',
						text: cskh.t('Cancel'),
						onclick: closeDrawer,
					}),
				]),
			])
		)
	}

	function openCancelCallbackDrawer() {
		if (!currentWi || !currentWi.open_callback_task_id) return
		const wi = currentWi
		const card = document.getElementById('wi-action-drawer')
		const body = document.getElementById('wi-action-drawer-body')
		cskh.clear(body)
		card.hidden = false

		let reqKey = 'ui-fu-cancel-' + cskh.uuid()
		let inFlight = false
		const alertBox = cskh.el('div', { 'aria-live': 'polite' })
		const reasonInput = cskh.el('textarea', {
			class: 'cskh-textarea',
			id: 'wi-cancel-cb-reason',
			placeholder: cskh.t('Cancel reason'),
		})
		reasonInput.addEventListener('input', function () {
			if (!inFlight) reqKey = 'ui-fu-cancel-' + cskh.uuid()
		})

		const submitBtn = cskh.el('button', {
			class: 'cskh-btn is-danger',
			id: 'wi-cancel-cb-submit',
			type: 'button',
			text: cskh.t('Close callback as canceled'),
		})

		async function submitCancel() {
			if (inFlight) return
			cskh.clear(alertBox)
			const reason = reasonInput.value.trim()
			if (!reason) {
				cskh.alert(
					alertBox,
					'warning',
					cskh.t('Please enter a reason to cancel or reschedule the callback.'),
					'[NOTE_REQUIRED]'
				)
				return
			}
			inFlight = true
			submitBtn.disabled = true
			try {
				await cskhP2Api.completeFollowup({
					work_item: wi.work_item_id,
					task_id: wi.open_callback_task_id,
					action: 'cancel',
					attempt_id: null,
					completion_reason: reason,
					expected_revision: wi.revision,
					idempotency_key: reqKey,
				})
				inFlight = false
				cskh.toast('info', cskh.t('Callback canceled with audit reason (not counted as connected).'))
				closeDrawer()
				await loadAll()
			} catch (err) {
				inFlight = false
				cskh.renderError(alertBox, err, {
					showBackLink: true,
					onSafeRetry: submitCancel,
					onReloadWorkItem: async function () {
						await loadAll()
						closeDrawer()
					},
				})
			} finally {
				submitBtn.disabled = false
			}
		}

		submitBtn.addEventListener('click', submitCancel)

		body.appendChild(
			cskh.el('div', { class: 'cskh-panel' }, [
				cskh.el('h3', { style: 'margin-top: 0;', text: cskh.t('Cancel callback') }),
				cskh.el('div', { class: 'cskh-banner-notice' }, [
					cskh.el('span', {
						text: cskh.t(
							'Task completion without a contact attempt never counts as connected or successful care.'
						),
					}),
				]),
				alertBox,
				cskh.el('label', { class: 'cskh-field' }, [
					cskh.el('span', { text: cskh.t('Cancel reason') + ' *' }),
					reasonInput,
				]),
				cskh.el('div', { class: 'cskh-actions', style: 'margin-top: 12px;' }, [
					submitBtn,
					cskh.el('button', {
						class: 'cskh-btn',
						type: 'button',
						text: cskh.t('Cancel'),
						onclick: closeDrawer,
					}),
				]),
			])
		)
	}

	function openCloseWorkItemDrawer() {
		if (!currentWi) return
		const wi = currentWi
		const card = document.getElementById('wi-action-drawer')
		const body = document.getElementById('wi-action-drawer-body')
		cskh.clear(body)
		card.hidden = false

		let reqKey = 'ui-wi-close-' + cskh.uuid()
		let inFlight = false
		const alertBox = cskh.el('div', { 'aria-live': 'polite' })
		const reasonInput = cskh.el('textarea', {
			class: 'cskh-textarea',
			id: 'wi-close-reason',
			placeholder: cskh.t('Completion reason'),
		})
		const invalidToggle = cskh.el('input', {
			type: 'checkbox',
			id: 'wi-close-invalid',
		})
		if (wi.attempt_count === 0) {
			invalidToggle.checked = true
		}
		;[reasonInput, invalidToggle].forEach(function (el) {
			el.addEventListener('input', function () {
				if (!inFlight) reqKey = 'ui-wi-close-' + cskh.uuid()
			})
			el.addEventListener('change', function () {
				if (!inFlight) reqKey = 'ui-wi-close-' + cskh.uuid()
			})
		})

		const submitBtn = cskh.el('button', {
			class: 'cskh-btn is-primary',
			id: 'wi-close-submit',
			type: 'button',
			text: cskh.t('Close work item'),
		})

		async function submitClose() {
			if (inFlight) return
			cskh.clear(alertBox)
			const reason = reasonInput.value.trim()
			if (!reason) {
				cskh.alert(
					alertBox,
					'warning',
					cskh.t('Please enter a completion reason to close this work item.'),
					'[NOTE_REQUIRED]'
				)
				return
			}
			inFlight = true
			submitBtn.disabled = true
			try {
				await cskhP2Api.closeWorkItem({
					work_item: wi.work_item_id,
					completion_reason: reason,
					close_category: invalidToggle.checked ? 'invalid_or_duplicate' : 'normal',
					expected_revision: wi.revision,
					idempotency_key: reqKey,
				})
				inFlight = false
				cskh.toast('success', cskh.t('Work item closed.'))
				closeDrawer()
				await loadAll()
			} catch (err) {
				inFlight = false
				cskh.renderError(alertBox, err, {
					showBackLink: true,
					onSafeRetry: submitClose,
					onReloadWorkItem: async function () {
						await loadAll()
					},
				})
			} finally {
				submitBtn.disabled = false
			}
		}

		submitBtn.addEventListener('click', submitClose)

		body.appendChild(
			cskh.el('div', { class: 'cskh-panel' }, [
				cskh.el('h3', { style: 'margin-top: 0;', text: cskh.t('Close work item') }),
				alertBox,
				cskh.el('label', { class: 'cskh-field' }, [
					cskh.el('span', { text: cskh.t('Completion reason') + ' *' }),
					reasonInput,
				]),
				cskh.el('label', { class: 'cskh-checkbox-row', style: 'margin-top: 8px;' }, [
					invalidToggle,
					cskh.el('span', { text: cskh.t('Wrong number') + ' / ' + cskh.t('Do not contact') }),
				]),
				cskh.el('div', { class: 'cskh-actions', style: 'margin-top: 12px;' }, [
					submitBtn,
					cskh.el('button', {
						class: 'cskh-btn',
						type: 'button',
						text: cskh.t('Cancel'),
						onclick: closeDrawer,
					}),
				]),
			])
		)
	}

	function openReopenWorkItemDrawer() {
		if (!currentWi) return
		const wi = currentWi
		const card = document.getElementById('wi-action-drawer')
		const body = document.getElementById('wi-action-drawer-body')
		cskh.clear(body)
		card.hidden = false

		let reqKey = 'ui-wi-reopen-' + cskh.uuid()
		let inFlight = false
		const alertBox = cskh.el('div', { 'aria-live': 'polite' })
		const targetInput = cskh.el('input', {
			class: 'cskh-input',
			id: 'wi-reopen-target',
			type: 'text',
			value: wi.assigned_to || cskh.currentUser(),
		})
		const reasonInput = cskh.el('textarea', {
			class: 'cskh-textarea',
			id: 'wi-reopen-reason',
			placeholder: cskh.t('Reopen reason'),
		})
		const overrideDncToggle = cskh.el('input', {
			type: 'checkbox',
			id: 'wi-reopen-override-dnc',
		})
		;[targetInput, reasonInput, overrideDncToggle].forEach(function (el) {
			el.addEventListener('input', function () {
				if (!inFlight) reqKey = 'ui-wi-reopen-' + cskh.uuid()
			})
			el.addEventListener('change', function () {
				if (!inFlight) reqKey = 'ui-wi-reopen-' + cskh.uuid()
			})
		})

		const submitBtn = cskh.el('button', {
			class: 'cskh-btn is-primary',
			id: 'wi-reopen-submit',
			type: 'button',
			text: cskh.t('Reopen work item'),
		})

		async function submitReopen() {
			if (inFlight) return
			cskh.clear(alertBox)
			const reason = reasonInput.value.trim()
			const targetUser = targetInput.value.trim()
			if (!reason) {
				cskh.alert(alertBox, 'warning', cskh.t('Please enter a reason.'), '[NOTE_REQUIRED]')
				return
			}
			inFlight = true
			submitBtn.disabled = true
			try {
				await cskhP2Api.reopenWorkItem({
					work_item: wi.work_item_id,
					target_user: targetUser || null,
					reason: reason,
					override_dnc: Boolean(caps.admin && overrideDncToggle.checked),
					expected_revision: wi.revision,
					idempotency_key: reqKey,
				})
				inFlight = false
				cskh.toast('success', cskh.t('Work item reopened.'))
				closeDrawer()
				await loadAll()
			} catch (err) {
				inFlight = false
				cskh.renderError(alertBox, err, {
					showBackLink: true,
					onSafeRetry: submitReopen,
					onReloadWorkItem: async function () {
						await loadAll()
					},
				})
			} finally {
				submitBtn.disabled = false
			}
		}

		submitBtn.addEventListener('click', submitReopen)

		body.appendChild(
			cskh.el('div', { class: 'cskh-panel' }, [
				cskh.el('h3', { style: 'margin-top: 0;', text: cskh.t('Reopen work item') }),
				alertBox,
				cskh.el('label', { class: 'cskh-field' }, [
					cskh.el('span', { text: cskh.t('New owner') }),
					targetInput,
				]),
				cskh.el('label', { class: 'cskh-field', style: 'margin-top: 8px;' }, [
					cskh.el('span', { text: cskh.t('Reopen reason') + ' *' }),
					reasonInput,
				]),
				caps.admin && wi.do_not_contact
					? cskh.el('label', { class: 'cskh-checkbox-row', style: 'margin-top: 8px;' }, [
							overrideDncToggle,
							cskh.el('span', { text: cskh.t('Do not contact') + ' (override)' }),
						])
					: null,
				cskh.el('div', { class: 'cskh-actions', style: 'margin-top: 12px;' }, [
					submitBtn,
					cskh.el('button', {
						class: 'cskh-btn',
						type: 'button',
						text: cskh.t('Cancel'),
						onclick: closeDrawer,
					}),
				]),
			])
		)
	}

	/* ------------------------------------------------------------------ */
	/* Unified Timeline & Append-Only Corrections                         */
	/* ------------------------------------------------------------------ */

	function unifiedTimelineItems() {
		const list = []
		if (timelineFilter === 'all' || timelineFilter === 'attempt') {
			;(currentTimeline.attempts || []).forEach(function (a) {
				list.push(a)
			})
		}
		if (timelineFilter === 'all' || timelineFilter === 'call_log') {
			;(currentTimeline.call_logs || []).forEach(function (c) {
				list.push(c)
			})
		}
		if (timelineFilter === 'all' || timelineFilter === 'task') {
			;(currentTimeline.tasks || []).forEach(function (t) {
				list.push(t)
			})
		}
		if (timelineFilter === 'all' || timelineFilter === 'note') {
			;(currentTimeline.notes || []).forEach(function (n) {
				list.push(n)
			})
		}
		if (timelineFilter === 'all') {
			;(currentTimeline.milestones || []).forEach(function (m) {
				list.push(m)
			})
		}
		list.sort(function (a, b) {
			const ta = a.occurred_at ? cskh.parseServerDate(a.occurred_at)?.getTime() || 0 : 0
			const tb = b.occurred_at ? cskh.parseServerDate(b.occurred_at)?.getTime() || 0 : 0
			return tb - ta
		})
		return list
	}

	function renderAttemptItem(att) {
		const wiCaps = (currentWi && currentWi.capabilities) || {}
		const badges = [
			cskh.el('span', {
				class: cskh.stateBadge('assigned'),
				text: cskh.t('Contact attempt (CSKH)'),
			}),
			cskh.el('span', {
				class: cskh.stateBadge(att.verification_status),
				text:
					att.verification_status === 'verified'
						? cskh.t('Verified')
						: cskh.t('Self-reported'),
			}),
		]
		if (att.is_void) {
			badges.push(
				cskh.el('span', {
					class: cskh.stateBadge('void'),
					text: cskh.t('Voided — excluded from counts'),
				})
			)
		} else {
			badges.push(
				cskh.el('span', {
					class: cskh.stateBadge(att.is_connected ? 'connected' : 'not_connected'),
					text: att.is_connected ? cskh.t('Connected') : cskh.t('Not connected'),
				})
			)
		}
		if (att.has_correction) {
			badges.push(
				cskh.el('span', {
					class: cskh.stateBadge('corrected'),
					text: cskh.t('Corrected'),
				})
			)
		}
		if (att.pending_correction_count > 0) {
			badges.push(
				cskh.el('span', {
					class: cskh.stateBadge('pending'),
					text: cskh.t('Pending correction review'),
				})
			)
		}

		const metaParts = [
			cskh.channelLabel(att.channel) +
				' → ' +
				cskh.outcomeLabel(att.outcome_code, att.outcome_label),
			cskh.t('Performed by') + ': ' + (att.performed_by_name || att.performed_by || '—'),
			cskh.t('Occurred at') + ': ' + cskh.formatDate(att.occurred_at),
			cskh.t('Recorded at') + ': ' + cskh.formatDate(att.recorded_at),
		]
		if (att.call_log_id) {
			metaParts.push(cskh.t('Call log (CRM)') + ': ' + att.call_log_id)
		}
		if (att.callback_task_id) {
			metaParts.push(cskh.t('Open callback') + ': ' + att.callback_task_id)
		}

		const bodyNodes = [
			cskh.el('div', { class: 'cskh-timeline-head' }, [
				cskh.el('div', { class: 'cskh-badge-stack' }, badges),
				cskh.el('span', { class: 'cskh-muted cskh-mono', text: '#' + att.attempt_id }),
			]),
			cskh.el('div', { class: 'cskh-muted', text: metaParts.join(' · ') }),
		]

		if (att.note) {
			bodyNodes.push(
				cskh.el('p', {
					style: 'margin: 6px 0 0; white-space: pre-wrap;',
					text: att.note,
				})
			)
		}

		if ((att.has_correction || att.is_void) && att.original_snapshot) {
			const orig = att.original_snapshot
			bodyNodes.push(
				cskh.el('div', { class: 'cskh-correction-box' }, [
					cskh.el('strong', { text: cskh.t('Original snapshot') + ': ' }),
					cskh.el('span', {
						text:
							cskh.channelLabel(orig.channel) +
							' · ' +
							cskh.outcomeLabel(orig.outcome_code, orig.outcome_label) +
							' · ' +
							cskh.formatDate(orig.occurred_at) +
							(orig.note ? ' — "' + orig.note + '"' : ''),
					}),
				])
			)
		}

		// Render existing corrections & review buttons (if `can_review_correction`)
		if (att.corrections && att.corrections.length) {
			const corrContainer = cskh.el('div', { class: 'cskh-correction-box' }, [
				cskh.el('strong', { text: cskh.t('Corrections and void history') }),
			])
			att.corrections.forEach(function (corr) {
				const corrStatusBadge = cskh.el('span', {
					class: cskh.stateBadge(
						corr.status === 'approved'
							? 'corrected'
							: corr.status === 'rejected'
								? 'rejected'
								: 'pending'
					),
					text:
						corr.status === 'approved'
							? cskh.t('Corrected')
							: corr.status === 'rejected'
								? cskh.t('Correction rejected')
								: cskh.t('Pending correction review'),
				})
				const corrActionLabel =
					corr.action === 'void'
						? cskh.t('Void attempt')
						: corr.action === 'restore'
							? cskh.t('Restore voided attempt')
							: cskh.t('Corrected')
				const summaryParts = [
					corrActionLabel,
					cskh.t('Requested by') + ': ' + (corr.requested_by || '—'),
					cskh.formatDate(corr.requested_at),
				]
				if (corr.proposed_outcome_code) {
					summaryParts.push(
						cskh.t('Proposed outcome') +
							': ' +
							cskh.outcomeLabel(corr.proposed_outcome_code, corr.proposed_outcome_label)
					)
				}
				if (corr.proposed_channel) {
					summaryParts.push(
						cskh.t('Proposed channel') + ': ' + cskh.channelLabel(corr.proposed_channel)
					)
				}
				if (corr.proposed_occurred_at) {
					summaryParts.push(
						cskh.t('Proposed timestamp') + ': ' + cskh.formatDate(corr.proposed_occurred_at)
					)
				}
				const corrNode = cskh.el(
					'div',
					{ style: 'margin-top: 8px; padding-top: 6px; border-top: 1px dashed #cbd5e1;' },
					[
						cskh.el('div', { class: 'cskh-badge-stack' }, [
							corrStatusBadge,
							cskh.el('span', { text: summaryParts.join(' · ') }),
						]),
						cskh.el('div', {
							style: 'margin-top: 4px;',
							text: cskh.t('Correction reason') + ': ' + (corr.reason || '—'),
						}),
						corr.reviewed_by
							? cskh.el('div', {
									class: 'cskh-muted',
									text:
										cskh.t('Reviewed by') +
										': ' +
										corr.reviewed_by +
										' (' +
										cskh.formatDate(corr.reviewed_at) +
										')' +
										(corr.review_note ? ' — ' + corr.review_note : ''),
								})
							: null,
					]
				)

				if (corr.status === 'pending' && wiCaps.can_review_correction) {
					corrNode.appendChild(renderReviewControls(corr))
				}
				corrContainer.appendChild(corrNode)
			})
			bodyNodes.push(corrContainer)
		}

		// Request correction / void control (NO direct Edit or Delete button!)
		const canStaffRequest =
			wiCaps.can_request_correction &&
			(caps.admin || caps.leader || att.performed_by === cskh.currentUser())
		if (canStaffRequest) {
			const formSlot = cskh.el('div')
			const reqBtn = cskh.el('button', {
				class: 'cskh-btn is-small',
				type: 'button',
				text: cskh.t('Request correction / void'),
				onclick: function () {
					if (formSlot.childNodes.length) {
						cskh.clear(formSlot)
					} else {
						mountCorrectionRequestForm(formSlot, att)
					}
				},
			})
			bodyNodes.push(cskh.el('div', { class: 'cskh-actions', style: 'margin-top: 8px;' }, [reqBtn]))
			bodyNodes.push(formSlot)
		}

		return cskh.el(
			'div',
			{ class: 'cskh-timeline-item ' + (att.is_void ? 'is-void' : 'is-attempt') },
			bodyNodes
		)
	}

	function mountCorrectionRequestForm(slot, att) {
		cskh.clear(slot)
		if (!currentWi) return
		const wi = currentWi
		const wiCaps = wi.capabilities || {}

		let reqKey = 'ui-corr-req-' + cskh.uuid()
		let inFlight = false
		const bump = function () {
			if (!inFlight) reqKey = 'ui-corr-req-' + cskh.uuid()
		}

		const alertBox = cskh.el('div', { 'aria-live': 'polite' })
		const typeOptions = [
			cskh.el('option', { value: 'change_outcome', text: cskh.t('Change outcome') }),
			cskh.el('option', { value: 'change_channel', text: cskh.t('Change channel') }),
			cskh.el('option', { value: 'change_timestamp', text: cskh.t('Change timestamp') }),
			cskh.el('option', { value: 'clarify', text: cskh.t('Add clarification') }),
			cskh.el('option', { value: 'void', text: cskh.t('Void attempt') }),
		]
		if (att.is_void && caps.admin) {
			typeOptions.unshift(
				cskh.el('option', { value: 'restore', text: cskh.t('Restore voided attempt') })
			)
		}
		const typeSelect = cskh.el('select', { class: 'cskh-select' }, typeOptions)

		const outcomeSelect = cskh.el('select', { class: 'cskh-select' })
		currentOutcomes
			.filter(function (o) {
				return o && o.enabled !== false
			})
			.forEach(function (o) {
				outcomeSelect.appendChild(
					cskh.el('option', {
						value: o.code,
						text: cskh.outcomeLabel(o.code, o.label),
					})
				)
			})
		if (att.outcome_code) outcomeSelect.value = att.outcome_code

		const channelSelect = cskh.el('select', { class: 'cskh-select' })
		;['phone_call', 'zalo', 'sms', 'facebook', 'other'].forEach(function (ch) {
			channelSelect.appendChild(cskh.el('option', { value: ch, text: cskh.channelLabel(ch) }))
		})
		if (att.channel) channelSelect.value = att.channel

		const tsInput = cskh.el('input', {
			class: 'cskh-input',
			type: 'datetime-local',
			value: cskh.toDatetimeLocalValue(att.occurred_at),
		})
		const clarifyInput = cskh.el('textarea', {
			class: 'cskh-textarea',
			value: att.note || '',
			placeholder: cskh.t('Clarification note'),
		})
		const reasonInput = cskh.el('textarea', {
			class: 'cskh-textarea',
			placeholder: cskh.t('Enter the reason for requesting this correction or void...'),
		})
		const autoApproveToggle = cskh.el('input', {
			type: 'checkbox',
			checked: !!wiCaps.can_review_correction,
		})

		const outWrap = cskh.el('label', { class: 'cskh-field' }, [
			cskh.el('span', { text: cskh.t('Proposed outcome') }),
			outcomeSelect,
		])
		const chWrap = cskh.el('label', { class: 'cskh-field', hidden: true }, [
			cskh.el('span', { text: cskh.t('Proposed channel') }),
			channelSelect,
		])
		const tsWrap = cskh.el('label', { class: 'cskh-field', hidden: true }, [
			cskh.el('span', { text: cskh.t('Proposed timestamp') }),
			tsInput,
		])
		const noteWrap = cskh.el('label', { class: 'cskh-field' }, [
			cskh.el('span', { text: cskh.t('Clarification note') }),
			clarifyInput,
		])

		function syncMode() {
			const mode = typeSelect.value
			outWrap.hidden = mode !== 'change_outcome'
			chWrap.hidden = mode !== 'change_channel'
			tsWrap.hidden = mode !== 'change_timestamp'
			noteWrap.hidden = mode === 'void' || mode === 'restore'
		}
		typeSelect.addEventListener('change', syncMode)
		;[typeSelect, outcomeSelect, channelSelect, tsInput, clarifyInput, reasonInput, autoApproveToggle].forEach(
			function (el) {
				el.addEventListener('input', bump)
				el.addEventListener('change', bump)
			}
		)

		const submitBtn = cskh.el('button', {
			class: 'cskh-btn is-primary is-small',
			type: 'button',
			text: cskh.t('Submit correction request'),
		})

		async function submitCorrection() {
			if (inFlight) return
			cskh.clear(alertBox)
			const reason = reasonInput.value.trim()
			if (!reason) {
				cskh.alert(
					alertBox,
					'warning',
					cskh.t('Please enter a reason for the correction request.'),
					'[NOTE_REQUIRED]'
				)
				return
			}
			const mode = typeSelect.value
			const action = mode === 'void' ? 'void' : mode === 'restore' ? 'restore' : 'correct'
			const correctedFields = {}
			if (mode === 'change_outcome') {
				correctedFields.outcome_code = outcomeSelect.value
				if (clarifyInput.value.trim()) correctedFields.note = clarifyInput.value.trim()
			} else if (mode === 'change_channel') {
				correctedFields.channel = channelSelect.value
				if (clarifyInput.value.trim()) correctedFields.note = clarifyInput.value.trim()
			} else if (mode === 'change_timestamp') {
				correctedFields.occurred_at = cskh.datetimeLocalToServer(tsInput.value)
				if (clarifyInput.value.trim()) correctedFields.note = clarifyInput.value.trim()
			} else if (mode === 'clarify') {
				correctedFields.note = clarifyInput.value.trim()
			}

			inFlight = true
			submitBtn.disabled = true
			try {
				await cskhP2Api.requestCorrection({
					attempt: att.attempt_id,
					action: action,
					corrected_fields: action === 'correct' ? correctedFields : null,
					reason: reason,
					auto_approve: Boolean(wiCaps.can_review_correction && autoApproveToggle.checked),
					expected_revision: wi.revision,
					idempotency_key: reqKey,
				})
				inFlight = false
				cskh.toast('success', cskh.t('Correction request submitted.'))
				await loadAll()
			} catch (err) {
				inFlight = false
				cskh.renderError(alertBox, err, {
					onSafeRetry: submitCorrection,
					onReloadWorkItem: loadAll,
				})
			} finally {
				submitBtn.disabled = false
			}
		}

		submitBtn.addEventListener('click', submitCorrection)
		syncMode()

		slot.appendChild(
			cskh.el('div', { class: 'cskh-panel', style: 'margin-top: 8px;' }, [
				alertBox,
				cskh.el('div', { class: 'cskh-form-grid' }, [
					cskh.el('label', { class: 'cskh-field' }, [
						cskh.el('span', { text: cskh.t('Correction type') }),
						typeSelect,
					]),
					outWrap,
					chWrap,
					tsWrap,
				]),
				noteWrap,
				cskh.el('label', { class: 'cskh-field', style: 'margin-top: 8px;' }, [
					cskh.el('span', { text: cskh.t('Correction reason') + ' *' }),
					reasonInput,
				]),
				wiCaps.can_review_correction
					? cskh.el('label', { class: 'cskh-checkbox-row', style: 'margin-top: 8px;' }, [
							autoApproveToggle,
							cskh.el('span', { text: cskh.t('Approve correction') }),
						])
					: null,
				cskh.el('div', { class: 'cskh-actions', style: 'margin-top: 10px;' }, [
					submitBtn,
					cskh.el('button', {
						class: 'cskh-btn is-small',
						type: 'button',
						text: cskh.t('Cancel'),
						onclick: function () {
							cskh.clear(slot)
						},
					}),
				]),
			])
		)
	}

	function renderReviewControls(corr) {
		const alertBox = cskh.el('div', { 'aria-live': 'polite' })
		const reviewNote = cskh.el('input', {
			class: 'cskh-input',
			type: 'text',
			placeholder: cskh.t('Review note'),
		})
		let reqKey = 'ui-corr-rev-' + cskh.uuid()
		let inFlight = false
		reviewNote.addEventListener('input', function () {
			if (!inFlight) reqKey = 'ui-corr-rev-' + cskh.uuid()
		})

		async function sendDecision(decision) {
			if (inFlight || !currentWi) return
			cskh.clear(alertBox)
			const noteVal = reviewNote.value.trim()
			if (decision === 'reject' && !noteVal) {
				cskh.alert(alertBox, 'warning', cskh.t('Please enter a review note.'))
				return
			}
			inFlight = true
			try {
				await cskhP2Api.reviewCorrection({
					correction_id: corr.correction_id,
					decision: decision,
					review_note: noteVal || cskh.t('Correction review saved.'),
					expected_revision: currentWi.revision,
					idempotency_key: reqKey,
				})
				inFlight = false
				cskh.toast('success', cskh.t('Correction review saved.'))
				await loadAll()
			} catch (err) {
				inFlight = false
				cskh.renderError(alertBox, err, {
					onReloadWorkItem: loadAll,
				})
			}
		}

		return cskh.el('div', { style: 'margin-top: 8px;' }, [
			alertBox,
			cskh.el('div', { class: 'cskh-actions' }, [
				reviewNote,
				cskh.el('button', {
					class: 'cskh-btn is-primary is-small',
					type: 'button',
					text: cskh.t('Approve correction'),
					onclick: function () {
						sendDecision('approve')
					},
				}),
				cskh.el('button', {
					class: 'cskh-btn is-danger is-small',
					type: 'button',
					text: cskh.t('Reject correction'),
					onclick: function () {
						sendDecision('reject')
					},
				}),
			]),
		])
	}

	function renderCallLogItem(cl) {
		const dirLabel =
			String(cl.direction || '').toLowerCase() === 'incoming'
				? cskh.t('Inbound')
				: String(cl.direction || '').toLowerCase() === 'outgoing'
					? cskh.t('Outbound')
					: cl.direction || '—'
		const linkBadge = cl.linked_attempt_id
			? cskh.el('span', {
					class: cskh.stateBadge('connected'),
					text: cskh.t('Linked to attempt') + ': #' + cl.linked_attempt_id,
				})
			: cskh.el('span', {
					class: cskh.stateBadge('not_connected'),
					text: cskh.t('Unlinked call log (not counted as attempt)'),
				})

		const rawStatus = String(cl.status || '').trim()
		const statusLabel =
			rawStatus === 'Completed'
				? cskh.t('Done')
				: rawStatus === 'No Answer'
					? cskh.t('No answer')
					: rawStatus === 'Busy'
						? cskh.t('Line busy')
						: rawStatus === 'Canceled'
							? cskh.t('Canceled')
							: rawStatus || '—'

		return cskh.el('div', { class: 'cskh-timeline-item is-call-log' }, [
			cskh.el('div', { class: 'cskh-timeline-head' }, [
				cskh.el('div', { class: 'cskh-badge-stack' }, [
					cskh.el('span', {
						class: cskh.stateBadge('in_progress'),
						text: cskh.t('Call log (CRM)'),
					}),
					linkBadge,
				]),
				cskh.el('span', { class: 'cskh-muted cskh-mono', text: cl.call_log_id }),
			]),
			cskh.el('div', {
				class: 'cskh-muted',
				text: [
					cskh.t('Call direction') + ': ' + dirLabel,
					cskh.t('Status') + ': ' + statusLabel,
					cl.duration !== undefined && cl.duration !== null
						? cskh.t('Duration (s)') + ': ' + cl.duration
						: null,
					cskh.formatDate(cl.occurred_at),
				]
					.filter(Boolean)
					.join(' · '),
			}),
			cl.note ? cskh.el('p', { style: 'margin: 6px 0 0;', text: cl.note }) : null,
		])
	}

	function renderTaskItem(task) {
		const dueKind = task.followup_state || cskh.classifyDueAt(task.due_at, task.status)
		return cskh.el('div', { class: 'cskh-timeline-item is-task' }, [
			cskh.el('div', { class: 'cskh-timeline-head' }, [
				cskh.el('div', { class: 'cskh-badge-stack' }, [
					cskh.el('span', {
						class: cskh.stateBadge('on_hold'),
						text: cskh.t('Callback / Task (CRM)'),
					}),
					cskh.el('span', {
						class: cskh.stateBadge(dueKind || 'open'),
						text: cskh.dueStateLabel(dueKind || 'open'),
					}),
					cskh.el('span', {
						class: cskh.stateBadge('not_connected'),
						text: cskh.t('Task — excluded from attempt count'),
					}),
				]),
				cskh.el('span', { class: 'cskh-muted cskh-mono', text: '#' + task.task_id }),
			]),
			cskh.el('div', {
				style: 'font-weight: 600; margin-top: 4px;',
				text: task.title || cskh.t('Callbacks schedule'),
			}),
			cskh.el('div', {
				class: 'cskh-muted',
				text: [
					cskh.t('Callback due at') + ': ' + cskh.formatDate(task.due_at),
					cskh.t('Current owner') + ': ' + (task.assigned_to || '—'),
					task.linked_attempt_id
						? cskh.t('Linked attempt') + ': #' + task.linked_attempt_id
						: null,
					task.close_reason ? cskh.t('Close / cancel reason') + ': ' + task.close_reason : null,
				]
					.filter(Boolean)
					.join(' · '),
			}),
			task.description
				? cskh.el('p', { style: 'margin: 6px 0 0;', text: task.description })
				: null,
		])
	}

	function renderNoteItem(note) {
		return cskh.el('div', { class: 'cskh-timeline-item is-note' }, [
			cskh.el('div', { class: 'cskh-timeline-head' }, [
				cskh.el('div', { class: 'cskh-badge-stack' }, [
					cskh.el('span', {
						class: cskh.stateBadge('queued'),
						text: cskh.t('Internal note (CRM)'),
					}),
					cskh.el('span', {
						class: cskh.stateBadge('not_connected'),
						text: cskh.t('Note — excluded from attempt count'),
					}),
				]),
				cskh.el('span', { class: 'cskh-muted', text: cskh.formatDate(note.occurred_at) }),
			]),
			note.title ? cskh.el('strong', { text: note.title }) : null,
			note.content
				? cskh.el('p', {
						style: 'margin: 6px 0 0; white-space: pre-wrap;',
						text: String(note.content).replace(/<[^>]+>/g, ' ').trim(),
					})
				: null,
		])
	}

	function formatMilestoneReason(value) {
		if (!value) return ''
		const text = String(value)
		const prefix = 'Distribution run '
		if (text.indexOf(prefix) === 0) {
			return cskh.t('The distribution run') + ' ' + text.slice(prefix.length)
		}
		return cskh.t(text, text)
	}

	function renderMilestoneItem(ms) {
		const actionMap = {
			assign: cskh.t('Assign'),
			reassign: cskh.t('Transfer owner'),
			revoke: cskh.t('Revoke'),
			close: cskh.t('Close work item'),
			reopen: cskh.t('Reopen work item'),
		}
		const reasonText = formatMilestoneReason(ms.reason)
		return cskh.el('div', { class: 'cskh-timeline-item' }, [
			cskh.el('div', { class: 'cskh-timeline-head' }, [
				cskh.el('div', { class: 'cskh-badge-stack' }, [
					cskh.el('span', {
						class: cskh.stateBadge('closed'),
						text: cskh.t('Ownership history') + ': ' + (actionMap[ms.action] || ms.action),
					}),
				]),
				cskh.el('span', { class: 'cskh-muted', text: cskh.formatDate(ms.occurred_at) }),
			]),
			cskh.el('div', {
				class: 'cskh-muted',
				text: [
					ms.from_user ? cskh.t('From owner') + ': ' + ms.from_user : null,
					ms.to_user ? cskh.t('To owner') + ': ' + ms.to_user : null,
					ms.actor ? cskh.t('Actor') + ': ' + ms.actor : null,
					reasonText ? cskh.t('Reason') + ': ' + reasonText : null,
				]
					.filter(Boolean)
					.join(' · '),
			}),
		])
	}

	function renderTimeline() {
		const card = document.getElementById('wi-timeline-card')
		const listEl = document.getElementById('wi-timeline-list')
		const emptyEl = document.getElementById('wi-timeline-empty')
		const tzEl = document.getElementById('wi-timeline-tz')
		cskh.clear(listEl)
		if (!currentWi) {
			card.hidden = true
			return
		}
		card.hidden = false
		if (tzEl) {
			tzEl.textContent = cskh.t('Site/user timezone') + ': ' + cskh.effectiveTimeZone()
		}

		const items = unifiedTimelineItems()
		if (!items.length) {
			emptyEl.hidden = false
			return
		}
		emptyEl.hidden = true
		items.forEach(function (item) {
			if (item.kind === 'attempt') listEl.appendChild(renderAttemptItem(item))
			else if (item.kind === 'call_log') listEl.appendChild(renderCallLogItem(item))
			else if (item.kind === 'task') listEl.appendChild(renderTaskItem(item))
			else if (item.kind === 'note') listEl.appendChild(renderNoteItem(item))
			else if (item.kind === 'milestone') listEl.appendChild(renderMilestoneItem(item))
		})
	}

	/* ------------------------------------------------------------------ */
	/* Main Loader                                                         */
	/* ------------------------------------------------------------------ */

	async function loadAll() {
		const loading = document.getElementById('wi-loading')
		const alertBox = document.getElementById('wi-alert')
		const q = queryParams()
		cskh.clear(alertBox)

		if (!q.work_item && !q.lead) {
			loading.hidden = true
			wipeWorkspaceDom()
			cskh.alert(alertBox, 'warning', cskh.errorText('INVALID_REQUEST'), '[INVALID_REQUEST]', [
				cskh.el('a', {
					class: 'cskh-btn is-small',
					href: '/cskh/my-customers',
					text: cskh.t('Back to my customers'),
				}),
			])
			return
		}

		loading.hidden = false
		const token = reads.next()
		try {
			const wi = await cskhP2Api.getWorkItem(q)
			if (token !== reads.current()) return
			const parallel = await Promise.all([
				cskhP2Api.listTimeline({
					work_item_id: wi.work_item_id,
					lead: wi.lead,
					page: pager.page(),
					page_size: PAGE_SIZE,
				}),
				cskhP2Api.getOutcomeCodes({ work_item: wi.work_item_id }),
			])
			if (token !== reads.current()) return
			currentWi = wi
			currentTimeline = parallel[0]
			currentOutcomes = (parallel[1] && parallel[1].items) || []
			pager.setTotal(currentTimeline.total)
			pager.render()
			renderSummary()
			renderTimeline()
		} catch (error) {
			if (token !== reads.current()) return
			wipeWorkspaceDom()
			cskh.renderError(alertBox, error, {
				showBackLink: true,
			})
		} finally {
			loading.hidden = true
		}
	}

	const chipsBar = document.getElementById('wi-timeline-chips')
	if (chipsBar) {
		chipsBar.addEventListener('click', function (ev) {
			const btn = ev.target.closest('.cskh-chip')
			if (!btn) return
			timelineFilter = btn.getAttribute('data-kind') || 'all'
			Array.from(chipsBar.querySelectorAll('.cskh-chip')).forEach(function (c) {
				if ((c.getAttribute('data-kind') || 'all') === timelineFilter) c.classList.add('is-active')
				else c.classList.remove('is-active')
			})
			renderTimeline()
		})
	}

	document.getElementById('wi-refresh').addEventListener('click', function () {
		loadAll()
	})
	document.getElementById('wi-prev').addEventListener('click', function () {
		pager.goto(Math.max(1, pager.page() - 1))
		loadAll()
	})
	document.getElementById('wi-next').addEventListener('click', function () {
		pager.goto(pager.page() + 1)
		loadAll()
	})

	loadAll()
})()
