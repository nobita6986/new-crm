/* Callbacks Schedule / List (`/cskh/callbacks`, P2 WP08).

   Lists scoped `CRM Task` callbacks (`cskh_is_followup = 1`) classified by
   site/user timezone (`overdue`, `due_today`, `upcoming`, `done`, `canceled`),
   and provides single-API atomic completion (`record_attempt` with
   `complete_open_followup = true`), rescheduling (`schedule_followup` with
   mandatory reason), and cancellation (`complete_followup` with mandatory
   reason, explicitly marked as NOT connected).
*/
(function () {
	'use strict'

	const PAGE_SIZE = 20
	const caps = cskh.capabilities() || {}
	const allowPii = !!caps.pii
	const canFilterAssignee = !!(caps.admin || caps.leader)

	const pager = cskh.pager({
		pageSize: PAGE_SIZE,
		range: document.getElementById('cb-range'),
		prev: document.getElementById('cb-prev'),
		next: document.getElementById('cb-next'),
	})
	const reads = cskh.sequencer()
	let cachedOutcomes = null

	const assigneeWrap = document.getElementById('cb-assignee-wrap')
	if (assigneeWrap && canFilterAssignee) assigneeWrap.hidden = false

	function mapBucketQuery(bucketKey) {
		const sameTz = cskh.effectiveTimeZone() === cskh.siteTimeZone()
		switch (bucketKey) {
			case 'due_today':
				return { status: 'open', bucket: sameTz ? 'today' : 'all', clientBucket: sameTz ? '' : 'due_today' }
			case 'overdue':
				return { status: 'open', bucket: 'overdue', clientBucket: '' }
			case 'upcoming':
				return { status: 'open', bucket: sameTz ? 'upcoming' : 'all', clientBucket: sameTz ? '' : 'upcoming' }
			case 'done':
				return { status: 'done', bucket: 'all', clientBucket: '' }
			case 'canceled':
				return { status: 'canceled', bucket: 'all', clientBucket: '' }
			case 'open':
			default:
				return { status: 'open', bucket: 'all', clientBucket: '' }
		}
	}

	function syncChips(selected) {
		const bar = document.getElementById('cb-chips')
		if (!bar) return
		Array.from(bar.querySelectorAll('.cskh-chip')).forEach(function (btn) {
			if ((btn.getAttribute('data-bucket') || 'open') === selected) {
				btn.classList.add('is-active')
			} else {
				btn.classList.remove('is-active')
			}
		})
	}

	function closeActionCard() {
		const card = document.getElementById('cb-action-card')
		const body = document.getElementById('cb-action-body')
		if (card) card.hidden = true
		cskh.clear(body)
	}

	async function openRecordAndComplete(task) {
		const card = document.getElementById('cb-action-card')
		const body = document.getElementById('cb-action-body')
		const alertBox = document.getElementById('cb-alert')
		cskh.clear(body)
		card.hidden = false
		body.appendChild(cskh.el('p', { class: 'cskh-loading', text: cskh.t('Loading...') }))
		card.scrollIntoView({ behavior: 'smooth', block: 'nearest' })

		try {
			const res = await Promise.all([
				cskhP2Api.getWorkItem({ work_item: task.work_item_id }),
				cachedOutcomes
					? Promise.resolve({ items: cachedOutcomes })
					: cskhP2Api.getOutcomeCodes({ work_item: task.work_item_id }),
			])
			const wi = res[0]
			const outList = (res[1] && res[1].items) || []
			if (!cachedOutcomes && outList.length) cachedOutcomes = outList

			cskhAttemptForm.mount(body, {
				workItem: wi,
				outcomes: outList,
				completeOpenFollowup: true,
				callbackTaskId: task.task_id,
				onCancel: closeActionCard,
				onSaved: async function () {
					closeActionCard()
					await load()
				},
				onReloadWorkItem: async function () {
					const fresh = await cskhP2Api.getWorkItem({ work_item: task.work_item_id })
					await load()
					return fresh
				},
				onReloadPolicy: async function () {
					const outRes = await cskhP2Api.getOutcomeCodes({ work_item: task.work_item_id })
					cachedOutcomes = outRes.items || []
					const freshWi = await cskhP2Api.getWorkItem({ work_item: task.work_item_id })
					return { outcomes: cachedOutcomes, workItem: freshWi }
				},
			})
		} catch (err) {
			closeActionCard()
			cskh.renderError(alertBox, err, { onReloadWorkItem: load })
			load()
		}
	}

	async function openReschedule(task) {
		const card = document.getElementById('cb-action-card')
		const body = document.getElementById('cb-action-body')
		const alertBox = document.getElementById('cb-alert')
		cskh.clear(body)
		card.hidden = false
		body.appendChild(cskh.el('p', { class: 'cskh-loading', text: cskh.t('Loading...') }))

		let wi
		try {
			wi = await cskhP2Api.getWorkItem({ work_item: task.work_item_id })
		} catch (err) {
			closeActionCard()
			cskh.renderError(alertBox, err, { onReloadWorkItem: load })
			return
		}

		cskh.clear(body)
		let reqKey = 'ui-cb-resched-' + cskh.uuid()
		let inFlight = false
		const formAlert = cskh.el('div', { 'aria-live': 'polite' })
		const dueInput = cskh.el('input', {
			class: 'cskh-input',
			id: 'cb-resched-due',
			type: 'datetime-local',
			value: cskh.toDatetimeLocalValue(
				task.due_at || new Date(cskh.serverNow().getTime() + 24 * 3600 * 1000)
			),
		})
		const reasonInput = cskh.el('input', {
			class: 'cskh-input',
			id: 'cb-resched-reason',
			type: 'text',
			placeholder: cskh.t('Reschedule reason'),
		})
		;[dueInput, reasonInput].forEach(function (el) {
			el.addEventListener('input', function () {
				if (!inFlight) reqKey = 'ui-cb-resched-' + cskh.uuid()
			})
			el.addEventListener('change', function () {
				if (!inFlight) reqKey = 'ui-cb-resched-' + cskh.uuid()
			})
		})

		const saveBtn = cskh.el('button', {
			class: 'cskh-btn is-primary',
			id: 'cb-resched-submit',
			type: 'button',
			text: cskh.t('Save callback'),
		})

		async function submitReschedule() {
			if (inFlight) return
			cskh.clear(formAlert)
			const dueVal = dueInput.value
			const reasonVal = reasonInput.value.trim()
			if (!dueVal) {
				cskh.alert(
					formAlert,
					'warning',
					cskh.t('A follow-up callback time is required for the selected outcome.')
				)
				return
			}
			if (!reasonVal) {
				cskh.alert(
					formAlert,
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
					due_at: cskh.datetimeLocalToServer(dueVal),
					title: task.title || cskh.t('Callbacks schedule'),
					note: task.description || '',
					reason: reasonVal,
					expected_revision: wi.revision,
					idempotency_key: reqKey,
				})
				inFlight = false
				cskh.toast('success', cskh.t('Callback scheduled successfully.'))
				closeActionCard()
				await load()
			} catch (err) {
				inFlight = false
				cskh.renderError(formAlert, err, {
					onSafeRetry: submitReschedule,
					onReloadWorkItem: async function () {
						await load()
						closeActionCard()
					},
				})
			} finally {
				saveBtn.disabled = false
			}
		}

		saveBtn.addEventListener('click', submitReschedule)

		body.appendChild(
			cskh.el('div', { class: 'cskh-panel' }, [
				cskh.el('h3', {
					style: 'margin-top: 0;',
					text: cskh.t('Reschedule') + ' — #' + task.task_id,
				}),
				formAlert,
				cskh.el('div', { class: 'cskh-form-grid' }, [
					cskh.el('label', { class: 'cskh-field' }, [
						cskh.el('span', {
							text: cskh.t('Callback due at') + ' (' + cskh.effectiveTimeZone() + ') *',
						}),
						dueInput,
					]),
					cskh.el('label', { class: 'cskh-field' }, [
						cskh.el('span', { text: cskh.t('Reschedule reason') + ' *' }),
						reasonInput,
					]),
				]),
				cskh.el('div', { class: 'cskh-actions', style: 'margin-top: 12px;' }, [
					saveBtn,
					cskh.el('button', {
						class: 'cskh-btn',
						type: 'button',
						text: cskh.t('Cancel'),
						onclick: closeActionCard,
					}),
				]),
			])
		)
	}

	async function openCancel(task) {
		const card = document.getElementById('cb-action-card')
		const body = document.getElementById('cb-action-body')
		const alertBox = document.getElementById('cb-alert')
		cskh.clear(body)
		card.hidden = false
		body.appendChild(cskh.el('p', { class: 'cskh-loading', text: cskh.t('Loading...') }))

		let wi
		try {
			wi = await cskhP2Api.getWorkItem({ work_item: task.work_item_id })
		} catch (err) {
			closeActionCard()
			cskh.renderError(alertBox, err, { onReloadWorkItem: load })
			return
		}

		cskh.clear(body)
		let reqKey = 'ui-cb-cancel-' + cskh.uuid()
		let inFlight = false
		const formAlert = cskh.el('div', { 'aria-live': 'polite' })
		const reasonInput = cskh.el('textarea', {
			class: 'cskh-textarea',
			id: 'cb-cancel-reason',
			placeholder: cskh.t('Cancel reason'),
		})
		reasonInput.addEventListener('input', function () {
			if (!inFlight) reqKey = 'ui-cb-cancel-' + cskh.uuid()
		})

		const submitBtn = cskh.el('button', {
			class: 'cskh-btn is-danger',
			id: 'cb-cancel-submit',
			type: 'button',
			text: cskh.t('Close callback as canceled'),
		})

		async function submitCancel() {
			if (inFlight) return
			cskh.clear(formAlert)
			const reasonVal = reasonInput.value.trim()
			if (!reasonVal) {
				cskh.alert(
					formAlert,
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
					task_id: task.task_id,
					action: 'cancel',
					attempt_id: null,
					completion_reason: reasonVal,
					expected_revision: wi.revision,
					idempotency_key: reqKey,
				})
				inFlight = false
				cskh.toast('info', cskh.t('Callback canceled with audit reason (not counted as connected).'))
				closeActionCard()
				await load()
			} catch (err) {
				inFlight = false
				cskh.renderError(formAlert, err, {
					onSafeRetry: submitCancel,
					onReloadWorkItem: async function () {
						await load()
						closeActionCard()
					},
				})
			} finally {
				submitBtn.disabled = false
			}
		}

		submitBtn.addEventListener('click', submitCancel)

		body.appendChild(
			cskh.el('div', { class: 'cskh-panel' }, [
				cskh.el('h3', {
					style: 'margin-top: 0;',
					text: cskh.t('Cancel callback') + ' — #' + task.task_id,
				}),
				cskh.el('div', { class: 'cskh-banner-notice' }, [
					cskh.el('span', {
						text: cskh.t(
							'Task completion without a contact attempt never counts as connected or successful care.'
						),
					}),
				]),
				formAlert,
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
						onclick: closeActionCard,
					}),
				]),
			])
		)
	}

	function renderRow(task) {
		const dueKind = task.followup_state || cskh.classifyDueAt(task.due_at, task.status)
		const actions = []
		if (task.work_item_id) {
			actions.push(
				cskh.el('a', {
					class: 'cskh-btn is-small',
					href: '/cskh/work-item?id=' + encodeURIComponent(task.work_item_id),
					text: cskh.t('Care workspace'),
				})
			)
		}
		if (task.can_complete) {
			actions.push(
				cskh.el('button', {
					class: 'cskh-btn is-primary is-small',
					type: 'button',
					text: cskh.t('Record & complete'),
					onclick: function () {
						openRecordAndComplete(task)
					},
				})
			)
		}
		if (task.can_reschedule) {
			actions.push(
				cskh.el('button', {
					class: 'cskh-btn is-small',
					type: 'button',
					text: cskh.t('Reschedule'),
					onclick: function () {
						openReschedule(task)
					},
				})
			)
		}
		if (task.can_complete) {
			actions.push(
				cskh.el('button', {
					class: 'cskh-btn is-small',
					type: 'button',
					text: cskh.t('Cancel callback'),
					onclick: function () {
						openCancel(task)
					},
				})
			)
		}

		const linkedInfo = task.linked_attempt_id
			? '#' + task.linked_attempt_id
			: task.close_reason
				? task.close_reason
				: '—'

		return cskh.el('tr', {}, [
			cskh.el('td', {
				text: (allowPii && task.display_name) || task.lead || task.work_item_id || '—',
			}),
			cskh.el('td', {
				class: 'cskh-mono',
				text: allowPii && task.phone ? cskh.maskPhone(task.phone) : '—',
			}),
			cskh.el('td', { text: task.campaign_title || task.campaign || '—' }),
			cskh.el('td', { text: task.assigned_to || '—' }),
			cskh.el('td', { text: cskh.formatDate(task.due_at) }),
			cskh.el('td', {}, [
				cskh.el('div', { class: 'cskh-badge-stack' }, [
					cskh.el('span', {
						class: cskh.stateBadge(dueKind || 'open'),
						text: cskh.dueStateLabel(dueKind || 'open'),
					}),
				]),
			]),
			cskh.el('td', { text: linkedInfo }),
			cskh.el('td', {}, [cskh.el('div', { class: 'cskh-row-actions' }, actions)]),
		])
	}

	async function load() {
		const loading = document.getElementById('cb-loading')
		const empty = document.getElementById('cb-empty')
		const table = document.getElementById('cb-table')
		const rows = document.getElementById('cb-rows')
		const alertBox = document.getElementById('cb-alert')
		const bucketKey = document.getElementById('cb-bucket').value || 'open'
		const campaign = document.getElementById('cb-campaign').value
		const assigneeEl = document.getElementById('cb-assignee')
		const assignee = assigneeEl ? assigneeEl.value.trim() : ''

		loading.hidden = false
		empty.hidden = true
		table.hidden = true
		cskh.clear(rows)
		cskh.clear(alertBox)

		const mapped = mapBucketQuery(bucketKey)
		const query = {
			page: pager.page(),
			page_size: PAGE_SIZE,
			status: mapped.status,
			bucket: mapped.bucket,
			campaign: campaign,
		}
		if (assignee && canFilterAssignee) query.assignee = assignee

		const token = reads.next()
		try {
			const res = await cskhP2Api.listFollowups(query)
			if (token !== reads.current()) return
			const visibleItems = mapped.clientBucket
				? res.items.filter(function (t) {
						return (t.followup_state || cskh.classifyDueAt(t.due_at, t.status)) === mapped.clientBucket
				  })
				: res.items
			pager.setTotal(mapped.clientBucket ? visibleItems.length : res.total)
			pager.render()
			if (!visibleItems.length) {
				empty.hidden = false
				return
			}
			visibleItems.forEach(function (t) {
				rows.appendChild(renderRow(t))
			})
			table.hidden = false
		} catch (err) {
			if (token !== reads.current()) return
			cskh.renderError(alertBox, err)
		} finally {
			loading.hidden = true
		}
	}

	async function loadCampaigns() {
		if (!canFilterAssignee) return
		const select = document.getElementById('cb-campaign')
		try {
			const data = await cskh.get('crm_cskh.api.dashboard.campaigns')
			;((data && data.campaigns) || []).forEach(function (c) {
				select.appendChild(cskh.el('option', { value: c.campaign, text: c.title || c.code }))
			})
		} catch (_err) {
			/* optional */
		}
	}

	const bucketSelect = document.getElementById('cb-bucket')
	bucketSelect.addEventListener('change', function () {
		syncChips(bucketSelect.value)
		pager.goto(1)
		load()
	})
	document.getElementById('cb-chips').addEventListener('click', function (ev) {
		const btn = ev.target.closest('.cskh-chip')
		if (!btn) return
		const b = btn.getAttribute('data-bucket') || 'open'
		bucketSelect.value = b
		syncChips(b)
		pager.goto(1)
		load()
	})
	document.getElementById('cb-campaign').addEventListener('change', function () {
		pager.goto(1)
		load()
	})
	document.getElementById('cb-refresh').addEventListener('click', function () {
		pager.goto(1)
		load()
	})
	document.getElementById('cb-prev').addEventListener('click', function () {
		pager.goto(Math.max(1, pager.page() - 1))
		load()
	})
	document.getElementById('cb-next').addEventListener('click', function () {
		pager.goto(pager.page() + 1)
		load()
	})

	loadCampaigns()
	load()
})()
