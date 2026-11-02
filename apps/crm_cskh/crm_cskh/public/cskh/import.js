/* Import list (P1 §9.2).

   Flow: upload a **private** file -> column mapping -> preview -> confirm ->
   progress and results.

   Contract (T1B, frozen):
     POST crm_cskh.api.imports.create_preview(file_name, mapping, idempotency_key, import_mode)
     POST crm_cskh.api.imports.confirm(batch)
     GET  crm_cskh.api.imports.get_status(batch)

   The file is uploaded through the standard Frappe `upload_file` endpoint as a
   private file; only its `file_name` is sent to the P1 API, and the API
   refuses a file that is not private. The preview is never trusted: the server
   re-reads the file and re-checks the canonical identity before every write.

   Every row is classified so the operator can tell the four situations apart:
   valid, duplicate in file, duplicate in CRM and conflict with an active
   campaign. */
(function () {
	'use strict'

	const API_CREATE = 'crm_cskh.api.imports.create_preview'
	const API_CONFIRM = 'crm_cskh.api.imports.confirm'
	const API_STATUS = 'crm_cskh.api.imports.get_status'

	let currentBatch = null
	let uploadedFile = null

	function uuid() {
		if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID()
		return 'cskh-' + Date.now() + '-' + Math.floor(Math.random() * 1e9)
	}

	function classLabel(value) {
		const map = {
			valid: cskh.t('valid'),
			duplicate_in_file: cskh.t('duplicate_in_file'),
			duplicate_in_crm: cskh.t('duplicate_in_crm'),
			conflict_active_campaign: cskh.t('conflict_active_campaign'),
			invalid: cskh.t('invalid'),
		}
		return map[value] || value || '—'
	}

	function statusLabel(value) {
		const map = {
			DRY_RUN: cskh.t('Status'),
			CONFIRMED: cskh.t('Status'),
			QUEUED: cskh.t('Status'),
			RUNNING: cskh.t('Status'),
			COMPLETED: cskh.t('Status'),
			COMPLETED_WITH_ERRORS: cskh.t('Error'),
			FAILED: cskh.t('Error'),
		}
		return map[value] || value || '—'
	}

	async function uploadAndPreview() {
		const alertBox = document.getElementById('import-alert')
		const fileInput = document.getElementById('import-file')
		const file = fileInput.files && fileInput.files[0]
		cskh.clear(alertBox)

		if (!file) {
			cskh.alert(alertBox, 'warning', cskh.t('The file must be CSV or XLSX.'))
			return
		}
		const name = (file.name || '').toLowerCase()
		if (!/\.(csv|xlsx)$/.test(name)) {
			cskh.alert(alertBox, 'warning', cskh.t('The file must be CSV or XLSX.'))
			return
		}

		const previewButton = document.getElementById('import-preview')
		previewButton.disabled = true
		try {
			uploadedFile = await cskh.uploadPrivateFile(file)
			const mapping = {}
			const phoneColumn = document.getElementById('import-phone-column').value.trim()
			if (phoneColumn) mapping.phone = phoneColumn

			const data = await cskh.post(API_CREATE, {
				file_name: uploadedFile.name || uploadedFile.file_name,
				mapping: mapping,
				idempotency_key: 'ui-import-' + uuid(),
				import_mode: 'standard',
			})
			currentBatch = data.batch
			renderPreview(data)
			document.getElementById('step-preview').hidden = false
			document.getElementById('step-upload').classList.add('is-done')
			document.getElementById('step-preview').classList.add('is-active')
		} catch (error) {
			cskh.renderError(alertBox, error)
		} finally {
			previewButton.disabled = false
		}
	}

	function renderPreview(data) {
		const summary = document.getElementById('import-summary')
		const rows = document.getElementById('import-rows')
		cskh.clear(summary)
		cskh.clear(rows)

		const totals = (data && data.totals) || {}
		summary.appendChild(
			cskh.el('div', { class: 'cskh-grid' }, [
				metric(cskh.t('Total rows'), totals.total_rows),
				metric(cskh.t('Valid rows'), totals.valid_rows),
				metric(cskh.t('New rows'), totals.new_rows),
				metric(cskh.t('Duplicate rows'), totals.duplicate_rows),
				metric(cskh.t('Error rows'), totals.error_rows),
			])
		)
		summary.appendChild(
			cskh.el('p', { class: 'cskh-muted', text: cskh.t('Batch') + ': ' + (data.batch || '—') })
		)
		cskh.alert(
			summary,
			'success',
			cskh.t('Preview is ready. Review the numbers before confirming.')
		)

		const previewRows = (data && data.preview) || []
		if (!previewRows.length) {
			rows.appendChild(
				cskh.el('div', { class: 'cskh-alert is-empty', text: cskh.t('No data') })
			)
			return
		}
		const table = cskh.el('table', { class: 'cskh-table' }, [
			cskh.el('thead', {}, [
				cskh.el('tr', {}, [
					cskh.el('th', { class: 'num', text: cskh.t('Row') }),
					cskh.el('th', { text: cskh.t('Classification') }),
					cskh.el('th', { text: cskh.t('Reason') }),
				]),
			]),
		])
		const body = cskh.el('tbody')
		previewRows.forEach(function (row) {
			body.appendChild(
				cskh.el('tr', {}, [
					cskh.el('td', { class: 'num', text: row.row_number }),
					cskh.el('td', {}, [
						cskh.el('span', {
							class: cskh.stateBadge(row.class),
							text: classLabel(row.class),
						}),
					]),
					cskh.el('td', { text: row.reason || '—' }),
				])
			)
		})
		table.appendChild(body)
		rows.appendChild(table)
		if (data.preview_truncated) {
			rows.appendChild(
				cskh.el('p', { class: 'cskh-muted', text: cskh.t('Total rows') + ': ' + data.totals.total_rows })
			)
		}
	}

	async function confirmImport() {
		const alertBox = document.getElementById('import-alert')
		const button = document.getElementById('import-confirm')
		button.disabled = true
		try {
			const data = await cskh.post(API_CONFIRM, { batch: currentBatch })
			renderProgress(data)
			const final = await cskh.poll({
				check: function () {
					return cskh.get(API_STATUS, { batch: currentBatch })
				},
				done: function (state) {
					return (
						state &&
						(state.status === 'COMPLETED' ||
							state.status === 'COMPLETED_WITH_ERRORS' ||
							state.status === 'FAILED')
					)
				},
			})
			renderProgress(final)
			const alertBox2 = document.getElementById('import-alert')
			if (final.status === 'COMPLETED') {
				cskh.alert(alertBox2, 'success', cskh.t('Status') + ': ' + final.status)
			} else if (final.status === 'FAILED') {
				cskh.alert(alertBox2, 'error', cskh.t('Error') + ': ' + final.status)
			} else {
				cskh.alert(alertBox2, 'warning', cskh.t('Status') + ': ' + final.status)
			}
		} catch (error) {
			cskh.renderError(alertBox, error)
		} finally {
			button.disabled = false
		}
	}

	function renderProgress(data) {
		const summary = document.getElementById('import-summary')
		const totals = (data && data.totals) || {}
		cskh.clear(summary)
		summary.appendChild(
			cskh.el('div', { class: 'cskh-grid' }, [
				metric(cskh.t('Status'), data && data.status),
				metric(cskh.t('Total rows'), totals.total_rows),
				metric(cskh.t('Valid rows'), totals.valid_rows),
				metric(cskh.t('Duplicate rows'), totals.duplicate_rows),
				metric(cskh.t('Error rows'), totals.error_rows),
				metric(cskh.t('Committed at'), cskh.formatDate(data && data.committed_at)),
			])
		)
	}

	function reset() {
		currentBatch = null
		uploadedFile = null
		document.getElementById('step-preview').hidden = true
		document.getElementById('step-upload').classList.remove('is-done')
		cskh.clear(document.getElementById('import-summary'))
		cskh.clear(document.getElementById('import-rows'))
		cskh.clear(document.getElementById('import-alert'))
		document.getElementById('import-file').value = ''
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

	document.getElementById('import-preview').addEventListener('click', function () {
		uploadAndPreview()
	})
	document.getElementById('import-confirm').addEventListener('click', function () {
		confirmImport()
	})
	document.getElementById('import-reset').addEventListener('click', function () {
		reset()
	})
	document.getElementById('import-file').addEventListener('change', function () {
		document.getElementById('import-preview').disabled = false
	})
})()
