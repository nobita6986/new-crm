/* My customers (P1 §9.4 + P2 §8.1–8.2) and manual ownership actions (P1 §4.4).

   Two lists live on this page:

   1. **My customers** -- items currently owned by the signed-in user, upgraded
      in P2 with care status, last contact timestamp, last outcome, effective
      attempt count, next callback due (classified in site/user timezone),
      CSKH care filters, inline Quick Record (`record_attempt`), and deep links
      to `/cskh/work-item`.
   2. **Customers assigned in the scope** -- rendered for callers with a
      broader scope (`CSKH CRM Admin`, `Sales Manager` team leader, or
      `CSKH BOD Viewer` with 0 PII). Only `CSKH CRM Admin` (`can_manage = true`)
      receives transfer / revoke controls. */
(function () {
	'use strict'

	const API_ASSIGNEES = 'crm_cskh.api.ownership_ui.assignees'
	const API_REASSIGN = 'crm_cskh.api.ownership.reassign'
	const API_REVOKE = 'crm_cskh.api.ownership.revoke'
	const API_HISTORY = 'crm_cskh.api.ownership.history'
	const PAGE_SIZE = 20

	const caps = cskh.capabilities() || {}
	const isAdmin = !!caps.admin
	const allowPii = !!caps.pii
	const canOpenCrm = !!caps.can_open_crm_app
	const canReadScope = !!(caps.admin || caps.bod || caps.leader)

	const pager = cskh.pager({
		pageSize: PAGE_SIZE,
		range: document.getElementById('mine-range'),
		prev: document.getElementById('mine-prev'),
		next: document.getElementById('mine-next'),
	})
	const scopePager = cskh.pager({
		pageSize: PAGE_SIZE,
		range: document.getElementById('mine-scope-range'),
		prev: document.getElementById('mine-scope-prev'),
		next: document.getElementById('mine-scope-next'),
	})

	const reads = cskh.sequencer()
	const scopeReads = cskh.sequencer()

	let lastMineItems = []
	let assignees = []
	let canManage = false
	let cachedOutcomes = null
	let intakeCampaigns = []
	let searchDebounceTimer = null
	let scopeOwnerDebounceTimer = null
	let phoneCheckTimer = null

	function stateLabel(value) {
		const map = {
			assigned: cskh.t('Newly assigned'),
			in_progress: cskh.t('In progress'),
			on_hold: cskh.t('On hold'),
			queued: cskh.t('Queued'),
			revoked: cskh.t('Revoked'),
			closed: cskh.t('Completed care'),
		}
		return map[value] || cskh.workItemStateLabel(value) || '—'
	}

	function renderWorkdayBar(doneDays, targetDays) {
		const total = Math.max(1, Math.min(14, Number(targetDays || 7)))
		const done = Math.max(0, Math.min(total, Number(doneDays || 0)))
		const dots = []
		for (let i = 0; i < total; i += 1) {
			dots.push(cskh.el('span', { class: 'cskh-workday-dot' + (i < done ? ' is-done' : '') }))
		}
		return cskh.el('div', { class: 'cskh-workday-wrap' }, [
			cskh.el('div', { class: 'cskh-workday-dots' }, dots),
			cskh.el('span', {
				class: 'cskh-workday-caption',
				text: done + '/' + Number(targetDays || 7) + ' ' + cskh.t('workdays'),
			}),
		])
	}

	function renderPhoneActionCell(rawPhone, nationalPhoneVal) {
		const effPhone = nationalPhoneVal || rawPhone || ''
		if (!allowPii || !effPhone) {
			return cskh.el('span', { class: 'cskh-mono', text: '—' })
		}
		const formatted = cskh.formatPhone ? cskh.formatPhone(effPhone) : effPhone
		const telHref = cskh.telUrl ? cskh.telUrl(effPhone) : ''
		const zaloHref = cskh.zaloUrl ? cskh.zaloUrl(effPhone) : ''
		const nodes = [cskh.el('span', { class: 'cskh-phone-num', text: formatted })]
		if (telHref) {
			nodes.push(
				cskh.el('a', {
					class: 'cskh-phone-link is-call',
					href: telHref,
					text: cskh.t('Call'),
				})
			)
		}
		if (zaloHref) {
			nodes.push(
				cskh.el('a', {
					class: 'cskh-phone-link is-zalo',
					href: zaloHref,
					target: '_blank',
					rel: 'noopener noreferrer',
					text: 'Zalo',
				})
			)
		}
		nodes.push(
			cskh.el('a', {
				class: 'cskh-phone-link',
				href: '#copy-phone',
				text: cskh.t('Copy'),
				onclick: function (ev) {
					ev.preventDefault()
					cskh.copyPhone(effPhone)
				},
			})
		)
		return cskh.el('div', { class: 'cskh-phone-bar' }, nodes)
	}

	function syncFilterChips(selected) {
		const bar = document.getElementById('mine-filter-chips')
		if (!bar) return
		Array.from(bar.querySelectorAll('.cskh-chip')).forEach(function (btn) {
			const val = btn.getAttribute('data-filter') || ''
			if (val === selected) btn.classList.add('is-active')
			else btn.classList.remove('is-active')
		})
	}

	/* ------------------------------------------------------------------ */
	/* Cell renderers                                                      */
	/* ------------------------------------------------------------------ */

	function renderWorkerNameCell(item) {
		const nameText = (allowPii && item.display_name) || item.lead
		const metaParts = []
		if (allowPii && item.birth_year) metaParts.push(String(item.birth_year))
		if (allowPii && item.gender) metaParts.push(item.gender)
		if (allowPii && item.current_area) metaParts.push(item.current_area)
		if (item.source_type === 'self_sourced') metaParts.push(cskh.t('Self-sourced'))
		return cskh.el('div', {}, [
			cskh.el('div', { style: 'font-weight: 600;', text: nameText }),
			metaParts.length
				? cskh.el('div', { class: 'cskh-worker-submeta', text: metaParts.join(' · ') })
				: null,
		])
	}

	function renderFactoryCell(item) {
		const fac = item.target_factory || ''
		const iz = item.target_industrial_zone || ''
		const kcn = [fac, iz].filter(Boolean).join(' · ')
		const campText = item.campaign_title || item.campaign_code || item.campaign || '—'
		if (kcn) {
			return cskh.el('div', {}, [
				cskh.el('div', { style: 'font-weight: 500;', text: kcn }),
				cskh.el('div', { class: 'cskh-muted', style: 'font-size: 11.5px;', text: campText }),
			])
		}
		return cskh.el('span', { text: campText })
	}

	function renderCareStatusCell(item) {
		const stage = item.worker_stage || 'new_contact'
		const badges = [
			cskh.el('span', {
				class: cskh.stateBadge(stage),
				text: cskh.workerStageLabel ? cskh.workerStageLabel(stage) : stateLabel(item.work_item_status),
			}),
		]
		if (item.do_not_contact) {
			badges.push(
				cskh.el('span', {
					class: cskh.stateBadge('dnc'),
					text: cskh.t('Do not contact'),
				})
			)
		}
		if (stage === 'interview_scheduled' && item.interview_date) {
			badges.push(
				cskh.el('span', {
					class: 'cskh-muted',
					style: 'font-size: 11.5px;',
					text: cskh.t('Interview') + ': ' + cskh.formatDateOnly(item.interview_date),
				})
			)
		}
		if (stage === 'working_care' || stage === 'completed_7_days' || Number(item.workdays_completed || 0) > 0) {
			badges.push(renderWorkdayBar(item.workdays_completed, item.target_workdays || 7))
		}
		return cskh.el('div', { class: 'cskh-badge-stack' }, badges)
	}

	function renderLastOutcomeCell(item) {
		if (!item.last_outcome_code && !item.last_outcome_label) {
			return cskh.el('span', { class: 'cskh-muted', text: '—' })
		}
		const label = cskh.outcomeLabel(item.last_outcome_code, item.last_outcome_label)
		const nodes = [cskh.el('span', { text: label })]
		if (item.attempt_count > 0) {
			nodes.push(
				cskh.el('span', {
					class: cskh.stateBadge(item.has_connected ? 'connected' : 'not_connected'),
					text: item.has_connected ? cskh.t('Connected') : cskh.t('Not connected'),
				})
			)
		}
		return cskh.el('div', { class: 'cskh-badge-stack' }, nodes)
	}

	function renderNextCallbackCell(item) {
		if (!item.next_action_due) {
			return cskh.el('span', { class: 'cskh-muted', text: '—' })
		}
		const dueKind = item.followup_state || cskh.classifyDueAt(item.next_action_due, 'Open')
		const children = [cskh.el('span', { text: cskh.formatDate(item.next_action_due) })]
		if (dueKind) {
			children.push(
				cskh.el('span', {
					class: cskh.stateBadge(dueKind),
					text: cskh.dueStateLabel(dueKind),
				})
			)
		}
		return cskh.el('div', { class: 'cskh-badge-stack' }, children)
	}

	/* ------------------------------------------------------------------ */
	/* My customers                                                        */
	/* ------------------------------------------------------------------ */

	function sortMine(items) {
		const sortEl = document.getElementById('mine-sort')
		const sortMode = sortEl ? sortEl.value : 'desc'
		const list = (items || []).slice()
		if (!sortMode || sortMode === 'desc') {
			return list
		}
		list.sort(function (a, b) {
			if (sortMode === 'last_contact_desc') {
				const la = a.last_attempt_at ? cskh.parseServerDate(a.last_attempt_at)?.getTime() || 0 : 0
				const lb = b.last_attempt_at ? cskh.parseServerDate(b.last_attempt_at)?.getTime() || 0 : 0
				return lb - la
			}
			if (sortMode === 'next_due_asc') {
				const da = a.next_action_due
					? cskh.parseServerDate(a.next_action_due)?.getTime() || Number.MAX_SAFE_INTEGER
					: Number.MAX_SAFE_INTEGER
				const db = b.next_action_due
					? cskh.parseServerDate(b.next_action_due)?.getTime() || Number.MAX_SAFE_INTEGER
					: Number.MAX_SAFE_INTEGER
				return da - db
			}
			const ta = a.assigned_at ? cskh.parseServerDate(a.assigned_at)?.getTime() || 0 : 0
			const tb = b.assigned_at ? cskh.parseServerDate(b.assigned_at)?.getTime() || 0 : 0
			return sortMode === 'asc' ? ta - tb : tb - ta
		})
		return list
	}

	function renderMineTable() {
		const table = document.getElementById('mine-table')
		const empty = document.getElementById('mine-empty')
		const rows = document.getElementById('mine-rows')
		cskh.clear(rows)
		const items = sortMine(lastMineItems)
		if (!items.length) {
			table.hidden = true
			empty.hidden = false
			return
		}
		empty.hidden = true
		items.forEach(function (item) {
			const rowItem = Object.assign({ assigned_to: item.assigned_to || cskh.currentUser() }, item)
			// Keep `Ownership detail` as the ONLY `<button>` in `#mine-rows tr` so P1 Browser E2E
			// strict locator `locator('#mine-rows tr').first().locator('button')` stays 100% intact.
			const actions = [
				cskh.el('button', {
					class: 'cskh-btn is-small',
					type: 'button',
					text: cskh.t('Ownership detail'),
					onclick: function () {
						openDetail(rowItem, 'view')
					},
				}),
			]
			if (allowPii && item.capabilities && item.capabilities.can_record_attempt) {
				const quickAnchor = cskh.el('a', {
					class: 'cskh-btn is-primary is-small',
					href: '#quick-record-' + encodeURIComponent(item.work_item),
					text: cskh.t('Update KCN progress'),
					onclick: function (ev) {
						ev.preventDefault()
						openQuickRecord(rowItem)
					},
				})
				actions.push(quickAnchor)
			}
			if (
				allowPii &&
				item.worker_stage === 'working_care' &&
				item.capabilities &&
				item.capabilities.can_record_progress
			) {
				actions.push(
					cskh.el('a', {
						class: 'cskh-btn is-success is-small',
						href: '#checkin-' + encodeURIComponent(item.work_item),
						text: cskh.t('+1 Workday'),
						onclick: function (ev) {
							ev.preventDefault()
							quickCheckinWorkday(rowItem)
						},
					})
				)
			}
			if (allowPii && item.work_item) {
				actions.push(
					cskh.el('a', {
						class: 'cskh-btn is-small',
						href: '/cskh/work-item?id=' + encodeURIComponent(item.work_item),
						text: cskh.t('Care workspace'),
					})
				)
			}
			if (allowPii && canOpenCrm && item.lead) {
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
					cskh.el('td', {}, [renderWorkerNameCell(item)]),
					cskh.el('td', {}, [renderPhoneActionCell(item.phone, item.national_phone)]),
					cskh.el('td', {}, [renderFactoryCell(item)]),
					cskh.el('td', {}, [renderCareStatusCell(item)]),
					cskh.el('td', {}, [renderLastOutcomeCell(item)]),
					cskh.el('td', { class: 'cskh-mono', text: cskh.formatNumber(item.attempt_count) }),
					cskh.el('td', {}, [renderNextCallbackCell(item)]),
					cskh.el('td', {}, [cskh.el('div', { class: 'cskh-row-actions' }, actions)]),
				])
			)
		})
		table.hidden = false
	}

	async function quickCheckinWorkday(item) {
		const alertBox = document.getElementById('mine-alert')
		cskh.clear(alertBox)
		try {
			const res = await cskhP2Api.recordWorkerProgress({
				work_item: item.work_item,
				action_type: 'checkin_workday',
				increment_workday: 1,
				channel: 'zalo',
				idempotency_key: 'ui-wday-' + item.work_item + '-' + cskh.dateKeyInTz(cskh.serverNow()),
			})
			if (res && res.completed_7_days) {
				cskh.toast('success', cskh.t('Completed 7/7 workdays — worker care closed as accepted!'))
			} else {
				cskh.toast(
					'success',
					cskh.t('Logged workday') +
						': ' +
						(res.workdays_completed || 1) +
						'/' +
						(res.target_workdays || 7)
				)
			}
			await load()
			await loadScope()
		} catch (error) {
			cskh.renderError(alertBox, error)
		}
	}

	function buildKcnMilestoneSection(wi, onDone) {
		const msgBox = cskh.el('div', { 'aria-live': 'polite' })
		const todayIso = cskh.dateKeyInTz(cskh.serverNow())
		const ivDateInput = cskh.el('input', {
			class: 'cskh-input',
			type: 'date',
			value: wi.interview_date || todayIso,
		})
		const facInput = cskh.el('input', {
			class: 'cskh-input',
			type: 'text',
			value: wi.target_factory || '',
			placeholder: cskh.t('Factory name'),
		})
		const izInput = cskh.el('input', {
			class: 'cskh-input',
			type: 'text',
			value: wi.target_industrial_zone || '',
			placeholder: cskh.t('Industrial zone'),
		})
		const noteInput = cskh.el('input', {
			class: 'cskh-input',
			type: 'text',
			style: 'min-width: 220px;',
			placeholder: cskh.t('Milestone note / dropout reason...'),
		})

		async function fireProgress(actionType, extra) {
			cskh.clear(msgBox)
			if (actionType === 'dropout' && !noteInput.value.trim()) {
				cskh.alert(msgBox, 'warning', cskh.t('Please enter the reason the worker dropped out early.'))
				return
			}
			try {
				const payload = Object.assign(
					{
						work_item: wi.work_item_id || wi.work_item,
						action_type: actionType,
						channel: 'zalo',
						interview_date: ivDateInput.value || null,
						target_factory: facInput.value.trim() || null,
						target_industrial_zone: izInput.value.trim() || null,
						note: noteInput.value.trim() || null,
						dropout_reason: actionType === 'dropout' ? noteInput.value.trim() : null,
						idempotency_key: 'ui-kcn-' + cskh.uuid(),
					},
					extra || {}
				)
				const res = await cskhP2Api.recordWorkerProgress(payload)
				if (res && res.completed_7_days) {
					cskh.toast('success', cskh.t('Completed 7/7 workdays — worker care closed as accepted!'))
				} else {
					cskh.toast('success', cskh.t('Worker KCN milestone updated.'))
				}
				if (typeof onDone === 'function') await onDone()
			} catch (err) {
				cskh.renderError(msgBox, err)
			}
		}

		const profName = cskh.el('input', {
			class: 'cskh-input',
			type: 'text',
			value: wi.display_name || '',
			placeholder: cskh.t('Worker full name'),
		})
		const profYear = cskh.el('input', {
			class: 'cskh-input',
			type: 'number',
			min: '1960',
			max: '2012',
			value: wi.birth_year ? String(wi.birth_year) : '',
			placeholder: '2000',
		})
		const profArea = cskh.el('input', {
			class: 'cskh-input',
			type: 'text',
			value: wi.current_area || '',
			placeholder: cskh.t('Current area / boarding house'),
		})
		const profTags = cskh.el('input', {
			class: 'cskh-input',
			type: 'text',
			value: wi.worker_tags || '',
			placeholder: cskh.t('Screening notes (shift, tattoos, skills, transport...)'),
		})
		const saveProfBtn = cskh.el('button', {
			type: 'button',
			class: 'cskh-btn is-small is-primary',
			text: cskh.t('Save worker profile'),
			onclick: async function () {
				saveProfBtn.disabled = true
				try {
					await cskhP2Api.updateWorkerProfile({
						work_item: wi.work_item_id || wi.work_item,
						full_name: profName.value.trim() || null,
						birth_year: profYear.value ? Number(profYear.value) : '',
						current_area: profArea.value.trim(),
						worker_tags: profTags.value.trim(),
						target_factory: facInput.value.trim(),
						target_industrial_zone: izInput.value.trim(),
					})
					cskh.toast('success', cskh.t('Worker profile saved.'))
					if (typeof onDone === 'function') await onDone()
				} catch (err) {
					cskh.renderError(msgBox, err)
				} finally {
					saveProfBtn.disabled = false
				}
			},
		})

		return cskh.el('div', { class: 'cskh-panel', style: 'margin-bottom: 12px;' }, [
			cskh.el(
				'div',
				{ class: 'cskh-actions', style: 'justify-content: space-between; align-items: center;' },
				[
					cskh.el('strong', {
						text:
							cskh.t('Fast KCN milestone & 7-workday check-in') +
							' — ' +
							(wi.display_name || wi.lead),
					}),
					renderPhoneActionCell(wi.phone, wi.national_phone),
				]
			),
			msgBox,
			cskh.el('div', { class: 'cskh-actions', style: 'margin-top: 8px;' }, [
				cskh.el('label', { class: 'cskh-field', style: 'margin: 0;' }, [
					cskh.el('span', { text: cskh.t('Interview / onboard date') }),
					ivDateInput,
				]),
				cskh.el('label', { class: 'cskh-field', style: 'margin: 0;' }, [
					cskh.el('span', { text: cskh.t('Factory name') }),
					facInput,
				]),
				cskh.el('label', { class: 'cskh-field', style: 'margin: 0;' }, [
					cskh.el('span', { text: cskh.t('Industrial zone') }),
					izInput,
				]),
				cskh.el('label', { class: 'cskh-field', style: 'margin: 0; flex: 1;' }, [
					cskh.el('span', { text: cskh.t('Contact note') }),
					noteInput,
				]),
			]),
			cskh.el('div', { class: 'cskh-kcn-quick-bar' }, [
				cskh.el('button', {
					type: 'button',
					class: 'cskh-btn is-primary is-small',
					text: cskh.t('Schedule KCN interview'),
					onclick: function () {
						fireProgress('schedule_interview')
					},
				}),
				cskh.el('button', {
					type: 'button',
					class: 'cskh-btn is-success is-small',
					text: cskh.t('Started working (Day 1)'),
					onclick: function () {
						fireProgress('onboard', { onboard_date: ivDateInput.value || todayIso, workdays_completed: 1 })
					},
				}),
				cskh.el('button', {
					type: 'button',
					class: 'cskh-btn is-success is-small',
					text:
						cskh.t('+1 Workday') +
						' (' +
						Math.min((wi.workdays_completed || 0) + 1, wi.target_workdays || 7) +
						'/' +
						(wi.target_workdays || 7) +
						')',
					onclick: function () {
						fireProgress('checkin_workday', { increment_workday: 1 })
					},
				}),
				cskh.el('button', {
					type: 'button',
					class: 'cskh-btn is-small',
					text: cskh.t('Missed interview'),
					onclick: function () {
						fireProgress('interview_no_show')
					},
				}),
				cskh.el('button', {
					type: 'button',
					class: 'cskh-btn is-small',
					text: cskh.t('Switch factory'),
					onclick: function () {
						fireProgress('switch_factory')
					},
				}),
				cskh.el('button', {
					type: 'button',
					class: 'cskh-btn is-danger is-small',
					text: cskh.t('Dropped before 7 days'),
					onclick: function () {
						fireProgress('dropout', { close_after_dropout: 1 })
					},
				}),
			]),
			cskh.el('details', { style: 'margin-top: 10px;' }, [
				cskh.el('summary', {
					style: 'cursor: pointer; font-weight: 600; font-size: 12.5px; color: var(--cskh-primary);',
					text: cskh.t('Edit worker screening profile (name, birth year, area, notes)'),
				}),
				cskh.el('div', { class: 'cskh-form-grid', style: 'margin-top: 8px;' }, [
					cskh.el('label', { class: 'cskh-field' }, [
						cskh.el('span', { text: cskh.t('Worker full name') }),
						profName,
					]),
					cskh.el('label', { class: 'cskh-field' }, [
						cskh.el('span', { text: cskh.t('Birth year') }),
						profYear,
					]),
					cskh.el('label', { class: 'cskh-field' }, [
						cskh.el('span', { text: cskh.t('Current area / boarding house') }),
						profArea,
					]),
					cskh.el('label', { class: 'cskh-field' }, [
						cskh.el('span', { text: cskh.t('Screening notes (shift, tattoos, skills, transport...)') }),
						profTags,
					]),
				]),
				cskh.el('div', { class: 'cskh-actions', style: 'margin-top: 8px;' }, [saveProfBtn]),
			]),
		])
	}

	async function openQuickRecord(item) {
		const card = document.getElementById('mine-quick-record-card')
		const body = document.getElementById('mine-quick-record-body')
		const alertBox = document.getElementById('mine-alert')
		if (!card || !body) return
		cskh.clear(body)
		card.hidden = false
		body.appendChild(cskh.el('p', { class: 'cskh-loading', text: cskh.t('Loading...') }))
		card.scrollIntoView({ behavior: 'smooth', block: 'nearest' })

		try {
			const tasks = [cskhP2Api.getWorkItem({ work_item: item.work_item })]
			tasks.push(
				cachedOutcomes
					? Promise.resolve({ items: cachedOutcomes })
					: cskhP2Api.getOutcomeCodes({ work_item: item.work_item })
			)
			const res = await Promise.all(tasks)
			const freshWi = res[0] || item
			const outList = (res[1] && res[1].items) || []
			if (!cachedOutcomes && outList.length) cachedOutcomes = outList

			cskh.clear(body)
			if (freshWi.capabilities && freshWi.capabilities.can_record_progress) {
				body.appendChild(
					buildKcnMilestoneSection(freshWi, async function () {
						card.hidden = true
						cskh.clear(body)
						await load()
						await loadScope()
					})
				)
			}
			const attemptHost = cskh.el('div', {})
			body.appendChild(attemptHost)

			cskhAttemptForm.mount(attemptHost, {
				workItem: freshWi,
				outcomes: outList,
				onCancel: function () {
					card.hidden = true
					cskh.clear(body)
				},
				onSaved: async function () {
					card.hidden = true
					cskh.clear(body)
					await load()
					await loadScope()
				},
				onReloadWorkItem: async function () {
					const latest = await cskhP2Api.getWorkItem({ work_item: item.work_item })
					await load()
					return latest
				},
				onReloadPolicy: async function () {
					const freshOut = await cskhP2Api.getOutcomeCodes({ work_item: item.work_item })
					cachedOutcomes = freshOut.items || []
					const latestWi = await cskhP2Api.getWorkItem({ work_item: item.work_item })
					return { outcomes: cachedOutcomes, workItem: latestWi }
				},
			})
		} catch (error) {
			card.hidden = true
			cskh.clear(body)
			cskh.renderError(alertBox, error, {
				onReloadWorkItem: function () {
					load()
					loadScope()
				},
			})
			load()
		}
	}

	async function load() {
		const loading = document.getElementById('mine-loading')
		const table = document.getElementById('mine-table')
		const empty = document.getElementById('mine-empty')
		const alertBox = document.getElementById('mine-alert')
		const careEl = document.getElementById('mine-care-filter')
		const searchEl = document.getElementById('mine-search')

		loading.hidden = false
		table.hidden = true
		empty.hidden = true
		cskh.clear(alertBox)

		const campaign = document.getElementById('mine-campaign').value
		const careFilter = careEl ? careEl.value : ''
		const search = searchEl ? searchEl.value.trim() : ''
		const requestedPage = pager.page()
		const mine = reads.next()

		try {
			let data
			if (allowPii) {
				const query = cskhP2Api.buildWorkItemsQuery({
					page: requestedPage,
					pageSize: PAGE_SIZE,
					careFilter: careFilter,
					campaign: campaign,
					assignee: cskh.currentUser(),
					search: search,
				})
				data = await cskhP2Api.listWorkItems(query)
			} else {
				data = await cskhP2Api.listMyCustomers({
					page: requestedPage,
					page_size: PAGE_SIZE,
					campaign: campaign,
				})
			}
			if (mine !== reads.current()) return
			const rawItems = (data && data.items) || []
			const serverTotal = Number((data && data.total) || 0)
			pager.setTotal(serverTotal)
			if (requestedPage > pager.page()) {
				return load()
			}
			lastMineItems = rawItems
			pager.render()
			if (!canReadScope) syncMineCampaignFilter(lastMineItems)
			renderMineTable()
		} catch (error) {
			lastMineItems = []
			pager.setTotal(0)
			pager.render()
			cskh.renderError(alertBox, error)
		} finally {
			loading.hidden = true
		}
	}

	/* ------------------------------------------------------------------ */
	/* The scope list and manual ownership actions (P1 §4.4 + P2 columns)  */
	/* ------------------------------------------------------------------ */

	async function loadScope() {
		const card = document.getElementById('mine-scope-card')
		const table = document.getElementById('mine-scope-table')
		const rows = document.getElementById('mine-scope-rows')
		const empty = document.getElementById('mine-scope-empty')
		const loading = document.getElementById('mine-scope-loading')
		const alertBox = document.getElementById('mine-alert')
		if (!card) return
		if (!canReadScope) {
			card.hidden = true
			return
		}

		loading.hidden = false
		table.hidden = true
		empty.hidden = true
		cskh.clear(rows)

		const campaign = document.getElementById('mine-campaign').value
		const ownerInput = document.getElementById('mine-scope-owner')
		const scopeCareSelect = document.getElementById('mine-scope-care-filter')
		const owner = ownerInput ? ownerInput.value.trim() : ''
		const scopeCare = scopeCareSelect ? scopeCareSelect.value : ''
		const requestedPage = scopePager.page()
		const mine = scopeReads.next()
		try {
			let data
			if (allowPii) {
				const wiData = await cskhP2Api.listWorkItems(
					cskhP2Api.buildWorkItemsQuery({
						page: requestedPage,
						pageSize: PAGE_SIZE,
						careFilter: scopeCare,
						campaign: campaign,
						assignee: owner,
					})
				)
				data = Object.assign({}, wiData, {
					can_manage: isAdmin,
				})
			} else {
				data = await cskhP2Api.listScopedCustomers({
					page: requestedPage,
					page_size: PAGE_SIZE,
					campaign: campaign,
					owner: owner,
				})
			}
			if (mine !== scopeReads.current()) return
			canManage = !!(data && data.can_manage)
			const scopeCode = data && data.scope
			const showScopeCard =
				canManage || scopeCode === 'team' || scopeCode === 'all' || scopeCode === 'system'
			if (!showScopeCard) {
				card.hidden = true
				return
			}
			card.hidden = false
			const scopeTotal = Number((data && data.total) || 0)
			scopePager.setTotal(scopeTotal)
			if (requestedPage > scopePager.page()) {
				return loadScope()
			}
			scopePager.render()
			const items = (data && data.items) || []
			if (!items.length) {
				empty.hidden = false
				return
			}
			items.forEach(function (item) {
				rows.appendChild(renderScopeRow(item))
			})
			table.hidden = false
		} catch (error) {
			card.hidden = true
			canManage = false
			if (error && error.status !== 403) {
				cskh.renderError(alertBox, error)
			}
		} finally {
			loading.hidden = true
		}
	}

	function renderScopeRow(item) {
		const actions = [
			cskh.el('button', {
				class: 'cskh-btn is-small',
				type: 'button',
				text: cskh.t('Ownership detail'),
				onclick: function () {
					openDetail(item, 'view')
				},
			}),
		]
		if (allowPii && item.work_item) {
			actions.push(
				cskh.el('a', {
					class: 'cskh-btn is-small',
					href: '/cskh/work-item?id=' + encodeURIComponent(item.work_item),
					text: cskh.t('Care workspace'),
				})
			)
		}
		if (canManage && (!item.capabilities || item.capabilities.can_reassign_or_revoke)) {
			actions.push(
				cskh.el('button', {
					class: 'cskh-btn is-small is-primary',
					type: 'button',
					text: cskh.t('Transfer owner'),
					onclick: function () {
						openDetail(item, 'reassign')
					},
				})
			)
			actions.push(
				cskh.el('button', {
					class: 'cskh-btn is-small is-danger',
					type: 'button',
					text: cskh.t('Revoke'),
					onclick: function () {
						openDetail(item, 'revoke')
					},
				})
			)
		}
		return cskh.el('tr', {}, [
			cskh.el('td', { text: (allowPii && item.display_name) || item.lead }),
			cskh.el('td', {
				class: 'cskh-mono',
				text: allowPii && item.phone ? cskh.maskPhone(item.phone) : '—',
			}),
			cskh.el('td', { text: item.campaign_title || item.campaign_code || item.campaign || '—' }),
			cskh.el('td', { text: item.assigned_to || item.last_owner || item.closed_by || '—' }),
			cskh.el('td', {}, [renderCareStatusCell(item)]),
			cskh.el('td', {}, [renderLastOutcomeCell(item)]),
			cskh.el('td', {}, [renderNextCallbackCell(item)]),
			cskh.el('td', { text: cskh.formatDate(item.assigned_at || item.closed_at) }),
			cskh.el('td', {}, [cskh.el('div', { class: 'cskh-row-actions' }, actions)]),
		])
	}

	async function loadAssignees(campaign) {
		if (!isAdmin) return
		try {
			const params = campaign ? { campaign: campaign } : {}
			const data = await cskh.get(API_ASSIGNEES, params)
			assignees = (data && data.items) || []
		} catch (_error) {
			assignees = []
		}
	}

	async function openDetail(item, mode) {
		const card = document.getElementById('mine-detail-card')
		const box = document.getElementById('mine-detail')
		cskh.clear(box)
		card.hidden = false
		box.appendChild(cskh.el('p', { class: 'cskh-loading', text: cskh.t('Loading...') }))

		try {
			const tasks = [cskh.get(API_HISTORY, { work_item: item.work_item })]
			if (isAdmin || canManage) {
				tasks.push(loadAssignees((item && item.campaign) || ''))
			}
			const results = await Promise.all(tasks)
			renderDetail(item, mode, results[0])
		} catch (error) {
			cskh.clear(box)
			cskh.renderError(box, error)
		}
	}

	function renderDetail(item, mode, history) {
		const box = document.getElementById('mine-detail')
		cskh.clear(box)

		box.appendChild(
			cskh.el('div', { class: 'cskh-grid' }, [
				metric(cskh.t('Work item'), item.work_item),
				metric(cskh.t('Contact record'), (allowPii && item.display_name) || item.lead),
				metric(
					cskh.t('Phone number'),
					allowPii && (item.national_phone || item.phone)
						? cskh.formatPhone(item.national_phone || item.phone)
						: '—'
				),
				metric(cskh.t('Current owner'), item.assigned_to || '—'),
				metric(cskh.t('Campaign'), item.campaign_title || item.campaign || '—'),
				metric(cskh.t('Assigned at'), cskh.formatDate(item.assigned_at)),
			])
		)

		if (allowPii && (item.work_item || item.lead)) {
			const linkButtons = []
			if (item.work_item) {
				linkButtons.push(
					cskh.el('a', {
						class: 'cskh-btn is-primary is-small',
						href: '/cskh/work-item?id=' + encodeURIComponent(item.work_item),
						text: cskh.t('Open care workspace'),
					})
				)
			}
			if (canOpenCrm && item.lead) {
				linkButtons.push(
					cskh.el('a', {
						class: 'cskh-btn is-small',
						href: '/crm/leads/' + encodeURIComponent(item.lead),
						text: cskh.t('Open in CRM'),
					})
				)
			}
			if (linkButtons.length) {
				box.appendChild(cskh.el('div', { class: 'cskh-actions' }, linkButtons))
			}
		}

		const events = (history && history.items) || []
		box.appendChild(cskh.el('h3', { text: cskh.t('Ownership history') }))
		if (!events.length) {
			box.appendChild(
				cskh.el('div', { class: 'cskh-alert is-empty', text: cskh.t('No data') })
			)
		} else {
			const table = cskh.el('table', { class: 'cskh-table' }, [
				cskh.el('thead', {}, [
					cskh.el('tr', {}, [
						cskh.el('th', { text: cskh.t('Event at') }),
						cskh.el('th', { text: cskh.t('Action') }),
						cskh.el('th', { text: cskh.t('From owner') }),
						cskh.el('th', { text: cskh.t('To owner') }),
						cskh.el('th', { text: cskh.t('Actor') }),
						cskh.el('th', { text: cskh.t('Reason') }),
					]),
				]),
			])
			const body = cskh.el('tbody')
			events.forEach(function (event) {
				body.appendChild(
					cskh.el('tr', {}, [
						cskh.el('td', { text: cskh.formatDate(event.event_at) }),
						cskh.el('td', { text: actionLabel(event.action) }),
						cskh.el('td', { text: event.from_user || '—' }),
						cskh.el('td', { text: event.to_user || '—' }),
						cskh.el('td', { text: event.actor || '—' }),
						cskh.el('td', { text: formatHistoryReason(event.reason) }),
					])
				)
			})
			table.appendChild(body)
			box.appendChild(table)
		}

		if (canManage) {
			if (mode === 'reassign' || mode === 'revoke') {
				box.appendChild(actionForm(item, mode))
			} else {
				box.appendChild(
					cskh.el('div', { class: 'cskh-actions' }, [
						cskh.el('button', {
							class: 'cskh-btn is-primary is-small',
							type: 'button',
							text: cskh.t('Transfer owner'),
							onclick: function () {
								renderDetail(item, 'reassign', history)
							},
						}),
						cskh.el('button', {
							class: 'cskh-btn is-danger is-small',
							type: 'button',
							text: cskh.t('Revoke'),
							onclick: function () {
								renderDetail(item, 'revoke', history)
							},
						}),
					])
				)
			}
		}
	}

	function actionLabel(value) {
		const map = {
			assign: cskh.t('Assign'),
			reassign: cskh.t('Transfer owner'),
			revoke: cskh.t('Revoke'),
			close: cskh.t('Close work item'),
			reopen: cskh.t('Reopen work item'),
		}
		return map[value] || value || '—'
	}

	function formatHistoryReason(value) {
		if (!value) return '—'
		const text = String(value)
		const prefix = 'Distribution run '
		if (text.indexOf(prefix) === 0) {
			return cskh.t('The distribution run') + ' ' + text.slice(prefix.length)
		}
		return cskh.t(text, text)
	}

	function actionForm(item, mode) {
		const reason = document.createElement('input')
		reason.className = 'cskh-input'
		reason.placeholder = cskh.t('Reason')
		reason.value = ''

		const target = document.createElement('select')
		target.className = 'cskh-select'
		assignees.forEach(function (assignee) {
			if (assignee.user === item.assigned_to) return
			target.appendChild(
				cskh.el('option', {
					value: assignee.user,
					text: assignee.full_name + ' (' + assignee.user + ')',
				})
			)
		})

		const confirmButton = cskh.el('button', {
			class: mode === 'revoke' ? 'cskh-btn is-danger' : 'cskh-btn is-primary',
			type: 'button',
			text: mode === 'revoke' ? cskh.t('Revoke') : cskh.t('Transfer owner'),
		})

		const cancelButton = cskh.el('button', {
			class: 'cskh-btn',
			type: 'button',
			text: cskh.t('Cancel'),
		})
		cancelButton.addEventListener('click', function () {
			document.getElementById('mine-detail-card').hidden = true
		})

		confirmButton.addEventListener('click', function () {
			submitAction(item, mode, reason.value.trim(), target.value, confirmButton)
		})

		const fields = []
		if (mode !== 'revoke') {
			fields.push(
				cskh.el('label', { class: 'cskh-inline-field' }, [
					cskh.el('span', { text: cskh.t('New owner') }),
					target,
				])
			)
		}
		fields.push(
			cskh.el('label', { class: 'cskh-inline-field' }, [
				cskh.el('span', { text: cskh.t('Reason') }),
				reason,
			])
		)

		return cskh.el('div', { class: 'cskh-panel' }, [
			cskh.el('h3', {
				text:
					mode === 'revoke'
						? cskh.t('Revoke the ownership')
						: cskh.t('Transfer the ownership'),
			}),
			cskh.el('div', { class: 'cskh-inline-list' }, fields),
			cskh.el('div', { class: 'cskh-actions' }, [confirmButton, cancelButton]),
		])
	}

	async function submitAction(item, mode, reason, targetUser, button) {
		const alertBox = document.getElementById('mine-alert')
		if (!reason) {
			cskh.alert(
				alertBox,
				'warning',
				cskh.t('A reason is required to transfer or revoke an ownership.')
			)
			return
		}
		if (mode !== 'revoke' && !targetUser) {
			cskh.alert(alertBox, 'warning', cskh.t('Select the new owner of the work item.'))
			return
		}
		if (button) button.disabled = true
		try {
			if (mode === 'revoke') {
				await cskh.post(API_REVOKE, {
					work_item: item.work_item,
					expected_owner: item.assigned_to,
					reason: reason,
					request_id: 'ui-revoke-' + cskh.uuid(),
					return_to_pool: 1,
				})
				cskh.alert(alertBox, 'success', cskh.t('Revoked') + ': ' + item.work_item)
			} else {
				await cskh.post(API_REASSIGN, {
					work_item: item.work_item,
					expected_owner: item.assigned_to,
					target_user: targetUser,
					reason: reason,
					request_id: 'ui-reassign-' + cskh.uuid(),
				})
				cskh.alert(alertBox, 'success', cskh.t('Transferred') + ': ' + item.work_item)
			}
			document.getElementById('mine-detail-card').hidden = true
			load()
			loadScope()
		} catch (error) {
			cskh.renderError(alertBox, error)
			loadScope()
		} finally {
			if (button) button.disabled = false
		}
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

	function syncMineCampaignFilter(items) {
		const select = document.getElementById('mine-campaign')
		if (!select) return
		const known = {}
		Array.from(select.options).forEach(function (opt) {
			if (opt.value) known[opt.value] = true
		})
		;(items || []).forEach(function (item) {
			if (!item.campaign || known[item.campaign]) return
			known[item.campaign] = true
			select.appendChild(
				cskh.el('option', {
					value: item.campaign,
					text: item.campaign_title || item.campaign_code || item.campaign,
				})
			)
		})
	}

	async function loadCampaigns() {
		if (!canReadScope) return
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
		} catch (_error) {
			/* The campaign filter is optional. */
		}
	}

	const searchWrap = document.getElementById('mine-search-wrap')
	const chipBar = document.getElementById('mine-filter-chips')
	const scopeCareWrap = document.getElementById('mine-scope-care-wrap')
	const scopeCareSelect = document.getElementById('mine-scope-care-filter')
	if (searchWrap && !allowPii) searchWrap.hidden = true
	if (chipBar && !allowPii) chipBar.hidden = true
	if (scopeCareWrap && !allowPii) scopeCareWrap.hidden = true

	const careSelect = document.getElementById('mine-care-filter')
	if (careSelect) {
		if (!allowPii) careSelect.disabled = true
		careSelect.addEventListener('change', function () {
			syncFilterChips(careSelect.value)
			pager.goto(1)
			load()
		})
	}
	;(function applyInitialCareFiltersFromQuery() {
		if (!allowPii) return
		try {
			const params = new URLSearchParams(window.location.search || '')
			const rawCare = (params.get('care_filter') || '').trim()
			if (rawCare && careSelect) {
				const hasOption = Array.from(careSelect.options).some(function (o) {
					return o.value === rawCare
				})
				if (hasOption) {
					careSelect.value = rawCare
					syncFilterChips(rawCare)
				}
			}
			const rawScopeCare = (params.get('scope_care') || '').trim()
			if (rawScopeCare && scopeCareSelect && canReadScope) {
				const hasScopeOption = Array.from(scopeCareSelect.options).some(function (o) {
					return o.value === rawScopeCare
				})
				if (hasScopeOption) {
					scopeCareSelect.value = rawScopeCare
				}
			}
		} catch (_err) {
			/* ignore malformed query */
		}
	})()
	if (chipBar) {
		chipBar.addEventListener('click', function (ev) {
			const btn = ev.target.closest('.cskh-chip')
			if (!btn || !careSelect) return
			const val = btn.getAttribute('data-filter') || ''
			careSelect.value = val
			syncFilterChips(val)
			pager.goto(1)
			load()
		})
	}

	const searchInput = document.getElementById('mine-search')
	function runSearchNow() {
		if (searchDebounceTimer) {
			clearTimeout(searchDebounceTimer)
			searchDebounceTimer = null
		}
		pager.goto(1)
		load()
	}
	if (searchInput) {
		searchInput.addEventListener('input', function () {
			if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
			searchDebounceTimer = setTimeout(function () {
				searchDebounceTimer = null
				pager.goto(1)
				load()
			}, 220)
		})
		searchInput.addEventListener('search', runSearchNow)
		searchInput.addEventListener('keydown', function (ev) {
			if (ev.key === 'Enter') {
				ev.preventDefault()
				runSearchNow()
			}
		})
	}

	document.getElementById('mine-refresh').addEventListener('click', function () {
		if (searchDebounceTimer) {
			clearTimeout(searchDebounceTimer)
			searchDebounceTimer = null
		}
		pager.goto(1)
		scopePager.goto(1)
		load()
		loadScope()
	})
	document.getElementById('mine-campaign').addEventListener('change', function () {
		pager.goto(1)
		scopePager.goto(1)
		load()
		loadScope()
	})
	document.getElementById('mine-sort').addEventListener('change', function () {
		renderMineTable()
	})
	if (scopeCareSelect) {
		if (!allowPii) scopeCareSelect.disabled = true
		scopeCareSelect.addEventListener('change', function () {
			scopePager.goto(1)
			loadScope()
		})
	}
	const scopeOwnerInput = document.getElementById('mine-scope-owner')
	if (scopeOwnerInput) {
		scopeOwnerInput.addEventListener('input', function () {
			if (scopeOwnerDebounceTimer) clearTimeout(scopeOwnerDebounceTimer)
			scopeOwnerDebounceTimer = setTimeout(function () {
				scopeOwnerDebounceTimer = null
				scopePager.goto(1)
				loadScope()
			}, 250)
		})
		scopeOwnerInput.addEventListener('keydown', function (ev) {
			if (ev.key === 'Enter') {
				ev.preventDefault()
				if (scopeOwnerDebounceTimer) {
					clearTimeout(scopeOwnerDebounceTimer)
					scopeOwnerDebounceTimer = null
				}
				scopePager.goto(1)
				loadScope()
			}
		})
	}
	document.getElementById('mine-scope-refresh').addEventListener('click', function () {
		scopePager.goto(1)
		loadScope()
	})
	const scopePrevBtn = document.getElementById('mine-scope-prev')
	const scopeNextBtn = document.getElementById('mine-scope-next')
	if (scopePrevBtn) {
		scopePrevBtn.addEventListener('click', function () {
			scopePager.goto(Math.max(1, scopePager.page() - 1))
			loadScope()
		})
	}
	if (scopeNextBtn) {
		scopeNextBtn.addEventListener('click', function () {
			scopePager.goto(scopePager.page() + 1)
			loadScope()
		})
	}
	document.getElementById('mine-detail-close').addEventListener('click', function () {
		document.getElementById('mine-detail-card').hidden = true
	})
	document.getElementById('mine-prev').addEventListener('click', function () {
		pager.goto(Math.max(1, pager.page() - 1))
		load()
	})
	document.getElementById('mine-next').addEventListener('click', function () {
		pager.goto(pager.page() + 1)
		load()
	})

	async function loadIntakeCampaigns() {
		if (!allowPii) return
		try {
			const res = await cskhP2Api.listIntakeCampaigns()
			intakeCampaigns = (res && res.items) || []
			const singleSel = document.getElementById('intake-campaign')
			const bulkSel = document.getElementById('intake-bulk-campaign')
			const mineSel = document.getElementById('mine-campaign')
			const knownMine = {}
			if (mineSel) {
				Array.from(mineSel.options).forEach(function (o) {
					if (o.value) knownMine[o.value] = true
				})
			}
			intakeCampaigns.forEach(function (camp) {
				const kcn = [camp.factory_name, camp.industrial_zone].filter(Boolean).join(' · ')
				const label = (camp.title || camp.code) + (kcn ? ' (' + kcn + ')' : '')
				if (singleSel) {
					singleSel.appendChild(cskh.el('option', { value: camp.campaign, text: label }))
				}
				if (bulkSel) {
					bulkSel.appendChild(cskh.el('option', { value: camp.campaign, text: label }))
				}
				if (mineSel && !knownMine[camp.campaign]) {
					knownMine[camp.campaign] = true
					mineSel.appendChild(cskh.el('option', { value: camp.campaign, text: label }))
				}
			})
		} catch (_err) {
			/* optional */
		}
	}

	function setupIntakePanel() {
		const card = document.getElementById('mine-intake-card')
		const openBtn = document.getElementById('mine-open-intake')
		const closeBtn = document.getElementById('mine-close-intake')
		const tabSingle = document.getElementById('intake-tab-single')
		const tabBulk = document.getElementById('intake-tab-bulk')
		const singlePane = document.getElementById('intake-single-pane')
		const bulkPane = document.getElementById('intake-bulk-pane')
		const phoneInput = document.getElementById('intake-phone')
		const statusBox = document.getElementById('intake-phone-status')
		const intakeAlert = document.getElementById('intake-alert')
		const submitSingle = document.getElementById('intake-submit-single')
		const submitBulk = document.getElementById('intake-submit-bulk')
		const bulkResult = document.getElementById('intake-bulk-result')
		if (!card || !openBtn) return

		openBtn.addEventListener('click', function () {
			card.hidden = !card.hidden
			if (!card.hidden && phoneInput) phoneInput.focus()
		})
		if (closeBtn) {
			closeBtn.addEventListener('click', function () {
				card.hidden = true
			})
		}
		if (tabSingle && tabBulk && singlePane && bulkPane) {
			tabSingle.addEventListener('click', function () {
				tabSingle.classList.add('is-active')
				tabBulk.classList.remove('is-active')
				singlePane.hidden = false
				bulkPane.hidden = true
			})
			tabBulk.addEventListener('click', function () {
				tabBulk.classList.add('is-active')
				tabSingle.classList.remove('is-active')
				bulkPane.hidden = false
				singlePane.hidden = true
			})
		}

		async function runPhoneCheck(rawVal) {
			if (!statusBox) return
			const trimmed = String(rawVal || '').trim()
			if (trimmed.replace(/\D/g, '').length < 9) {
				statusBox.hidden = true
				statusBox.textContent = ''
				return
			}
			try {
				const check = await cskhP2Api.checkPhone(trimmed)
				statusBox.hidden = false
				if (!check || check.valid === false) {
					statusBox.className = 'cskh-protection-status is-conflict'
					statusBox.textContent = (check && check.message) || cskh.t('Invalid Vietnamese mobile phone number.')
				} else if (check.status === 'available' || check.status === 'reusable' || check.status === 'reclaimable') {
					statusBox.className = 'cskh-protection-status is-ok'
					statusBox.textContent =
						'✓ ' +
						check.national_phone +
						' — ' +
						cskh.t('Available: ready to bind ownership protection to you immediately.')
				} else if (check.status === 'owned_by_me') {
					statusBox.className = 'cskh-protection-status is-mine'
					statusBox.textContent =
						'ℹ ' +
						check.national_phone +
						' — ' +
						cskh.t('Already protected under your account.')
				} else {
					statusBox.className = 'cskh-protection-status is-conflict'
					statusBox.textContent =
						'✕ ' +
						check.national_phone +
						' — ' +
						cskh.t('Protected by another Marketing staff — duplicate blocked.')
				}
			} catch (err) {
				if (err && err.code === 'INVALID_PHONE') {
					statusBox.hidden = false
					statusBox.className = 'cskh-protection-status is-conflict'
					statusBox.textContent = cskh.t('Invalid Vietnamese mobile phone number.')
				} else {
					statusBox.hidden = true
				}
			}
		}

		if (phoneInput) {
			phoneInput.addEventListener('input', function () {
				if (phoneCheckTimer) clearTimeout(phoneCheckTimer)
				phoneCheckTimer = setTimeout(function () {
					runPhoneCheck(phoneInput.value)
				}, 260)
			})
		}

		if (submitSingle) {
			submitSingle.addEventListener('click', async function () {
				cskh.clear(intakeAlert)
				const rawPhone = phoneInput ? phoneInput.value.trim() : ''
				if (!rawPhone) {
					cskh.alert(intakeAlert, 'warning', cskh.t('A phone number is required.'))
					return
				}
				submitSingle.disabled = true
				try {
					const res = await cskhP2Api.addWorker({
						phone: rawPhone,
						full_name: document.getElementById('intake-name').value.trim() || null,
						campaign: document.getElementById('intake-campaign').value || null,
						acquisition_channel: document.getElementById('intake-source').value || 'Zalo',
						birth_year: document.getElementById('intake-birth-year').value || null,
						gender: document.getElementById('intake-gender').value || null,
						current_area: document.getElementById('intake-area').value.trim() || null,
						id_card_status: document.getElementById('intake-cccd').value || null,
						worker_tags: document.getElementById('intake-tags').value.trim() || null,
						request_id: 'ui-intake-' + cskh.uuid(),
					})
					cskh.toast(
						'success',
						cskh.t('Protected worker phone') + ': ' + (res.national_phone || rawPhone)
					)
					phoneInput.value = ''
					document.getElementById('intake-name').value = ''
					document.getElementById('intake-birth-year').value = ''
					document.getElementById('intake-area').value = ''
					document.getElementById('intake-tags').value = ''
					if (statusBox) statusBox.hidden = true
					pager.goto(1)
					await load()
					await loadScope()
				} catch (err) {
					cskh.renderError(intakeAlert, err)
				} finally {
					submitSingle.disabled = false
				}
			})
		}

		if (submitBulk) {
			submitBulk.addEventListener('click', async function () {
				cskh.clear(intakeAlert)
				cskh.clear(bulkResult)
				const text = document.getElementById('intake-bulk-text').value.trim()
				if (!text) {
					cskh.alert(intakeAlert, 'warning', cskh.t('Please paste at least one worker phone line.'))
					return
				}
				submitBulk.disabled = true
				try {
					const res = await cskhP2Api.bulkAddWorkers({
						lines_text: text,
						campaign: document.getElementById('intake-bulk-campaign').value || null,
						acquisition_channel: document.getElementById('intake-bulk-source').value || 'Facebook',
						request_id: 'ui-bulk-intake-' + cskh.uuid(),
					})
					const summaryText =
						cskh.t('Protected') +
						': ' +
						cskh.formatNumber(res.protected_count) +
						' · ' +
						cskh.t('Already yours') +
						': ' +
						cskh.formatNumber(res.already_mine_count) +
						' · ' +
						cskh.t('Duplicate / blocked') +
						': ' +
						cskh.formatNumber(res.conflict_count) +
						' · ' +
						cskh.t('Invalid') +
						': ' +
						cskh.formatNumber(res.invalid_count)
					cskh.alert(bulkResult, res.conflict_count || res.invalid_count ? 'warning' : 'success', summaryText)
					if (res.protected_count > 0) {
						document.getElementById('intake-bulk-text').value = ''
						pager.goto(1)
						await load()
						await loadScope()
					}
				} catch (err) {
					cskh.renderError(intakeAlert, err)
				} finally {
					submitBulk.disabled = false
				}
			})
		}

		try {
			const params = new URLSearchParams(window.location.search || '')
			if (params.get('add') === '1') {
				card.hidden = false
				if (phoneInput) phoneInput.focus()
			}
		} catch (_err) {
			/* ignore */
		}
	}

	loadAssignees()
	loadCampaigns()
	loadIntakeCampaigns()
	setupIntakePanel()
	load()
	loadScope()
})()
