/* Reusable Quick Contact Attempt Form (`window.cskhAttemptForm`).

   Enforces P2 §0.6–0.7, §8.2, §9.2, §10.1–10.2 & `docs/api-contract-p2.md` §4.5:
     * ONE save action invokes ONE atomic backend orchestration endpoint
       (`cskhP2Api.recordAttempt`), never chaining separate Task/Status writes;
     * Filters channels by `policy.enabled_channels` and outcomes by `allowed_channels`;
     * Explicitly surfaces `is_connected` semantics (e.g. `message_sent_no_reply`
       and `no_answer` never claim `Connected`);
     * Uses authoritative site/user timezone (`cskh.toDatetimeLocalValue` and
       `cskh.datetimeLocalToServer`);
     * Preserves `idempotency_key` across safe retries / double-clicks of identical
       input, and regenerates `idempotency_key` whenever any input changes;
     * Displays clear Vietnamese recovery actions on `OWNERSHIP_CHANGED`,
       `STALE_WORK_ITEM`, `POLICY_CHANGED`, `DO_NOT_CONTACT`, `WORK_ITEM_NOT_ACTIVE`.
*/

window.cskhAttemptForm = (function () {
	'use strict'

	const DEFAULT_CHANNELS = ['phone_call', 'zalo', 'sms', 'facebook', 'other']

	function mount(container, options) {
		cskh.clear(container)
		if (!container || !options || !options.workItem) return null

		let workItem = options.workItem
		let outcomes = (options.outcomes || []).filter(function (o) {
			return o && o.enabled !== false
		})
		const unlinkedCallLogs = (options.callLogs || []).filter(function (cl) {
			return cl && !cl.linked_attempt_id
		})
		const callbackTaskId =
			options.callbackTaskId ||
			(options.completeOpenFollowup && workItem.open_callback_task_id) ||
			''

		let pendingKey = 'ui-att-' + cskh.uuid()
		let inFlight = false
		let lockedByConflict = false
		let occurredUserEdited = false

		function bumpIdempotencyKey() {
			if (!inFlight) {
				pendingKey = 'ui-att-' + cskh.uuid()
			}
		}

		const alertBox = cskh.el('div', { 'aria-live': 'polite' })
		const semanticBar = cskh.el('div', {
			class: 'cskh-badge-stack',
			style: 'margin: 6px 0 12px;',
		})

		const channelSelect = cskh.el('select', {
			class: 'cskh-select',
			id: 'cskh-att-channel',
		})

		function populateChannels() {
			const prev = channelSelect.value
			cskh.clear(channelSelect)
			const enabledChannels =
				workItem.policy &&
				Array.isArray(workItem.policy.enabled_channels) &&
				workItem.policy.enabled_channels.length
					? workItem.policy.enabled_channels
					: DEFAULT_CHANNELS
			enabledChannels.forEach(function (ch) {
				channelSelect.appendChild(
					cskh.el('option', { value: ch, text: cskh.channelLabel(ch) })
				)
			})
			if (
				prev &&
				Array.from(channelSelect.options).some(function (opt) {
					return opt.value === prev
				})
			) {
				channelSelect.value = prev
			}
		}

		const outcomeSelect = cskh.el('select', {
			class: 'cskh-select',
			id: 'cskh-att-outcome',
		})

		const occurredInput = cskh.el('input', {
			class: 'cskh-input',
			id: 'cskh-att-occurred-at',
			type: 'datetime-local',
			value: cskh.toDatetimeLocalValue(cskh.serverNow()),
		})

		const callLogSelect = cskh.el('select', {
			class: 'cskh-select',
			id: 'cskh-att-call-log',
		})
		callLogSelect.appendChild(
			cskh.el('option', { value: '', text: cskh.t('No unlinked call log') })
		)
		unlinkedCallLogs.forEach(function (cl) {
			const label =
				cl.call_log_id +
				' (' +
				cskh.formatDate(cl.occurred_at) +
				(cl.duration !== undefined && cl.duration !== null ? ' · ' + cl.duration + 's' : '') +
				')'
			callLogSelect.appendChild(cskh.el('option', { value: cl.call_log_id, text: label }))
		})
		const callLogField = cskh.el('label', { class: 'cskh-field' }, [
			cskh.el('span', { text: cskh.t('Link native call log (optional)') }),
			callLogSelect,
		])

		const noteLabelSpan = cskh.el('span', { text: cskh.t('Contact note') })
		const noteInput = cskh.el('textarea', {
			class: 'cskh-textarea',
			id: 'cskh-att-note',
			placeholder: cskh.t('Enter contact summary or customer feedback...'),
		})

		const followupToggle = cskh.el('input', {
			type: 'checkbox',
			id: 'cskh-att-has-followup',
		})
		const followupInput = cskh.el('input', {
			class: 'cskh-input',
			id: 'cskh-att-followup-at',
			type: 'datetime-local',
		})
		const followupTitleInput = cskh.el('input', {
			class: 'cskh-input',
			id: 'cskh-att-followup-title',
			type: 'text',
			placeholder: cskh.t('Callback title / note'),
		})
		const followupHint = cskh.el('span', {
			class: 'cskh-muted',
			style: 'font-size: 12px;',
		})
		const followupWrap = cskh.el('div', { class: 'cskh-panel' }, [
			cskh.el('label', { class: 'cskh-checkbox-row' }, [
				followupToggle,
				cskh.el('span', { text: cskh.t('Schedule follow-up callback') }),
			]),
			cskh.el(
				'div',
				{
					style:
						'margin-top: 8px; display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end;',
				},
				[
					cskh.el('label', { class: 'cskh-field', style: 'margin: 0;' }, [
						cskh.el('span', { text: cskh.t('Callback due at') }),
						followupInput,
					]),
					cskh.el('label', { class: 'cskh-field', style: 'margin: 0; min-width: 220px;' }, [
						cskh.el('span', { text: cskh.t('Callback title / note') }),
						followupTitleInput,
					]),
					followupHint,
				]
			),
		])

		const closeToggle = cskh.el('input', {
			type: 'checkbox',
			id: 'cskh-att-close-wi',
		})
		const completionReasonInput = cskh.el('input', {
			class: 'cskh-input',
			id: 'cskh-att-completion-reason',
			type: 'text',
			placeholder: cskh.t('Completion reason'),
		})
		const closeWrap = cskh.el('div', { class: 'cskh-panel', hidden: true }, [
			cskh.el('label', { class: 'cskh-checkbox-row' }, [
				closeToggle,
				cskh.el('span', { text: cskh.t('Close care work item with this outcome') }),
			]),
			cskh.el('label', { class: 'cskh-field', style: 'margin-top: 8px;' }, [
				cskh.el('span', { text: cskh.t('Completion reason') }),
				completionReasonInput,
			]),
		])

		const submitBtn = cskh.el('button', {
			class: 'cskh-btn is-primary',
			id: 'cskh-att-submit',
			type: 'button',
			text: cskh.t('Save contact result'),
		})
		const cancelBtn = options.onCancel
			? cskh.el('button', {
					class: 'cskh-btn',
					type: 'button',
					text: cskh.t('Cancel'),
					onclick: function () {
						options.onCancel()
					},
				})
			: null

		function currentOutcome() {
			const code = outcomeSelect.value
			for (let i = 0; i < outcomes.length; i += 1) {
				if (outcomes[i].code === code) return outcomes[i]
			}
			return null
		}

		function defaultFollowupLocal() {
			const futureMs = cskh.serverNow().getTime() + 24 * 3600 * 1000
			return cskh.toDatetimeLocalValue(new Date(futureMs))
		}

		function populateOutcomes() {
			const ch = channelSelect.value
			const prev = outcomeSelect.value
			cskh.clear(outcomeSelect)
			outcomeSelect.appendChild(
				cskh.el('option', { value: '', text: cskh.t('Select outcome...') })
			)
			outcomes.forEach(function (o) {
				if (o.allowed_channels && o.allowed_channels.length && o.allowed_channels.indexOf(ch) < 0) {
					return
				}
				outcomeSelect.appendChild(
					cskh.el('option', {
						value: o.code,
						text: cskh.outcomeLabel(o.code, o.label),
					})
				)
			})
			if (
				prev &&
				Array.from(outcomeSelect.options).some(function (opt) {
					return opt.value === prev
				})
			) {
				outcomeSelect.value = prev
			}
			callLogField.hidden = ch !== 'phone_call' || !unlinkedCallLogs.length
			syncOutcomeConstraints()
		}

		function syncOutcomeConstraints() {
			const out = currentOutcome()
			cskh.clear(semanticBar)
			const needNote = !!(out && out.requires_note)
			noteLabelSpan.textContent = cskh.t('Contact note') + (needNote ? ' *' : '')

			if (!out) {
				followupToggle.disabled = false
				followupInput.disabled = !followupToggle.checked
				followupTitleInput.disabled = !followupToggle.checked
				followupHint.textContent = ''
				closeWrap.hidden = true
				return
			}

			semanticBar.appendChild(
				cskh.el('span', {
					class: cskh.stateBadge(out.is_connected ? 'connected' : 'not_connected'),
					text: out.is_connected
						? cskh.t('Counts as connected')
						: cskh.t('Does not count as connected'),
				})
			)
			if (needNote) {
				semanticBar.appendChild(
					cskh.el('span', {
						class: cskh.stateBadge('due_today'),
						text: cskh.t('Requires note'),
					})
				)
			}
			if (out.requires_followup) {
				semanticBar.appendChild(
					cskh.el('span', {
						class: cskh.stateBadge('due_today'),
						text: cskh.t('Requires callback'),
					})
				)
			}
			if (out.is_terminal) {
				semanticBar.appendChild(
					cskh.el('span', {
						class: cskh.stateBadge('closed'),
						text: cskh.t('Terminal outcome'),
					})
				)
			}
			if (out.sets_do_not_contact) {
				semanticBar.appendChild(
					cskh.el('span', {
						class: cskh.stateBadge('dnc'),
						text: cskh.t('Sets Do Not Contact (DNC)'),
					})
				)
			}
			if (out.is_conversion || out.code === 'converted') {
				semanticBar.appendChild(
					cskh.el('span', {
						class: 'cskh-muted',
						style: 'font-size: 12px;',
						text: cskh.t(
							'Campaign goal completion closes outreach intake only — it does not confirm hiring or placement.'
						),
					})
				)
			}
			if (
				out.code === 'message_sent_no_reply' ||
				(!out.is_connected &&
					(channelSelect.value === 'sms' ||
						channelSelect.value === 'zalo' ||
						channelSelect.value === 'facebook'))
			) {
				semanticBar.appendChild(
					cskh.el('span', {
						class: 'cskh-muted',
						style: 'font-size: 12px;',
						text: cskh.t('Message without reply is never counted as connected.'),
					})
				)
			}

			if (!out.allows_followup || out.is_terminal || out.sets_do_not_contact) {
				followupToggle.checked = false
				followupToggle.disabled = true
				followupInput.value = ''
				followupInput.disabled = true
				followupTitleInput.disabled = true
				followupHint.textContent = cskh.t(
					'This outcome does not allow scheduling a follow-up callback.'
				)
			} else if (out.requires_followup) {
				followupToggle.checked = true
				followupToggle.disabled = true
				followupInput.disabled = false
				followupTitleInput.disabled = false
				if (!followupInput.value) followupInput.value = defaultFollowupLocal()
				followupHint.textContent = cskh.t('Callback is required for this outcome.')
			} else {
				followupToggle.disabled = false
				followupInput.disabled = !followupToggle.checked
				followupTitleInput.disabled = !followupToggle.checked
				followupHint.textContent = ''
			}

			if (out.is_terminal) {
				closeWrap.hidden = false
				closeToggle.checked = true
				closeToggle.disabled = out.sets_do_not_contact
			} else {
				closeWrap.hidden = true
				closeToggle.checked = false
				closeToggle.disabled = false
			}
		}

		function setInputsLocked(locked) {
			lockedByConflict = locked
			channelSelect.disabled = locked
			outcomeSelect.disabled = locked
			occurredInput.disabled = locked
			noteInput.disabled = locked
			callLogSelect.disabled = locked
			submitBtn.disabled = locked
			if (locked) {
				followupToggle.disabled = true
				followupInput.disabled = true
				followupTitleInput.disabled = true
			} else {
				syncOutcomeConstraints()
			}
		}

		async function handleSubmit() {
			if (inFlight || lockedByConflict) return
			cskh.clear(alertBox)

			const channel = channelSelect.value
			const out = currentOutcome()
			const occurredLocal = occurredInput.value
			const note = noteInput.value.trim()
			const hasFollowup = followupToggle.checked && !followupInput.disabled
			const followupLocal = hasFollowup ? followupInput.value : ''
			const followupTitle = hasFollowup ? followupTitleInput.value.trim() : ''
			const shouldClose = !closeWrap.hidden && closeToggle.checked
			const completionReason = completionReasonInput.value.trim()

			if (!channel) {
				cskh.alert(alertBox, 'warning', cskh.t('Please select a contact channel.'))
				return
			}
			if (!out) {
				cskh.alert(alertBox, 'warning', cskh.t('Please select a contact outcome.'))
				return
			}
			if (!occurredLocal) {
				cskh.alert(alertBox, 'warning', cskh.t('Please enter the contact timestamp.'))
				return
			}
			if (out.requires_note && !note) {
				cskh.alert(
					alertBox,
					'warning',
					cskh.t('A contact note is required for the selected outcome.'),
					'[NOTE_REQUIRED]'
				)
				return
			}
			if (out.requires_followup && !followupLocal) {
				cskh.alert(
					alertBox,
					'warning',
					cskh.t('A follow-up callback time is required for the selected outcome.'),
					'[FOLLOWUP_REQUIRED]'
				)
				return
			}
			if (shouldClose && out.requires_completion_reason && !completionReason && !note) {
				cskh.alert(
					alertBox,
					'warning',
					cskh.t('Please enter a completion reason to close this work item.')
				)
				return
			}

			const payload = {
				work_item: workItem.work_item_id,
				channel: channel,
				outcome_code: out.code,
				occurred_at: occurredUserEdited ? cskh.datetimeLocalToServer(occurredLocal) : null,
				note: note,
				call_log: channel === 'phone_call' && callLogSelect.value ? callLogSelect.value : null,
				followup: followupLocal
					? {
							due_at: cskh.datetimeLocalToServer(followupLocal),
							title:
								followupTitle ||
								cskh.t('Callbacks schedule') +
									(workItem.display_name ? ' — ' + workItem.display_name : ''),
							description: note || followupTitle || '',
						}
					: null,
				complete_open_followup: Boolean(callbackTaskId || workItem.open_callback_task_id),
				close_work_item: Boolean(shouldClose),
				completion_reason: shouldClose
					? completionReason || note || out.label || out.code
					: null,
				expected_revision: workItem.revision,
				idempotency_key: pendingKey,
			}
			if (workItem.policy_version) {
				payload.expected_policy_version = workItem.policy_version
			}

			inFlight = true
			submitBtn.disabled = true
			submitBtn.textContent = cskh.t('Saving...')
			try {
				const res = await cskhP2Api.recordAttempt(payload)
				inFlight = false
				pendingKey = 'ui-att-' + cskh.uuid()
				cskh.toast('success', cskh.t('Contact result recorded successfully.'))
				if (typeof options.onSaved === 'function') {
					await options.onSaved(res)
				}
			} catch (error) {
				inFlight = false
				const code = error && error.code ? error.code : 'ERROR'
				cskh.renderError(alertBox, error, {
					showBackLink: true,
					onSafeRetry: function () {
						handleSubmit()
					},
					onReloadWorkItem: async function () {
						if (typeof options.onReloadWorkItem === 'function') {
							const refreshed = await options.onReloadWorkItem()
							if (refreshed) {
								workItem = refreshed
								headerRevEl.textContent =
									cskh.t('Revision') +
									': v' +
									workItem.revision +
									' · ' +
									cskh.effectiveTimeZone()
								populateChannels()
								populateOutcomes()
								pendingKey = 'ui-att-' + cskh.uuid()
								cskh.clear(alertBox)
								setInputsLocked(false)
							}
						}
					},
					onReloadPolicy: async function () {
						if (typeof options.onReloadPolicy === 'function') {
							const fresh = await options.onReloadPolicy()
							if (fresh) {
								if (Array.isArray(fresh)) {
									outcomes = fresh.filter(function (o) {
										return o && o.enabled !== false
									})
								} else {
									if (Array.isArray(fresh.outcomes)) {
										outcomes = fresh.outcomes.filter(function (o) {
											return o && o.enabled !== false
										})
									}
									if (fresh.workItem) {
										workItem = fresh.workItem
									}
								}
								headerRevEl.textContent =
									cskh.t('Revision') +
									': v' +
									workItem.revision +
									' · ' +
									cskh.effectiveTimeZone()
								populateChannels()
								populateOutcomes()
								pendingKey = 'ui-att-' + cskh.uuid()
								cskh.clear(alertBox)
							}
						}
					},
				})
				if (
					code === 'OWNERSHIP_CHANGED' ||
					code === 'FORBIDDEN' ||
					code === 'WORK_ITEM_NOT_ACTIVE' ||
					code === 'DO_NOT_CONTACT'
				) {
					setInputsLocked(true)
					return
				}
			} finally {
				if (!lockedByConflict) {
					submitBtn.disabled = false
				}
				submitBtn.textContent = cskh.t('Save contact result')
			}
		}

		;[
			channelSelect,
			outcomeSelect,
			occurredInput,
			noteInput,
			followupToggle,
			followupInput,
			followupTitleInput,
			callLogSelect,
			closeToggle,
			completionReasonInput,
		].forEach(function (ctrl) {
			ctrl.addEventListener('change', bumpIdempotencyKey)
			ctrl.addEventListener('input', bumpIdempotencyKey)
		})

		channelSelect.addEventListener('change', populateOutcomes)
		outcomeSelect.addEventListener('change', syncOutcomeConstraints)
		occurredInput.addEventListener('input', function () {
			occurredUserEdited = true
		})
		occurredInput.addEventListener('change', function () {
			occurredUserEdited = true
		})
		followupToggle.addEventListener('change', function () {
			followupInput.disabled = !followupToggle.checked
			followupTitleInput.disabled = !followupToggle.checked
			if (followupToggle.checked && !followupInput.value) {
				followupInput.value = defaultFollowupLocal()
			}
		})
		submitBtn.addEventListener('click', handleSubmit)

		const headerRevEl = cskh.el('span', {
			class: 'cskh-muted cskh-mono',
			text:
				cskh.t('Revision') +
				': v' +
				workItem.revision +
				' · ' +
				cskh.effectiveTimeZone(),
		})

		const headerChildren = [
			cskh.el('h3', {
				style: 'margin: 0;',
				text:
					cskh.t('Quick record contact result') +
					(workItem.display_name || workItem.lead
						? ' — ' + (workItem.display_name || workItem.lead)
						: ''),
			}),
			headerRevEl,
		]

		const activeOpenTask = callbackTaskId || workItem.open_callback_task_id
		const bodyNodes = [
			cskh.el(
				'div',
				{
					class: 'cskh-actions',
					style: 'justify-content: space-between; margin-bottom: 10px;',
				},
				headerChildren
			),
			activeOpenTask
				? cskh.el('div', { class: 'cskh-alert is-info' }, [
						cskh.el('span', {
							text: cskh.t('Completing callback task') + ': ' + activeOpenTask,
						}),
					])
				: null,
			alertBox,
			cskh.el('div', { class: 'cskh-form-grid' }, [
				cskh.el('label', { class: 'cskh-field' }, [
					cskh.el('span', { text: cskh.t('Contact channel') + ' *' }),
					channelSelect,
				]),
				cskh.el('label', { class: 'cskh-field' }, [
					cskh.el('span', { text: cskh.t('Contact outcome') + ' *' }),
					outcomeSelect,
				]),
				cskh.el('label', { class: 'cskh-field' }, [
					cskh.el('span', {
						text:
							cskh.t('Occurred at') +
							' (' +
							cskh.effectiveTimeZone() +
							') *',
					}),
					occurredInput,
				]),
				callLogField,
			]),
			semanticBar,
			cskh.el('label', { class: 'cskh-field', style: 'margin-bottom: 10px;' }, [
				noteLabelSpan,
				noteInput,
			]),
			followupWrap,
			closeWrap,
			cskh.el('div', { class: 'cskh-actions', style: 'margin-top: 12px;' }, [
				submitBtn,
				cancelBtn,
			]),
		]

		container.appendChild(cskh.el('div', { class: 'cskh-panel' }, bodyNodes))
		populateChannels()
		populateOutcomes()

		return {
			updateWorkItem: function (nextWi) {
				workItem = nextWi
				headerRevEl.textContent =
					cskh.t('Revision') +
					': v' +
					workItem.revision +
					' · ' +
					cskh.effectiveTimeZone()
				pendingKey = 'ui-att-' + cskh.uuid()
			},
		}
	}

	return {
		mount: mount,
	}
})()
