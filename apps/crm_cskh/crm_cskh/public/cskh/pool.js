/* Customer pool (P1 §9.1).

   The list comes from `crm_cskh.api.customers.pool_list`. The scope is decided
   by the backend: the browser sends no `user` and no `team`, so it can never
   widen the scope (ADR-P1-07).

   The pool lists the **customers (leads)** of the scope with their *derived*
   distribution state (P1 §9.1): the state is computed by the backend from the
   work items and the active claims and is never written back to
   `CRM Lead.status`.

   The phone number is masked: only the last four digits are shown, so a
   screenshot of the pool never leaks a real phone number. */
(function () {
	'use strict'

	const API = 'crm_cskh.api.customers.pool_list'
	const PAGE_SIZE = 20

	const pager = cskh.pager({
		pageSize: PAGE_SIZE,
		range: document.getElementById('pool-range'),
		prev: document.getElementById('pool-prev'),
		next: document.getElementById('pool-next'),
	})

	/* The derived distribution states (P1 §9.1). */
	const STATE_LABELS = {
		eligible: 'Eligible',
		queued: 'Queued',
		assigned: 'Assigned',
		conflict: 'Conflict',
		revoked: 'Revoked',
		closed: 'Closed',
	}

	function stateLabel(value) {
		const key = STATE_LABELS[value] ? value : 'eligible'
		return cskh.t(STATE_LABELS[key])
	}

	function stateBadgeClass(value) {
		if (value === 'assigned') return cskh.stateBadge('assigned')
		if (value === 'queued') return cskh.stateBadge('queued')
		if (value === 'conflict') return cskh.stateBadge('conflict')
		if (value === 'revoked') return cskh.stateBadge('revoked')
		if (value === 'closed') return cskh.stateBadge('closed')
		return cskh.stateBadge('eligible')
	}

	async function load() {
		const loading = document.getElementById('pool-loading')
		const table = document.getElementById('pool-table')
		const empty = document.getElementById('pool-empty')
		const rows = document.getElementById('pool-rows')
		const alertBox = document.getElementById('pool-alert')

		loading.hidden = false
		table.hidden = true
		empty.hidden = true
		cskh.clear(alertBox)

		const campaign = document.getElementById('pool-campaign').value
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
			renderCounters(data, items.length)
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
						cskh.el('td', { text: item.source || '—' }),
						cskh.el('td', {
							text:
								item.campaign_title ||
								item.campaign_code ||
								item.campaign ||
								cskh.t('No data'),
						}),
						cskh.el('td', {}, [
							cskh.el('span', {
								class: stateBadgeClass(item.state),
								text: stateLabel(item.state),
							}),
						]),
						cskh.el('td', { text: item.owner || cskh.t('No data') }),
						cskh.el('td', { text: cskh.formatDate(item.created_at) }),
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

	/**
	 * The two counters of P1 §9.1: the **unique customers** (the leads) and
	 * the **work items per campaign**. They are kept apart so the page never
	 * shows a fictitious number.
	 */
	function renderCounters(data, shown) {
		const box = document.getElementById('pool-counters')
		if (!box) return
		cskh.clear(box)
		box.appendChild(
			cskh.el('div', { class: 'cskh-grid' }, [
				metric(
					cskh.t('Unique customers'),
					data && data.unique_customers !== undefined ? data.unique_customers : data && data.total
				),
				metric(cskh.t('Work items'), data && data.work_items),
				metric(cskh.t('Scope'), data && data.scope),
			])
		)
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
		const select = document.getElementById('pool-campaign')
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
			/* The campaign filter is optional: a failure must not block the list. */
		}
	}

	document.getElementById('pool-refresh').addEventListener('click', function () {
		pager.goto(1)
		load()
	})
	document.getElementById('pool-prev').addEventListener('click', function () {
		pager.goto(Math.max(1, pager.page() - 1))
		load()
	})
	document.getElementById('pool-next').addEventListener('click', function () {
		pager.goto(pager.page() + 1)
		load()
	})

	loadCampaigns()
	load()
})()
