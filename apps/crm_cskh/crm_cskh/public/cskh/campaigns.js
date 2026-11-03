/* Campaigns, assignment rules and distribution (P1 §9.3).

   Contract (T1B, frozen + async background distribution):
     GET  crm_cskh.api.campaigns.list(status, campaign)
     GET  crm_cskh.api.campaigns.rules(campaign)
     POST crm_cskh.api.campaigns.save(code, title, description, status, team_scope, campaign, selection)
     POST crm_cskh.api.campaigns.save_rule(campaign, user, weight, capacity_limit, team, enabled, version)
     POST crm_cskh.api.distribution.preview(campaign, idempotency_key)
     POST crm_cskh.api.distribution.execute(preview_id, expected_rule_version, idempotency_key)
     GET  crm_cskh.api.distribution.get_run(run_id)

   The preview is a dry run; executing it is a **separate, explicitly
   confirmed** action that carries the rule version of the preview. A stale
   preview is refused by the backend with `RULE_CHANGED` or `PREVIEW_EXPIRED`
   and the page shows the conflict state instead of distributing stale
   numbers (P1 §7.4).

   When `execute` enqueues a background job (`queued` / `running`), enqueue
   is **never** treated as completion: the UI displays live progress, polls
   `get_run(run_id)` single-flight, recovers tracking across page reloads,
   and surfaces `completed`, `completed_with_errors`, or `failed` accurately. */
