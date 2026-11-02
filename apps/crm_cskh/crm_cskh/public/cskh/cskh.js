/* Shared CSKH page helpers.

   Same Frappe site and session as CRM (ADR-UI-001 P0, ADR-P1-06 P1):
   there is no second SPA and no second backend.

   Rules enforced here:
     * every read is a GET on the whitelisted P1 API;
     * every mutation is a POST with the Frappe CSRF token;
     * every user-facing string comes from `window.cskhI18n` (the Frappe
       i18n pipeline), never from a hard-coded literal;
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
		return ''
	}

	/** Map an HTTP response body to the stable contract code. */
	function errorCode(body, status) {
		if (!body) return 'HTTP_' + status
		if (body.p1_code) return body.p1_code
		if (body.exc_type) return body.exc_type
		if (body.code) return body.code
		return 'HTTP_' + status
	}

	function serverMessage(body) {
		if (!body) return ''
		if (body._server_messages) {
			try {
				const parsed = JSON.parse(body._server_messages)
				if (Array.isArray(parsed) && parsed.length) return parsed[0]
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
			else if (key === 'html') node.innerHTML = attrs[key]
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
	 * The branch always uses the **raw** code (the frozen contract of
	 * P1 §10.1); only the text shown to the operator is translated.
	 */
	function renderError(container, error) {
		const code = error && error.code ? error.code : 'ERROR'
		const message = error && error.message ? error.message : String(error)
		// The page shows the **Vietnamese** explanation of the situation and
		// keeps the stable code visible, because the code is the contract the
		// frontend branches on (P1 §10.1).
		const explanation = errorText(code)
		const label = explanation ? explanation + ' [' + code + ']' : code
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
			/**
			 * Set the server total and keep the current page inside the
			 * range. The list pages call it after every read, so the pager
			 * never shows a stale "0 / 0".
			 */
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
		const interval = options.interval || 1500
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
	 *
	 * The standard `upload_file` endpoint is not used: the P0 guard makes a
	 * `File` reachable only through the `CRM Lead` it is attached to, and an
	 * import source file is attached to no lead. The P1 endpoint accepts the
	 * file only from the `CSKH CRM Admin` role and always stores it private
	 * (P1 §6.1, ADR-P1-03).
	 */
	async function uploadPrivateFile(file) {
		const base64 = await new Promise(function (resolve, reject) {
			const reader = new FileReader()
			reader.onload = function () {
				const result = String(reader.result || '')
				resolve(result.indexOf(',') >= 0 ? result.split(',')[1] : result)
			}
			reader.onerror = function () {
				reject(new Error('read_failed'))
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
		maskPhone: maskPhone,
		alert: alert,
		renderError: renderError,
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
