/* My customers (P1 §9.4).

   The list comes from `crm_cskh.api.customers.my_list`, which always resolves
   the caller as the owner: there is no `user` parameter, so the browser can
   only ever see the work that belongs to the signed-in user. Opening a lead
   in CRM is a normal CRM link: the CRM route enforces its own permission. */
(function () {
	'use strict'

	const API = 'crm_cskh.api.customers.my_list'
	const PAGE_SIZE = 20

	const pager = cskh.pager({
		pageSize: PAGE_SIZE,
		range: document.getElementById('mine-range'),
		prev: document.getElementById('mine-prev'),
		next: document.getElementById('mine-next'),
	})

	function stateLabel(value) {
		const map = {
			assigned: cskh.t('Assigned'),
			queued: cskh.t('Queued'),
			revoked: cskh.t('Revoked'),
			closed: cskh.t('Closed'),
		}
		return map[value] || value || '—'
	}

	async function load() {
		const loading = document.getElementById('mine-loading')
		const table = document.getElementById('mine-table')
		const empty = document.getElementById('mine-empty')
		const rows = document.getElementById('mine-rows')
		const alertBox = document.getElementById('mine-alert')

		loading.hidden = false
		table.hidden = true
		empty.hidden = true
		cskh.clear(alertBox)

		const campaign = document.getElementById('mine-campaign').value
		try {
			const data = await cskh.get(API, {
				page: pager.page(),
				page_size: PAGE_SIZE,
				campaign: campaign,
			})
			pager.setTotal(data && data.total)
			pager.render()
			cskh.clear(rows)
			const items = (data && data.items) || []
			if (!items.length) {
				empty.hidden = false
				return
			}
			items.forEach(function (item) {
				rows.appendChild(
					cskh.el('tr', {}, [
						cskh.el('td', { text: item.display_name || item.lead }),
						cskh.el('td', {
							class: 'cskh-mono',
							text: cskh.maskPhone(item.phone),
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
						cskh.el('td', {}, [
							cskh.el('a', {
								class: 'cskh-btn',
								href: '/crm/leads/' + item.lead,
								text: cskh.t('Open in CRM'),
							}),
						]),
					])
				)
			})
			table.hidden = false
		} catch (error) {
			cskh.renderError(alertBox, error)
		} finally {
			loading.hidden = true
		}
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

	document.getElementById('mine-refresh').addEventListener('click', function () {
		pager.goto(1)
		load()
	})
	document.getElementById('mine-prev').addEventListener('click', function () {
		pager.goto(Math.max(1, pager.page() - 1))
		load()
	})
	document.getElementById('mine-next').addEventListener('click', function () {
		pager.goto(pager.page() + 1)
		load()
	})

	loadCampaigns()
	load()
})()
