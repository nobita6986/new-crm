/* Aggregate-only worker care desk. Retains the last snapshot visibly marked stale on failure. */
(function () {
	'use strict'
	const root = document.getElementById('care-overview')
	if (!root) return
	const t = cskh.t
	const el = cskh.el
	const number = cskh.formatNumber
	const status = document.getElementById('care-status')
	const dataWrap = document.getElementById('care-data')
	const refresh = document.getElementById('care-refresh')
	const caps = cskh.capabilities() || {}
	const STALE_AFTER_MS = 300000
	let loadedAt = 0
	let lastAsOfText = ''
	let failed = false
	let busy = false

	function formatTemplate(template, values) {
		let out = String(template || '')
		;(values || []).forEach(function (val, idx) {
			out = out.split('{' + idx + '}').join(String(val))
		})
		return out
	}

	function resolveRoleLabel() {
		if (!caps.pii && caps.bod) return t('Executive aggregate view (0 PII)')
		if (caps.admin) return t('Marketing care administration view')
		if (caps.leader) return t('Marketing team leader view')
		return t('Marketing staff view')
	}

	function describeError(error) {
		const code = error && error.code ? String(error.code) : ''
		const mapped = code ? cskh.errorText(code) : ''
		const rawMsg = error && error.message ? String(error.message) : ''
		const translatedMsg = rawMsg && rawMsg !== ('HTTP ' + (error && error.status)) ? t(rawMsg, '') : ''
		const detail = mapped || translatedMsg || ''
		const suffix = code ? '[' + code + ']' : ''
		return [detail, suffix].filter(Boolean).join(' ')
	}

	function renderNotice(kind, message, actionLabel) {
		cskh.clear(status)
		status.className = 'care-notice' + (kind ? ' ' + kind : '')
		if (!message) return
		const children = [el('span', { class: 'care-notice-text', text: message })]
		if (actionLabel) {
			children.push(
				el('button', {
					type: 'button',
					class: 'cskh-btn is-small care-notice-btn',
					text: actionLabel,
					onclick: load,
				})
			)
		}
		status.appendChild(el('div', { class: 'care-notice-row' }, children))
	}

	function metricCard(opts) {
		const cardChildren = [
			el('span', { class: 'care-metric-label', text: t(opts.label) }),
			el('div', { class: 'care-metric-value-row' }, [
				el('strong', { class: 'care-metric-value', text: number(opts.value) }),
				el('span', { class: 'care-metric-unit', text: t(opts.unit) }),
			]),
			el('span', { class: 'care-caption', text: opts.noteText || t(opts.note) }),
		]
		if (opts.subnoteText) {
			cardChildren.push(el('span', { class: 'care-metric-subnote', text: opts.subnoteText }))
		}
		cardChildren.push(
			el('span', {
				class: 'care-metric-meta',
				text: t('Scope') + ': ' + opts.scopeText + ' · ' + t('Updated') + ': ' + opts.updatedText,
			})
		)
		return el(
			'div',
			{ class: 'care-metric' + (opts.urgent && opts.value > 0 ? ' is-urgent' : '') },
			cardChildren
		)
	}

	function priorityRow(opts) {
		const copy = el('div', { class: 'care-priority-copy' }, [
			el('h3', { text: t(opts.label) }),
			el('p', { class: 'care-caption', text: t(opts.help) }),
		])
		const countBox = el('div', { class: 'care-priority-count-wrap' }, [
			el('strong', { class: 'care-priority-count', text: number(opts.count) }),
			el('span', { class: 'care-priority-unit', text: t(opts.unit) }),
		])
		const row = el('div', { class: 'care-priority-row' }, [copy, countBox])
		if (opts.href) {
			const linkText = t(opts.linkLabel || 'View care tasks')
			row.appendChild(
				el('a', {
					class: 'care-text-link',
					href: opts.href,
					text: linkText + ' →',
					'aria-label': t(opts.label) + ': ' + linkText,
				})
			)
		}
		return row
	}

	function bottleneckItem(label, value, unit, href, linkLabel) {
		const children = [
			el('span', { class: 'care-bottleneck-label', text: t(label) }),
			el('div', { class: 'care-bottleneck-value-row' }, [
				el('strong', { class: 'care-bottleneck-value', text: number(value) }),
				el('span', { class: 'care-metric-unit', text: t(unit) }),
			]),
		]
		if (href) {
			children.push(
				el('a', {
					class: 'care-text-link',
					href: href,
					text: t(linkLabel) + ' →',
				})
			)
		}
		return el('div', { class: 'care-bottleneck-item' }, children)
	}

	function render(data) {
		const w = data.workload || {}
		const cb = data.callbacks || {}
		const bn = data.bottlenecks || {}
		const tz = data.timezone || {}
		const day = data.day || {}
		const scopeText = cskh.scopeLabel(data.scope)
		const updatedText = cskh.formatDate(data.as_of)
		lastAsOfText = updatedText

		document.getElementById('care-role').textContent = t('Role view') + ': ' + resolveRoleLabel()
		document.getElementById('care-scope').textContent = t('Care data scope') + ': ' + scopeText
		document.getElementById('care-day').textContent =
			t('Calendar day') + ': ' + cskh.formatDateOnly(day.date)
		document.getElementById('care-updated').textContent =
			t('Care data updated') + ': ' + updatedText + ' · ' + (tz.effective || 'Asia/Ho_Chi_Minh')

		const tzNote = document.getElementById('care-tz-note')
		if (tz.site && tz.effective && tz.site !== tz.effective) {
			tzNote.hidden = false
			tzNote.textContent = formatTemplate(
				t('Day boundaries follow your timezone ({0}) converted against site timezone ({1}).'),
				[tz.effective, tz.site]
			)
		} else {
			tzNote.hidden = true
			tzNote.textContent = ''
		}

		const bodBanner = document.getElementById('care-bod-banner')
		if (!caps.pii) {
			bodBanner.hidden = false
			bodBanner.textContent = t(
				'Aggregate-only executive view: worker names, phone numbers, and personal identifiers are never transmitted to this screen.'
			)
		} else {
			bodBanner.hidden = true
			bodBanner.textContent = ''
		}

		const emptyBanner = document.getElementById('care-empty')
		if (Number(w.active || 0) === 0 && Number(cb.open || 0) === 0) {
			emptyBanner.hidden = false
			cskh.clear(emptyBanner)
			let emptyDetail = t(
				'You currently have zero assigned care work items and zero open callbacks. New worker records will appear here once assigned from a care campaign.'
			)
			if (!caps.pii) {
				emptyDetail = t(
					'There are currently zero active care work items or open callbacks across the authorized scope.'
				)
			} else if (caps.leader || caps.admin) {
				emptyDetail = t(
					'There are currently zero active care work items in this scope. Open the worker contact pool or care campaigns to distribute records.'
				)
			}
			emptyBanner.appendChild(
				el('strong', { text: t('No active care workload in this scope yet') })
			)
			emptyBanner.appendChild(el('p', { class: 'care-caption', text: emptyDetail }))
		} else {
			emptyBanner.hidden = true
			cskh.clear(emptyBanner)
		}

		const metrics = document.getElementById('care-metrics')
		cskh.clear(metrics)
		metrics.appendChild(
			metricCard({
				label: 'Worker records in active care',
				value: w.contact_records,
				unit: 'distinct workers',
				note: 'Distinct contact records currently assigned',
				subnoteText: formatTemplate(t('Across {0} active care work items (Work Item)'), [
					number(w.active),
				]),
				scopeText: scopeText,
				updatedText: updatedText,
			})
		)
		metrics.appendChild(
			metricCard({
				label: 'Care items awaiting first contact',
				value: w.unattempted,
				unit: 'care work items',
				note: 'No effective contact attempt recorded',
				scopeText: scopeText,
				updatedText: updatedText,
			})
		)
		metrics.appendChild(
			metricCard({
				label: 'Care callbacks due today',
				value: cb.today,
				unit: 'care callbacks',
				note: 'Open callbacks within your calendar day',
				scopeText: scopeText,
				updatedText: updatedText,
			})
		)
		metrics.appendChild(
			metricCard({
				label: 'Overdue care callbacks',
				value: cb.overdue,
				unit: 'care callbacks',
				note: 'Open appointments before the update time',
				urgent: true,
				scopeText: scopeText,
				updatedText: updatedText,
			})
		)

		const priorityEyebrow = document.getElementById('care-priority-eyebrow')
		const priorityTitle = document.getElementById('care-priority-title')
		const openSummary = document.getElementById('care-open-summary')
		openSummary.textContent = formatTemplate(t('Total open care callbacks in scope: {0}'), [
			number(cb.open),
		])

		const priorities = document.getElementById('care-priorities')
		cskh.clear(priorities)
		if (!caps.pii) {
			// BOD has aggregate-only reports; never route it to a PII workflow.
			priorityEyebrow.textContent = t('Executive care summary')
			priorityTitle.textContent = t('Aggregate care volume & attention areas')
			priorities.appendChild(
				priorityRow({
					label: 'Overdue care callbacks',
					count: cb.overdue,
					unit: 'care callbacks',
					help: 'Aggregate open callbacks past their scheduled contact time.',
					href: caps.p3_enabled ? '/cskh/sla-operations' : '/cskh/progress',
					linkLabel: 'View aggregate report',
				})
			)
			priorities.appendChild(
				priorityRow({
					label: "Keep today's contact promises",
					count: cb.today,
					unit: 'care callbacks',
					help: 'Open callbacks scheduled within the current calendar day.',
					href: caps.p3_enabled ? '/cskh/quality-overview' : '/cskh/progress',
					linkLabel: 'View aggregate report',
				})
			)
			priorities.appendChild(
				priorityRow({
					label: 'Care items awaiting first contact',
					count: w.unattempted,
					unit: 'care work items',
					help: 'Assigned care work items without an effective contact attempt yet.',
					href: '/cskh/progress',
					linkLabel: 'Review care activity details',
				})
			)
		} else if (caps.leader || caps.admin) {
			priorityEyebrow.textContent = t('Team care coordination')
			priorityTitle.textContent = t('Team priorities & bottlenecks today')
			priorities.appendChild(
				priorityRow({
					label: 'Resolve overdue care callbacks in scope',
					count: cb.overdue,
					unit: 'care callbacks',
					help: 'Review overdue callbacks across marketing staff in your scope.',
					href: '/cskh/callbacks?bucket=overdue',
					linkLabel: 'Open overdue callbacks',
				})
			)
			priorities.appendChild(
				priorityRow({
					label: "Keep today's contact promises",
					count: cb.today,
					unit: 'care callbacks',
					help: 'Monitor callbacks due today so care commitments stay on time.',
					href: '/cskh/callbacks?bucket=due_today',
					linkLabel: 'Open today callbacks',
				})
			)
			priorities.appendChild(
				priorityRow({
					label: 'Clear uncontacted care backlog',
					count: w.unattempted,
					unit: 'care work items',
					help: 'Inspect newly assigned worker records with zero contact attempts.',
					href: '/cskh/my-customers?scope_care=not_contacted#mine-scope-card',
					linkLabel: 'Inspect scope backlog',
				})
			)
		} else {
			priorityEyebrow.textContent = t('Daily worker care')
			priorityTitle.textContent = t('Care priorities today')
			priorities.appendChild(
				priorityRow({
					label: 'Follow up overdue appointments',
					count: cb.overdue,
					unit: 'care callbacks',
					help: 'Prioritize workers whose agreed contact time has passed.',
					href: '/cskh/callbacks?bucket=overdue',
					linkLabel: 'Open overdue callbacks',
				})
			)
			priorities.appendChild(
				priorityRow({
					label: "Keep today's contact promises",
					count: cb.today,
					unit: 'care callbacks',
					help: 'Check the callback schedule and record the real outcome.',
					href: '/cskh/callbacks?bucket=due_today',
					linkLabel: 'Open today callbacks',
				})
			)
			priorities.appendChild(
				priorityRow({
					label: 'Contact untouched worker records',
					count: w.unattempted,
					unit: 'care work items',
					help: 'Review the worker record before making the first call.',
					href: '/cskh/my-customers?care_filter=not_contacted',
					linkLabel: 'Open uncontacted records',
				})
			)
		}

		const coverage = document.getElementById('care-coverage')
		cskh.clear(coverage)
		coverage.appendChild(
			el('p', {
				class: 'care-coverage-total',
				text: formatTemplate(t('{0} active care work items across {1} distinct workers'), [
					number(w.active),
					number(w.contact_records),
				]),
			})
		)
		const groups = [
			['Not yet contacted', w.unattempted, 'is-unattempted'],
			['Attempted without two-way connection', w.attempted_unconnected, 'is-attempted'],
			['Two-way connection recorded', w.connected, 'is-connected'],
		]
		groups.forEach(function (group) {
			const countVal = Number(group[1] || 0)
			const activeTotal = Number(w.active || 0)
			const percent = activeTotal > 0 ? Math.round((countVal / activeTotal) * 100) : 0
			const track = el(
				'div',
				{
					class: 'care-progress-track',
					role: 'progressbar',
					'aria-label': t(group[0]),
					'aria-valuemin': '0',
					'aria-valuemax': String(activeTotal || 1),
					'aria-valuenow': String(countVal),
				},
				[]
			)
			const fill = el('span', { class: 'care-progress-fill ' + group[2] })
			fill.style.width = percent + '%'
			track.appendChild(fill)
			coverage.appendChild(
				el('div', { class: 'care-progress-row' }, [
					el('div', { class: 'care-progress-label' }, [
						el('span', { text: t(group[0]) }),
						el('strong', {
							text:
								number(countVal) +
								' / ' +
								number(activeTotal) +
								' (' +
								percent +
								'%)',
						}),
					]),
					track,
				])
			)
		})
		if (bn.on_hold !== undefined) {
			coverage.appendChild(
				el('p', {
					class: 'care-on-hold-note',
					text: formatTemplate(
						t('{0} care work items currently on hold awaiting scheduled callback.'),
						[number(bn.on_hold)]
					),
				})
			)
		}

		const bottlenecksCard = document.getElementById('care-bottlenecks-card')
		const bottlenecksGrid = document.getElementById('care-bottlenecks')
		cskh.clear(bottlenecksGrid)
		if (caps.leader || caps.admin || !caps.pii) {
			bottlenecksCard.hidden = false
			const staffReportHref = caps.p3_enabled ? '/cskh/employee-quality' : '/cskh/dashboard'
			const overdueHref = caps.pii
				? '/cskh/callbacks?bucket=overdue'
				: caps.p3_enabled
					? '/cskh/sla-operations'
					: '/cskh/progress'
			const untouchedHref = caps.pii
				? '/cskh/my-customers?scope_care=not_contacted#mine-scope-card'
				: '/cskh/progress'
			const onHoldHref = caps.pii
				? '/cskh/my-customers?scope_care=on_hold#mine-scope-card'
				: '/cskh/progress'

			bottlenecksGrid.appendChild(
				bottleneckItem(
					'Marketing staff holding active care items',
					bn.active_assignees || 0,
					'marketing staff',
					staffReportHref,
					'View staff workload'
				)
			)
			bottlenecksGrid.appendChild(
				bottleneckItem(
					'Marketing staff with overdue callbacks',
					bn.assignees_with_overdue || 0,
					'marketing staff',
					overdueHref,
					caps.pii ? 'Open overdue callbacks' : 'View aggregate report'
				)
			)
			bottlenecksGrid.appendChild(
				bottleneckItem(
					'Marketing staff with untouched items',
					bn.assignees_with_unattempted || 0,
					'marketing staff',
					untouchedHref,
					caps.pii ? 'Inspect scope backlog' : 'Review care activity details'
				)
			)
			bottlenecksGrid.appendChild(
				bottleneckItem(
					'Care items awaiting follow-up (on hold)',
					bn.on_hold || 0,
					'care work items',
					onHoldHref,
					caps.pii ? 'View on-hold items' : 'Review care activity details'
				)
			)
		} else {
			bottlenecksCard.hidden = true
		}

		const dnc = document.getElementById('care-dnc')
		dnc.hidden = false
		if (data.do_not_contact === null || data.do_not_contact === undefined) {
			dnc.className = 'care-compliance-muted'
			dnc.textContent = t('Do-not-contact (DNC) counter is not available on this site schema.')
		} else {
			dnc.className = ''
			dnc.textContent =
				number(data.do_not_contact) +
				' ' +
				t('worker contact records marked do not contact in your scope. Respect their contact preference.')
		}

		const sla = document.getElementById('care-sla')
		sla.hidden = false
		if (data.sla) {
			sla.className = data.sla.overdue > 0 ? 'care-compliance-urgent' : ''
			sla.textContent =
				number(data.sla.overdue) +
				' / ' +
				number(data.sla.pending) +
				' ' +
				t('pending care SLA obligations are overdue. Paused and informational obligations are excluded.')
		} else {
			sla.className = 'care-compliance-muted'
			sla.textContent = t('Care SLA obligation tracking is currently disabled in site configuration.')
		}

		dataWrap.classList.remove('is-stale-snapshot')
		dataWrap.removeAttribute('data-stale')
		dataWrap.hidden = false
	}

	function checkStaleSnapshot() {
		if (loadedAt && !busy && !failed && Date.now() - loadedAt > STALE_AFTER_MS) {
			dataWrap.classList.add('is-stale-snapshot')
			dataWrap.setAttribute('data-stale', '1')
			renderNotice(
				'is-stale',
				t('Care overview is more than five minutes old. Refresh before acting on these counts.'),
				t('Refresh care overview')
			)
		}
	}

	async function load() {
		if (busy) return
		busy = true
		refresh.disabled = true
		root.setAttribute('aria-busy', 'true')
		renderNotice(
			'is-loading',
			loadedAt ? t('Refreshing care overview...') : t('Loading care overview')
		)
		try {
			const data = await cskh.get('crm_cskh.api.worker_overview.get_overview', {})
			if (!data || typeof data !== 'object' || !data.workload || !data.callbacks) {
				const invalidErr = new Error('Invalid overview response')
				invalidErr.code = 'INVALID_REQUEST'
				throw invalidErr
			}
			render(data)
			loadedAt = Date.now()
			failed = false
			renderNotice('', '')
		} catch (error) {
			failed = true
			const detail = describeError(error)
			if (loadedAt) {
				dataWrap.classList.add('is-stale-snapshot')
				dataWrap.setAttribute('data-stale', '1')
				const msg =
					t('Care overview could not refresh. The last snapshot is still displayed.') +
					(lastAsOfText ? ' (' + t('Updated') + ': ' + lastAsOfText + ')' : '') +
					(detail ? ' — ' + detail : '')
				renderNotice('is-error', msg, t('Try again'))
			} else {
				// Never turn an API failure into fake 0 counters.
				dataWrap.hidden = true
				const msg =
					t('Care overview could not load. Refresh to try again.') +
					(detail ? ' — ' + detail : '')
				renderNotice('is-error', msg, t('Try again'))
			}
		} finally {
			busy = false
			refresh.disabled = false
			root.setAttribute('aria-busy', 'false')
		}
	}

	refresh.addEventListener('click', load)
	window.setInterval(checkStaleSnapshot, 30000)
	document.addEventListener('visibilitychange', function () {
		if (!document.hidden) checkStaleSnapshot()
	})
	window.addEventListener('focus', checkStaleSnapshot)
	load()
})()
