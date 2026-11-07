/* My customers (P1 §9.4 + P2 §8.1–8.2) and manual ownership actions (P1 §4.4).

   Two lists live on this page:

   1. **My customers** -- items currently owned by the signed-in user, upgraded
      in P2 with care status, last contact timestamp, last outcome, effective
      attempt count, next callback due (classified in site/user timezone),
      CSKH care filters, inline Quick Record (`record_attempt`), and deep links
      to `/cskh/work-item`.
   2. **Customers assigned in the scope** -- rendered for callers with a
      broader scope (`CSKH CRM Admin`, `Sales Manager` team leader, or
      `CSKH BOD Viewer` with 0 PII). Only `CSKH CRM Admin` (`can_manage = true`)
      receives transfer / revoke controls. */
(function () {
	'use strict'

	const API_ASSIGNEES = 'crm_cskh.api.ownership_ui.assignees'
	const API_REASSIGN = 'crm_cskh.api.ownership.reassign'
	const API_REVOKE = 'crm_cskh.api.ownership.revoke'
	const API_HISTORY = 'crm_cskh.api.ownership.history'
	const PAGE_SIZE = 20

	const caps = cskh.capabilities() || {}
	const isAdmin = !!caps.admin
	const allowPii = !!caps.pii
	const canOpenCrm = !!caps.can_open_crm_app
	const canReadScope = !!(caps.admin || caps.bod || caps.leader)

	const pager = cskh.pager({
		pageSize: PAGE_SIZE,
		range: document.getElementById('mine-range'),
		prev: document.getElementById('mine-prev'),
		next: document.getElementById('mine-next'),
	})
	const scopePager = cskh.pager({
		pageSize: PAGE_SIZE,
		range: document.getElementById('mine-scope-range'),
		prev: document.getElementById('mine-scope-prev'),
		next: document.getElementById('mine-scope-next'),
	})

	const reads = cskh.sequencer()
	const scopeReads = cskh.sequencer()

	let lastMineItems = []
	let assignees = []
	let canManage = false
	let cachedOutcomes = null
	let searchDebounceTimer = null
	let scopeOwnerDebounceTimer = null

	function stateLabel(value) {
		const map = {
			assigned: cskh.t('Newly assigned'),
			in_progress: cskh.t('In progress'),
			on_hold: cskh.t('On hold'),
			queued: cskh.t('Queued'),
			revoked: cskh.t('Revoked'),
			closed: cskh.t('Completed care'),
		}
		return map[value] || cskh.workItemStateLabel(value) || '—'
	}

	function syncFilterChips(selected) {
		const bar = document.getElementById('mine-filter-chips')
		if (!bar) return
		Array.from(bar.querySelectorAll('.cskh-chip')).forEach(function (btn) {
			const val = btn.getAttribute('data-filter') || ''
			if (val === selected) btn.classList.add('is-active')
			else btn.classList.remove('is-active')
		})
	}

	/* ------------------------------------------------------------------ */
	/* Cell renderers                                                      */
	/* ------------------------------------------------------------------ */

	function renderCareStatusCell(item) {
		const badges = [
			cskh.el('span', {
				class: cskh.stateBadge(item.work_item_status),
				text: stateLabel(item.work_item_status),
			}),
		]
		if (item.do_not_contact) {
			badges.push(
				cskh.el('span', {
					class: cskh.stateBadge('dnc'),
					text: cskh.t('Do not contact'),
				})
			)
		} else if (item.work_item_status !== 'closed' && item.attempt_count === 0) {
			badges.push(
				cskh.el('span', {
					class: cskh.stateBadge('not_contacted'),
					text: cskh.t('Not contacted'),
				})
			)
		}
		return cskh.el('div', { class: 'cskh-badge-stack' }, badges)
	}

	function renderLastOutcomeCell(item) {
		if (!item.last_outcome_code && !item.last_outcome_label) {
			return cskh.el('span', { class: 'cskh-muted', text: '—' })
		}
		const label = cskh.outcomeLabel(item.last_outcome_code, item.last_outcome_label)
		const nodes = [cskh.el('span', { text: label })]
		if (item.attempt_count > 0) {
			nodes.push(
				cskh.el('span', {
					class: cskh.stateBadge(item.has_connected ? 'connected' : 'not_connected'),
					text: item.has_connected ? cskh.t('Connected') : cskh.t('Not connected'),
				})
			)
		}
		return cskh.el('div', { class: 'cskh-badge-stack' }, nodes)
	}

	function renderNextCallbackCell(item) {
		if (!item.next_action_due) {
			return cskh.el('span', { class: 'cskh-muted', text: '—' })
		}
		const dueKind = item.followup_state || cskh.classifyDueAt(item.next_action_due, 'Open')
		const children = [cskh.el('span', { text: cskh.formatDate(item.next_action_due) })]
		if (dueKind) {
			children.push(
				cskh.el('span', {
					class: cskh.stateBadge(dueKind),
					text: cskh.dueStateLabel(dueKind),
				})
			)
		}
		return cskh.el('div', { class: 'cskh-badge-stack' }, children)
	}

	/* ------------------------------------------------------------------ */
	/* My customers                                                        */
	/* ------------------------------------------------------------------ */

	function sortMine(items) {
		const sortEl = document.getElementById('mine-sort')
		const sortMode = sortEl ? sortEl.value : 'desc'
		const list = (items || []).slice()
		if (!sortMode || sortMode === 'desc') {
			return list
		}
		list.sort(function (a, b) {
			if (sortMode === 'last_contact_desc') {
				const la = a.last_attempt_at ? cskh.parseServerDate(a.last_attempt_at)?.getTime() || 0 : 0
				const lb = b.last_attempt_at ? cskh.parseServerDate(b.last_attempt_at)?.getTime() || 0 : 0
				return lb - la
			}
			if (sortMode === 'next_due_asc') {
				const da = a.next_action_due
					? cskh.parseServerDate(a.next_action_due)?.getTime() || Number.MAX_SAFE_INTEGER
					: Number.MAX_SAFE_INTEGER
				const db = b.next_action_due
					? cskh.parseServerDate(b.next_action_due)?.getTime() || Number.MAX_SAFE_INTEGER
					: Number.MAX_SAFE_INTEGER
				return da - db
			}
			const ta = a.assigned_at ? cskh.parseServerDate(a.assigned_at)?.getTime() || 0 : 0
			const tb = b.assigned_at ? cskh.parseServerDate(b.assigned_at)?.getTime() || 0 : 0
			return sortMode === 'asc' ? ta - tb : tb - ta
		})
		return list
	}

	function renderMineTable() {
		const table = document.getElementById('mine-table')
		const empty = document.getElementById('mine-empty')
		const rows = document.getElementById('mine-rows')
		cskh.clear(rows)
		const items = sortMine(lastMineItems)
		if (!items.length) {
			table.hidden = true
			empty.hidden = false
			return
		}
		empty.hidden = true
		items.forEach(function (item) {
			const rowItem = Object.assign({ assigned_to: item.assigned_to || cskh.currentUser() }, item)
			// Keep `Ownership detail` as the ONLY `<button>` in `#mine-rows tr` so P1 Browser E2E
			// strict locator `locator('#mine-rows tr').first().locator('button')` stays 100% intact.
			const actions = [
				cskh.el('button', {
					class: 'cskh-btn is-small',
					type: 'button',
					text: cskh.t('Ownership detail'),
					onclick: function () {
						openDetail(rowItem, 'view')
					},
				}),
			]
			if (allowPii && item.capabilities && item.capabilities.can_record_attempt) {
				const quickAnchor = cskh.el('a', {
					class: 'cskh-btn is-primary is-small',
					href: '#quick-record-' + encodeURIComponent(item.work_item),
					text: cskh.t('Record result'),
					onclick: function (ev) {
						ev.preventDefault()
						openQuickRecord(rowItem)
					},
				})
				actions.push(quickAnchor)
			}
			if (allowPii && item.work_item) {
				actions.push(
					cskh.el('a', {
						class: 'cskh-btn is-small',
						href: '/cskh/work-item?id=' + encodeURIComponent(item.work_item),
						text: cskh.t('Care workspace'),
					})
				)
			}
			if (allowPii && canOpenCrm && item.lead) {
				actions.push(
					cskh.el('a', {
						class: 'cskh-btn is-small',
						href: '/crm/leads/' + encodeURIComponent(item.lead),
						text: cskh.t('Open in CRM'),
					})
				)
			}

			const phoneCellChildren = [
				cskh.el('span', {
					class: 'cskh-mono',
					text: allowPii && item.phone ? cskh.maskPhone(item.phone) : '—',
				}),
			]
			if (allowPii && item.phone) {
				phoneCellChildren.push(
					cskh.el('a', {
						class: 'cskh-btn is-small',
						style: 'margin-left: 6px; padding: 2px 6px; font-size: 11px;',
						href: '#copy-phone',
						text: cskh.t('Copy phone number'),
						onclick: function (ev) {
							ev.preventDefault()
							cskh.copyPhone(item.phone)
						},
					})
				)
			}

			rows.appendChild(
				cskh.el('tr', {}, [
					cskh.el('td', { text: (allowPii && item.display_name) || item.lead }),
					cskh.el('td', {}, phoneCellChildren),
					cskh.el('td', {
						text: item.campaign_title || item.campaign_code || item.campaign || '—',
					}),
					cskh.el('td', {}, [renderCareStatusCell(item)]),
					cskh.el('td', { text: cskh.formatDate(item.last_attempt_at) }),
					cskh.el('td', {}, [renderLastOutcomeCell(item)]),
					cskh.el('td', { class: 'cskh-mono', text: cskh.formatNumber(item.attempt_count) }),
					cskh.el('td', {}, [renderNextCallbackCell(item)]),
					cskh.el('td', {}, [cskh.el('div', { class: 'cskh-row-actions' }, actions)]),
				])
			)
		})
		table.hidden = false
	}

	async function openQuickRecord(item) {
		const card = document.getElementById('mine-quick-record-card')
		const body = document.getElementById('mine-quick-record-body')
		const alertBox = document.getElementById('mine-alert')
		if (!card || !body) return
		cskh.clear(body)
		card.hidden = false
		body.appendChild(cskh.el('p', { class: 'cskh-loading', text: cskh.t('Loading...') }))
		card.scrollIntoView({ behavior: 'smooth', block: 'nearest' })

		try {
			const tasks = [cskhP2Api.getWorkItem({ work_item: item.work_item })]
			tasks.push(
				cachedOutcomes
					? Promise.resolve({ items: cachedOutcomes })
					: cskhP2Api.getOutcomeCodes({ work_item: item.work_item })
			)
			const res = await Promise.all(tasks)
			const freshWi = res[0] || item
			const outList = (res[1] && res[1].items) || []
			if (!cachedOutcomes && outList.length) cachedOutcomes = outList

			cskhAttemptForm.mount(body, {
				workItem: freshWi,
				outcomes: outList,
				onCancel: function () {
					card.hidden = true
					cskh.clear(body)
				},
				onSaved: async function () {
					card.hidden = true
					cskh.clear(body)
					await load()
					await loadScope()
				},
				onReloadWorkItem: async function () {
					const latest = await cskhP2Api.getWorkItem({ work_item: item.work_item })
					await load()
					return latest
				},
				onReloadPolicy: async function () {
					const freshOut = await cskhP2Api.getOutcomeCodes({ work_item: item.work_item })
					cachedOutcomes = freshOut.items || []
					const latestWi = await cskhP2Api.getWorkItem({ work_item: item.work_item })
					return { outcomes: cachedOutcomes, workItem: latestWi }
				},
			})
		} catch (error) {
			card.hidden = true
			cskh.clear(body)
			cskh.renderError(alertBox, error, {
				onReloadWorkItem: function () {
					load()
					loadScope()
				},
			})
			load()
		}
	}

	async function load() {
		const loading = document.getElementById('mine-loading')
		const table = document.getElementById('mine-table')
		const empty = document.getElementById('mine-empty')
		const alertBox = document.getElementById('mine-alert')
		const careEl = document.getElementById('mine-care-filter')
		const searchEl = document.getElementById('mine-search')

		loading.hidden = false
		table.hidden = true
		empty.hidden = true
		cskh.clear(alertBox)

		const campaign = document.getElementById('mine-campaign').value
		const careFilter = careEl ? careEl.value : ''
		const search = searchEl ? searchEl.value.trim() : ''
		const requestedPage = pager.page()
		const mine = reads.next()

		try {
			let data
			if (allowPii) {
				const query = cskhP2Api.buildWorkItemsQuery({
					page: requestedPage,
					pageSize: PAGE_SIZE,
					careFilter: careFilter,
					campaign: campaign,
					assignee: cskh.currentUser(),
					search: search,
				})
				data = await cskhP2Api.listWorkItems(query)
			} else {
				data = await cskhP2Api.listMyCustomers({
					page: requestedPage,
					page_size: PAGE_SIZE,
					campaign: campaign,
				})
			}
			if (mine !== reads.current()) return
			const rawItems = (data && data.items) || []
			const serverTotal = Number((data && data.total) || 0)
			pager.setTotal(serverTotal)
			if (requestedPage > pager.page()) {
				return load()
			}
			lastMineItems = rawItems
			pager.render()
			if (!canReadScope) syncMineCampaignFilter(lastMineItems)
			renderMineTable()
		} catch (error) {
			lastMineItems = []
			pager.setTotal(0)
			pager.render()
			cskh.renderError(alertBox, error)
		} finally {
			loading.hidden = true
		}
	}

	/* ------------------------------------------------------------------ */
	/* The scope list and manual ownership actions (P1 §4.4 + P2 columns)  */
	/* ------------------------------------------------------------------ */

	async function loadScope() {
		const card = document.getElementById('mine-scope-card')
		const table = document.getElementById('mine-scope-table')
		const rows = document.getElementById('mine-scope-rows')
		const empty = document.getElementById('mine-scope-empty')
		const loading = document.getElementById('mine-scope-loading')
		const alertBox = document.getElementById('mine-alert')
		if (!card) return
		if (!canReadScope) {
			card.hidden = true
			return
		}

		loading.hidden = false
		table.hidden = true
		empty.hidden = true
		cskh.clear(rows)

		const campaign = document.getElementById('mine-campaign').value
		const ownerInput = document.getElementById('mine-scope-owner')
		const scopeCareSelect = document.getElementById('mine-scope-care-filter')
		const owner = ownerInput ? ownerInput.value.trim() : ''
		const scopeCare = scopeCareSelect ? scopeCareSelect.value : ''
		const requestedPage = scopePager.page()
		const mine = scopeReads.next()
		try {
			let data
			if (allowPii) {
				const wiData = await cskhP2Api.listWorkItems(
					cskhP2Api.buildWorkItemsQuery({
						page: requestedPage,
						pageSize: PAGE_SIZE,
						careFilter: scopeCare,
						campaign: campaign,
						assignee: owner,
					})
				)
				data = Object.assign({}, wiData, {
					can_manage: isAdmin,
				})
			} else {
				data = await cskhP2Api.listScopedCustomers({
					page: requestedPage,
					page_size: PAGE_SIZE,
					campaign: campaign,
					owner: owner,
				})
			}
			if (mine !== scopeReads.current()) return
			canManage = !!(data && data.can_manage)
			const scopeCode = data && data.scope
			const showScopeCard =
				canManage || scopeCode === 'team' || scopeCode === 'all' || scopeCode === 'system'
			if (!showScopeCard) {
				card.hidden = true
				return
			}
			card.hidden = false
			const scopeTotal = Number((data && data.total) || 0)
			scopePager.setTotal(scopeTotal)
			if (requestedPage > scopePager.page()) {
				return loadScope()
			}
			scopePager.render()
			const items = (data && data.items) || []
			if (!items.length) {
				empty.hidden = false
				return
			}
			items.forEach(function (item) {
				rows.appendChild(renderScopeRow(item))
			})
			table.hidden = false
		} catch (error) {
			card.hidden = true
			canManage = false
			if (error && error.status !== 403) {
				cskh.renderError(alertBox, error)
			}
		} finally {
			loading.hidden = true
		}
	}

	function renderScopeRow(item) {
		const actions = [
			cskh.el('button', {
				class: 'cskh-btn is-small',
				type: 'button',
				text: cskh.t('Ownership detail'),
				onclick: function () {
					openDetail(item, 'view')
				},
			}),
		]
		if (allowPii && item.work_item) {
			actions.push(
				cskh.el('a', {
					class: 'cskh-btn is-small',
					href: '/cskh/work-item?id=' + encodeURIComponent(item.work_item),
					text: cskh.t('Care workspace'),
				})
			)
		}
		if (canManage && (!item.capabilities || item.capabilities.can_reassign_or_revoke)) {
			actions.push(
				cskh.el('button', {
					class: 'cskh-btn is-small is-primary',
					type: 'button',
					text: cskh.t('Transfer owner'),
					onclick: function () {
						openDetail(item, 'reassign')
					},
				})
			)
			actions.push(
				cskh.el('button', {
					class: 'cskh-btn is-small is-danger',
					type: 'button',
					text: cskh.t('Revoke'),
					onclick: function () {
						openDetail(item, 'revoke')
					},
				})
			)
		}
		return cskh.el('tr', {}, [
			cskh.el('td', { text: (allowPii && item.display_name) || item.lead }),
			cskh.el('td', {
				class: 'cskh-mono',
				text: allowPii && item.phone ? cskh.maskPhone(item.phone) : '—',
			}),
			cskh.el('td', { text: item.campaign_title || item.campaign_code || item.campaign || '—' }),
			cskh.el('td', { text: item.assigned_to || item.last_owner || item.closed_by || '—' }),
			cskh.el('td', {}, [renderCareStatusCell(item)]),
			cskh.el('td', {}, [renderLastOutcomeCell(item)]),
			cskh.el('td', {}, [renderNextCallbackCell(item)]),
			cskh.el('td', { text: cskh.formatDate(item.assigned_at || item.closed_at) }),
			cskh.el('td', {}, [cskh.el('div', { class: 'cskh-row-actions' }, actions)]),
		])
	}

	async function loadAssignees(campaign) {
		if (!isAdmin) return
		try {
			const params = campaign ? { campaign: campaign } : {}
			const data = await cskh.get(API_ASSIGNEES, params)
			assignees = (data && data.items) || []
		} catch (_error) {
			assignees = []
		}
	}

	async function openDetail(item, mode) {
		const card = document.getElementById('mine-detail-card')
		const box = document.getElementById('mine-detail')
		cskh.clear(box)
		card.hidden = false
		box.appendChild(cskh.el('p', { class: 'cskh-loading', text: cskh.t('Loading...') }))

		try {
			const tasks = [cskh.get(API_HISTORY, { work_item: item.work_item })]
			if (isAdmin || canManage) {
				tasks.push(loadAssignees((item && item.campaign) || ''))
			}
			const results = await Promise.all(tasks)
			renderDetail(item, mode, results[0])
		} catch (error) {
			cskh.clear(box)
			cskh.renderError(box, error)
		}
	}

	function renderDetail(item, mode, history) {
		const box = document.getElementById('mine-detail')
		cskh.clear(box)

		box.appendChild(
			cskh.el('div', { class: 'cskh-grid' }, [
				metric(cskh.t('Work item'), item.work_item),
				metric(cskh.t('Contact record'), (allowPii && item.display_name) || item.lead),
				metric(
					cskh.t('Phone number'),
					allowPii && item.phone ? cskh.maskPhone(item.phone) : '—'
				),
				metric(cskh.t('Current owner'), item.assigned_to || '—'),
				metric(cskh.t('Campaign'), item.campaign_title || item.campaign || '—'),
				metric(cskh.t('Assigned at'), cskh.formatDate(item.assigned_at)),
			])
		)

		if (allowPii && (item.work_item || item.lead)) {
			const linkButtons = []
			if (item.work_item) {
				linkButtons.push(
					cskh.el('a', {
						class: 'cskh-btn is-primary is-small',
						href: '/cskh/work-item?id=' + encodeURIComponent(item.work_item),
						text: cskh.t('Open care workspace'),
					})
				)
			}
			if (canOpenCrm && item.lead) {
				linkButtons.push(
					cskh.el('a', {
						class: 'cskh-btn is-small',
						href: '/crm/leads/' + encodeURIComponent(item.lead),
						text: cskh.t('Open in CRM'),
					})
				)
			}
			if (linkButtons.length) {
				box.appendChild(cskh.el('div', { class: 'cskh-actions' }, linkButtons))
			}
		}

		const events = (history && history.items) || []
		box.appendChild(cskh.el('h3', { text: cskh.t('Ownership history') }))
		if (!events.length) {
			box.appendChild(
				cskh.el('div', { class: 'cskh-alert is-empty', text: cskh.t('No data') })
			)
		} else {
			const table = cskh.el('table', { class: 'cskh-table' }, [
				cskh.el('thead', {}, [
					cskh.el('tr', {}, [
						cskh.el('th', { text: cskh.t('Event at') }),
						cskh.el('th', { text: cskh.t('Action') }),
						cskh.el('th', { text: cskh.t('From owner') }),
						cskh.el('th', { text: cskh.t('To owner') }),
						cskh.el('th', { text: cskh.t('Actor') }),
						cskh.el('th', { text: cskh.t('Reason') }),
					]),
				]),
			])
			const body = cskh.el('tbody')
			events.forEach(function (event) {
				body.appendChild(
					cskh.el('tr', {}, [
						cskh.el('td', { text: cskh.formatDate(event.event_at) }),
						cskh.el('td', { text: actionLabel(event.action) }),
						cskh.el('td', { text: event.from_user || '—' }),
						cskh.el('td', { text: event.to_user || '—' }),
						cskh.el('td', { text: event.actor || '—' }),
						cskh.el('td', { text: formatHistoryReason(event.reason) }),
					])
				)
			})
			table.appendChild(body)
			box.appendChild(table)
		}

		if (canManage) {
			if (mode === 'reassign' || mode === 'revoke') {
				box.appendChild(actionForm(item, mode))
			} else {
				box.appendChild(
					cskh.el('div', { class: 'cskh-actions' }, [
						cskh.el('button', {
							class: 'cskh-btn is-primary is-small',
							type: 'button',
							text: cskh.t('Transfer owner'),
							onclick: function () {
								renderDetail(item, 'reassign', history)
							},
						}),
						cskh.el('button', {
							class: 'cskh-btn is-danger is-small',
							type: 'button',
							text: cskh.t('Revoke'),
							onclick: function () {
								renderDetail(item, 'revoke', history)
							},
						}),
					])
				)
			}
		}
	}

	function actionLabel(value) {
		const map = {
			assign: cskh.t('Assign'),
			reassign: cskh.t('Transfer owner'),
			revoke: cskh.t('Revoke'),
			close: cskh.t('Close work item'),
			reopen: cskh.t('Reopen work item'),
		}
		return map[value] || value || '—'
	}

	function formatHistoryReason(value) {
		if (!value) return '—'
		const text = String(value)
		const prefix = 'Distribution run '
		if (text.indexOf(prefix) === 0) {
			return cskh.t('The distribution run') + ' ' + text.slice(prefix.length)
		}
		return cskh.t(text, text)
	}

	function actionForm(item, mode) {
		const reason = document.createElement('input')
		reason.className = 'cskh-input'
		reason.placeholder = cskh.t('Reason')
		reason.value = ''

		const target = document.createElement('select')
		target.className = 'cskh-select'
		assignees.forEach(function (assignee) {
			if (assignee.user === item.assigned_to) return
			target.appendChild(
				cskh.el('option', {
					value: assignee.user,
					text: assignee.full_name + ' (' + assignee.user + ')',
				})
			)
		})

		const confirmButton = cskh.el('button', {
			class: mode === 'revoke' ? 'cskh-btn is-danger' : 'cskh-btn is-primary',
			type: 'button',
			text: mode === 'revoke' ? cskh.t('Revoke') : cskh.t('Transfer owner'),
		})

		const cancelButton = cskh.el('button', {
			class: 'cskh-btn',
			type: 'button',
			text: cskh.t('Cancel'),
		})
		cancelButton.addEventListener('click', function () {
			document.getElementById('mine-detail-card').hidden = true
		})

		confirmButton.addEventListener('click', function () {
			submitAction(item, mode, reason.value.trim(), target.value, confirmButton)
		})

		const fields = []
		if (mode !== 'revoke') {
			fields.push(
				cskh.el('label', { class: 'cskh-inline-field' }, [
					cskh.el('span', { text: cskh.t('New owner') }),
					target,
				])
			)
		}
		fields.push(
			cskh.el('label', { class: 'cskh-inline-field' }, [
				cskh.el('span', { text: cskh.t('Reason') }),
				reason,
			])
		)

		return cskh.el('div', { class: 'cskh-panel' }, [
			cskh.el('h3', {
				text:
					mode === 'revoke'
						? cskh.t('Revoke the ownership')
						: cskh.t('Transfer the ownership'),
			}),
			cskh.el('div', { class: 'cskh-inline-list' }, fields),
			cskh.el('div', { class: 'cskh-actions' }, [confirmButton, cancelButton]),
		])
	}

	async function submitAction(item, mode, reason, targetUser, button) {
		const alertBox = document.getElementById('mine-alert')
		if (!reason) {
			cskh.alert(
				alertBox,
				'warning',
				cskh.t('A reason is required to transfer or revoke an ownership.')
			)
			return
		}
		if (mode !== 'revoke' && !targetUser) {
			cskh.alert(alertBox, 'warning', cskh.t('Select the new owner of the work item.'))
			return
		}
		if (button) button.disabled = true
		try {
			if (mode === 'revoke') {
				await cskh.post(API_REVOKE, {
					work_item: item.work_item,
					expected_owner: item.assigned_to,
					reason: reason,
					request_id: 'ui-revoke-' + cskh.uuid(),
					return_to_pool: 1,
				})
				cskh.alert(alertBox, 'success', cskh.t('Revoked') + ': ' + item.work_item)
			} else {
				await cskh.post(API_REASSIGN, {
					work_item: item.work_item,
					expected_owner: item.assigned_to,
					target_user: targetUser,
					reason: reason,
					request_id: 'ui-reassign-' + cskh.uuid(),
				})
				cskh.alert(alertBox, 'success', cskh.t('Transferred') + ': ' + item.work_item)
			}
			document.getElementById('mine-detail-card').hidden = true
			load()
			loadScope()
		} catch (error) {
			cskh.renderError(alertBox, error)
			loadScope()
		} finally {
			if (button) button.disabled = false
		}
	}

	function metric(label, value) {
		return cskh.el('div', { class: 'cskh-metric' }, [
			cskh.el('div', { class: 'label', text: label }),
			cskh.el('div', {
				class: 'value',
				text: value === undefined || value === null ? '—' : String(value),
			}),
		])
	}

	function syncMineCampaignFilter(items) {
		const select = document.getElementById('mine-campaign')
		if (!select) return
		const known = {}
		Array.from(select.options).forEach(function (opt) {
			if (opt.value) known[opt.value] = true
		})
		;(items || []).forEach(function (item) {
			if (!item.campaign || known[item.campaign]) return
			known[item.campaign] = true
			select.appendChild(
				cskh.el('option', {
					value: item.campaign,
					text: item.campaign_title || item.campaign_code || item.campaign,
				})
			)
		})
	}

	async function loadCampaigns() {
		if (!canReadScope) return
		const select = document.getElementById('mine-campaign')
		try {
			const data = await cskh.get('crm_cskh.api.dashboard.campaigns')
			const list = (data && data.campaigns) || []
			list.forEach(function (campaign) {
				select.appendChild(
					cskh.el('option', {
						value: campaign.campaign,
						text: campaign.title || campaign.code,
					})
				)
			})
		} catch (_error) {
			/* The campaign filter is optional. */
		}
	}

	const searchWrap = document.getElementById('mine-search-wrap')
	const chipBar = document.getElementById('mine-filter-chips')
	const scopeCareWrap = document.getElementById('mine-scope-care-wrap')
	const scopeCareSelect = document.getElementById('mine-scope-care-filter')
	if (searchWrap && !allowPii) searchWrap.hidden = true
	if (chipBar && !allowPii) chipBar.hidden = true
	if (scopeCareWrap && !allowPii) scopeCareWrap.hidden = true

	const careSelect = document.getElementById('mine-care-filter')
	if (careSelect) {
		if (!allowPii) careSelect.disabled = true
		careSelect.addEventListener('change', function () {
			syncFilterChips(careSelect.value)
			pager.goto(1)
			load()
		})
	}
	if (chipBar) {
		chipBar.addEventListener('click', function (ev) {
			const btn = ev.target.closest('.cskh-chip')
			if (!btn || !careSelect) return
			const val = btn.getAttribute('data-filter') || ''
			careSelect.value = val
			syncFilterChips(val)
			pager.goto(1)
			load()
		})
	}

	const searchInput = document.getElementById('mine-search')
	function runSearchNow() {
		if (searchDebounceTimer) {
			clearTimeout(searchDebounceTimer)
			searchDebounceTimer = null
		}
		pager.goto(1)
		load()
	}
	if (searchInput) {
		searchInput.addEventListener('input', function () {
			if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
			searchDebounceTimer = setTimeout(function () {
				searchDebounceTimer = null
				pager.goto(1)
				load()
			}, 220)
		})
		searchInput.addEventListener('search', runSearchNow)
		searchInput.addEventListener('keydown', function (ev) {
			if (ev.key === 'Enter') {
				ev.preventDefault()
				runSearchNow()
			}
		})
	}

	document.getElementById('mine-refresh').addEventListener('click', function () {
		if (searchDebounceTimer) {
			clearTimeout(searchDebounceTimer)
			searchDebounceTimer = null
		}
		pager.goto(1)
		scopePager.goto(1)
		load()
		loadScope()
	})
	document.getElementById('mine-campaign').addEventListener('change', function () {
		pager.goto(1)
		scopePager.goto(1)
		load()
		loadScope()
	})
	document.getElementById('mine-sort').addEventListener('change', function () {
		renderMineTable()
	})
	if (scopeCareSelect) {
		if (!allowPii) scopeCareSelect.disabled = true
		scopeCareSelect.addEventListener('change', function () {
			scopePager.goto(1)
			loadScope()
		})
	}
	const scopeOwnerInput = document.getElementById('mine-scope-owner')
	if (scopeOwnerInput) {
		scopeOwnerInput.addEventListener('input', function () {
			if (scopeOwnerDebounceTimer) clearTimeout(scopeOwnerDebounceTimer)
			scopeOwnerDebounceTimer = setTimeout(function () {
				scopeOwnerDebounceTimer = null
				scopePager.goto(1)
				loadScope()
			}, 250)
		})
		scopeOwnerInput.addEventListener('keydown', function (ev) {
			if (ev.key === 'Enter') {
				ev.preventDefault()
				if (scopeOwnerDebounceTimer) {
					clearTimeout(scopeOwnerDebounceTimer)
					scopeOwnerDebounceTimer = null
				}
				scopePager.goto(1)
				loadScope()
			}
		})
	}
	document.getElementById('mine-scope-refresh').addEventListener('click', function () {
		scopePager.goto(1)
		loadScope()
	})
	const scopePrevBtn = document.getElementById('mine-scope-prev')
	const scopeNextBtn = document.getElementById('mine-scope-next')
	if (scopePrevBtn) {
		scopePrevBtn.addEventListener('click', function () {
			scopePager.goto(Math.max(1, scopePager.page() - 1))
			loadScope()
		})
	}
	if (scopeNextBtn) {
		scopeNextBtn.addEventListener('click', function () {
			scopePager.goto(scopePager.page() + 1)
			loadScope()
		})
	}
	document.getElementById('mine-detail-close').addEventListener('click', function () {
		document.getElementById('mine-detail-card').hidden = true
	})
	document.getElementById('mine-prev').addEventListener('click', function () {
		pager.goto(Math.max(1, pager.page() - 1))
		load()
	})
	document.getElementById('mine-next').addEventListener('click', function () {
		pager.goto(pager.page() + 1)
		load()
	})

	loadAssignees()
	loadCampaigns()
	load()
	loadScope()
})()
