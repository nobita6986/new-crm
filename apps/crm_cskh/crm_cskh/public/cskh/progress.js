/* Basic Scoped Care Progress (`/cskh/progress`, P2 WP09).

   Displays 0-PII operational counts from `crm_cskh.api.workflow.progress_summary`
   separated strictly into:
     1) Work Item counters (distinct work items)
     2) Contact Attempt counters (effective non-voided attempts)
     3) Callback Task counters (`open`, `due_today`, `overdue`)
     4) Per-user breakdown within the caller's server-resolved scope.
   Explicitly states it is NOT a KPI or SLA score (P3 boundary).
*/
(function () {
	'use strict'

	const caps = cskh.capabilities() || {}
	const canLoadCampaignList = !!(caps.admin || caps.bod || caps.leader)
	const reads = cskh.sequencer()

	function metric(label, value) {
		return cskh.el('div', { class: 'cskh-metric' }, [
			cskh.el('div', { class: 'label', text: label }),
			cskh.el('div', { class: 'value', text: cskh.formatNumber(value || 0) }),
		])
	}

	function renderData(data) {
		const cards = (data && data.cards) || {}
		const byChannel = (data && data.by_channel) || {}
		const perUser = (data && data.per_user) || []

		const scopeBadge = document.getElementById('prog-scope-badge')
		if (scopeBadge) {
			scopeBadge.textContent =
				cskh.t('Data scope') +
				': ' +
				cskh.scopeLabel((data && data.scope) || 'self') +
				(data && data.as_of ? ' · ' + cskh.formatDate(data.as_of) : '')
		}

		// 1. Work item counters
		const wiMetrics = document.getElementById('prog-wi-metrics')
		cskh.clear(wiMetrics)
		wiMetrics.appendChild(
			metric(cskh.t('Active assigned work items'), cards.total_active_owned)
		)
		wiMetrics.appendChild(metric(cskh.t('Newly assigned'), cards.assigned))
		wiMetrics.appendChild(metric(cskh.t('In progress'), cards.in_progress))
		wiMetrics.appendChild(
			metric(cskh.t('On-hold work items (awaiting callback)'), cards.on_hold)
		)
		wiMetrics.appendChild(metric(cskh.t('Completed work items'), cards.closed))
		wiMetrics.appendChild(metric(cskh.t('Do-not-contact (DNC) work items'), cards.dnc_leads))
		document.getElementById('prog-wi-card').hidden = false

		// 2. Contact attempt counters
		const attMetrics = document.getElementById('prog-att-metrics')
		cskh.clear(attMetrics)
		attMetrics.appendChild(
			metric(cskh.t('Effective contact attempts'), cards.effective_attempts)
		)
		attMetrics.appendChild(metric(cskh.t('Connected'), cards.connected_attempts))
		attMetrics.appendChild(
			metric(cskh.t('Attempted, not connected'), cards.unreached_attempts)
		)
		attMetrics.appendChild(
			metric(cskh.t('Pending correction review'), cards.pending_corrections)
		)

		const chWrap = document.getElementById('prog-channel-breakdown')
		cskh.clear(chWrap)
		const channelKeys = Object.keys(byChannel)
		if (channelKeys.length) {
			const badges = channelKeys.map(function (ch) {
				return cskh.el('span', {
					class: cskh.stateBadge('in_progress'),
					text: cskh.channelLabel(ch) + ': ' + cskh.formatNumber(byChannel[ch] || 0),
				})
			})
			chWrap.appendChild(cskh.el('div', { class: 'cskh-badge-stack' }, badges))
		}
		document.getElementById('prog-att-card').hidden = false

		// 3. Callback task counters
		const cbMetrics = document.getElementById('prog-cb-metrics')
		cskh.clear(cbMetrics)
		cbMetrics.appendChild(metric(cskh.t('Open callbacks'), cards.open_followups))
		cbMetrics.appendChild(metric(cskh.t('Callbacks due today'), cards.due_today_followups))
		cbMetrics.appendChild(metric(cskh.t('Overdue callbacks'), cards.overdue_followups))
		document.getElementById('prog-cb-card').hidden = false

		// 4. Per-user breakdown
		const usersWrap = document.getElementById('prog-users-wrap')
		const usersCard = document.getElementById('prog-users-card')
		cskh.clear(usersWrap)
		if (!perUser.length) {
			usersWrap.appendChild(
				cskh.el('div', {
					class: 'cskh-alert is-empty',
					text: cskh.t('No data in the current scope.'),
				})
			)
		} else {
			const tbody = cskh.el('tbody')
			perUser.forEach(function (u) {
				tbody.appendChild(
					cskh.el('tr', {}, [
						cskh.el('td', { text: u.user || '—' }),
						cskh.el('td', { text: u.team || '—' }),
						cskh.el('td', { class: 'cskh-mono', text: cskh.formatNumber(u.assigned || 0) }),
						cskh.el('td', { class: 'cskh-mono', text: cskh.formatNumber(u.in_progress || 0) }),
						cskh.el('td', { class: 'cskh-mono', text: cskh.formatNumber(u.on_hold || 0) }),
						cskh.el('td', { class: 'cskh-mono', text: cskh.formatNumber(u.closed || 0) }),
						cskh.el('td', {
							class: 'cskh-mono',
							text: cskh.formatNumber(u.effective_attempts || 0),
						}),
						cskh.el('td', {
							class: 'cskh-mono',
							text: cskh.formatNumber(u.connected_attempts || 0),
						}),
						cskh.el('td', {
							class: 'cskh-mono',
							text: cskh.formatNumber(u.open_followups || 0),
						}),
						cskh.el('td', {
							class: 'cskh-mono',
							text: cskh.formatNumber(u.overdue_followups || 0),
						}),
					])
				)
			})
			usersWrap.appendChild(
				cskh.el('table', { class: 'cskh-table' }, [
					cskh.el('thead', {}, [
						cskh.el('tr', {}, [
							cskh.el('th', { text: cskh.t('Assignee') }),
							cskh.el('th', { text: cskh.t('Team') }),
							cskh.el('th', { text: cskh.t('Newly assigned') }),
							cskh.el('th', { text: cskh.t('In progress') }),
							cskh.el('th', { text: cskh.t('On hold') }),
							cskh.el('th', { text: cskh.t('Completed care') }),
							cskh.el('th', { text: cskh.t('Effective contact attempts') }),
							cskh.el('th', { text: cskh.t('Connected') }),
							cskh.el('th', { text: cskh.t('Open callbacks') }),
							cskh.el('th', { text: cskh.t('Overdue callbacks') }),
						]),
					]),
					tbody,
				])
			)
		}
		usersCard.hidden = false
	}

	async function load() {
		const loading = document.getElementById('prog-loading')
		const alertBox = document.getElementById('prog-alert')
		const campaign = document.getElementById('prog-campaign').value
		const dateFrom = document.getElementById('prog-date-from').value
		const dateTo = document.getElementById('prog-date-to').value

		loading.hidden = false
		cskh.clear(alertBox)
		const token = reads.next()
		try {
			const data = await cskhP2Api.getProgressSummary({
				campaign: campaign,
				date_from: dateFrom,
				date_to: dateTo,
			})
			if (token !== reads.current()) return
			renderData(data)
		} catch (err) {
			if (token !== reads.current()) return
			cskh.renderError(alertBox, err)
		} finally {
			loading.hidden = true
		}
	}

	async function loadCampaigns() {
		if (!canLoadCampaignList) return
		const select = document.getElementById('prog-campaign')
		try {
			const data = await cskh.get('crm_cskh.api.dashboard.campaigns')
			;((data && data.campaigns) || []).forEach(function (c) {
				select.appendChild(cskh.el('option', { value: c.campaign, text: c.title || c.code }))
			})
		} catch (_err) {
			/* optional */
		}
	}

	document.getElementById('prog-refresh').addEventListener('click', load)
	document.getElementById('prog-campaign').addEventListener('change', load)
	document.getElementById('prog-clear-dates').addEventListener('click', function () {
		document.getElementById('prog-date-from').value = ''
		document.getElementById('prog-date-to').value = ''
		load()
	})

	loadCampaigns()
	load()
})()
