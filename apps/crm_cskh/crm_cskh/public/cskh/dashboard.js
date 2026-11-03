/* Distribution dashboard (P1 §9.5).

   The figures come from `crm_cskh.api.dashboard.distribution` and
   `crm_cskh.api.dashboard.campaigns`. The backend applies the scope of the
   actor (whole installation for the BOD viewer and CRM Admin, team subtree for
   a leader), so the browser never sends a `user` or `team` parameter to widen
   the scope (ADR-P1-07). The `Team`, `Assignee` and `Run ID` controls narrow
   the already-scoped breakdown on the client.

   The dashboard distinguishes **current owners** (counted from live work
   items) from **assignment events** (counted from the append-only audit
   trail): a manual transfer adds one event while the number of currently owned
   work items stays constant (P1 §12.5). */
(function () {
	'use strict'

	const API = 'crm_cskh.api.dashboard.distribution'
	const API_CAMPAIGNS = 'crm_cskh.api.dashboard.campaigns'
	const API_RULES = 'crm_cskh.api.campaigns.rules'
	const API_RUNS = 'crm_cskh.api.run_history.runs'

	const teamByUser = {}
	let lastPerUser = []
	let lastRuns = []
	let assignedAllRunsByUser = {}
	let assignedPerRun = {}

	const reads = cskh.sequencer()

	function runStatusLabel(value) {
		const map = {
			planned: cskh.t('Preview'),
			running: cskh.t('Running'),
			completed: cskh.t('Completed'),
			completed_with_errors: cskh.t('Completed with errors'),
			failed: cskh.t('Failed'),
		}
		return map[value] || value || '—'
	}

	function runBadgeClass(value) {
		if (value === 'completed') return cskh.stateBadge('assigned')
		if (value === 'completed_with_errors') return cskh.stateBadge('queued')
		if (value === 'failed') return cskh.stateBadge('invalid')
		return cskh.stateBadge('eligible')
	}

	async function loadTeamMap(campaigns) {
		const list = (campaigns || []).filter(Boolean)
		for (let index = 0; index < list.length; index += 1) {
			const campaign = list[index]
			try {
				const rules = await cskh.get(API_RULES, { campaign: campaign })
				const buckets = [].concat(rules.eligible || [], rules.excluded || [])
				buckets.forEach(function (rule) {
					if (rule.user && rule.team && !teamByUser[rule.user]) {
						teamByUser[rule.user] = rule.team
					}
				})
			} catch (error) {
				/* Optional enrichment: never block the dashboard. */
			}
		}
	}

	async function loadRunsSnapshot(campaign) {
		const allAssigned = {}
		const byRun = {}
		let items = []
		try {
			const runs = await cskh.get(API_RUNS, {
				page: 1,
				page_size: 50,
				campaign: campaign || '',
			})
			items = (runs && runs.items) || []
			items.forEach(function (item) {
				const runMap = {}
				const assigned = item.assigned || {}
				Object.keys(assigned).forEach(function (user) {
					const n = Number(assigned[user] || 0)
					runMap[user] = n
					if (item.status === 'completed' || item.status === 'completed_with_errors') {
						allAssigned[user] = (allAssigned[user] || 0) + n
					}
				})
				byRun[item.run_id] = runMap
			})
		} catch (error) {
			/* Optional enrichment: never block the dashboard. */
		}
		return { items: items, allAssigned: allAssigned, byRun: byRun }
	}

	function metric(label, value) {
		return cskh.el('div', { class: 'cskh-metric' }, [
			cskh.el('div', { class: 'label', text: label }),
			cskh.el('div', { class: 'value', text: cskh.formatNumber(value) }),
		])
	}

	function renderCards(cards, extra) {
		const box = document.getElementById('dash-cards')
		cskh.clear(box)
		const currentRows = [
			[cskh.t('Unique customers in scope'), cards.unique_customers],
			[cskh.t('Eligible in the pool'), cards.eligible_unassigned],
			[cskh.t('Assigned work items'), cards.assigned_work_items],
			[cskh.t('Unassigned or conflicted work items'), cards.unassigned_work_items],
			[cskh.t('Revoked'), cards.revoked_work_items],
			[cskh.t('Closed'), cards.closed_work_items],
			[cskh.t('Active claims'), cards.active_claims],
			[cskh.t('Active campaigns'), cards.active_campaigns],
			[cskh.t('Failed batches'), cards.failed_batches],
			[cskh.t('Failed runs'), cards.failed_runs],
		]
		const eventRows = [
			[cskh.t('Assignment events'), (extra || {}).assignment_events],
			[cskh.t('Current owners'), cards.assigned_work_items],
		]

		box.appendChild(
			cskh.el('div', { class: 'cskh-panel' }, [
				cskh.el('h3', { text: cskh.t('Current ownership (live work items)') }),
				cskh.el(
					'div',
					{ class: 'cskh-grid' },
					currentRows.map(function (row) {
						return metric(row[0], row[1])
					})
				),
			])
		)
		box.appendChild(
			cskh.el('div', { class: 'cskh-panel' }, [
				cskh.el('h3', { text: cskh.t('Assignment events (audit trail)') }),
				cskh.el(
					'div',
					{ class: 'cskh-grid' },
					eventRows.map(function (row) {
						return metric(row[0], row[1])
					})
				),
				cskh.el('p', {
					class: 'cskh-muted',
					text: cskh.t(
						'A transfer adds one event but the number of owned work items stays the same.'
					),
				}),
			])
		)
	}

	function refreshNarrowingOptions(perUser, runs) {
		const teamSelect = document.getElementById('dash-team')
		const userSelect = document.getElementById('dash-user')
		const runSelect = document.getElementById('dash-run')
		if (!teamSelect || !userSelect) return

		const prevTeam = teamSelect.value
		const prevUser = userSelect.value
		const prevRun = runSelect ? runSelect.value : ''

		const teams = {}
		const users = []
		;(perUser || []).forEach(function (row) {
			if (row.user) users.push(row.user)
			const tName = teamByUser[row.user]
			if (tName) teams[tName] = true
		})

		cskh.clear(teamSelect)
		teamSelect.appendChild(cskh.el('option', { value: '', text: cskh.t('All') }))
		Object.keys(teams)
			.sort()
			.forEach(function (tName) {
				teamSelect.appendChild(cskh.el('option', { value: tName, text: tName }))
			})
		if (prevTeam && teams[prevTeam]) teamSelect.value = prevTeam

		cskh.clear(userSelect)
		userSelect.appendChild(cskh.el('option', { value: '', text: cskh.t('All') }))
		users.forEach(function (user) {
			userSelect.appendChild(cskh.el('option', { value: user, text: user }))
		})
		if (prevUser && users.indexOf(prevUser) >= 0) userSelect.value = prevUser

		if (runSelect) {
			cskh.clear(runSelect)
			runSelect.appendChild(cskh.el('option', { value: '', text: cskh.t('All') }))
			const runIds = []
			;(runs || []).forEach(function (item) {
				if (!item.run_id || item.status === 'planned') return
				runIds.push(item.run_id)
				runSelect.appendChild(
					cskh.el('option', {
						value: item.run_id,
						text: item.run_id + ' (' + runStatusLabel(item.status) + ')',
					})
				)
			})
			if (prevRun && runIds.indexOf(prevRun) >= 0) runSelect.value = prevRun
		}
	}

	function renderOwners() {
		const wrap = document.getElementById('dash-table-wrap')
		const teamSelect = document.getElementById('dash-team')
		const userSelect = document.getElementById('dash-user')
		const runSelect = document.getElementById('dash-run')
		const wantTeam = teamSelect ? teamSelect.value : ''
		const wantUser = userSelect ? userSelect.value : ''
		const wantRun = runSelect ? runSelect.value : ''

		cskh.clear(wrap)
		const totalWeight = (lastPerUser || []).reduce(function (sum, row) {
			return sum + (row.weight || 0)
		}, 0)

		const filtered = (lastPerUser || []).filter(function (row) {
			if (wantUser && row.user !== wantUser) return false
			if (wantTeam && (teamByUser[row.user] || '') !== wantTeam) return false
			return true
		})

		if (!filtered.length) {
			wrap.appendChild(
				cskh.el('div', {
					class: 'cskh-alert is-empty',
					text: cskh.t('No data in the current scope.'),
				})
			)
			return
		}

		const activeRunMap = wantRun ? assignedPerRun[wantRun] || {} : assignedAllRunsByUser

		const table = cskh.el('table', { class: 'cskh-table' }, [
			cskh.el('thead', {}, [
				cskh.el('tr', {}, [
					cskh.el('th', { text: cskh.t('Assignee') }),
					cskh.el('th', { class: 'num', text: cskh.t('Weight') }),
					cskh.el('th', { class: 'num', text: cskh.t('Share') }),
					cskh.el('th', { class: 'num', text: cskh.t('Currently assigned') }),
					cskh.el('th', { class: 'num', text: cskh.t('Assigned in the run') }),
					cskh.el('th', { class: 'num', text: cskh.t('Capacity') }),
					cskh.el('th', { class: 'num', text: cskh.t('Remaining capacity') }),
					cskh.el('th', { text: cskh.t('Team') }),
				]),
			]),
		])
		const body = cskh.el('tbody')
		filtered.forEach(function (row) {
			const share = totalWeight ? Math.round((row.weight / totalWeight) * 1000) / 10 : 0
			const runTotal =
				wantRun
					? activeRunMap[row.user] || 0
					: activeRunMap[row.user] !== undefined
						? activeRunMap[row.user]
						: row.current_total
			body.appendChild(
				cskh.el('tr', {}, [
					cskh.el('td', { text: row.user }),
					cskh.el('td', { class: 'num', text: cskh.formatNumber(row.weight) }),
					cskh.el('td', { class: 'num', text: share + '%' }),
					cskh.el('td', { class: 'num', text: cskh.formatNumber(row.current_total) }),
					cskh.el('td', { class: 'num', text: cskh.formatNumber(runTotal) }),
					cskh.el('td', {
						class: 'num',
						text: cskh.capacityLabel(row.capacity),
					}),
					cskh.el('td', {
						class: 'num',
						text: cskh.capacityLabel(row.remaining_capacity),
					}),
					cskh.el('td', { text: teamByUser[row.user] || '—' }),
				])
			)
		})
		table.appendChild(body)
		wrap.appendChild(table)
	}

	function renderCampaigns(campaigns) {
		const box = document.getElementById('dash-campaigns')
		cskh.clear(box)
		if (!campaigns || !campaigns.length) {
			box.appendChild(
				cskh.el('div', {
					class: 'cskh-alert is-empty',
					text: cskh.t('No data in the current scope.'),
				})
			)
			return
		}
		const table = cskh.el('table', { class: 'cskh-table' }, [
			cskh.el('thead', {}, [
				cskh.el('tr', {}, [
					cskh.el('th', { text: cskh.t('Campaign') }),
					cskh.el('th', { text: cskh.t('Status') }),
					cskh.el('th', { class: 'num', text: cskh.t('Rule version') }),
					cskh.el('th', { class: 'num', text: cskh.t('Currently assigned') }),
					cskh.el('th', { class: 'num', text: cskh.t('Eligible in the pool') }),
				]),
			]),
		])
		const body = cskh.el('tbody')
		campaigns.forEach(function (campaign) {
			body.appendChild(
				cskh.el('tr', {}, [
					cskh.el('td', { text: campaign.title || campaign.code }),
					cskh.el('td', {}, [
						cskh.el('span', {
							class: cskh.stateBadge(
								campaign.status === 'Active' ? 'assigned' : 'queued'
							),
							text: cskh.campaignStatusLabel(campaign.status),
						}),
					]),
					cskh.el('td', { class: 'num', text: campaign.rule_version }),
					cskh.el('td', { class: 'num', text: cskh.formatNumber(campaign.assigned) }),
					cskh.el('td', { class: 'num', text: cskh.formatNumber(campaign.queued) }),
				])
			)
		})
		table.appendChild(body)
		box.appendChild(table)
	}

	function renderRuns(runs) {
		const box = document.getElementById('dash-runs')
		if (!box) return
		const runSelect = document.getElementById('dash-run')
		const wantRun = runSelect ? runSelect.value : ''
		cskh.clear(box)

		const visible = (runs || []).filter(function (item) {
			if (item.status === 'planned') return false
			if (wantRun && item.run_id !== wantRun) return false
			return true
		})

		if (!visible.length) {
			box.appendChild(
				cskh.el('div', {
					class: 'cskh-alert is-empty',
					text: cskh.t('No data in the current scope.'),
				})
			)
			return
		}

		const table = cskh.el('table', { class: 'cskh-table' }, [
			cskh.el('thead', {}, [
				cskh.el('tr', {}, [
					cskh.el('th', { text: cskh.t('Run ID') }),
					cskh.el('th', { text: cskh.t('Campaign') }),
					cskh.el('th', { text: cskh.t('Status') }),
					cskh.el('th', { class: 'num', text: cskh.t('Rule version') }),
					cskh.el('th', { class: 'num', text: cskh.t('Created') }),
					cskh.el('th', { class: 'num', text: cskh.t('Errors') }),
					cskh.el('th', { text: cskh.t('Ended at') }),
				]),
			]),
		])
		const body = cskh.el('tbody')
		visible.forEach(function (item) {
			body.appendChild(
				cskh.el('tr', {}, [
					cskh.el('td', { class: 'cskh-mono', text: item.run_id || '—' }),
					cskh.el('td', { text: item.campaign_title || item.campaign || '—' }),
					cskh.el('td', {}, [
						cskh.el('span', {
							class: runBadgeClass(item.status),
							text: runStatusLabel(item.status),
						}),
					]),
					cskh.el('td', { class: 'num', text: cskh.formatNumber(item.rule_version) }),
					cskh.el('td', { class: 'num', text: cskh.formatNumber(item.created) }),
					cskh.el('td', { class: 'num', text: cskh.formatNumber(item.errors) }),
					cskh.el('td', { text: cskh.formatDate(item.finished_at || item.started_at) }),
				])
			)
		})
		table.appendChild(body)
		box.appendChild(table)
	}

	async function load() {
		const loading = document.getElementById('dash-loading')
		const alertBox = document.getElementById('dash-alert')
		loading.hidden = false
		cskh.clear(alertBox)

		const campaign = document.getElementById('dash-campaign').value
		const dateFrom = document.getElementById('dash-date-from').value
		const dateTo = document.getElementById('dash-date-to').value
		if (dateFrom && dateTo && dateFrom > dateTo) {
			cskh.alert(
				alertBox,
				'warning',
				cskh.t('The start date must not be after the end date.')
			)
			loading.hidden = true
			return
		}
		const mine = reads.next()
		try {
			const pair = await Promise.all([
				cskh.get(API, {
					campaign: campaign,
					date_from: dateFrom,
					date_to: dateTo,
				}),
				cskh.get(API_CAMPAIGNS),
				loadRunsSnapshot(campaign),
			])
			if (mine !== reads.current()) return
			const data = pair[0]
			const summary = pair[1]
			const runSnap = pair[2] || {}
			lastRuns = runSnap.items || []
			assignedAllRunsByUser = runSnap.allAssigned || {}
			assignedPerRun = runSnap.byRun || {}

			const campaignItems = (summary && summary.campaigns) || []
			const campaignIds = campaign
				? [campaign]
				: campaignItems.map(function (item) {
						return item.campaign
					})
			await loadTeamMap(campaignIds)
			if (mine !== reads.current()) return

			const cards = (data && data.cards) || {}
			lastPerUser = (data && data.per_user) || []

			renderCards(cards, {
				assignment_events: data && data.assignment_events,
			})
			refreshNarrowingOptions(lastPerUser, lastRuns)
			renderOwners()
			renderCampaigns(campaignItems)
			renderRuns(lastRuns)

			document.getElementById('dash-generated').textContent =
				cskh.t('Scope') +
				': ' +
				cskh.scopeLabel(data && data.scope) +
				' · ' +
				cskh.t('Data as of') +
				': ' +
				cskh.formatDate(data && data.as_of)
		} catch (error) {
			cskh.renderError(alertBox, error)
		} finally {
			loading.hidden = true
		}
	}

	async function loadCampaignFilter() {
		const select = document.getElementById('dash-campaign')
		try {
			const data = await cskh.get(API_CAMPAIGNS)
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
			/* The filter is optional. */
		}
	}

	document.getElementById('dash-refresh').addEventListener('click', function () {
		load()
	})
	document.getElementById('dash-clear-dates').addEventListener('click', function () {
		document.getElementById('dash-date-from').value = ''
		document.getElementById('dash-date-to').value = ''
		load()
	})
	document.getElementById('dash-date-from').addEventListener('change', function () {
		load()
	})
	document.getElementById('dash-date-to').addEventListener('change', function () {
		load()
	})
	document.getElementById('dash-campaign').addEventListener('change', function () {
		load()
	})
	document.getElementById('dash-team').addEventListener('change', function () {
		renderOwners()
	})
	document.getElementById('dash-user').addEventListener('change', function () {
		renderOwners()
	})
	document.getElementById('dash-run').addEventListener('change', function () {
		renderOwners()
		renderRuns(lastRuns)
	})

	loadCampaignFilter()
	load()
})()
