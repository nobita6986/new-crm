/* Distribution dashboard (P1 §9.5).

   The figures come from `crm_cskh.api.dashboard.distribution` and
   `crm_cskh.api.dashboard.campaigns`. The backend applies the scope of the
   actor (whole installation for the BOD viewer, team for a leader, self for a
   staff member), so the browser can only filter by campaign.

   The dashboard distinguishes **current owners** (counted from the work items)
   from **assignment events** (counted from the audit trail): a transfer adds
   one event but the number of currently owned work items stays the same
   (P1 §12.5). */
(function () {
	'use strict'

	const API = 'crm_cskh.api.dashboard.distribution'
	const API_CAMPAIGNS = 'crm_cskh.api.dashboard.campaigns'
	const API_RULES = 'crm_cskh.api.campaigns.rules'

	/* The team of an assignee is not part of the dashboard payload: it is read
	   from the assignment rules of the campaign, which is the same source the
	   backend uses to build the eligibility. */
	const teamByUser = {}

	async function loadTeamMap(campaigns) {
		const list = campaigns && campaigns.length ? campaigns : ['']
		for (let index = 0; index < list.length; index += 1) {
			const campaign = list[index]
			if (!campaign) continue
			try {
				const rules = await cskh.get(API_RULES, { campaign: campaign })
				const buckets = [].concat(rules.eligible || [], rules.excluded || [])
				buckets.forEach(function (rule) {
					if (rule.user && rule.team && !teamByUser[rule.user]) {
						teamByUser[rule.user] = rule.team
					}
				})
			} catch (error) {
				/* The team column is optional: a missing rule set must not
				   break the dashboard. */
			}
		}
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
		const rows = [
			[cskh.t('Unique customers in scope'), cards.unique_customers],
			[cskh.t('Eligible in the pool'), cards.eligible_unassigned],
			[cskh.t('Assigned work items'), cards.assigned_work_items],
			[cskh.t('Unassigned or conflicted work items'), cards.unassigned_work_items],
			[cskh.t('Active campaigns'), cards.active_campaigns],
			[cskh.t('Failed batches'), cards.failed_batches],
			// The assignment events are the **audit trail**, the current owners
			// are the **live state**: a transfer adds one event but the number
			// of owned work items does not change (P1 §12.5).
			[cskh.t('Assignment events'), (extra || {}).assignment_events],
			[cskh.t('Current owners'), cards.assigned_work_items],
		]
		rows.forEach(function (row) {
			box.appendChild(metric(row[0], row[1]))
		})
	}

	function renderOwners(perUser) {
		const wrap = document.getElementById('dash-table-wrap')
		cskh.clear(wrap)
		if (!perUser || !perUser.length) {
			wrap.appendChild(
				cskh.el('div', { class: 'cskh-alert is-empty', text: cskh.t('No data in the current scope.') })
			)
			return
		}
		const totalWeight = perUser.reduce(function (sum, row) {
			return sum + (row.weight || 0)
		}, 0)
		const table = cskh.el('table', { class: 'cskh-table' }, [
			cskh.el('thead', {}, [
				cskh.el('tr', {}, [
					cskh.el('th', { text: cskh.t('Assignee') }),
					cskh.el('th', { class: 'num', text: cskh.t('Share') }),
					cskh.el('th', { class: 'num', text: cskh.t('Currently assigned') }),
					cskh.el('th', { class: 'num', text: cskh.t('Remaining capacity') }),
					cskh.el('th', { text: cskh.t('Team') }),
				]),
			]),
		])
		const body = cskh.el('tbody')
		perUser.forEach(function (row) {
			const share = totalWeight ? Math.round((row.weight / totalWeight) * 1000) / 10 : 0
			body.appendChild(
				cskh.el('tr', {}, [
					cskh.el('td', { text: row.user }),
					cskh.el('td', { class: 'num', text: share + '%' }),
					cskh.el('td', { class: 'num', text: cskh.formatNumber(row.current_total) }),
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
				cskh.el('div', { class: 'cskh-alert is-empty', text: cskh.t('No data in the current scope.') })
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
							class: cskh.stateBadge(campaign.status === 'Active' ? 'assigned' : 'queued'),
							text: campaign.status,
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

	async function load() {
		const loading = document.getElementById('dash-loading')
		const alertBox = document.getElementById('dash-alert')
		loading.hidden = false
		cskh.clear(alertBox)

		const campaign = document.getElementById('dash-campaign').value
		try {
			const data = await cskh.get(API, { campaign: campaign })
			const cards = (data && data.cards) || {}
			const perUser = (data && data.per_user) || []

			const campaignList = (data && data.campaigns) || []
			await loadTeamMap(campaignList.map(function (item) {
				return item.campaign
			}))

			renderCards(cards, {
				assignment_events: data && data.assignment_events,
			})
			renderOwners(perUser)

			const summary = await cskh.get(API_CAMPAIGNS)
			renderCampaigns((summary && summary.campaigns) || [])

			document.getElementById('dash-generated').textContent =
				cskh.t('Data as of') + ': ' + cskh.formatDate(data && data.as_of)
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

	loadCampaignFilter()
	load()
})()
