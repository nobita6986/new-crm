/* P3 Screen 5 — SLA & QA Operations Center (`/cskh/sla-operations`).
   Server-paginated SLA obligations, QA disabled fallback notice, and audited SLA waivers.
*/

;(function () {
	'use strict'

	const cskh = window.cskh
	const api = window.cskhP3Api
	const w = window.cskhP3Widgets
	const t = cskh.t
	const el = cskh.el

	const bannersHost = document.getElementById('p3-banners')
	const filterHost = document.getElementById('p3-filter-host')
	const watermarkHost = document.getElementById('p3-watermark-host')
	const alertHost = document.getElementById('p3-state-alert')
	const mainHost = document.getElementById('p3-main-content')

	const seq = cskh.sequencer()
	let currentPage = 1
	const pageSize = 20

	const filterBar = w.mountFilterBar(filterHost, {
		showCampaign: true,
		showTeam: true,
		showEmployee: true,
		showSlaFilters: true,
		onChange: function () {
			currentPage = 1
			loadPage()
		},
	})

	w.bindHeaderActions(
		'sla_operations',
		function () {
			return Object.assign({}, filterBar.getState(), { page: currentPage, page_size: pageSize })
		},
		function () {
			loadPage()
		}
	)

	;(async function () {
		try {
			const lists = await api.getFilterOptions()
			filterBar.setFilterLists(lists)
		} catch (_err) {
			/* ignore */
		}
	})()

	function renderSlaOps(data) {
		cskh.clear(mainHost)

		if (data.summaryMetrics && data.summaryMetrics.length) {
			w.renderThreePillars(mainHost, data.summaryMetrics, {
				onDrilldown: function (metric) {
					w.openDrilldownModal(metric, filterBar.getState())
				},
			})
		}

		const card = el('div', { class: 'cskh-card' }, [el('h2', { text: t('Top overdue obligations') })])

		if (!data.rows || !data.rows.length) {
			card.appendChild(
				el('div', {
					class: 'cskh-alert is-empty',
					text: t('No SLA obligations match the current filter.'),
				})
			)
			mainHost.appendChild(card)
			return
		}

		const canWaive = !!cskh.capabilities().can_waive_sla
		const canOpenPii = !!cskh.capabilities().pii

		const tbody = el(
			'tbody',
			{},
			data.rows.map(function (row) {
				const obId = row.obligation_id || row.name || '—'
				const wi = row.work_item || row.work_item_id || '—'
				const st = String(row.sla_status || row.status || 'pending').toLowerCase()
				const eligibleForWaive =
					canWaive &&
					(st === 'pending' || st === 'breached_open' || st === 'fulfilled_late' || st === 'escalated')

				return el('tr', { 'data-obligation-id': obId }, [
					el('td', { class: 'cskh-mono', text: obId }),
					el('td', {}, [
						canOpenPii && wi !== '—' && !row.pii_redacted
							? el('a', {
									href: '/cskh/work-item?name=' + encodeURIComponent(wi),
									class: 'cskh-mono',
									text: wi,
								})
							: el('span', { class: 'cskh-mono', text: wi }),
					]),
					el('td', {
						text: row.pii_redacted
							? t('PII redacted after handover or by role policy')
							: row.customer_display || row.customer_ref || '—',
					}),
					el('td', { text: w.obligationTypeLabel(row.obligation_type) }),
					el('td', { text: row.responsible_user || row.owner_user || '—' }),
					el('td', {
						text:
							row.team_at_event === 'historical_attribution_unknown'
								? t('Historical attribution unknown')
								: row.team_at_event || row.team || '—',
					}),
					el('td', { text: row.effective_due_at ? cskh.formatDate(row.effective_due_at) : '—' }),
					el('td', {
						text: row.fulfilled_at
							? cskh.formatDate(row.fulfilled_at)
							: row.breached_at
								? cskh.formatDate(row.breached_at)
								: '—',
					}),
					el('td', {}, [
						el('span', {
							class: cskh.stateBadge(st),
							text: w.slaStatusLabel(st),
						}),
					]),
					el('td', { text: row.waiver_reason || '—' }),
					el('td', {}, [
						eligibleForWaive
							? el('button', {
									type: 'button',
									class: 'cskh-btn is-small',
									'data-waive-obligation': obId,
									text: t('Waive SLA obligation'),
									onclick: function () {
										w.openWaiveModal(row, function () {
											loadPage()
										})
									},
								})
							: el('span', { class: 'cskh-muted', text: '—' }),
					]),
				])
			})
		)

		const rangeNode = el('span', {})
		const prevBtn = el('button', { type: 'button', class: 'cskh-btn is-small', text: t('Previous') })
		const nextBtn = el('button', { type: 'button', class: 'cskh-btn is-small', text: t('Next') })
		const pagerCtrl = cskh.pager({
			pageSize: pageSize,
			total: data.total,
			range: rangeNode,
			prev: prevBtn,
			next: nextBtn,
		})
		pagerCtrl.goto(currentPage)
		pagerCtrl.setTotal(data.total)
		pagerCtrl.render()

		prevBtn.addEventListener('click', function () {
			if (currentPage > 1) {
				currentPage -= 1
				loadPage()
			}
		})
		nextBtn.addEventListener('click', function () {
			if (currentPage < pagerCtrl.pages()) {
				currentPage += 1
				loadPage()
			}
		})

		card.appendChild(
			el('div', { class: 'cskh-table-scroll' }, [
				el('table', { class: 'cskh-table', id: 'p3-sla-table' }, [
					el('thead', {}, [
						el('tr', {}, [
							el('th', { text: t('Obligation ID') }),
							el('th', { text: t('Work item') }),
							el('th', { text: t('Customer ref') }),
							el('th', { text: t('Obligation type') }),
							el('th', { text: t('Responsible user') }),
							el('th', { text: t('Team at event') }),
							el('th', { text: t('Effective due at') }),
							el('th', { text: t('Fulfilled at') }),
							el('th', { text: t('SLA status') }),
							el('th', { text: t('Exemption / waiver reason') }),
							el('th', { text: t('Actions') }),
						]),
					]),
					tbody,
				]),
			])
		)
		card.appendChild(el('div', { class: 'cskh-pager' }, [prevBtn, rangeNode, nextBtn]))
		mainHost.appendChild(card)
	}

	async function loadPage() {
		const token = seq.next()
		cskh.clear(alertHost)
		cskh.clear(mainHost)
		mainHost.appendChild(el('div', { class: 'cskh-loading', text: t('Loading...') }))
		try {
			const params = Object.assign({}, filterBar.getState(), {
				page: currentPage,
				page_size: pageSize,
			})
			const data = await api.getSlaOperations(params)
			if (token !== seq.current()) return
			w.renderBanners(bannersHost, data.meta)
			w.renderWatermarkBar(watermarkHost, data.meta, params)
			renderSlaOps(data)
		} catch (err) {
			if (token !== seq.current()) return
			cskh.clear(mainHost)
			cskh.renderError(alertHost, err)
		}
	}

	loadPage()
})()
