/* Import list (P1 §9.2).

   Flow: upload a **private** file -> column mapping -> preview -> confirm ->
   progress and results -> the history of the batches with the error file.

   Contract (T1B, frozen):
     POST crm_cskh.api.imports.create_preview(file_name, mapping, idempotency_key, import_mode)
     POST crm_cskh.api.imports.confirm(batch)
     GET  crm_cskh.api.imports.get_status(batch)
     GET  crm_cskh.api.import_history.history(page, page_size, status)          (read side, T1A)
     GET  crm_cskh.api.import_history.download_error_file(batch)                (download, T1A)

   The file is uploaded through the dedicated private-file endpoint of the P1
   app; only its `name` is sent to the P1 API, and the API refuses a file
   that is not private. The preview is never trusted: the server re-reads the
   file and re-checks the canonical identity before every write. */
(function () {
	'use strict'

	const API_CREATE = 'crm_cskh.api.imports.create_preview'
	const API_CONFIRM = 'crm_cskh.api.imports.confirm'
	const API_STATUS = 'crm_cskh.api.imports.get_status'
	const API_HISTORY = 'crm_cskh.api.import_history.history'
	const API_DETAIL = 'crm_cskh.api.import_history.batch_detail'
	const API_DOWNLOAD_ERROR = '/api/method/crm_cskh.api.import_history.download_error_file'

	const HISTORY_PAGE_SIZE = 20

	let currentBatch = null
	let uploadedFile = null

	const historyPager = cskh.pager({
		pageSize: HISTORY_PAGE_SIZE,
		range: document.getElementById('import-history-range'),
		prev: document.getElementById('import-history-prev'),
		next: document.getElementById('import-history-next'),
	})

	/* A slower read must never overwrite a newer one (see `cskh.sequencer`). */
	const historyReads = cskh.sequencer()

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
			DRY_RUN: cskh.t('Preview'),
			CONFIRMED: cskh.t('Confirmed'),
			QUEUED: cskh.t('Queued'),
			RUNNING: cskh.t('Running'),
			COMPLETED: cskh.t('Completed'),
			COMPLETED_WITH_ERRORS: cskh.t('Completed with errors'),
			FAILED: cskh.t('Failed'),
		}
		return map[value] || value || '—'
	}

	function statusBadgeClass(value) {
		if (value === 'COMPLETED') return cskh.stateBadge('assigned')
		if (value === 'COMPLETED_WITH_ERRORS') return cskh.stateBadge('queued')
		if (value === 'FAILED') return cskh.stateBadge('invalid')
		return cskh.stateBadge('eligible')
	}

	function buildErrorDownloadUrl(batch, artifact) {
		if (artifact && typeof artifact === 'object' && artifact.download_url) {
			return artifact.download_url
		}
		if (!batch || !artifact) return ''
		return API_DOWNLOAD_ERROR + '?batch=' + encodeURIComponent(batch)
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
			const nameColumnEl = document.getElementById('import-name-column')
			const sourceColumnEl = document.getElementById('import-source-column')
			const nameColumn = nameColumnEl ? nameColumnEl.value.trim() : ''
			const sourceColumn = sourceColumnEl ? sourceColumnEl.value.trim() : ''
			if (phoneColumn) mapping.phone = phoneColumn
			if (nameColumn) mapping.name = nameColumn
			if (sourceColumn) mapping.source = sourceColumn

			const data = await cskh.post(API_CREATE, {
				file_name: uploadedFile.name || uploadedFile.file_name,
				mapping: mapping,
				idempotency_key: 'ui-import-' + uuid(),
				import_mode: 'standard',
			})
			currentBatch = data.batch
			renderPreview(data)
			document.getElementById('step-preview').hidden = false
			document.getElementById('step-progress').hidden = true
			document.getElementById('step-upload').classList.add('is-done')
			document.getElementById('step-preview').classList.add('is-active')
			document.getElementById('step-preview').classList.remove('is-done')
			document.getElementById('import-confirm').disabled = false
			loadHistory()
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

		const alertContainer = cskh.el('div')
		cskh.alert(
			alertContainer,
			'success',
			cskh.t('Preview is ready. Review the numbers before confirming.')
		)
		summary.appendChild(alertContainer)

		const totals = (data && data.totals) || {}
		summary.appendChild(
			cskh.el('div', { class: 'cskh-grid' }, [
				metric(cskh.t('Total rows'), cskh.formatNumber(totals.total_rows)),
				metric(cskh.t('Valid rows'), cskh.formatNumber(totals.valid_rows)),
				metric(cskh.t('New rows'), cskh.formatNumber(totals.new_rows)),
				metric(cskh.t('Duplicate rows'), cskh.formatNumber(totals.duplicate_rows)),
				metric(cskh.t('Error rows'), cskh.formatNumber(totals.error_rows)),
			])
		)
		summary.appendChild(
			cskh.el('p', {
				class: 'cskh-muted',
				text:
					cskh.t('Batch') +
					': ' +
					(data.batch || '—') +
					(data.checksum ? ' · SHA-256: ' + String(data.checksum).slice(0, 16) + '…' : ''),
			})
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
					cskh.el('td', { text: row.reason ? cskh.t(row.reason) : '—' }),
				])
			)
		})
		table.appendChild(body)
		rows.appendChild(table)
		if (data.preview_truncated) {
			rows.appendChild(
				cskh.el('p', {
					class: 'cskh-muted',
					text: cskh.t('Total rows') + ': ' + cskh.formatNumber(totals.total_rows),
				})
			)
		}
	}

	async function confirmImport() {
		const alertBox = document.getElementById('import-alert')
		const button = document.getElementById('import-confirm')
		if (!currentBatch) {
			cskh.alert(alertBox, 'warning', cskh.t('Preview is ready. Review the numbers before confirming.'))
			return
		}
		// Disable for the whole confirmation + worker poll to prevent double-submit (P1 §12.4).
		button.disabled = true
		try {
			const data = await cskh.post(API_CONFIRM, { batch: currentBatch })
			const stepProgress = document.getElementById('step-progress')
			stepProgress.hidden = false
			stepProgress.classList.add('is-active')
			document.getElementById('step-preview').classList.add('is-done')
			renderProgress(data)

			let final = await cskh.poll({
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
			try {
				const detail = await cskh.get(API_DETAIL, { batch: currentBatch })
				if (detail) final = detail
			} catch (err) {
				/* Keep `final` from `get_status` if enrichment fails. */
			}
			renderProgress(final)
			if (final.status === 'COMPLETED') {
				cskh.alert(alertBox, 'success', cskh.t('Completed') + ': ' + (final.batch || currentBatch))
			} else if (final.status === 'FAILED') {
				cskh.alert(
					alertBox,
					'error',
					cskh.t('Failed') + ': ' + statusLabel(final.status),
					final.error ? cskh.t(final.error) : final.batch || currentBatch
				)
			} else if (final.status === 'COMPLETED_WITH_ERRORS') {
				cskh.alert(
					alertBox,
					'warning',
					cskh.t('Completed with errors') + ': ' + (final.batch || currentBatch),
					cskh.t('Error rows') + ': ' + ((final.totals && final.totals.error_rows) || 0)
				)
			} else {
				cskh.alert(alertBox, 'warning', cskh.t('Status') + ': ' + statusLabel(final.status))
			}
			loadHistory()
		} catch (error) {
			cskh.renderError(alertBox, error)
			button.disabled = false
		}
	}

	function renderProgress(data) {
		const progressBox = document.getElementById('import-progress')
		const stepProgress = document.getElementById('step-progress')
		if (!progressBox) return
		stepProgress.hidden = false
		const totals = (data && data.totals) || {}
		cskh.clear(progressBox)
		progressBox.appendChild(
			cskh.el('div', { class: 'cskh-grid' }, [
				metric(cskh.t('Batch'), data && data.batch),
				metric(cskh.t('Status'), statusLabel(data && data.status)),
				metric(cskh.t('Total rows'), cskh.formatNumber(totals.total_rows)),
				metric(cskh.t('Created rows'), cskh.formatNumber(totals.created_rows)),
				metric(cskh.t('Reused rows'), cskh.formatNumber(totals.reused_rows)),
				metric(cskh.t('Skipped rows'), cskh.formatNumber(totals.skipped_rows)),
				metric(cskh.t('Error rows'), cskh.formatNumber(totals.error_rows)),
				metric(cskh.t('Committed at'), cskh.formatDate(data && data.committed_at)),
			])
		)
		if (data && data.error) {
			progressBox.appendChild(
				cskh.el('div', {
					class: 'cskh-alert is-error',
					text: cskh.t('Reason') + ': ' + cskh.t(data.error),
				})
			)
		}
		if (data && data.error_artifact) {
			progressBox.appendChild(
				cskh.el('div', { class: 'cskh-actions' }, [
					renderArtifact(data.batch || currentBatch, data.error_artifact),
				])
			)
		}
	}

	function reset() {
		currentBatch = null
		uploadedFile = null
		document.getElementById('step-preview').hidden = true
		document.getElementById('step-progress').hidden = true
		document.getElementById('step-upload').classList.remove('is-done')
		document.getElementById('step-preview').classList.remove('is-done')
		document.getElementById('import-confirm').disabled = false
		cskh.clear(document.getElementById('import-summary'))
		cskh.clear(document.getElementById('import-rows'))
		cskh.clear(document.getElementById('import-progress'))
		cskh.clear(document.getElementById('import-alert'))
		document.getElementById('import-file').value = ''
		document.getElementById('import-preview').disabled = true
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

	/* ------------------------------------------------------------------ */
	/* The history of the batches (P1 §9.2)                                */
	/* ------------------------------------------------------------------ */

	async function loadHistory() {
		const loading = document.getElementById('import-history-loading')
		const table = document.getElementById('import-history-table')
		const empty = document.getElementById('import-history-empty')
		const rows = document.getElementById('import-history-rows')
		const alertBox = document.getElementById('import-alert')

		loading.hidden = false
		table.hidden = true
		empty.hidden = true
		cskh.clear(rows)

		const status = document.getElementById('import-history-status').value
		const mine = historyReads.next()
		try {
			const data = await cskh.get(API_HISTORY, {
				page: historyPager.page(),
				page_size: HISTORY_PAGE_SIZE,
				status: status,
			})
			if (mine !== historyReads.current()) return
			historyPager.setTotal(data && data.total)
			historyPager.render()
			const items = (data && data.items) || []
			if (!items.length) {
				empty.hidden = false
				return
			}
			items.forEach(function (item) {
				rows.appendChild(renderHistoryRow(item))
			})
			table.hidden = false
		} catch (error) {
			cskh.renderError(alertBox, error)
		} finally {
			loading.hidden = true
		}
	}

	function renderHistoryRow(item) {
		const totals = item.totals || {}
		const actions = []
		if (item.status === 'FAILED') {
			const retry = cskh.el('button', {
				class: 'cskh-btn is-small',
				type: 'button',
				text: cskh.t('Retry the failed job'),
				onclick: function () {
					retryBatch(item.batch, retry)
				},
			})
			actions.push(retry)
		}
		const statusChildren = [
			cskh.el('span', {
				class: statusBadgeClass(item.status),
				text: statusLabel(item.status),
			}),
		]
		if (item.error) {
			statusChildren.push(
				cskh.el('div', { class: 'cskh-muted', text: cskh.t(item.error) })
			)
		}
		return cskh.el('tr', {}, [
			cskh.el('td', { class: 'cskh-mono', text: item.batch || '—' }),
			cskh.el('td', {}, statusChildren),
			cskh.el('td', { class: 'num', text: cskh.formatNumber(totals.total_rows) }),
			cskh.el('td', { class: 'num', text: cskh.formatNumber(totals.created_rows) }),
			cskh.el('td', { class: 'num', text: cskh.formatNumber(totals.reused_rows) }),
			cskh.el('td', { class: 'num', text: cskh.formatNumber(totals.skipped_rows) }),
			cskh.el('td', { class: 'num', text: cskh.formatNumber(totals.error_rows) }),
			cskh.el('td', { text: cskh.formatDate(item.committed_at) }),
			cskh.el('td', {}, [renderArtifact(item.batch, item.error_artifact)]),
			cskh.el('td', {}, [cskh.el('div', { class: 'cskh-row-actions' }, actions)]),
		])
	}

	async function retryBatch(batch, button) {
		const alertBox = document.getElementById('import-alert')
		if (!batch) return
		button.disabled = true
		cskh.clear(alertBox)
		try {
			const data = await cskh.post(API_CONFIRM, { batch: batch })
			renderProgress(data)
			let final = await cskh.poll({
				check: function () {
					return cskh.get(API_STATUS, { batch: batch })
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
			try {
				const detail = await cskh.get(API_DETAIL, { batch: batch })
				if (detail) final = detail
			} catch (err) {
				/* Keep `final` from `get_status` if enrichment fails. */
			}
			renderProgress(final)
			if (final.status === 'FAILED') {
				cskh.alert(
					alertBox,
					'error',
					cskh.t('Failed') + ': ' + statusLabel(final.status),
					final.error || batch
				)
			} else if (final.status === 'COMPLETED_WITH_ERRORS') {
				cskh.alert(
					alertBox,
					'warning',
					cskh.t('Completed with errors') + ': ' + statusLabel(final.status),
					cskh.t('Error rows') + ': ' + ((final.totals && final.totals.error_rows) || 0)
				)
			} else {
				cskh.alert(alertBox, 'success', cskh.t('Completed') + ': ' + statusLabel(final.status), batch)
			}
			loadHistory()
		} catch (error) {
			cskh.renderError(alertBox, error)
		} finally {
			button.disabled = false
		}
	}

	/**
	 * Render the link to the authorised error-file download endpoint.
	 *
	 * `crm_cskh.api.import_history.download_error_file` enforces the
	 * `CSKH CRM Admin` business role (returning HTTP 403 FORBIDDEN to all
	 * other roles) while keeping the P0 `/private/files/...` File guard intact.
	 */
	function renderArtifact(batch, artifact) {
		const url = buildErrorDownloadUrl(batch, artifact)
		if (!url) {
			return cskh.el('span', { class: 'cskh-muted', text: '—' })
		}
		return cskh.el('a', {
			class: 'cskh-btn is-small cskh-download',
			href: url,
			rel: 'noopener',
			text: cskh.t('Download error file'),
		})
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
		const hasFile = !!(this.files && this.files.length)
		document.getElementById('import-preview').disabled = !hasFile
	})
	document.getElementById('import-history-refresh').addEventListener('click', function () {
		historyPager.goto(1)
		loadHistory()
	})
	document.getElementById('import-history-status').addEventListener('change', function () {
		historyPager.goto(1)
		loadHistory()
	})
	document.getElementById('import-history-prev').addEventListener('click', function () {
		historyPager.goto(Math.max(1, historyPager.page() - 1))
		loadHistory()
	})
	document.getElementById('import-history-next').addEventListener('click', function () {
		historyPager.goto(historyPager.page() + 1)
		loadHistory()
	})

	loadHistory()
})()
