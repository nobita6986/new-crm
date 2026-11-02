/* Campaigns, assignment rules and distribution (P1 §9.3).

   Contract (T1B, frozen):
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
   numbers (P1 §7.4). */
(function () {
	'use strict'

	const API_LIST = 'crm_cskh.api.campaigns.list'
	const API_RULES = 'crm_cskh.api.campaigns.rules'
	const API_SAVE = 'crm_cskh.api.campaigns.save'
	const API_SAVE_RULE = 'crm_cskh.api.campaigns.save_rule'
	const API_PREVIEW = 'crm_cskh.api.distribution.preview'
	const API_EXECUTE = 'crm_cskh.api.distribution.execute'
	const API_RUN = 'crm_cskh.api.distribution.get_run'

	/* The preview currently held by the page. The execute button is enabled
	   only when a fresh preview exists. */
	let preview = null
	let campaigns = []

	function uuid() {
		if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID()
		return 'cskh-' + Date.now() + '-' + Math.floor(Math.random() * 1e9)
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
		}
		return map[reason] || reason || '—'
	}

	/* ------------------------------------------------------------------ */
	/* Campaigns                                                            */
	/* ------------------------------------------------------------------ */

	async function loadCampaigns() {
		const alertBox = document.getElementById('campaign-alert')
		const list = document.getElementById('campaign-list')
		const loading = document.getElementById('campaign-list-loading')
		loading.hidden = false
		cskh.clear(alertBox)
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
		const table = cskh.el('table', { class: 'cskh-table' }, [
			cskh.el('thead', {}, [
				cskh.el('tr', {}, [
					cskh.el('th', { text: cskh.t('Code') }),
					cskh.el('th', { text: cskh.t('Title') }),
					cskh.el('th', { text: cskh.t('Status') }),
					cskh.el('th', { text: cskh.t('Team scope') }),
					cskh.el('th', { class: 'num', text: cskh.t('Rule version') }),
					cskh.el('th', { text: cskh.t('Started at') }),
					cskh.el('th', { text: cskh.t('Ended at') }),
				]),
			]),
		])
		const body = cskh.el('tbody')
		items.forEach(function (campaign) {
			body.appendChild(
				cskh.el('tr', {}, [
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
							text: campaign.status,
						}),
					]),
					cskh.el('td', { text: campaign.team_scope || '—' }),
					cskh.el('td', { class: 'num', text: campaign.rule_version }),
					cskh.el('td', { text: cskh.formatDate(campaign.started_at) }),
					cskh.el('td', { text: cskh.formatDate(campaign.ended_at) }),
				])
			)
		})
		table.appendChild(body)
		list.appendChild(table)
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
		select.value = current
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
		if (!campaign) {
			loading.hidden = true
			box.appendChild(
				cskh.el('div', { class: 'cskh-alert is-empty', text: cskh.t('No assignment rule yet.') })
			)
			return
		}
		try {
			const data = await cskh.get(API_RULES, { campaign: campaign })
			renderRules(data)
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
		if (!eligible.length && !excluded.length) {
			// The "add a rule" form is still rendered: a campaign without any
			// rule is the normal starting point of an operator.
			box.appendChild(
				cskh.el('div', { class: 'cskh-alert is-empty', text: cskh.t('No assignment rule yet.') })
			)
			box.appendChild(ruleForm(null))
			return
		}
		const totalWeight = eligible.reduce(function (sum, rule) {
			return sum + (rule.weight || 0)
		}, 0)

		box.appendChild(
			cskh.el('p', { class: 'cskh-muted', text: cskh.t('Rule version') + ': ' + (data.rule_version || 1) })
		)

		const table = cskh.el('table', { class: 'cskh-table' }, [
			cskh.el('thead', {}, [
				cskh.el('tr', {}, [
					cskh.el('th', { text: cskh.t('Assignee') }),
					cskh.el('th', { text: cskh.t('Team') }),
					cskh.el('th', { class: 'num', text: cskh.t('Weight') }),
					cskh.el('th', { class: 'num', text: cskh.t('Normalized share') }),
					cskh.el('th', { class: 'num', text: cskh.t('Capacity') }),
					cskh.el('th', { class: 'num', text: cskh.t('Remaining capacity') }),
					cskh.el('th', { text: cskh.t('Eligibility') }),
					cskh.el('th', {}),
				]),
			]),
		])
		const body = cskh.el('tbody')

		eligible.forEach(function (rule) {
			const share = totalWeight ? Math.round((rule.weight / totalWeight) * 1000) / 10 : 0
			body.appendChild(
				cskh.el('tr', {}, [
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
					cskh.el('td', {}, [ruleForm(rule)]),
				])
			)
		})

		excluded.forEach(function (rule) {
			body.appendChild(
				cskh.el('tr', {}, [
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
					cskh.el('td', {}),
				])
			)
		})

		table.appendChild(body)
		box.appendChild(table)
		box.appendChild(ruleForm(null))
	}

	/** The inline form to save (or change) the rule of one assignee. */
	function ruleForm(rule) {
		const user = document.createElement('input')
		user.className = 'cskh-input'
		user.placeholder = cskh.t('Assignee')
		user.value = (rule && rule.user) || ''

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
		capacity.value = (rule && rule.capacity) || 0

		const save = cskh.el('button', {
			class: 'cskh-btn is-primary',
			type: 'button',
			text: cskh.t('Save'),
		})

		save.addEventListener('click', function () {
			const campaign = document.getElementById('rule-campaign').value
			if (!campaign) return
			const alertBox = document.getElementById('campaign-alert')
			cskh.post(API_SAVE_RULE, {
				campaign: campaign,
				user: user.value.trim(),
				weight: Number(weight.value || 0),
				capacity_limit: Number(capacity.value || 0),
				team: team.value.trim() || null,
				enabled: 1,
			})
				.then(function () {
					cskh.alert(alertBox, 'success', cskh.t('Save'))
					loadRules()
					loadCampaigns()
				})
				.catch(function (error) {
					cskh.renderError(alertBox, error)
				})
		})

		return cskh.el('div', { class: 'cskh-inline-list' }, [user, team, weight, capacity, save])
	}

	/* ------------------------------------------------------------------ */
	/* Distribution: preview -> confirm -> execute                          */
	/* ------------------------------------------------------------------ */

	async function runPreview() {
		const alertBox = document.getElementById('campaign-alert')
		const campaign = document.getElementById('rule-campaign').value
		const summary = document.getElementById('dist-summary')
		const targets = document.getElementById('dist-targets')
		cskh.clear(summary)
		cskh.clear(targets)
		cskh.clear(alertBox)
		preview = null
		document.getElementById('dist-execute').disabled = true

		if (!campaign) {
			cskh.alert(alertBox, 'warning', cskh.t('Campaign'))
			return
		}
		try {
			const data = await cskh.post(API_PREVIEW, {
				campaign: campaign,
				idempotency_key: 'ui-preview-' + uuid(),
			})
			preview = data
			renderPreview(data)
		} catch (error) {
			cskh.renderError(alertBox, error)
		}
	}

	function renderPreview(data) {
		const summary = document.getElementById('dist-summary')
		const targets = document.getElementById('dist-targets')
		const info = (data && data.summary) || {}
		const simulated = info.simulated || {}

		summary.appendChild(
			cskh.el('div', { class: 'cskh-grid' }, [
				metric(cskh.t('Preview ID'), data.preview_id),
				metric(cskh.t('Eligible customers'), info.eligible_count),
				metric(cskh.t('Excluded customers'), info.excluded_count),
				metric(cskh.t('Rule version'), data.rule_version),
			])
		)
		summary.appendChild(
			cskh.el('p', { class: 'cskh-muted' })
		)
		summary.appendChild(
			cskh.el('p', {
				class: 'cskh-muted',
				text:
					cskh.t('Selection fingerprint') + ': ' + (data.selection_fingerprint || '—'),
			})
		)
		summary.appendChild(
			cskh.el('p', {
				class: 'cskh-muted',
				text: cskh.t('Expires at') + ': ' + cskh.formatDate(data.expires_at),
			})
		)

		const table = cskh.el('table', { class: 'cskh-table' }, [
			cskh.el('thead', {}, [
				cskh.el('tr', {}, [
					cskh.el('th', { text: cskh.t('Assignee') }),
					cskh.el('th', { class: 'num', text: cskh.t('Planned') }),
				]),
			]),
		])
		const body = cskh.el('tbody')
		Object.keys(simulated).forEach(function (user) {
			body.appendChild(
				cskh.el('tr', {}, [
					cskh.el('td', { text: user }),
					cskh.el('td', { class: 'num', text: cskh.formatNumber(simulated[user]) }),
				])
			)
		})
		table.appendChild(body)
		targets.appendChild(table)

		document.getElementById('dist-execute').disabled = false
	}

	async function runExecute() {
		const alertBox = document.getElementById('campaign-alert')
		const results = document.getElementById('dist-results')
		cskh.clear(results)
		if (!preview) return
		try {
			const data = await cskh.post(API_EXECUTE, {
				preview_id: preview.preview_id,
				expected_rule_version: preview.rule_version,
				idempotency_key: 'ui-execute-' + uuid(),
			})
			renderRun(data)
			preview = null
			document.getElementById('dist-execute').disabled = true
		} catch (error) {
			const code = cskh.renderError(alertBox, error)
			if (code === 'RULE_CHANGED' || code === 'PREVIEW_EXPIRED') {
				cskh.alert(
					alertBox,
					'warning',
					code === 'RULE_CHANGED'
						? cskh.t('The rules changed after the preview. Run the preview again.')
						: cskh.t('The preview has expired. Run the preview again.'),
					code
				)
				document.getElementById('dist-execute').disabled = true
			}
		}
	}

	function renderRun(run) {
		const results = document.getElementById('dist-results')
		cskh.clear(results)
		if (!run) return
		const partial = run.errors > 0
		cskh.alert(
			results,
			partial ? 'warning' : 'success',
			partial ? cskh.t('Distribution completed with errors.') : cskh.t('Distribution completed.'),
			run.status
		)
		results.appendChild(
			cskh.el('div', { class: 'cskh-grid' }, [
				metric(cskh.t('Run ID'), run.run_id),
				metric(cskh.t('Assigned in this run'), run.created),
				metric(cskh.t('Failed'), run.errors),
			])
		)
		const assigned = run.assigned || {}
		const table = cskh.el('table', { class: 'cskh-table' }, [
			cskh.el('thead', {}, [
				cskh.el('tr', {}, [
					cskh.el('th', { text: cskh.t('Assignee') }),
					cskh.el('th', { class: 'num', text: cskh.t('Assigned in this run') }),
				]),
			]),
		])
		const body = cskh.el('tbody')
		Object.keys(assigned).forEach(function (user) {
			body.appendChild(
				cskh.el('tr', {}, [
					cskh.el('td', { text: user }),
					cskh.el('td', { class: 'num', text: cskh.formatNumber(assigned[user]) }),
				])
			)
		})
		table.appendChild(body)
		results.appendChild(table)

		if (run.errors > 0) {
			const skipped = run.skipped || []
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
			results.appendChild(failedTable)
		}
	}

	function metric(label, value) {
		return cskh.el('div', { class: 'cskh-metric' }, [
			cskh.el('div', { class: 'label', text: label }),
			cskh.el('div', { class: 'value', text: value === undefined || value === null ? '—' : String(value) }),
		])
	}

	/* ------------------------------------------------------------------ */

	async function createCampaign() {
		const alertBox = document.getElementById('campaign-alert')
		const code = document.getElementById('campaign-code').value.trim()
		const title = document.getElementById('campaign-title').value.trim()
		if (!code || !title) {
			cskh.alert(alertBox, 'warning', cskh.t('Code') + ' / ' + cskh.t('Title'))
			return
		}
		try {
			await cskh.post(API_SAVE, {
				code: code,
				title: title,
				description: document.getElementById('campaign-description').value.trim() || null,
				status: document.getElementById('campaign-status').value,
				team_scope: document.getElementById('campaign-team').value.trim() || null,
			})
			cskh.alert(alertBox, 'success', cskh.t('Save'))
			document.getElementById('campaign-code').value = ''
			document.getElementById('campaign-title').value = ''
			document.getElementById('campaign-description').value = ''
			loadCampaigns().then(loadRules)
		} catch (error) {
			cskh.renderError(alertBox, error)
		}
	}

	document.getElementById('campaign-create').addEventListener('click', function () {
		createCampaign()
	})
	document.getElementById('rule-campaign').addEventListener('change', function () {
		loadRules()
	})
	document.getElementById('dist-preview').addEventListener('click', function () {
		runPreview()
	})
	document.getElementById('dist-execute').addEventListener('click', function () {
		runExecute()
	})

	loadCampaigns().then(loadRules)
})()
