/* Customer pool (P1 §9.1).

   The list comes from `crm_cskh.api.pool_ui.list` (backed by `reporting._scope`
   so `crm_cskh.api.customers.pool_list` stays frozen for queued work items).
   The scope is decided strictly by the backend: the browser sends no `user` and
   no `team`, so it can never widen the scope (ADR-P1-07, SEC-07).

   The pool lists the **customers (leads)** of the scope with their *derived*
   distribution state (P1 §9.1): the state is computed by the backend from
   `CSKH Identity Index`, `CSKH Work Item` and `CSKH Active Claim`, and is never
   written back to `CRM Lead.status`.

   The phone number is masked: only the last four digits are shown (and omitted
   entirely for roles without PII capability such as `CSKH BOD Viewer`). */
(function () {
	'use strict'

	const API = 'crm_cskh.api.pool_ui.list'
	const PAGE_SIZE = 20

	const pager = cskh.pager({
		pageSize: PAGE_SIZE,
		range: document.getElementById('pool-range'),
		prev: document.getElementById('pool-prev'),
		next: document.getElementById('pool-next'),
	})

	/* A slower read must never overwrite a newer one (see `cskh.sequencer`). */
	const reads = cskh.sequencer()

	/* The derived distribution states (P1 §9.1). */
	const STATE_LABELS = {
		eligible: 'Not distributed',
		queued: 'Queued',
		assigned: 'Assigned',
		conflict: 'Conflict',
		revoked: 'Revoked',
		closed: 'Closed',
		invalid: 'Data error',
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
		if (value === 'closed' || value === 'invalid') return cskh.stateBadge('closed')
		return cskh.stateBadge('eligible')
	}

	function renderTable(items) {
		const table = document.getElementById('pool-table')
		const empty = document.getElementById('pool-empty')
		const rows = document.getElementById('pool-rows')
		const allowPii = !!(cskh.capabilities() && cskh.capabilities().pii)

		cskh.clear(rows)
		const list = items || []
		if (!list.length) {
			table.hidden = true
			empty.hidden = false
			return
		}
		empty.hidden = true
		list.forEach(function (item) {
			const actions = []
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
					cskh.el('td', {}, actions),
				])
			)
		})
		table.hidden = false
	}

	async function load() {
		const loading = document.getElementById('pool-loading')
		const table = document.getElementById('pool-table')
		const empty = document.getElementById('pool-empty')
		const alertBox = document.getElementById('pool-alert')
		const allowPii = !!(cskh.capabilities() && cskh.capabilities().pii)

		loading.hidden = false
		table.hidden = true
		empty.hidden = true
		cskh.clear(alertBox)

		const campaign = document.getElementById('pool-campaign').value
		const state = document.getElementById('pool-state').value
		const source = document.getElementById('pool-source').value.trim()
		const batchEl = document.getElementById('pool-batch')
		const batch = batchEl ? batchEl.value.trim() : ''
		const searchEl = document.getElementById('pool-search')
		const search = allowPii && searchEl ? searchEl.value.trim() : ''

		const mine = reads.next()
		try {
			const data = await cskh.get(API, {
				page: pager.page(),
				page_size: PAGE_SIZE,
				campaign: campaign,
				state: state,
				source: source,
				batch: batch,
				search: search,
			})
			if (mine !== reads.current()) return
			pager.setTotal(data && data.total)
			pager.render()
			renderCounters(data)
			renderTable((data && data.items) || [])
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
	function renderCounters(data) {
		const box = document.getElementById('pool-counters')
		if (!box) return
		cskh.clear(box)
		box.appendChild(
			cskh.el('div', { class: 'cskh-grid' }, [
				metric(
					cskh.t('Unique customers'),
					cskh.formatNumber(
						data && data.unique_customers !== undefined
							? data.unique_customers
							: data && data.total
					)
				),
				metric(cskh.t('Work items'), cskh.formatNumber(data && data.work_items)),
				metric(cskh.t('Scope'), cskh.scopeLabel(data && data.scope)),
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

	const searchWrap = document.getElementById('pool-search-wrap')
	if (searchWrap && !(cskh.capabilities() && cskh.capabilities().pii)) {
		searchWrap.hidden = true
	}

	document.getElementById('pool-refresh').addEventListener('click', function () {
		pager.goto(1)
		load()
	})
	document.getElementById('pool-campaign').addEventListener('change', function () {
		pager.goto(1)
		load()
	})
	document.getElementById('pool-state').addEventListener('change', function () {
		pager.goto(1)
		load()
	})
	;['pool-source', 'pool-batch', 'pool-search'].forEach(function (id) {
		const input = document.getElementById(id)
		if (!input) return
		input.addEventListener('keydown', function (event) {
			if (event.key === 'Enter') {
				event.preventDefault()
				pager.goto(1)
				load()
			}
		})
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