(function () {
	'use strict'

	const API_LIST = 'crm_cskh.api.campaigns.list'
	const API_RULES = 'crm_cskh.api.campaigns.rules'
	const API_SAVE = 'crm_cskh.api.campaigns.save'
	const API_SAVE_RULE = 'crm_cskh.api.campaigns.save_rule'
	const API_PREVIEW = 'crm_cskh.api.distribution.preview'
	const API_EXECUTE = 'crm_cskh.api.distribution.execute'
	const API_RUN = 'crm_cskh.api.distribution.get_run'
	const API_RUNS = 'crm_cskh.api.run_history.runs'
	const API_ASSIGNEES = 'crm_cskh.api.ownership_ui.assignees'

	const ACTIVE_RUN_STORAGE_KEY = 'cskh_active_dist_run'
	const RUN_POLL_INTERVAL_MS = 1000

	const isAdmin = !!(cskh.capabilities() && cskh.capabilities().admin)

	let preview = null
	let campaigns = []
	let editingCampaign = null
	let rulesByUser = {}
	let currentEligibleRules = []
	let availableAssignees = []

	let trackedRunId = null
	let trackedMeta = null
	let pollGeneration = 0
	let pollTimer = null

	function uuid() {
		if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID()
		return 'cskh-' + Date.now() + '-' + Math.floor(Math.random() * 1e9)
	}

	function normRunStatus(value) {
		return String(value || '')
			.trim()
			.toLowerCase()
	}

	function isTerminalRunStatus(value) {
		const s = normRunStatus(value)
		return s === 'completed' || s === 'completed_with_errors' || s === 'failed'
	}

	function isActiveRunStatus(value) {
		const s = normRunStatus(value)
		return s === 'queued' || s === 'running' || s === 'in_progress' || s === 'pending'
	}

	function reasonLabel(reason) {
		const map = {
			no_canonical_identity: cskh.t('no_canonical_identity'),
			work_item_exists_in_campaign: cskh.t('work_item_exists_in_campaign'),
			active_claim_in_other_campaign: cskh.t('active_claim_in_other_campaign'),
			owned_outside_cskh: cskh.t('owned_outside_cskh'),
			no_assignment_rule: cskh.t('no_assignment_rule'),
			user_disabled: cskh.t('user_disabled'),
			user_out_of_team_scope: cskh.t('user_out_of_team_scope'),
			user_is_not_sales_user: cskh.t('user_is_not_sales_user'),
			no_positive_weight: cskh.t('no_positive_weight'),
			capacity_full: cskh.t('capacity_full'),
			no_eligible_assignee: cskh.t('no_eligible_assignee'),
			assignment_failed: cskh.t('assignment_failed'),
			already_claimed: cskh.t('already_claimed'),
		}
		return map[reason] || reason || '—'
	}

	function formatRunError(rawError) {
		const raw = String(rawError || '').trim()
		if (!raw) return { message: '', code: 'FAILED' }
		const match = raw.match(/^([A-Z][A-Z0-9_]+):\s*(.+)$/)
		if (match) {
			const code = match[1]
			const msg = match[2].trim()
			const fallback =
				(cskh.explainError && cskh.explainError(code)) ||
				cskh.t('An error occurred while processing the request.')
			return {
				message: cskh.t(msg, fallback),
				code: code,
			}
		}
		return {
			message: cskh.t(raw, cskh.t('An error occurred while processing the request.')),
			code: 'FAILED',
		}
	}

	function readStoredActiveRun() {
		try {
			const params = new URLSearchParams(window.location.search || '')
			const queryRunId = (params.get('run_id') || '').trim()
			const raw = window.sessionStorage
				? window.sessionStorage.getItem(ACTIVE_RUN_STORAGE_KEY)
				: null
			const parsed = raw ? JSON.parse(raw) : null
			if (queryRunId) {
				if (parsed && parsed.run_id === queryRunId) return parsed
				return { run_id: queryRunId }
			}
			if (parsed && parsed.run_id) return parsed
		} catch (_err) {
			// Ignore storage/URL parsing errors.
		}
		return null
	}

	function persistActiveRun(meta) {
		if (!meta || !meta.run_id) return
		try {
			if (window.sessionStorage) {
				window.sessionStorage.setItem(
					ACTIVE_RUN_STORAGE_KEY,
					JSON.stringify({
						run_id: meta.run_id,
						campaign: meta.campaign || '',
						total: meta.total !== undefined ? meta.total : null,
						rule_version: meta.rule_version !== undefined ? meta.rule_version : null,
						lookupOnly: !!meta.lookupOnly,
					})
				)
			}
			if (window.history && window.history.replaceState) {
				const url = new URL(window.location.href)
				url.searchParams.set('run_id', meta.run_id)
				window.history.replaceState(null, '', url.pathname + url.search + url.hash)
			}
		} catch (_err) {
			// Ignore storage/URL update errors.
		}
	}

	function clearStoredActiveRun(clearQuery) {
		try {
			if (window.sessionStorage) {
				window.sessionStorage.removeItem(ACTIVE_RUN_STORAGE_KEY)
			}
			if (clearQuery && window.history && window.history.replaceState) {
				const url = new URL(window.location.href)
				if (url.searchParams.has('run_id')) {
					url.searchParams.delete('run_id')
					window.history.replaceState(null, '', url.pathname + url.search + url.hash)
				}
			}
		} catch (_err) {
			// Ignore storage/URL cleanup errors.
		}
	}

	function stopTracking(clearPersistence) {
		pollGeneration += 1
		if (pollTimer) {
			clearTimeout(pollTimer)
			pollTimer = null
		}
		trackedRunId = null
		trackedMeta = null
		if (clearPersistence) {
			clearStoredActiveRun(true)
		}
	}

	async function loadAssignees(campaign) {
		if (!isAdmin) return
		try {
			const params = campaign ? { campaign: campaign } : {}
			const data = await cskh.get(API_ASSIGNEES, params)
			availableAssignees = (data && data.items) || []
		} catch (_error) {
			availableAssignees = []
		}
	}

	/* ------------------------------------------------------------------ */
	/* Campaigns                                                            */
	/* ------------------------------------------------------------------ */

	async function loadCampaigns() {
		const alertBox = document.getElementById('campaign-alert')
		const loading = document.getElementById('campaign-list-loading')
		loading.hidden = false
		try {
			const data = await cskh.get(API_LIST)
			campaigns = (data && data.items) || []
			renderCampaigns(campaigns)
			renderCampaignFilter(campaigns)
		} catch (error) {
			cskh.renderError(alertBox, error)
		} finally {
			loading.hidden = true
		}
	}

	function renderCampaigns(items) {
		const list = document.getElementById('campaign-list')
		cskh.clear(list)
		if (!items.length) {
			list.appendChild(
				cskh.el('div', { class: 'cskh-alert is-empty', text: cskh.t('No campaign yet.') })
			)
			return
		}
		const headCells = [
			cskh.el('th', { text: cskh.t('Code') }),
			cskh.el('th', { text: cskh.t('Title') }),
			cskh.el('th', { text: cskh.t('Status') }),
			cskh.el('th', { text: cskh.t('Team scope') }),
			cskh.el('th', { class: 'num', text: cskh.t('Rule version') }),
			cskh.el('th', { text: cskh.t('Started at') }),
			cskh.el('th', { text: cskh.t('Ended at') }),
		]
		if (isAdmin) headCells.push(cskh.el('th', { text: cskh.t('Actions') }))

		const table = cskh.el('table', { class: 'cskh-table' }, [
			cskh.el('thead', {}, [cskh.el('tr', {}, headCells)]),
		])
		const body = cskh.el('tbody')
		items.forEach(function (campaign) {
			const cells = [
				cskh.el('td', { text: campaign.code }),
				cskh.el('td', { text: campaign.title }),
				cskh.el('td', {}, [
					cskh.el('span', {
						class: cskh.stateBadge(
							campaign.status === 'Active'
								? 'assigned'
								: campaign.status === 'Paused'
									? 'queued'
									: 'invalid'
						),
						text: cskh.campaignStatusLabel(campaign.status),
					}),
				]),
				cskh.el('td', { text: campaign.team_scope || '—' }),
				cskh.el('td', { class: 'num', text: campaign.rule_version }),
				cskh.el('td', { text: cskh.formatDate(campaign.started_at) }),
				cskh.el('td', { text: cskh.formatDate(campaign.ended_at) }),
			]
			if (isAdmin) {
				const rowActions = [
					cskh.el('button', {
						class: 'cskh-btn is-small',
						type: 'button',
						text: cskh.t('Edit campaign'),
						onclick: function () {
							startEditCampaign(campaign)
						},
					}),
				]
				if (campaign.status !== 'Closed') {
					const closeBtn = cskh.el('button', {
						class: 'cskh-btn is-small is-danger',
						type: 'button',
						text: cskh.t('Close campaign'),
						onclick: function () {
							closeCampaign(campaign, closeBtn)
						},
					})
					rowActions.push(closeBtn)
				}
				cells.push(cskh.el('td', {}, [cskh.el('div', { class: 'cskh-row-actions' }, rowActions)]))
			}
			body.appendChild(cskh.el('tr', {}, cells))
		})
		table.appendChild(body)
		list.appendChild(table)
	}

	function startEditCampaign(campaign) {
		editingCampaign = campaign.campaign
		document.getElementById('campaign-form-heading').textContent = cskh.t('Edit campaign')
		document.getElementById('campaign-code').value = campaign.code || ''
		document.getElementById('campaign-title').value = campaign.title || ''
		document.getElementById('campaign-description').value = campaign.description || ''
		document.getElementById('campaign-status').value = campaign.status || 'Active'
		document.getElementById('campaign-team').value = campaign.team_scope || ''
		document.getElementById('campaign-create').textContent = cskh.t('Save')
		document.getElementById('campaign-cancel-edit').hidden = false
	}

	function cancelEditCampaign() {
		editingCampaign = null
		document.getElementById('campaign-form-heading').textContent = cskh.t('New campaign')
		document.getElementById('campaign-code').value = ''
		document.getElementById('campaign-title').value = ''
		document.getElementById('campaign-description').value = ''
		document.getElementById('campaign-status').value = 'Draft'
		document.getElementById('campaign-team').value = ''
		document.getElementById('campaign-create').textContent = cskh.t('Create campaign')
		document.getElementById('campaign-cancel-edit').hidden = true
	}

	async function closeCampaign(campaign, button) {
		const alertBox = document.getElementById('campaign-alert')
		if (!campaign || !campaign.campaign) return
		button.disabled = true
		cskh.clear(alertBox)
		try {
			await cskh.post(API_SAVE, {
				campaign: campaign.campaign,
				code: campaign.code,
				title: campaign.title,
				status: 'Closed',
				team_scope: campaign.team_scope || null,
			})
			cskh.alert(
				alertBox,
				'success',
				cskh.t('Campaign closed') + ': ' + (campaign.title || campaign.code)
			)
			await loadCampaigns()
			loadRules()
		} catch (error) {
			cskh.renderError(alertBox, error)
		} finally {
			button.disabled = false
		}
	}

	function renderCampaignFilter(items) {
		const select = document.getElementById('rule-campaign')
		const current = select.value
		cskh.clear(select)
		select.appendChild(cskh.el('option', { value: '', text: cskh.t('All') }))
		items.forEach(function (campaign) {
			select.appendChild(
				cskh.el('option', {
					value: campaign.campaign,
					text: campaign.title || campaign.code,
				})
			)
		})
		if (current && items.some(function (c) { return c.campaign === current })) {
			select.value = current
		} else if (!current && items.length) {
			select.value = items[0].campaign
		}
	}

	/* ------------------------------------------------------------------ */
	/* Rules                                                                */
	/* ------------------------------------------------------------------ */

	async function loadRules() {
		const alertBox = document.getElementById('campaign-alert')
		const box = document.getElementById('rule-list')
		const loading = document.getElementById('rule-list-loading')
		const campaign = document.getElementById('rule-campaign').value
		loading.hidden = false
		cskh.clear(box)
		rulesByUser = {}
		currentEligibleRules = []
		if (!campaign) {
			loading.hidden = true
			box.appendChild(
				cskh.el('div', { class: 'cskh-alert is-empty', text: cskh.t('No assignment rule yet.') })
			)
			return
		}
		try {
			const results = await Promise.all([
				cskh.get(API_RULES, { campaign: campaign }),
				loadAssignees(campaign),
			])
			renderRules(results[0])
		} catch (error) {
			cskh.renderError(alertBox, error)
		} finally {
			loading.hidden = true
		}
	}

	function renderRules(data) {
		const box = document.getElementById('rule-list')
		cskh.clear(box)
		const eligible = (data && data.eligible) || []
		const excluded = (data && data.excluded) || []
		currentEligibleRules = eligible
		eligible.forEach(function (rule) {
			rulesByUser[rule.user] = rule
		})

		if (!eligible.length && !excluded.length) {
			box.appendChild(
				cskh.el('div', { class: 'cskh-alert is-empty', text: cskh.t('No assignment rule yet.') })
			)
			if (isAdmin) box.appendChild(ruleForm(null, 0))
			return
		}
		const totalWeight = eligible.reduce(function (sum, rule) {
			return sum + (rule.weight || 0)
		}, 0)

		box.appendChild(
			cskh.el('p', {
				class: 'cskh-muted',
				text: cskh.t('Rule version') + ': ' + (data.rule_version || 1),
			})
		)

		const headCells = [
			cskh.el('th', { text: cskh.t('Assignee') }),
			cskh.el('th', { text: cskh.t('Team') }),
			cskh.el('th', { class: 'num', text: cskh.t('Weight') }),
			cskh.el('th', { class: 'num', text: cskh.t('Normalized share') }),
			cskh.el('th', { class: 'num', text: cskh.t('Capacity') }),
			cskh.el('th', { class: 'num', text: cskh.t('Remaining capacity') }),
			cskh.el('th', { text: cskh.t('Eligibility') }),
		]
		if (isAdmin) headCells.push(cskh.el('th', {}))

		const table = cskh.el('table', { class: 'cskh-table' }, [
			cskh.el('thead', {}, [cskh.el('tr', {}, headCells)]),
		])
		const body = cskh.el('tbody')

		eligible.forEach(function (rule) {
			const share = totalWeight ? Math.round((rule.weight / totalWeight) * 1000) / 10 : 0
			const cells = [
				cskh.el('td', { text: rule.user }),
				cskh.el('td', { text: rule.team || '—' }),
				cskh.el('td', { class: 'num', text: rule.weight }),
				cskh.el('td', { class: 'num', text: share + '%' }),
				cskh.el('td', { class: 'num', text: cskh.capacityLabel(rule.capacity) }),
				cskh.el('td', {
					class: 'num',
					text: cskh.capacityLabel(rule.remaining),
				}),
				cskh.el('td', {}, [
					cskh.el('span', {
						class: cskh.stateBadge('eligible'),
						text: cskh.t('Eligible'),
					}),
				]),
			]
			if (isAdmin) cells.push(cskh.el('td', {}, [ruleForm(rule, totalWeight)]))
			body.appendChild(cskh.el('tr', {}, cells))
		})

		excluded.forEach(function (rule) {
			const cells = [
				cskh.el('td', { text: rule.user }),
				cskh.el('td', { text: '—' }),
				cskh.el('td', { class: 'num', text: rule.weight || '—' }),
				cskh.el('td', { class: 'num', text: '—' }),
				cskh.el('td', { class: 'num', text: '—' }),
				cskh.el('td', { class: 'num', text: '—' }),
				cskh.el('td', {}, [
					cskh.el('span', {
						class: cskh.stateBadge('excluded'),
						text: cskh.t('Excluded'),
					}),
					cskh.el('div', { class: 'cskh-muted', text: reasonLabel(rule.reason) }),
				]),
			]
			if (isAdmin) cells.push(cskh.el('td', {}, [ruleForm(rule, totalWeight)]))
			body.appendChild(cskh.el('tr', {}, cells))
		})

		table.appendChild(body)
		box.appendChild(table)
		if (isAdmin) box.appendChild(ruleForm(null, totalWeight))
	}

	/** The inline form to save (or change) the rule of one assignee. */
	function ruleForm(rule, totalWeight) {
		let userControl
		if (!rule && availableAssignees.length) {
			userControl = document.createElement('select')
			userControl.className = 'cskh-select'
			userControl.appendChild(cskh.el('option', { value: '', text: cskh.t('Assignee') }))
			availableAssignees.forEach(function (item) {
				userControl.appendChild(
					cskh.el('option', {
						value: item.user,
						text: item.full_name + ' (' + item.user + ')',
					})
				)
			})
		} else {
			userControl = document.createElement('input')
			userControl.className = 'cskh-input'
			userControl.placeholder = cskh.t('Assignee')
			userControl.value = (rule && rule.user) || ''
			if (rule && rule.user) userControl.readOnly = true
		}

		const team = document.createElement('input')
		team.className = 'cskh-input'
		team.placeholder = cskh.t('Team')
		team.value = (rule && rule.team) || ''

		const weight = document.createElement('input')
		weight.className = 'cskh-input'
		weight.type = 'number'
		weight.min = '1'
		weight.placeholder = cskh.t('Weight')
		weight.value = (rule && rule.weight) || 1

		const capacity = document.createElement('input')
		capacity.className = 'cskh-input'
		capacity.type = 'number'
		capacity.min = '0'
		capacity.placeholder = cskh.t('Capacity')
		capacity.value =
			rule && rule.capacity !== undefined && rule.capacity !== null ? rule.capacity : 0

		const liveShare = cskh.el('span', { class: 'cskh-muted cskh-mono' })
		function updateLiveShare() {
			const w = Math.max(0, Number(weight.value || 0))
			const otherWeight = Math.max(
				0,
				Number(totalWeight || 0) - Number((rule && rule.weight) || 0)
			)
			const denom = otherWeight + w
			const pct = denom > 0 ? Math.round((w / denom) * 1000) / 10 : 0
			const capNum = Number(capacity.value || 0)
			liveShare.textContent = pct + '% · ' + cskh.capacityLabel(capNum)
		}
		weight.addEventListener('input', updateLiveShare)
		capacity.addEventListener('input', updateLiveShare)
		updateLiveShare()

		const save = cskh.el('button', {
			class: 'cskh-btn is-primary is-small',
			type: 'button',
			text: cskh.t('Save'),
		})

		save.addEventListener('click', async function () {
			const campaign = document.getElementById('rule-campaign').value
			const alertBox = document.getElementById('campaign-alert')
			if (!campaign) return
			const chosenUser = userControl.value.trim()
			if (!chosenUser) {
				cskh.alert(alertBox, 'warning', cskh.t('Assignee'))
				return
			}
			save.disabled = true
			try {
				await cskh.post(API_SAVE_RULE, {
					campaign: campaign,
					user: chosenUser,
					weight: Number(weight.value || 0),
					capacity_limit: Number(capacity.value || 0),
					team: team.value.trim() || null,
					enabled: 1,
				})
				cskh.alert(alertBox, 'success', cskh.t('Save') + ': ' + chosenUser)
				await loadRules()
				await loadCampaigns()
			} catch (error) {
				cskh.renderError(alertBox, error)
			} finally {
				save.disabled = false
			}
		})

		return cskh.el('div', { class: 'cskh-inline-list' }, [
			userControl,
			team,
			weight,
			capacity,
			liveShare,
			save,
		])
	}

	/* ------------------------------------------------------------------ */
	/* Distribution: preview -> confirm -> execute + background polling    */
	/* ------------------------------------------------------------------ */

	async function runPreview() {
		const alertBox = document.getElementById('campaign-alert')
		const campaign = document.getElementById('rule-campaign').value
		const summary = document.getElementById('dist-summary')
		const targets = document.getElementById('dist-targets')
		const previewBtn = document.getElementById('dist-preview')
		cskh.clear(summary)
		cskh.clear(targets)
		cskh.clear(alertBox)
		preview = null
		document.getElementById('dist-execute').disabled = true

		if (!campaign) {
			cskh.alert(alertBox, 'warning', cskh.t('Campaign'))
			return
		}
		previewBtn.disabled = true
		try {
			const data = await cskh.post(API_PREVIEW, {
				campaign: campaign,
				idempotency_key: 'ui-preview-' + uuid(),
			})
			preview = data
			renderPreview(data)
		} catch (error) {
			cskh.renderError(alertBox, error)
		} finally {
			previewBtn.disabled = false
		}
	}

	function renderPreview(data) {
		const summary = document.getElementById('dist-summary')
		const targets = document.getElementById('dist-targets')
		const info = (data && data.summary) || {}
		const simulated = info.simulated || {}
		const targetList = info.targets || []
		const excludedAssignees = info.excluded_assignees || []

		summary.appendChild(
			cskh.el('div', { class: 'cskh-grid' }, [
				metric(cskh.t('Preview ID'), data.preview_id),
				metric(cskh.t('Eligible customers'), cskh.formatNumber(info.eligible_count)),
				metric(cskh.t('Excluded customers'), cskh.formatNumber(info.excluded_count)),
				metric(cskh.t('Rule version'), data.rule_version),
			])
		)
		summary.appendChild(
			cskh.el('p', {
				class: 'cskh-muted',
				text:
					cskh.t('Selection fingerprint') +
					': ' +
					(data.selection_fingerprint || '—') +
					' · ' +
					cskh.t('Expires at') +
					': ' +
					cskh.formatDate(data.expires_at),
			})
		)

		const totalWeight = targetList.reduce(function (sum, item) {
			return sum + (item.weight || 0)
		}, 0)
		const targetByUser = {}
		targetList.forEach(function (item) {
			targetByUser[item.user] = item
		})
		const users = targetList.length
			? targetList.map(function (item) { return item.user })
			: Object.keys(simulated)

		const table = cskh.el('table', { class: 'cskh-table' }, [
			cskh.el('thead', {}, [
				cskh.el('tr', {}, [
					cskh.el('th', { text: cskh.t('Assignee') }),
					cskh.el('th', { class: 'num', text: cskh.t('Normalized share') }),
					cskh.el('th', { class: 'num', text: cskh.t('Planned') }),
					cskh.el('th', { class: 'num', text: cskh.t('Assigned now') }),
					cskh.el('th', { class: 'num', text: cskh.t('Remaining capacity') }),
				]),
			]),
		])
		const body = cskh.el('tbody')
		users.forEach(function (user) {
			const tRow = targetByUser[user] || {}
			const rRow = rulesByUser[user] || {}
			const weight = tRow.weight || rRow.weight || 0
			const pct = totalWeight ? Math.round((weight / totalWeight) * 1000) / 10 : 0
			const plannedCount =
				simulated[user] !== undefined ? simulated[user] : tRow.planned || 0
			const cap = rRow.capacity
			const rem = rRow.remaining
			const assignedNow =
				rRow.assigned !== undefined
					? rRow.assigned
					: cap && Number(cap) > 0 && rem !== null && rem !== undefined
						? Math.max(0, Number(cap) - Number(rem))
						: 0
			body.appendChild(
				cskh.el('tr', {}, [
					cskh.el('td', { text: user }),
					cskh.el('td', { class: 'num', text: pct + '%' }),
					cskh.el('td', { class: 'num', text: cskh.formatNumber(plannedCount) }),
					cskh.el('td', { class: 'num', text: cskh.formatNumber(assignedNow) }),
					cskh.el('td', {
						class: 'num',
						text: cskh.capacityLabel(rem !== undefined ? rem : null),
					}),
				])
			)
		})
		table.appendChild(body)
		targets.appendChild(table)

		if (excludedAssignees.length) {
			const exTable = cskh.el('table', { class: 'cskh-table' }, [
				cskh.el('thead', {}, [
					cskh.el('tr', {}, [
						cskh.el('th', { text: cskh.t('Excluded') }),
						cskh.el('th', { text: cskh.t('Reason') }),
					]),
				]),
			])
			const exBody = cskh.el('tbody')
			excludedAssignees.forEach(function (row) {
				exBody.appendChild(
					cskh.el('tr', {}, [
						cskh.el('td', { text: row.user }),
						cskh.el('td', { text: reasonLabel(row.reason) }),
					])
				)
			})
			exTable.appendChild(exBody)
			targets.appendChild(exTable)
		}

		document.getElementById('dist-execute').disabled = false
	}

	async function enrichRunFromHistory(run, meta) {
		if (!run) return run
		const runId = run.run_id || run.preview_id || run.name
		if (!runId) return run
		const s = normRunStatus(run.status)
		const needsProgress = run.progress === undefined || run.progress === null
		const needsTotal =
			run.total === undefined &&
			run.eligible_count === undefined &&
			(!meta || meta.total === undefined || meta.total === null)
		const needsError = (s === 'failed' || s === 'completed_with_errors') && !run.error
		if (!needsProgress && !needsTotal && !needsError) return run

		try {
			const hist = await cskh.get(API_RUNS, { run_id: runId, page: 1, page_size: 1 })
			const item = hist && hist.items && hist.items[0]
			if (!item || item.run_id !== runId) return run
			const merged = Object.assign({}, run)
			if (merged.progress === undefined || merged.progress === null) {
				merged.progress = item.progress
			}
			if (merged.total === undefined || merged.total === null) {
				merged.total = item.total
			}
			if (!merged.error && item.error) {
				merged.error = item.error
			}
			if (!merged.campaign && item.campaign) {
				merged.campaign = item.campaign
			}
			if (!merged.rule_version && item.rule_version) {
				merged.rule_version = item.rule_version
			}
			return merged
		} catch (_err) {
			return run
		}
	}

	function buildRunMeta(run, baseMeta) {
		const runId = (run && (run.run_id || run.preview_id || run.name)) || (baseMeta && baseMeta.run_id) || ''
		const totalCandidate =
			run && run.total !== undefined && run.total !== null
				? Number(run.total)
				: run && run.eligible_count !== undefined && run.eligible_count !== null
					? Number(run.eligible_count)
					: baseMeta && baseMeta.total !== undefined && baseMeta.total !== null
						? Number(baseMeta.total)
						: null
		return {
			run_id: runId,
			campaign:
				(run && run.campaign) ||
				(baseMeta && baseMeta.campaign) ||
				document.getElementById('rule-campaign').value ||
				'',
			total: totalCandidate,
			rule_version:
				(run && run.rule_version) || (baseMeta && baseMeta.rule_version) || null,
			lookupOnly: !!(baseMeta && baseMeta.lookupOnly),
		}
	}

	async function handleExecuteResult(rawRun, baseMeta) {
		const alertBox = document.getElementById('campaign-alert')
		cskh.clear(alertBox)
		const meta = buildRunMeta(rawRun, baseMeta)
		const run = await enrichRunFromHistory(rawRun, meta)
		const updatedMeta = buildRunMeta(run, meta)

		renderRun(run, updatedMeta)
		if (isActiveRunStatus(run && run.status)) {
			persistActiveRun(updatedMeta)
			trackRun(updatedMeta.run_id, updatedMeta)
			loadRuns()
		} else {
			stopTracking(true)
			loadRules()
			loadRuns()
		}
	}

	async function runExecute() {
		const alertBox = document.getElementById('campaign-alert')
		const results = document.getElementById('dist-results')
		const executeBtn = document.getElementById('dist-execute')
		cskh.clear(results)
		if (!preview) return
		const activePreview = preview
		const activeTotal =
			activePreview.summary && activePreview.summary.eligible_count !== undefined
				? Number(activePreview.summary.eligible_count)
				: null
		// Disable immediately to block double-submit while request is in flight (P1 §12.4).
		executeBtn.disabled = true
		try {
			const data = await cskh.post(API_EXECUTE, {
				preview_id: activePreview.preview_id,
				expected_rule_version: activePreview.rule_version,
				idempotency_key: 'ui-execute-' + activePreview.preview_id,
			})
			preview = null
			await handleExecuteResult(data, {
				run_id: (data && data.run_id) || activePreview.preview_id,
				campaign: activePreview.campaign || document.getElementById('rule-campaign').value,
				total: activeTotal,
				rule_version: activePreview.rule_version,
			})
		} catch (error) {
			const code = cskh.renderError(alertBox, error)
			if (code === 'RULE_CHANGED' || code === 'PREVIEW_EXPIRED') {
				preview = null
				cskh.alert(
					alertBox,
					'warning',
					code === 'RULE_CHANGED'
						? cskh.t('The rules changed after the preview. Run the preview again.')
						: cskh.t('The preview has expired. Run the preview again.'),
					'[' + code + ']'
				)
			} else if (preview) {
				executeBtn.disabled = false
			}
		}
	}

	async function fetchRunForActor(runId, baseMeta) {
		if (isAdmin || canLookup) {
			const raw = await cskh.get(API_RUN, { run_id: runId })
			const meta = buildRunMeta(raw, baseMeta)
			return enrichRunFromHistory(raw, meta)
		}
		const hist = await cskh.get(API_RUNS, { run_id: runId, page: 1, page_size: 1 })
		const item = hist && hist.items && hist.items[0]
		if (!item || item.run_id !== runId) {
			const err = new Error(cskh.t('The distribution run does not exist.'))
			err.status = 404
			err.code = 'NOT_FOUND'
			throw err
		}
		return item
	}

	function trackRun(runId, meta) {
		if (!runId) return
		if (trackedRunId === runId && pollTimer) {
			trackedMeta = Object.assign({}, trackedMeta || {}, meta || {})
			return
		}
		if (pollTimer) {
			clearTimeout(pollTimer)
			pollTimer = null
		}
		trackedRunId = runId
		trackedMeta = Object.assign({ run_id: runId }, meta || {})
		pollGeneration += 1
		const myGen = pollGeneration
		let lastStatus = null
		let consecutiveErrors = 0

		async function pollTick() {
			if (myGen !== pollGeneration || trackedRunId !== runId) return
			pollTimer = null
			try {
				const run = await fetchRunForActor(runId, trackedMeta)
				if (myGen !== pollGeneration || trackedRunId !== runId) return

				consecutiveErrors = 0
				trackedMeta = buildRunMeta(run, trackedMeta)
				const targetIsLookup = trackedMeta.lookupOnly || !isAdmin
				if (targetIsLookup) {
					const lookupBox = document.getElementById('dist-run-lookup-result')
					if (lookupBox) renderRunLookup(lookupBox, run, trackedMeta)
				} else {
					renderRun(run, trackedMeta)
				}

				const currentStatus = normRunStatus(run && run.status)
				if (isActiveRunStatus(currentStatus)) {
					persistActiveRun(trackedMeta)
					if (lastStatus && lastStatus !== currentStatus) {
						loadRuns()
					}
					lastStatus = currentStatus
					pollTimer = setTimeout(pollTick, RUN_POLL_INTERVAL_MS)
				} else {
					stopTracking(true)
					loadRules()
					loadRuns()
				}
			} catch (error) {
				if (myGen !== pollGeneration || trackedRunId !== runId) return
				consecutiveErrors += 1
				const code = cskh.errorCode(error)
				const alertBox = document.getElementById('campaign-alert')
				if (code === 'FORBIDDEN' || code === 'NOT_FOUND' || consecutiveErrors >= 3) {
					stopTracking(true)
					cskh.renderError(alertBox, error)
				} else {
					pollTimer = setTimeout(pollTick, RUN_POLL_INTERVAL_MS * 2)
				}
			}
		}

		pollTimer = setTimeout(pollTick, RUN_POLL_INTERVAL_MS)
	}

	async function pollTrackedRunNow(runId, baseMeta) {
		if (!runId) return
		const alertBox = document.getElementById('campaign-alert')
		try {
			const run = await fetchRunForActor(runId, baseMeta || trackedMeta)
			const updatedMeta = buildRunMeta(run, baseMeta || trackedMeta)
			if (updatedMeta.lookupOnly || !isAdmin) {
				renderRunLookup(document.getElementById('dist-run-lookup-result'), run, updatedMeta)
			} else {
				renderRun(run, updatedMeta)
			}
			if (isActiveRunStatus(run && run.status)) {
				persistActiveRun(updatedMeta)
				trackRun(runId, updatedMeta)
			} else {
				stopTracking(true)
				loadRules()
				loadRuns()
			}
		} catch (error) {
			cskh.renderError(alertBox, error)
		}
	}

	async function recoverActiveRun() {
		const stored = readStoredActiveRun()
		if (!stored || !stored.run_id) return
		try {
			const run = await fetchRunForActor(stored.run_id, stored)
			const updatedMeta = buildRunMeta(run, stored)

			if (updatedMeta.campaign) {
				const select = document.getElementById('rule-campaign')
				if (
					select &&
					Array.from(select.options).some(function (opt) {
						return opt.value === updatedMeta.campaign
					})
				) {
					select.value = updatedMeta.campaign
				}
			}

			if (!isAdmin || updatedMeta.lookupOnly) {
				const input = document.getElementById('dist-run-id')
				if (input) input.value = updatedMeta.run_id
				renderRunLookup(
					document.getElementById('dist-run-lookup-result'),
					run,
					updatedMeta
				)
			} else {
				renderRun(run, updatedMeta)
			}

			if (isActiveRunStatus(run && run.status)) {
				persistActiveRun(updatedMeta)
				trackRun(updatedMeta.run_id, updatedMeta)
			} else {
				clearStoredActiveRun(true)
			}
		} catch (error) {
			clearStoredActiveRun(true)
			const targetBox = isAdmin
				? document.getElementById('campaign-alert')
				: document.getElementById('dist-run-lookup-result')
			if (targetBox) cskh.renderError(targetBox, error)
		}
	}

	function formatRunProgress(run, meta) {
		const created = Number((run && run.created) || 0)
		const errors = Number((run && run.errors) || 0)
		const rawProgress =
			run && run.progress !== undefined && run.progress !== null
				? Number(run.progress)
				: 0
		let processed = Math.max(rawProgress, created + errors)
		const total =
			run && run.total !== undefined && run.total !== null
				? Number(run.total)
				: run && run.eligible_count !== undefined && run.eligible_count !== null
					? Number(run.eligible_count)
					: meta && meta.total !== undefined && meta.total !== null
						? Number(meta.total)
						: null
		if (total !== null && total > 0) {
			if (normRunStatus(run && run.status) === 'completed' && processed < total && created >= total) {
				processed = total
			}
			const clamped = Math.min(processed, total)
			const pct = Math.min(100, Math.round((clamped / total) * 100))
			return cskh.formatNumber(clamped) + ' / ' + cskh.formatNumber(total) + ' (' + pct + '%)'
		}
		if (total === 0 && isTerminalRunStatus(run && run.status)) {
			return '0 / 0 (100%)'
		}
		return cskh.formatNumber(processed)
	}

	function renderRunDetails(container, run, meta, includeBanner) {
		cskh.clear(container)
		if (!run) return
		const runId = run.run_id || run.preview_id || run.name || '—'
		const normStatus = normRunStatus(run.status)

		if (includeBanner) {
			const alertWrap = cskh.el('div')
			if (normStatus === 'queued') {
				cskh.alert(
					alertWrap,
					'info',
					cskh.t('Distribution queued. Waiting for background worker...'),
					runStatusLabel(run.status)
				)
			} else if (isActiveRunStatus(normStatus)) {
				cskh.alert(
					alertWrap,
					'info',
					cskh.t('Distribution is running in the background...'),
					runStatusLabel(run.status)
				)
			} else if (normStatus === 'planned') {
				cskh.alert(alertWrap, 'info', cskh.t('Preview'), runStatusLabel(run.status))
			} else if (normStatus === 'failed') {
				const parsedErr = formatRunError(run.error)
				cskh.alert(
					alertWrap,
					'error',
					cskh.t('Distribution failed.') +
						(parsedErr.message ? ' ' + parsedErr.message : ''),
					'[' + parsedErr.code + ']'
				)
			} else if (normStatus === 'completed_with_errors' || Number(run.errors || 0) > 0) {
				const parsedErr = run.error ? formatRunError(run.error) : null
				cskh.alert(
					alertWrap,
					'warning',
					cskh.t('Distribution completed with errors.') +
						(parsedErr && parsedErr.message ? ' ' + parsedErr.message : ''),
					parsedErr && parsedErr.code !== 'FAILED'
						? '[' + parsedErr.code + ']'
						: runStatusLabel(run.status)
				)
			} else if (normStatus === 'completed') {
				cskh.alert(
					alertWrap,
					'success',
					cskh.t('Distribution completed.'),
					runStatusLabel(run.status)
				)
			} else {
				cskh.alert(alertWrap, 'info', runStatusLabel(run.status), runId)
			}
			container.appendChild(alertWrap)
		}

		container.appendChild(
			cskh.el('div', { class: 'cskh-grid' }, [
				metric(cskh.t('Run ID'), runId),
				metric(cskh.t('Status'), runStatusLabel(run.status)),
				metric(cskh.t('Progress'), formatRunProgress(run, meta)),
				metric(cskh.t('Assigned in this run'), cskh.formatNumber(run.created || 0)),
				metric(cskh.t('Failed'), cskh.formatNumber(run.errors || 0)),
			])
		)

		const actionButtons = []
		if (isActiveRunStatus(normStatus) && runId !== '—') {
			actionButtons.push(
				cskh.el('button', {
					class: 'cskh-btn is-small',
					type: 'button',
					text: cskh.t('Refresh'),
					onclick: function () {
						pollTrackedRunNow(runId, meta)
					},
				})
			)
		}
		if (
			isAdmin &&
			(normStatus === 'failed' || normStatus === 'completed_with_errors') &&
			(run.campaign || (meta && meta.campaign))
		) {
			const retryBtn = cskh.el('button', {
				class: 'cskh-btn is-small',
				type: 'button',
				text: cskh.t('Retry the failed job'),
				onclick: function () {
					retryRun(
						{
							run_id: runId,
							campaign: run.campaign || (meta && meta.campaign),
							status: run.status,
							rule_version: run.rule_version || (meta && meta.rule_version),
							total: meta && meta.total,
						},
						retryBtn
					)
				},
			})
			actionButtons.push(retryBtn)
		}
		if (actionButtons.length) {
			container.appendChild(
				cskh.el('div', { class: 'cskh-actions', style: 'margin-top: 10px;' }, actionButtons)
			)
		}

		const assigned = run.assigned || {}
		const assignedUsers = Object.keys(assigned)
		if (
			assignedUsers.length ||
			normStatus === 'completed' ||
			normStatus === 'completed_with_errors'
		) {
			const table = cskh.el('table', { class: 'cskh-table' }, [
				cskh.el('thead', {}, [
					cskh.el('tr', {}, [
						cskh.el('th', { text: cskh.t('Assignee') }),
						cskh.el('th', { class: 'num', text: cskh.t('Assigned in this run') }),
					]),
				]),
			])
			const body = cskh.el('tbody')
			assignedUsers.forEach(function (user) {
				body.appendChild(
					cskh.el('tr', {}, [
						cskh.el('td', { text: user }),
						cskh.el('td', { class: 'num', text: cskh.formatNumber(assigned[user]) }),
					])
				)
			})
			table.appendChild(body)
			container.appendChild(table)
		}

		const skipped = run.skipped || []
		if (skipped.length || Number(run.errors || 0) > 0) {
			const failedTable = cskh.el('table', { class: 'cskh-table' }, [
				cskh.el('thead', {}, [
					cskh.el('tr', {}, [
						cskh.el('th', { text: cskh.t('Row') }),
						cskh.el('th', { text: cskh.t('Reason') }),
					]),
				]),
			])
			const failedBody = cskh.el('tbody')
			skipped.forEach(function (row) {
				failedBody.appendChild(
					cskh.el('tr', {}, [
						cskh.el('td', { text: row.lead }),
						cskh.el('td', { text: reasonLabel(row.reason) }),
					])
				)
			})
			failedTable.appendChild(failedBody)
			container.appendChild(failedTable)
		}
	}

	function renderRun(run, meta) {
		const results = document.getElementById('dist-results')
		if (!results) return
		renderRunDetails(results, run, meta, true)
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

	/* ------------------------------------------------------------------ */

	async function createCampaign() {
		const alertBox = document.getElementById('campaign-alert')
		const createBtn = document.getElementById('campaign-create')
		const code = document.getElementById('campaign-code').value.trim()
		const title = document.getElementById('campaign-title').value.trim()
		if (!code || !title) {
			cskh.alert(alertBox, 'warning', cskh.t('Code') + ' / ' + cskh.t('Title'))
			return
		}
		createBtn.disabled = true
		try {
			const payload = {
				code: code,
				title: title,
				description: document.getElementById('campaign-description').value.trim() || null,
				status: document.getElementById('campaign-status').value,
				team_scope: document.getElementById('campaign-team').value.trim() || null,
			}
			if (editingCampaign) payload.campaign = editingCampaign
			await cskh.post(API_SAVE, payload)
			cskh.alert(alertBox, 'success', cskh.t('Save') + ': ' + title)
			cancelEditCampaign()
			await loadCampaigns()
			loadRules()
		} catch (error) {
			cskh.renderError(alertBox, error)
		} finally {
			createBtn.disabled = false
		}
	}

	/* ------------------------------------------------------------------ */
	/* The history of the runs and the lookup by run id (P1 §9.3)          */
	/* ------------------------------------------------------------------ */

	const RUNS_PAGE_SIZE = 20

	const runsPager = cskh.pager({
		pageSize: RUNS_PAGE_SIZE,
		range: document.getElementById('dist-runs-range'),
		prev: document.getElementById('dist-runs-prev'),
		next: document.getElementById('dist-runs-next'),
	})

	const runReads = cskh.sequencer()
	let canLookup = !!(cskh.capabilities() && (cskh.capabilities().admin || cskh.capabilities().bod))
	let canDistribute = isAdmin

	async function loadRuns() {
		const loading = document.getElementById('dist-runs-loading')
		const table = document.getElementById('dist-runs-table')
		const empty = document.getElementById('dist-runs-empty')
		const rows = document.getElementById('dist-runs-rows')
		const alertBox = document.getElementById('campaign-alert')
		if (!loading) return

		loading.hidden = false
		table.hidden = true
		empty.hidden = true
		cskh.clear(rows)

		const campaign = document.getElementById('rule-campaign').value
		const status = document.getElementById('dist-runs-status').value
		const mine = runReads.next()
		try {
			const data = await cskh.get(API_RUNS, {
				page: runsPager.page(),
				page_size: RUNS_PAGE_SIZE,
				campaign: campaign,
				status: status,
			})
			if (mine !== runReads.current()) return
			runsPager.setTotal(data && data.total)
			runsPager.render()
			canLookup = !!(data && data.can_lookup)
			canDistribute = !!(data && data.can_distribute)

			const lookupBtn = document.getElementById('dist-run-lookup')
			const lookupWrap = document.getElementById('dist-run-id-wrap')
			if (lookupBtn) lookupBtn.hidden = !canLookup
			if (lookupWrap) lookupWrap.hidden = !canLookup

			const items = (data && data.items) || []
			if (!items.length) {
				empty.hidden = false
				return
			}
			items.forEach(function (item) {
				rows.appendChild(renderRunRow(item))
			})
			table.hidden = false

			// Auto-attach background tracking if an active queued/running run exists in scope
			// and no run is currently tracked (recovers tracking even in a fresh tab).
			if (!trackedRunId && !readStoredActiveRun() && runsPager.page() === 1) {
				const activeItem = items.find(function (item) {
					return isActiveRunStatus(item.status)
				})
				if (activeItem && activeItem.run_id) {
					const autoMeta = {
						run_id: activeItem.run_id,
						campaign: activeItem.campaign || campaign,
						total: activeItem.total !== undefined ? Number(activeItem.total) : null,
						rule_version: activeItem.rule_version || null,
						lookupOnly: !isAdmin,
					}
					if (isAdmin) {
						renderRun(activeItem, autoMeta)
					} else {
						renderRunLookup(
							document.getElementById('dist-run-lookup-result'),
							activeItem,
							autoMeta
						)
					}
					persistActiveRun(autoMeta)
					trackRun(activeItem.run_id, autoMeta)
				}
			}
		} catch (error) {
			cskh.renderError(alertBox, error)
		} finally {
			loading.hidden = true
		}
	}

	function renderRunRow(item) {
		const normStatus = normRunStatus(item.status)
		const actions = []
		if (canLookup) {
			actions.push(
				cskh.el('button', {
					class: 'cskh-btn is-small',
					type: 'button',
					text: cskh.t('Look up run'),
					onclick: function () {
						lookupRun(item.run_id)
					},
				})
			)
		}
		if ((normStatus === 'failed' || normStatus === 'completed_with_errors') && canDistribute) {
			const retry = cskh.el('button', {
				class: 'cskh-btn is-small',
				type: 'button',
				text: cskh.t('Retry the failed job'),
				onclick: function () {
					retryRun(item, retry)
				},
			})
			actions.push(retry)
		}
		const statusChildren = [
			cskh.el('span', {
				class: runBadgeClass(item.status),
				text: runStatusLabel(item.status),
			}),
		]
		if (isActiveRunStatus(normStatus)) {
			statusChildren.push(
				cskh.el('div', {
					class: 'cskh-muted cskh-mono',
					text: formatRunProgress(item, { total: item.total }),
				})
			)
		}
		if (item.error) {
			statusChildren.push(
				cskh.el('div', {
					class: 'cskh-muted',
					text: formatRunError(item.error).message,
				})
			)
		}
		return cskh.el('tr', {}, [
			cskh.el('td', { class: 'cskh-mono', text: item.run_id || '—' }),
			cskh.el('td', {
				text: item.campaign_title || item.campaign_code || item.campaign || '—',
			}),
			cskh.el('td', {}, statusChildren),
			cskh.el('td', { class: 'num', text: cskh.formatNumber(item.created) }),
			cskh.el('td', { class: 'num', text: cskh.formatNumber(item.errors) }),
			cskh.el('td', { text: cskh.formatDate(item.created_at) }),
			cskh.el('td', {}, [cskh.el('div', { class: 'cskh-row-actions' }, actions)]),
		])
	}

	async function retryRun(item, button) {
		const alertBox = document.getElementById('campaign-alert')
		const results = document.getElementById('dist-results')
		if (!item || !item.campaign) return
		if (button) button.disabled = true
		cskh.clear(alertBox)
		cskh.clear(results)
		try {
			const normStatus = normRunStatus(item.status)
			let run = null
			let retryTotal = item.total !== undefined ? item.total : null
			let retryVersion = item.rule_version || null

			if (normStatus === 'completed_with_errors' && item.run_id) {
				try {
					run = await cskh.post(API_EXECUTE, {
						preview_id: item.run_id,
						expected_rule_version: item.rule_version,
						idempotency_key: 'ui-resume-' + uuid(),
					})
				} catch (resumeErr) {
					const errCode = cskh.errorCode(resumeErr)
					if (errCode === 'FORBIDDEN') {
						throw resumeErr
					}
					run = null
				}
				if (!run || normRunStatus(run.status) === 'completed_with_errors') {
					const fresh = await cskh.post(API_PREVIEW, {
						campaign: item.campaign,
						idempotency_key: 'ui-retry-' + uuid(),
					})
					retryTotal =
						fresh.summary && fresh.summary.eligible_count !== undefined
							? Number(fresh.summary.eligible_count)
							: null
					retryVersion = fresh.rule_version
					run = await cskh.post(API_EXECUTE, {
						preview_id: fresh.preview_id,
						expected_rule_version: fresh.rule_version,
						idempotency_key: 'ui-retry-' + uuid(),
					})
				}
			} else if (normStatus === 'failed' && item.run_id) {
				try {
					run = await cskh.post(API_EXECUTE, {
						preview_id: item.run_id,
						expected_rule_version: item.rule_version,
						idempotency_key: 'ui-resume-' + uuid(),
					})
				} catch (resumeErr) {
					const errCode = cskh.errorCode(resumeErr)
					if (errCode === 'RULE_CHANGED' || errCode === 'FORBIDDEN') {
						throw resumeErr
					}
					run = null
				}
				if (!run || normRunStatus(run.status) === 'failed') {
					const fresh = await cskh.post(API_PREVIEW, {
						campaign: item.campaign,
						idempotency_key: 'ui-retry-' + uuid(),
					})
					retryTotal =
						fresh.summary && fresh.summary.eligible_count !== undefined
							? Number(fresh.summary.eligible_count)
							: null
					retryVersion = fresh.rule_version
					run = await cskh.post(API_EXECUTE, {
						preview_id: fresh.preview_id,
						expected_rule_version: fresh.rule_version,
						idempotency_key: 'ui-retry-' + uuid(),
					})
				}
			} else {
				const fresh = await cskh.post(API_PREVIEW, {
					campaign: item.campaign,
					idempotency_key: 'ui-retry-' + uuid(),
				})
				retryTotal =
					fresh.summary && fresh.summary.eligible_count !== undefined
						? Number(fresh.summary.eligible_count)
						: null
				retryVersion = fresh.rule_version
				run = await cskh.post(API_EXECUTE, {
					preview_id: fresh.preview_id,
					expected_rule_version: fresh.rule_version,
					idempotency_key: 'ui-retry-' + uuid(),
				})
			}

			await handleExecuteResult(run, {
				run_id: (run && run.run_id) || item.run_id,
				campaign: item.campaign,
				total: retryTotal,
				rule_version: retryVersion,
			})
		} catch (error) {
			cskh.renderError(alertBox, error)
		} finally {
			if (button) button.disabled = false
		}
	}

	function runStatusLabel(value) {
		const key = normRunStatus(value)
		const map = {
			planned: cskh.t('Preview'),
			queued: cskh.t('Queued'),
			pending: cskh.t('Queued'),
			running: cskh.t('Running'),
			in_progress: cskh.t('Running'),
			completed: cskh.t('Completed'),
			completed_with_errors: cskh.t('Completed with errors'),
			failed: cskh.t('Failed'),
		}
		return map[key] || value || '—'
	}

	function runBadgeClass(value) {
		const key = normRunStatus(value)
		if (key === 'completed') return cskh.stateBadge('assigned')
		if (key === 'completed_with_errors') return cskh.stateBadge('queued')
		if (key === 'queued' || key === 'running' || key === 'in_progress' || key === 'pending') {
			return cskh.stateBadge('queued')
		}
		if (key === 'failed') return cskh.stateBadge('conflict')
		return cskh.stateBadge('eligible')
	}

	async function lookupRun(runId) {
		const box = document.getElementById('dist-run-lookup-result')
		const alertBox = document.getElementById('campaign-alert')
		cskh.clear(box)
		const id = (runId || '').trim()
		if (!id) {
			cskh.alert(alertBox, 'warning', cskh.t('Run ID'))
			return
		}
		const input = document.getElementById('dist-run-id')
		if (input) input.value = id
		try {
			let run = await cskh.get(API_RUN, { run_id: id })
			const meta = buildRunMeta(run, { run_id: id, lookupOnly: true })
			run = await enrichRunFromHistory(run, meta)
			const updatedMeta = buildRunMeta(run, meta)
			renderRunLookup(box, run, updatedMeta)
			if (isActiveRunStatus(run && run.status)) {
				persistActiveRun(updatedMeta)
				trackRun(id, updatedMeta)
			}
		} catch (error) {
			cskh.renderError(box, error)
		}
	}

	function renderRunLookup(box, run, meta) {
		if (!box) return
		renderRunDetails(box, run, meta, true)
	}

	const formCard = document.getElementById('campaign-form-card')
	const distCard = document.getElementById('dist-card')
	const initLookupBtn = document.getElementById('dist-run-lookup')
	const initLookupWrap = document.getElementById('dist-run-id-wrap')
	if (formCard) formCard.hidden = !isAdmin
	if (distCard) distCard.hidden = !isAdmin
	if (initLookupBtn) initLookupBtn.hidden = !canLookup
	if (initLookupWrap) initLookupWrap.hidden = !canLookup

	document.getElementById('campaign-create').addEventListener('click', function () {
		createCampaign()
	})
	document.getElementById('campaign-cancel-edit').addEventListener('click', function () {
		cancelEditCampaign()
	})
	document.getElementById('rule-campaign').addEventListener('change', function () {
		preview = null
		document.getElementById('dist-execute').disabled = true
		cskh.clear(document.getElementById('dist-summary'))
		cskh.clear(document.getElementById('dist-targets'))
		runsPager.goto(1)
		loadRules()
		loadRuns()
	})
	document.getElementById('dist-preview').addEventListener('click', function () {
		runPreview()
	})
	document.getElementById('dist-execute').addEventListener('click', function () {
		runExecute()
	})
	document.getElementById('dist-run-lookup').addEventListener('click', function () {
		lookupRun(document.getElementById('dist-run-id').value)
	})
	document.getElementById('dist-runs-status').addEventListener('change', function () {
		runsPager.goto(1)
		loadRuns()
	})
	document.getElementById('dist-runs-prev').addEventListener('click', function () {
		runsPager.goto(Math.max(1, runsPager.page() - 1))
		loadRuns()
	})
	document.getElementById('dist-runs-next').addEventListener('click', function () {
		runsPager.goto(runsPager.page() + 1)
		loadRuns()
	})

	loadCampaigns()
		.then(function () {
			const stored = readStoredActiveRun()
			if (stored && stored.campaign) {
				const select = document.getElementById('rule-campaign')
				if (
					select &&
					Array.from(select.options).some(function (opt) {
						return opt.value === stored.campaign
					})
				) {
					select.value = stored.campaign
				}
			}
			return Promise.all([loadRules(), loadRuns()])
		})
		.then(function () {
			return recoverActiveRun()
		})
})()
