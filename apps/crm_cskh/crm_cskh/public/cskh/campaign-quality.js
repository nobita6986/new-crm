/* P3 Screen 4 — Campaign & Lead Source Performance (`/cskh/campaign-quality`).
   Explicitly contrasts `DISTINCT Work Item` vs `Ownership Interval` (`A -> B -> A`)
   and separates DNC/closure compliance from SLA execution (`report-api-contract.md` §3.4).
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

	const filterBar = w.mountFilterBar(filterHost, {
		showCampaign: true,
		showLeadSource: true,
		showTeam: true,
		onChange: function () {
			loadPage()
		},
	})

	w.bindHeaderActions(
		'campaign_quality',
		function () {
			return filterBar.getState()
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

	function renderCampaigns(data) {
		cskh.clear(mainHost)
		if (!data.rows || !data.rows.length) {
			mainHost.appendChild(
				el('div', {
					class: 'cskh-alert is-empty',
					text: t('No campaigns match the current filters.'),
				})
			)
			return
		}

		const tbody = el(
			'tbody',
			{},
			data.rows.map(function (row) {
				const campId = row.campaign || row.campaign_id || ''
				const campLabel = row.campaign_name || row.campaign_label || campId || '—'
				return el('tr', { 'data-campaign': campId }, [
					el('td', {}, [
						el('strong', { text: campLabel }),
						row.lead_source
							? el('div', { class: 'cskh-muted', text: t('Lead source') + ': ' + row.lead_source })
							: null,
					]),
					el('td', {}, [
						el('span', { class: 'cskh-pill-subtle', text: row.policy_version || 'DEV_CONFIGURABLE' }),
					]),
					el('td', {
						class: 'num',
						text: w.formatInlineRateCell(row.unique_assigned_work_items, 'count'),
					}),
					el('td', {
						class: 'num',
						text: w.formatInlineRateCell(row.ownership_intervals_count, 'count'),
					}),
					el('td', {
						class: 'num',
						text: w.formatInlineRateCell(row.work_item_first_touch_rate, 'percent'),
					}),
					el('td', {
						class: 'num',
						text: w.formatInlineRateCell(row.first_touch_on_time_rate, 'percent'),
					}),
					el('td', {
						class: 'num',
						text: w.formatInlineRateCell(row.followup_on_time_rate, 'percent'),
					}),
					el('td', {
						class: 'num',
						text: w.formatInlineRateCell(row.connected_rate, 'percent'),
					}),
					el('td', {
						class: 'num',
						text: w.formatInlineRateCell(row.conversion_rate, 'percent'),
					}),
					el('td', {
						class: 'num',
						text: w.formatInlineRateCell(row.first_connected_rate, 'percent'),
					}),
					el('td', {
						class: 'num',
						text: w.formatInlineRateCell(row.dnc_compliance_rate, 'percent'),
					}),
					el('td', {}, [
						el('button', {
							type: 'button',
							class: 'cskh-btn is-small',
							text: t('Drill down'),
							onclick: function () {
								const m = api.normalizeMetric(
									'WORK_ITEM_FIRST_ATTEMPT_SLA_RATE',
									row.work_item_first_touch_rate || {},
									{}
								)
								w.openDrilldownModal(
									m,
									Object.assign({}, filterBar.getState(), { campaign: campId })
								)
							},
						}),
					]),
				])
			})
		)

		mainHost.appendChild(
			el('div', { class: 'cskh-card' }, [
				el('div', { class: 'cskh-pillar-head' }, [
					el('h2', { text: t('Campaign & lead source breakdown') }),
					el('span', {
						class: 'cskh-muted',
						style: 'font-size: 12px;',
						text: t(
							'Note: Company totals count distinct Work Items, whereas employee accountability counts Ownership Intervals (e.g., A -> B -> A creates 3 intervals on 1 Work Item).'
						),
					}),
				]),
				el('div', { class: 'cskh-table-scroll' }, [
					el('table', { class: 'cskh-table', id: 'p3-campaign-table' }, [
						el('thead', {}, [
							el('tr', {}, [
								el('th', { text: t('Campaign') }),
								el('th', { text: t('Policy version') }),
								el('th', { class: 'num', text: t('Unique assigned work items') }),
								el('th', { class: 'num', text: t('Assigned intervals') }),
								el('th', { class: 'num', text: t('First-touch on-time rate (Work Item)') }),
								el('th', { class: 'num', text: t('First-touch on-time rate') }),
								el('th', { class: 'num', text: t('Follow-up on-time rate') }),
								el('th', { class: 'num', text: t('Connected rate') }),
								el('th', { class: 'num', text: t('Campaign conversion rate') }),
								el('th', {
									class: 'num',
									text: t('First-connected tracking (informational, non-penalizing)'),
								}),
								el('th', { class: 'num', text: t('DNC & closure compliance rate') }),
								el('th', { text: t('Actions') }),
							]),
						]),
						tbody,
					]),
				]),
			])
		)
	}

	async function loadPage() {
		const token = seq.next()
		cskh.clear(alertHost)
		cskh.clear(mainHost)
		mainHost.appendChild(el('div', { class: 'cskh-loading', text: t('Loading...') }))
		try {
			const data = await api.getCampaignQuality(filterBar.getState())
			if (token !== seq.current()) return
			w.renderBanners(bannersHost, data.meta)
			w.renderWatermarkBar(watermarkHost, data.meta, filterBar.getState())
			renderCampaigns(data)
		} catch (err) {
			if (token !== seq.current()) return
			cskh.clear(mainHost)
			cskh.renderError(alertHost, err)
		}
	}

	loadPage()
})()
