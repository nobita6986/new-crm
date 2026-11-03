/* Shared CSKH page helpers.

   Same Frappe site and session as CRM (ADR-UI-001 P0, ADR-P1-06 P1):
   there is no second SPA and no second backend.

   Rules enforced here:
     * every read is a GET on the whitelisted P1 API;
     * every mutation is a POST with the Frappe CSRF token;
     * every user-facing string comes from `window.cskhI18n` (the Frappe
       i18n pipeline), never from a hard-coded literal;
     * every DOM element is built via `textContent` / `createTextNode` (never
       `innerHTML`), so untrusted cell values cannot execute HTML/JS;
     * every failure is mapped to a stable business error code so the page
       can show loading / empty / error / conflict / permission-denied.
   */

window.cskh = (function () {
	'use strict'

	function csrfToken() {
		if (window.frappe && window.frappe.csrf_token) return window.frappe.csrf_token
		const meta = document.querySelector('meta[name="csrf-token"]')
		return meta ? meta.getAttribute('content') : ''
	}

	function t(message, fallback) {
		const catalogue = window.cskhI18n || {}
		if (Object.prototype.hasOwnProperty.call(catalogue, message)) {
			return catalogue[message]
		}
		return fallback === undefined ? message : fallback
	}

	function hasTranslation(message) {
		const catalogue = window.cskhI18n || {}
		return Object.prototype.hasOwnProperty.call(catalogue, message)
	}

	function currentUser() {
		return window.cskhUser || ''
	}

	function capabilities() {
		return window.cskhCapabilities || {}
	}

	/** The operator-facing text of a stable error code (P1 §10.1). */
	function errorText(code) {
		const catalogue = window.cskhErrorTexts || {}
		if (Object.prototype.hasOwnProperty.call(catalogue, code)) {
			return catalogue[code]
		}
		if (code === 'CSRF_ERROR') {
			return t('Invalid or expired session/CSRF token. Reload the page and try again.')
		}
		return ''
	}

	/** Map an HTTP response body to the stable contract code. */
	function errorCode(body, status) {
		if (body && body.p1_code) return body.p1_code
		if (body && body.exc_type === 'CSRFTokenError') return 'CSRF_ERROR'
		if ((body && body.exc_type === 'PermissionError') || status === 403) return 'FORBIDDEN'
		if ((body && body.exc_type === 'DoesNotExistError') || status === 404) return 'NOT_FOUND'
		if (body && body.exc_type === 'ValidationError') return 'INVALID_REQUEST'
		if (body && body.exc_type) return body.exc_type
		if (body && body.code) return body.code
		return 'HTTP_' + status
	}

	function serverMessage(body) {
		if (!body) return ''
		if (body._server_messages) {
			try {
				const parsed = JSON.parse(body._server_messages)
				if (Array.isArray(parsed) && parsed.length) {
					const first = parsed[0]
					if (typeof first === 'string' && first.charAt(0) === '{') {
						try {
							const inner = JSON.parse(first)
							if (inner && inner.message) return String(inner.message)
						} catch (err2) {
							/* fall through */
						}
					}
					return String(first)
				}
			} catch (err) {
				/* fall through */
			}
		}
		if (body.exception && body.exception.indexOf('crm_cskh') === 0) return ''
		return body.message || body.exc_type || ''
	}

	/**
	 * Call a Frappe whitelisted method.
	 *
	 * GET is used for reads, POST (with CSRF) for every mutation. The caller
	 * never decides the verb from the data: `cskh.get` and `cskh.post` are
	 * the only two entry points.
	 */
	async function request(method, args, verb) {
		const payload = Object.assign({}, args || {})
		const url = '/api/method/' + method
		let response

		if (verb === 'GET') {
			const query = new URLSearchParams()
			Object.keys(payload).forEach(function (key) {
				const value = payload[key]
				if (value === undefined || value === null || value === '') return
				query.append(key, typeof value === 'object' ? JSON.stringify(value) : value)
			})
			const qs = query.toString()
			response = await fetch(url + (qs ? '?' + qs : ''), {
				method: 'GET',
				credentials: 'same-origin',
				headers: { Accept: 'application/json' },
			})
		} else {
			response = await fetch(url, {
				method: 'POST',
				credentials: 'same-origin',
				headers: {
					'Content-Type': 'application/json',
					Accept: 'application/json',
					'X-Frappe-CSRF-Token': csrfToken(),
				},
				body: JSON.stringify(payload),
			})
		}

		let body = null
		try {
			body = await response.json()
		} catch (err) {
			body = null
		}

		if (!response.ok) {
			const error = new Error(serverMessage(body) || 'HTTP ' + response.status)
			error.status = response.status
			error.code = errorCode(body, response.status)
			error.body = body
			throw error
		}
		return body ? body.message : null
	}

	function get(method, args) {
		return request(method, args, 'GET')
	}

	function post(method, args) {
		return request(method, args, 'POST')
	}

	function el(tag, attrs, children) {
		const node = document.createElement(tag)
		Object.keys(attrs || {}).forEach(function (key) {
			if (key === 'class') node.className = attrs[key]
			else if (key === 'text') node.textContent = attrs[key]
			else if (key.indexOf('on') === 0 && typeof attrs[key] === 'function') {
				node.addEventListener(key.slice(2).toLowerCase(), attrs[key])
			} else if (attrs[key] !== undefined && attrs[key] !== null) {
				node.setAttribute(key, attrs[key])
			}
		})
		;(children || []).forEach(function (child) {
			if (child === null || child === undefined) return
			node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child)
		})
		return node
	}

	function clear(node) {
		while (node && node.firstChild) node.removeChild(node.firstChild)
	}

	/** The visual state of a work item / import row. */
	function stateBadge(kind) {
		const map = {
			assigned: 'is-assigned',
			queued: 'is-queued',
			revoked: 'is-conflict',
			closed: 'is-invalid',
			valid: 'is-assigned',
			duplicate_in_file: 'is-queued',
			duplicate_in_crm: 'is-queued',
			conflict_active_campaign: 'is-conflict',
			invalid: 'is-invalid',
			conflict: 'is-conflict',
			excluded: 'is-invalid',
			eligible: 'is-assigned',
		}
		return 'cskh-state ' + (map[kind] || '')
	}

	function formatDate(value) {
		if (!value) return '—'
		const date = new Date(value)
		if (isNaN(date.getTime())) return String(value)
		return new Intl.DateTimeFormat('vi-VN', {
			dateStyle: 'short',
			timeStyle: 'short',
			timeZone: 'Asia/Ho_Chi_Minh',
		}).format(date)
	}

	function formatNumber(value) {
		if (value === undefined || value === null || value === '') return '—'
		return new Intl.NumberFormat('vi-VN').format(value)
	}

	/**
	 * Mask a phone number for the evidence screenshots: only the last four
	 * digits are shown, so no real phone number ever reaches an image or a
	 * log file (P1 delivery rule).
	 */
	function maskPhone(value) {
		if (!value) return ''
		const digits = String(value).replace(/\D/g, '')
		if (digits.length <= 4) return '*'.repeat(digits.length)
		return '*'.repeat(digits.length - 4) + digits.slice(-4)
	}

	/**
	 * The label of a capacity value.
	 *
	 * `capacity_limit = 0` means **no limit** (P1 §7.2, ADR-P1-04 §2.6). The
	 * contract of T1B sends `0` for the limit of an unlimited rule and `null`
	 * for its remaining capacity, so both are rendered with the dedicated
	 * "Không giới hạn" label instead of a dash.
	 */
	function capacityLabel(value) {
		if (value === undefined) return '—'
		if (value === null) return t('Unlimited')
		if (Number(value) === 0) return t('Unlimited')
		return formatNumber(value)
	}

	/**
	 * The label of a scope.
	 *
	 * The backend returns the scope as a stable code (`system`, `team`,
	 * `self`, `all`); the page shows the Vietnamese label of the code so the
	 * screen never mixes an English identifier into a Vietnamese sentence
	 * (P1 §9.6).
	 */
	function scopeLabel(value) {
		const map = {
			system: 'Whole installation',
			all: 'Whole installation',
			team: 'Team scope',
			self: 'Only my own data',
		}
		return map[value] ? t(map[value]) : value || '—'
	}

	/**
	 * The label of a campaign state.
	 *
	 * The four states of a campaign are shown in Vietnamese; the raw code is
	 * never rendered as an English word (P1 §9.6).
	 */
	function campaignStatusLabel(value) {
		const map = {
			Draft: 'Campaign draft',
			Active: 'Campaign active',
			Paused: 'Campaign paused',
			Closed: 'Campaign closed',
		}
		return map[value] ? t(map[value]) : value || '—'
	}

	/**
	 * A guard against **interleaved reads**.
	 */
	function sequencer() {
		let token = 0
		return {
			next: function () {
				token += 1
				return token
			},
			current: function () {
				return token
			},
		}
	}

	/** Render an alert box (error / warning / success / empty). */
	function alert(container, kind, message, extra) {
		clear(container)
		if (!message) return
		const children = [cskh.el('span', { text: message })]
		if (extra) children.push(cskh.el('span', { class: 'cskh-muted', text: ' · ' + extra }))
		container.appendChild(cskh.el('div', { class: 'cskh-alert is-' + kind }, children))
	}

	/**
	 * Turn a business error into the right page state.
	 * `FORBIDDEN` becomes the permission-denied state, the conflict codes
	 * become the conflict state, everything else the generic error state.
	 *
	 * Untranslated raw technical exceptions are never leaked as English text:
	 * the page shows the translated Vietnamese message or the Vietnamese
	 * explanation of the stable contract code, plus `[CODE]`.
	 */
	function renderError(container, error) {
		const code = error && error.code ? error.code : 'ERROR'
		const rawMessage = error && error.message ? String(error.message) : ''
		const translated = rawMessage && hasTranslation(rawMessage) ? t(rawMessage) : ''
		const explanation = errorText(code)
		const message = translated || explanation || t('An error occurred while processing the request.')
		const label =
			translated && explanation && translated !== explanation
				? explanation + ' [' + code + ']'
				: '[' + code + ']'
		if (code === 'FORBIDDEN') {
			alert(container, 'warning', message, label)
			return code
		}
		if (
			code === 'CONFLICT' ||
			code === 'RULE_CHANGED' ||
			code === 'PREVIEW_EXPIRED' ||
			code === 'ALREADY_ASSIGNED' ||
			code === 'CAPACITY_FULL' ||
			code === 'CAPACITY_EXCEEDED' ||
			code === 'NO_ELIGIBLE_LEADS' ||
			code === 'PARTIAL_SUCCESS'
		) {
			alert(container, 'warning', message, label)
			return code
		}
		alert(container, 'error', message, label)
		return code
	}

	/** Build a pager and keep it in sync with the server total. */
	function pager(options) {
		const state = { page: 1, pageSize: options.pageSize || 20 }
		let total = options.total || 0
		const pages = () => Math.max(1, Math.ceil(total / state.pageSize))
		return {
			state: state,
			page: function () {
				return state.page
			},
			total: function () {
				return total
			},
			setTotal: function (value) {
				total = Number(value || 0)
				const last = pages()
				if (state.page > last) state.page = last
			},
			render: function () {
				const range = options.range
				const prev = options.prev
				const next = options.next
				if (range) {
					const from = total === 0 ? 0 : (state.page - 1) * state.pageSize + 1
					const to = Math.min(state.page * state.pageSize, total)
					range.textContent = from + '–' + to + ' / ' + formatNumber(total)
				}
				if (prev) prev.disabled = state.page <= 1
				if (next) next.disabled = state.page >= pages()
			},
			goto: function (page) {
				state.page = page
				return state.page
			},
			pages: pages,
		}
	}

	/** Poll a GET endpoint until `done(state)` returns true. */
	async function poll(options) {
		const interval = options.interval || 1200
		const timeout = options.timeout || 120000
		const started = Date.now()
		while (Date.now() - started < timeout) {
			const state = await options.check()
			if (options.done(state)) return state
			await new Promise(function (resolve) {
				setTimeout(resolve, interval)
			})
		}
		throw new Error('Timeout')
	}

	/**
	 * Upload a file as a **private** Frappe file for the import pipeline.
	 */
	async function uploadPrivateFile(file) {
		const base64 = await new Promise(function (resolve, reject) {
			const reader = new FileReader()
			reader.onload = function () {
				const result = String(reader.result || '')
				resolve(result.indexOf(',') >= 0 ? result.split(',')[1] : result)
			}
			reader.onerror = function () {
				reject(new Error('The upload failed.'))
			}
			reader.readAsDataURL(file)
		})

		const response = await fetch('/api/method/crm_cskh.api.import_files.upload_import_file', {
			method: 'POST',
			credentials: 'same-origin',
			headers: {
				'Content-Type': 'application/json',
				Accept: 'application/json',
				'X-Frappe-CSRF-Token': csrfToken(),
			},
			body: JSON.stringify({ file_name: file.name, content: base64 }),
		})
		let body = null
		try {
			body = await response.json()
		} catch (err) {
			body = null
		}
		if (!response.ok) {
			const error = new Error(serverMessage(body) || 'HTTP ' + response.status)
			error.status = response.status
			error.code = errorCode(body, response.status)
			throw error
		}
		return body && body.message ? body.message : null
	}

	return {
		get: get,
		post: post,
		call: function (method, args) {
			return get(method, args)
		},
		el: el,
		clear: clear,
		stateBadge: stateBadge,
		formatDate: formatDate,
		formatNumber: formatNumber,
		capacityLabel: capacityLabel,
		scopeLabel: scopeLabel,
		campaignStatusLabel: campaignStatusLabel,
		maskPhone: maskPhone,
		alert: alert,
		renderError: renderError,
		sequencer: sequencer,
		pager: pager,
		poll: poll,
		uploadPrivateFile: uploadPrivateFile,
		t: t,
		errorText: errorText,
		currentUser: currentUser,
		capabilities: capabilities,
		csrfToken: csrfToken,
	}
})()
