/* My customers (P1 §9.4) and the manual ownership actions (P1 §4.4).

   Two lists live on this page:

   1. **My customers** -- `crm_cskh.api.customers.my_list`. The list always
      resolves the caller as the owner: there is no `user` parameter, so the
      browser can only ever see the work that belongs to the signed-in user.
   2. **The customers assigned in the scope** --
      `crm_cskh.api.ownership_ui.assigned_list`. Rendered for callers with a
      broader scope (`CSKH CRM Admin` or `Sales Manager` team leader). Only
      `CSKH CRM Admin` (`can_manage = true`) receives the transfer / revoke
      controls; leaders inspect team items and ownership history read-only. */
(function () {
	'use strict'

	const API = 'crm_cskh.api.customers.my_list'
	const API_SCOPE = 'crm_cskh.api.ownership_ui.assigned_list'
	const API_ASSIGNEES = 'crm_cskh.api.ownership_ui.assignees'
	const API_REASSIGN = 'crm_cskh.api.ownership.reassign'
	const API_REVOKE = 'crm_cskh.api.ownership.revoke'
	const API_HISTORY = 'crm_cskh.api.ownership.history'
	const PAGE_SIZE = 20

	const isAdmin = !!(cskh.capabilities() && cskh.capabilities().admin)
	const allowPii = !!(cskh.capabilities() && cskh.capabilities().pii)

	const pager = cskh.pager({
		pageSize: PAGE_SIZE,
		range: document.getElementById('mine-range'),
		prev: document.getElementById('mine-prev'),
		next: document.getElementById('mine-next'),
	})

	const reads = cskh.sequencer()
	const scopeReads = cskh.sequencer()

	let lastMineItems = []
	let assignees = []
	let canManage = false

	function stateLabel(value) {
		const map = {
			assigned: cskh.t('Assigned'),
			queued: cskh.t('Queued'),
			revoked: cskh.t('Revoked'),
			closed: cskh.t('Closed'),
		}
		return map[value] || value || '—'
	}

	function uuid() {
		if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID()
		return 'cskh-' + Date.now() + '-' + Math.floor(Math.random() * 1e9)
	}

	/* ------------------------------------------------------------------ */
	/* My customers                                                        */
	/* ------------------------------------------------------------------ */

	function filterAndSortMine(items) {
		const searchEl = document.getElementById('mine-search')
		const sortEl = document.getElementById('mine-sort')
		const query = searchEl ? searchEl.value.trim().toLowerCase() : ''
		const dir = sortEl ? sortEl.value : 'desc'

		const filtered = (items || []).filter(function (item) {
			if (!query) return true
			const haystack = [item.display_name, item.lead, item.phone, item.campaign_title]
				.filter(Boolean)
				.join(' ')
				.toLowerCase()
			return haystack.indexOf(query) >= 0
		})
		filtered.sort(function (a, b) {
			const ta = a.assigned_at ? new Date(a.assigned_at).getTime() : 0
			const tb = b.assigned_at ? new Date(b.assigned_at).getTime() : 0
			return dir === 'asc' ? ta - tb : tb - ta
		})
		return filtered
	}

	function renderMineTable() {
		const table = document.getElementById('mine-table')
		const empty = document.getElementById('mine-empty')
		const rows = document.getElementById('mine-rows')
		cskh.clear(rows)
		const items = filterAndSortMine(lastMineItems)
		if (!items.length) {
			table.hidden = true
			empty.hidden = false
			return
		}
		empty.hidden = true
		items.forEach(function (item) {
			const rowItem = Object.assign({ assigned_to: cskh.currentUser() }, item)
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
			if (allowPii && item.lead) {
				actions.push(
					cskh.el('a', {
						class: 'cskh-btn is-small',
						href: '/crm/leads/' + encodeURIComponent(item.lead),
						text: cskh.t('Open in CRM'),
					})
				)
			}
			rows.appendChild(
				cskh.el('tr', {}, [
					cskh.el('td', { text: (allowPii && item.display_name) || item.lead }),
					cskh.el('td', {
						class: 'cskh-mono',
						text: allowPii && item.phone ? cskh.maskPhone(item.phone) : '—',
					}),
					cskh.el('td', {
						text: item.campaign_title || item.campaign_code || item.campaign || '—',
					}),
					cskh.el('td', { text: item.source || '—' }),
					cskh.el('td', { text: cskh.formatDate(item.assigned_at) }),
					cskh.el('td', {}, [
						cskh.el('span', {
							class: cskh.stateBadge(item.work_item_status),
							text: stateLabel(item.work_item_status),
						}),
					]),
					cskh.el('td', {}, [cskh.el('div', { class: 'cskh-row-actions' }, actions)]),
				])
			)
		})
		table.hidden = false
	}

	async function load() {
		const loading = document.getElementById('mine-loading')
		const table = document.getElementById('mine-table')
		const empty = document.getElementById('mine-empty')
		const alertBox = document.getElementById('mine-alert')

		loading.hidden = false
		table.hidden = true
		empty.hidden = true
		cskh.clear(alertBox)

		const campaign = document.getElementById('mine-campaign').value
		const mine = reads.next()
		try {
			const data = await cskh.get(API, {
				page: pager.page(),
				page_size: PAGE_SIZE,
				campaign: campaign,
			})
			if (mine !== reads.current()) return
			pager.setTotal(data && data.total)
			pager.render()
			lastMineItems = (data && data.items) || []
			renderMineTable()
		} catch (error) {
			lastMineItems = []
			cskh.renderError(alertBox, error)
		} finally {
			loading.hidden = true
		}
	}

	/* ------------------------------------------------------------------ */
	/* The scope list and the manual ownership actions (P1 §4.4)           */
	/* ------------------------------------------------------------------ */

	async function loadScope() {
		const card = document.getElementById('mine-scope-card')
		const table = document.getElementById('mine-scope-table')
		const rows = document.getElementById('mine-scope-rows')
		const empty = document.getElementById('mine-scope-empty')
		const loading = document.getElementById('mine-scope-loading')
		const alertBox = document.getElementById('mine-alert')
		if (!card) return

		loading.hidden = false
		table.hidden = true
		empty.hidden = true
		cskh.clear(rows)

		const campaign = document.getElementById('mine-campaign').value
		const ownerInput = document.getElementById('mine-scope-owner')
		const owner = ownerInput ? ownerInput.value.trim() : ''
		const mine = scopeReads.next()
		try {
			const data = await cskh.get(API_SCOPE, {
				page: 1,
				page_size: PAGE_SIZE,
				campaign: campaign,
				owner: owner,
			})
			if (mine !== scopeReads.current()) return
			canManage = !!(data && data.can_manage)
			const scopeCode = data && data.scope
			// Staff (`scope === 'self'`) already sees all their items in `mine-table`.
			// Admin (`system`) and Leaders (`team`) / BOD (`all`) see the scoped list.
			const showScopeCard = canManage || scopeCode === 'team' || scopeCode === 'all'
			if (!showScopeCard) {
				card.hidden = true
				return
			}
			card.hidden = false
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
		if (canManage) {
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
			cskh.el('td', { text: item.assigned_to || '—' }),
			cskh.el('td', { text: cskh.formatDate(item.assigned_at) }),
			cskh.el('td', {}, [cskh.el('div', { class: 'cskh-row-actions' }, actions)]),
		])
	}

	async function loadAssignees(campaign) {
		if (!isAdmin) return
		try {
			const params = campaign ? { campaign: campaign } : {}
			const data = await cskh.get(API_ASSIGNEES, params)
			assignees = (data && data.items) || []
		} catch (error) {
			assignees = []
		}
	}

	/**
	 * Open the detail panel of one work item: current owner, append-only
	 * history, and (only when `canManage`) the transfer / revoke form.
	 */
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
				metric(cskh.t('Customer'), (allowPii && item.display_name) || item.lead),
				metric(
					cskh.t('Phone number'),
					allowPii && item.phone ? cskh.maskPhone(item.phone) : '—'
				),
				metric(cskh.t('Current owner'), item.assigned_to || '—'),
				metric(cskh.t('Campaign'), item.campaign_title || item.campaign || '—'),
				metric(cskh.t('Assigned at'), cskh.formatDate(item.assigned_at)),
			])
		)

		if (allowPii && item.lead) {
			box.appendChild(
				cskh.el('div', { class: 'cskh-actions' }, [
					cskh.el('a', {
						class: 'cskh-btn is-small',
						href: '/crm/leads/' + encodeURIComponent(item.lead),
						text: cskh.t('Open in CRM'),
					}),
				])
			)
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
					request_id: 'ui-revoke-' + uuid(),
					return_to_pool: 1,
				})
				cskh.alert(alertBox, 'success', cskh.t('Revoked') + ': ' + item.work_item)
			} else {
				await cskh.post(API_REASSIGN, {
					work_item: item.work_item,
					expected_owner: item.assigned_to,
					target_user: targetUser,
					reason: reason,
					request_id: 'ui-reassign-' + uuid(),
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

	async function loadCampaigns() {
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
		} catch (error) {
			/* The campaign filter is optional. */
		}
	}

	const searchWrap = document.getElementById('mine-search-wrap')
	if (searchWrap && !allowPii) searchWrap.hidden = true

	document.getElementById('mine-refresh').addEventListener('click', function () {
		pager.goto(1)
		load()
		loadScope()
	})
	document.getElementById('mine-campaign').addEventListener('change', function () {
		pager.goto(1)
		load()
		loadScope()
	})
	document.getElementById('mine-search').addEventListener('input', function () {
		renderMineTable()
	})
	document.getElementById('mine-sort').addEventListener('change', function () {
		renderMineTable()
	})
	document.getElementById('mine-scope-refresh').addEventListener('click', function () {
		loadScope()
	})
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
