/* Outcome Code & Versioned Policy Settings (`/cskh/settings`, P2 WP09).

   Invariants:
     * `CSKH CRM Admin` (`can_manage = true`) may create/update/disable Outcome
       Codes (`settings.save_outcome` — NO Delete control) and save versioned
       Policies (`settings.save_policy`).
     * `Sales Manager` and `CSKH BOD Viewer` inspect the catalog and policies
       in strict read-only mode.
*/
(function () {
	'use strict'

	const reads = cskh.sequencer()
	let canManage = false
	let outReqKey = 'ui-out-' + cskh.uuid()
	let polReqKey = 'ui-pol-' + cskh.uuid()

	function groupLabel(group) {
		const map = {
			unreached: cskh.t('Attempted, not connected'),
			engaged: cskh.t('Needs follow-up'),
			positive: cskh.t('Interested'),
			negative: cskh.t('Not interested'),
			converted: cskh.t('Converted / closed'),
			invalid_contact: cskh.t('Wrong number'),
			compliance: cskh.t('Do not contact'),
			other: cskh.t('Other'),
		}
		return map[group] || group || '—'
	}

	function scopeTypeLabel(scopeType) {
		const map = {
			Global: cskh.t('Global'),
			Team: cskh.t('Team'),
			Campaign: cskh.t('Campaign'),
		}
		return map[scopeType] || scopeType || '—'
	}

	function yesNoBadge(flag, yesClass) {
		return cskh.el('span', {
			class: cskh.stateBadge(flag ? yesClass || 'connected' : 'not_connected'),
			text: flag ? cskh.t('Yes') : cskh.t('No'),
		})
	}

	function resetOutcomeForm() {
		const codeEl = document.getElementById('out-code')
		codeEl.value = ''
		codeEl.readOnly = false
		document.getElementById('out-label-vi').value = ''
		document.getElementById('out-group').value = 'unreached'
		document.getElementById('out-channels').value = 'phone_call,sms,zalo,facebook,other'
		document.getElementById('out-next-status').value = 'in_progress'
		document.getElementById('out-sort').value = '100'
		document.getElementById('out-connected').checked = false
		document.getElementById('out-interested').checked = false
		document.getElementById('out-req-note').checked = false
		document.getElementById('out-req-followup').checked = false
		document.getElementById('out-allow-followup').checked = true
		document.getElementById('out-terminal').checked = false
		document.getElementById('out-dnc').checked = false
		document.getElementById('out-enabled').checked = true
		cskh.clear(document.getElementById('set-outcome-alert'))
		outReqKey = 'ui-out-' + cskh.uuid()
	}

	function populateOutcomeForm(item) {
		const codeEl = document.getElementById('out-code')
		codeEl.value = item.code
		codeEl.readOnly = true
		document.getElementById('out-label-vi').value = cskh.outcomeLabel(
			item.code,
			item.label_vi || item.label || ''
		)
		document.getElementById('out-group').value = item.result_group || 'unreached'
		document.getElementById('out-channels').value = (item.allowed_channels || []).join(',')
		document.getElementById('out-next-status').value =
			item.suggested_next_status || 'in_progress'
		document.getElementById('out-sort').value = String(item.sort_order || 100)
		document.getElementById('out-connected').checked = !!item.is_connected
		document.getElementById('out-interested').checked = !!item.is_interested
		document.getElementById('out-req-note').checked = !!item.requires_note
		document.getElementById('out-req-followup').checked = !!item.requires_followup
		document.getElementById('out-allow-followup').checked = !!item.allows_followup
		document.getElementById('out-terminal').checked = !!item.is_terminal
		document.getElementById('out-dnc').checked = !!item.is_do_not_contact
		document.getElementById('out-enabled').checked = item.enabled !== false
		cskh.clear(document.getElementById('set-outcome-alert'))
		outReqKey = 'ui-out-' + cskh.uuid()
		document.getElementById('set-outcome-form-wrap').scrollIntoView({
			behavior: 'smooth',
			block: 'nearest',
		})
	}

	function syncPolicyScopeFields() {
		const scopeVal = document.getElementById('pol-scope').value
		document.getElementById('pol-team-wrap').hidden = scopeVal !== 'Team'
		document.getElementById('pol-campaign-wrap').hidden = scopeVal !== 'Campaign'
	}

	function resetPolicyForm() {
		const codeEl = document.getElementById('pol-code')
		codeEl.value = ''
		codeEl.readOnly = false
		document.getElementById('pol-title').value = ''
		document.getElementById('pol-scope').value = 'Global'
		document.getElementById('pol-team').value = ''
		document.getElementById('pol-campaign').value = ''
		document.getElementById('pol-backdate').value = '120'
		document.getElementById('pol-channels').value = 'phone_call,sms,zalo,facebook,other'
		document.getElementById('pol-dnc-behavior').value = 'block_and_auto_close'
		document.getElementById('pol-req-attempt-close').checked = true
		document.getElementById('pol-allow-invalid-close').checked = true
		document.getElementById('pol-leader-reopen').checked = true
		document.getElementById('pol-leader-review').checked = true
		document.getElementById('pol-enabled').checked = true
		syncPolicyScopeFields()
		cskh.clear(document.getElementById('set-policy-alert'))
		polReqKey = 'ui-pol-' + cskh.uuid()
	}

	function populatePolicyForm(pol) {
		const codeEl = document.getElementById('pol-code')
		codeEl.value = pol.policy_code
		codeEl.readOnly = true
		document.getElementById('pol-title').value = pol.title || ''
		document.getElementById('pol-scope').value = pol.scope_type || 'Global'
		document.getElementById('pol-team').value = pol.team || ''
		document.getElementById('pol-campaign').value = pol.campaign || ''
		document.getElementById('pol-backdate').value = String(pol.allowed_backdate_minutes)
		document.getElementById('pol-channels').value = (pol.enabled_channels || []).join(',')
		document.getElementById('pol-dnc-behavior').value =
			pol.do_not_contact_behavior || 'block_and_auto_close'
		document.getElementById('pol-req-attempt-close').checked = !!pol.require_attempt_before_close
		document.getElementById('pol-allow-invalid-close').checked =
			!!pol.allow_close_without_contact_for_invalid
		document.getElementById('pol-leader-reopen').checked = !!pol.allow_leader_reopen
		document.getElementById('pol-leader-review').checked = !!pol.allow_leader_review_correction
		document.getElementById('pol-enabled').checked = pol.enabled !== false
		syncPolicyScopeFields()
		cskh.clear(document.getElementById('set-policy-alert'))
		polReqKey = 'ui-pol-' + cskh.uuid()
		document.getElementById('set-policy-form-wrap').scrollIntoView({
			behavior: 'smooth',
			block: 'nearest',
		})
	}

	function renderSnapshot(snap) {
		canManage = !!snap.can_manage
		document.getElementById('set-readonly-banner').hidden = canManage
		document.getElementById('set-outcome-form-wrap').hidden = !canManage
		document.getElementById('set-policy-form-wrap').hidden = !canManage
		document.getElementById('set-outcome-actions-th').hidden = !canManage
		document.getElementById('set-policy-actions-th').hidden = !canManage

		const outRows = document.getElementById('set-outcomes-rows')
		cskh.clear(outRows)
		;(snap.outcomes || []).forEach(function (item) {
			const cells = [
				cskh.el('td', { class: 'cskh-mono', text: item.code + ' (v' + item.version + ')' }),
				cskh.el('td', { text: cskh.outcomeLabel(item.code, item.label) }),
				cskh.el('td', { text: groupLabel(item.result_group) }),
				cskh.el('td', {
					text: (item.allowed_channels || [])
						.map(function (c) {
							return cskh.channelLabel(c)
						})
						.join(', '),
				}),
				cskh.el('td', {}, [yesNoBadge(item.is_connected, 'connected')]),
				cskh.el('td', {}, [yesNoBadge(item.requires_note, 'due_today')]),
				cskh.el('td', {}, [yesNoBadge(item.requires_followup, 'due_today')]),
				cskh.el('td', {}, [yesNoBadge(item.is_terminal, 'closed')]),
				cskh.el('td', {}, [
					cskh.el('span', {
						class: cskh.stateBadge(item.enabled !== false ? 'assigned' : 'revoked'),
						text: item.enabled !== false ? cskh.t('Enabled') : cskh.t('Disabled'),
					}),
				]),
			]
			if (canManage) {
				cells.push(
					cskh.el('td', {}, [
						cskh.el('button', {
							class: 'cskh-btn is-small',
							type: 'button',
							text: cskh.t('Edit'),
							onclick: function () {
								populateOutcomeForm(item)
							},
						}),
					])
				)
			}
			outRows.appendChild(cskh.el('tr', {}, cells))
		})
		document.getElementById('set-outcomes-card').hidden = false

		const polRows = document.getElementById('set-policies-rows')
		cskh.clear(polRows)
		;(snap.policies || []).forEach(function (pol) {
			const scopeDetail =
				scopeTypeLabel(pol.scope_type) +
				(pol.team ? ' (' + pol.team + ')' : '') +
				(pol.campaign ? ' (' + pol.campaign + ')' : '')
			const cells = [
				cskh.el('td', { class: 'cskh-mono', text: pol.policy_code }),
				cskh.el('td', { text: pol.title }),
				cskh.el('td', { text: scopeDetail }),
				cskh.el('td', { class: 'cskh-mono', text: 'v' + pol.version }),
				cskh.el('td', {
					class: 'cskh-mono',
					text: cskh.formatNumber(pol.allowed_backdate_minutes),
				}),
				cskh.el('td', {
					text: (pol.enabled_channels || [])
						.map(function (c) {
							return cskh.channelLabel(c)
						})
						.join(', '),
				}),
				cskh.el('td', {}, [
					cskh.el('span', {
						class: cskh.stateBadge(pol.enabled !== false ? 'assigned' : 'revoked'),
						text: pol.enabled !== false ? cskh.t('Enabled') : cskh.t('Disabled'),
					}),
				]),
			]
			if (canManage) {
				cells.push(
					cskh.el('td', {}, [
						cskh.el('button', {
							class: 'cskh-btn is-small',
							type: 'button',
							text: cskh.t('Edit'),
							onclick: function () {
								populatePolicyForm(pol)
							},
						}),
					])
				)
			}
			polRows.appendChild(cskh.el('tr', {}, cells))
		})
		document.getElementById('set-policies-card').hidden = false
	}

	async function load() {
		const loading = document.getElementById('set-loading')
		const alertBox = document.getElementById('set-alert')
		loading.hidden = false
		cskh.clear(alertBox)
		const token = reads.next()
		try {
			const snap = await cskhP2Api.getSettingsSnapshot()
			if (token !== reads.current()) return
			renderSnapshot(snap)
		} catch (err) {
			if (token !== reads.current()) return
			cskh.renderError(alertBox, err)
		} finally {
			loading.hidden = true
		}
	}

	async function saveOutcomeHandler() {
		const alertBox = document.getElementById('set-outcome-alert')
		const btn = document.getElementById('out-save-btn')
		cskh.clear(alertBox)
		const code = document.getElementById('out-code').value.trim()
		const labelVi = document.getElementById('out-label-vi').value.trim()
		if (!code || !labelVi) {
			cskh.alert(alertBox, 'warning', cskh.errorText('INVALID_REQUEST'), '[INVALID_REQUEST]')
			return
		}
		btn.disabled = true
		try {
			await cskhP2Api.saveOutcome({
				code: code,
				label_vi: labelVi,
				result_group: document.getElementById('out-group').value,
				allowed_channels: document.getElementById('out-channels').value.trim(),
				suggested_next_status: document.getElementById('out-next-status').value,
				sort_order: Number(document.getElementById('out-sort').value || 100),
				is_contact_attempt: true,
				is_connected: document.getElementById('out-connected').checked,
				is_interested: document.getElementById('out-interested').checked,
				requires_note: document.getElementById('out-req-note').checked,
				requires_followup: document.getElementById('out-req-followup').checked,
				allows_followup: document.getElementById('out-allow-followup').checked,
				is_terminal: document.getElementById('out-terminal').checked,
				is_do_not_contact: document.getElementById('out-dnc').checked,
				requires_completion_reason: document.getElementById('out-dnc').checked,
				enabled: document.getElementById('out-enabled').checked,
				idempotency_key: outReqKey,
			})
			outReqKey = 'ui-out-' + cskh.uuid()
			cskh.toast('success', cskh.t('Outcome code saved.'))
			resetOutcomeForm()
			await load()
		} catch (err) {
			cskh.renderError(alertBox, err)
		} finally {
			btn.disabled = false
		}
	}

	async function savePolicyHandler() {
		const alertBox = document.getElementById('set-policy-alert')
		const btn = document.getElementById('pol-save-btn')
		cskh.clear(alertBox)
		const code = document.getElementById('pol-code').value.trim()
		const title = document.getElementById('pol-title').value.trim()
		const scopeType = document.getElementById('pol-scope').value
		const team = document.getElementById('pol-team').value.trim()
		const campaign = document.getElementById('pol-campaign').value.trim()
		if (!code || !title) {
			cskh.alert(alertBox, 'warning', cskh.errorText('INVALID_REQUEST'), '[INVALID_REQUEST]')
			return
		}
		btn.disabled = true
		try {
			await cskhP2Api.savePolicy({
				policy_code: code,
				title: title,
				scope_type: scopeType,
				team: scopeType === 'Team' ? team : null,
				campaign: scopeType === 'Campaign' ? campaign : null,
				enabled: document.getElementById('pol-enabled').checked,
				allowed_backdate_minutes: Number(document.getElementById('pol-backdate').value || 120),
				require_attempt_before_close: document.getElementById('pol-req-attempt-close').checked,
				allow_close_without_contact_for_invalid:
					document.getElementById('pol-allow-invalid-close').checked,
				do_not_contact_behavior: document.getElementById('pol-dnc-behavior').value,
				enabled_channels: document.getElementById('pol-channels').value.trim(),
				allow_leader_reopen: document.getElementById('pol-leader-reopen').checked,
				allow_leader_review_correction: document.getElementById('pol-leader-review').checked,
				idempotency_key: polReqKey,
			})
			polReqKey = 'ui-pol-' + cskh.uuid()
			cskh.toast('success', cskh.t('Policy saved as a new version.'))
			resetPolicyForm()
			await load()
		} catch (err) {
			cskh.renderError(alertBox, err)
		} finally {
			btn.disabled = false
		}
	}

	document.getElementById('pol-scope').addEventListener('change', syncPolicyScopeFields)
	document.getElementById('out-save-btn').addEventListener('click', saveOutcomeHandler)
	document.getElementById('out-reset-btn').addEventListener('click', resetOutcomeForm)
	document.getElementById('pol-save-btn').addEventListener('click', savePolicyHandler)
	document.getElementById('pol-reset-btn').addEventListener('click', resetPolicyForm)
	document.getElementById('set-refresh').addEventListener('click', load)

	syncPolicyScopeFields()
	load()
})()
