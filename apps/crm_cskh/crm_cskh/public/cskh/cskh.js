/* Shared CSKH page helpers (P1 + P2).

   Same Frappe site and session as CRM (ADR-UI-001 P0, ADR-P1-06 P1, ADR-P2-01 P2):
   there is no second SPA and no second backend.

   Rules enforced here:
     * every read is a GET on the whitelisted P1/P2 API;
     * every mutation is a POST with the Frappe CSRF token;
     * every user-facing string comes from `window.cskhI18n` (the Frappe
       i18n pipeline), never from a hard-coded literal;
     * every DOM element is built via `textContent` / `createTextNode` (never
       `innerHTML`), so untrusted cell/note values cannot execute HTML/JS;
     * every timestamp and day boundary (23:59 / 00:00) is parsed and formatted
       in the authoritative Frappe site/user timezone (`window.cskhTimezone`),
       never in the arbitrary host OS timezone of the browser;
     * every failure is mapped to a stable business error code so the page
       can show loading / empty / error / conflict / permission-denied.
   */

window.cskh = (function () {
	'use strict'

	const bootPerfMs =
		typeof window.performance !== 'undefined' && window.performance.now
			? window.performance.now()
			: 0
	const bootWallMs = Date.now()

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

	function uuid() {
		if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID()
		return 'cskh-' + Date.now() + '-' + Math.floor(Math.random() * 1e9)
	}

	/* ------------------------------------------------------------------ */
	/* Authoritative Site/User Timezone Engine (P2 §12.5, 23:59 / 00:00)  */
	/* ------------------------------------------------------------------ */

	function timezoneInfo() {
		const raw = window.cskhTimezone || {}
		const site = raw.site || 'Asia/Ho_Chi_Minh'
		const effective = raw.effective || raw.user || site || 'Asia/Ho_Chi_Minh'
		return {
			site: site,
			user: raw.user || '',
			effective: effective,
			serverNowIso: raw.server_now_iso || '',
		}
	}

	function effectiveTimeZone() {
		return timezoneInfo().effective
	}

	function siteTimeZone() {
		return timezoneInfo().site
	}

	function partsAtEpochInTz(epochMs, tz) {
		const zone = tz || effectiveTimeZone()
		let fmt
		try {
			fmt = new Intl.DateTimeFormat('en-CA', {
				timeZone: zone,
				year: 'numeric',
				month: '2-digit',
				day: '2-digit',
				hour: '2-digit',
				minute: '2-digit',
				second: '2-digit',
				hourCycle: 'h23',
			})
		} catch (_err) {
			fmt = new Intl.DateTimeFormat('en-CA', {
				timeZone: 'Asia/Ho_Chi_Minh',
				year: 'numeric',
				month: '2-digit',
				day: '2-digit',
				hour: '2-digit',
				minute: '2-digit',
				second: '2-digit',
				hourCycle: 'h23',
			})
		}
		const map = {}
		fmt.formatToParts(new Date(epochMs)).forEach(function (part) {
			if (part.type !== 'literal') map[part.type] = part.value
		})
		const year = Number(map.year)
		const month = Number(map.month)
		const day = Number(map.day)
		const hour = Number(map.hour === '24' ? '0' : map.hour)
		const minute = Number(map.minute)
		const second = Number(map.second)
		const asUtc = Date.UTC(year, month - 1, day, hour, minute, second)
		const offsetMin = Math.round((asUtc - epochMs) / 60000)
		const sign = offsetMin >= 0 ? '+' : '-'
		const absMin = Math.abs(offsetMin)
		const offH = String(Math.floor(absMin / 60)).padStart(2, '0')
		const offM = String(absMin % 60).padStart(2, '0')
		const pad = function (n) {
			return String(n).padStart(2, '0')
		}
		return {
			year: year,
			month: month,
			day: day,
			hour: hour,
			minute: minute,
			second: second,
			dateKey: year + '-' + pad(month) + '-' + pad(day),
			timeKey: pad(hour) + ':' + pad(minute),
			offsetIso: sign + offH + ':' + offM,
		}
	}

	/**
	 * Convert a wall-clock tuple `(year, month, day, hour, minute, second)` in `tz`
	 * into exact UTC epoch milliseconds without consulting the browser OS timezone.
	 */
	function tzWallClockToEpochMs(year, month, day, hour, minute, second, tz) {
		const zone = tz || effectiveTimeZone()
		const targetUtc = Date.UTC(year, month - 1, day, hour || 0, minute || 0, second || 0)
		const p1 = partsAtEpochInTz(targetUtc, zone)
		const p1Utc = Date.UTC(p1.year, p1.month - 1, p1.day, p1.hour, p1.minute, p1.second)
		const refined = targetUtc - (p1Utc - targetUtc)
		const p2 = partsAtEpochInTz(refined, zone)
		const p2Utc = Date.UTC(p2.year, p2.month - 1, p2.day, p2.hour, p2.minute, p2.second)
		return refined - (p2Utc - targetUtc)
	}

	/**
	 * Parse an ISO-8601 or Frappe naive datetime string into a `Date` instant.
	 * Naive strings (`YYYY-MM-DD HH:MM:SS`) are interpreted in `siteTimeZone()`,
	 * never in the browser OS local timezone.
	 */
	function parseServerDate(value, defaultTz) {
		if (!value && value !== 0) return null
		if (value instanceof Date) return isNaN(value.getTime()) ? null : value
		if (typeof value === 'number') {
			const d = new Date(value)
			return isNaN(d.getTime()) ? null : d
		}
		const text = String(value).trim()
		if (!text) return null

		// Explicit ISO-8601 offset (`Z`, `+07:00`, `-0500`)
		if (/(?:Z|[+-]\d{2}:?\d{2})$/i.test(text)) {
			const normalized = text.replace(' ', 'T')
			const d = new Date(normalized)
			return isNaN(d.getTime()) ? null : d
		}

		// Date-only `YYYY-MM-DD` -> 00:00:00 in target timezone
		const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text)
		if (dateOnly) {
			const ms = tzWallClockToEpochMs(
				Number(dateOnly[1]),
				Number(dateOnly[2]),
				Number(dateOnly[3]),
				0,
				0,
				0,
				defaultTz || effectiveTimeZone()
			)
			return new Date(ms)
		}

		// Naive Frappe datetime `YYYY-MM-DD[ T]HH:MM(:SS(.ffffff))?` -> site timezone
		const naive =
			/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?$/.exec(text)
		if (naive) {
			const ms = tzWallClockToEpochMs(
				Number(naive[1]),
				Number(naive[2]),
				Number(naive[3]),
				Number(naive[4]),
				Number(naive[5]),
				Number(naive[6] || 0),
				defaultTz || siteTimeZone()
			)
			return new Date(ms)
		}

		const fallback = new Date(text)
		return isNaN(fallback.getTime()) ? null : fallback
	}

	/** Current server-anchored Date instant (immune to skewed client OS clock). */
	function serverNow() {
		const info = timezoneInfo()
		const base = info.serverNowIso ? parseServerDate(info.serverNowIso) : null
		if (!base) return new Date()
		const elapsed =
			typeof window.performance !== 'undefined' && window.performance.now
				? Math.max(0, window.performance.now() - bootPerfMs)
				: Math.max(0, Date.now() - bootWallMs)
		return new Date(base.getTime() + elapsed)
	}

	function formatDate(value) {
		if (!value) return '—'
		const date = parseServerDate(value)
		if (!date) return String(value)
		return new Intl.DateTimeFormat('vi-VN', {
			year: 'numeric',
			month: '2-digit',
			day: '2-digit',
			hour: '2-digit',
			minute: '2-digit',
			hour12: false,
			timeZone: effectiveTimeZone(),
		}).format(date)
	}

	function formatDateOnly(value) {
		if (!value) return '—'
		const text = String(value).trim()
		if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
			const parts = text.split('-')
			return parts[2] + '/' + parts[1] + '/' + parts[0]
		}
		const date = parseServerDate(value)
		if (!date) return text
		return new Intl.DateTimeFormat('vi-VN', {
			year: 'numeric',
			month: '2-digit',
			day: '2-digit',
			timeZone: effectiveTimeZone(),
		}).format(date)
	}

	function dateKeyInTz(value, tz) {
		const text = String(value || '').trim()
		if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text
		const date = parseServerDate(value)
		if (!date) return ''
		return partsAtEpochInTz(date.getTime(), tz || effectiveTimeZone()).dateKey
	}

	/** Format an instant as `YYYY-MM-DDTHH:mm` in the effective site/user timezone. */
	function toDatetimeLocalValue(value, tz) {
		const date = value ? parseServerDate(value) : serverNow()
		if (!date) return ''
		const p = partsAtEpochInTz(date.getTime(), tz || effectiveTimeZone())
		return p.dateKey + 'T' + p.timeKey
	}

	/**
	 * Convert a `<input type="datetime-local">` value (`YYYY-MM-DDTHH:mm`) in the
	 * effective site/user timezone into an explicit ISO-8601 string with offset.
	 */
	function datetimeLocalToIso(localValue, tz) {
		const text = String(localValue || '').trim()
		if (!text) return ''
		if (/(?:Z|[+-]\d{2}:?\d{2})$/i.test(text)) return text
		const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$/.exec(text)
		if (!m) return text
		const zone = tz || effectiveTimeZone()
		const sec = Number(m[6] || 0)
		const ms = tzWallClockToEpochMs(
			Number(m[1]),
			Number(m[2]),
			Number(m[3]),
			Number(m[4]),
			Number(m[5]),
			sec,
			zone
		)
		const p = partsAtEpochInTz(ms, zone)
		const pad = function (n) {
			return String(n).padStart(2, '0')
		}
		return p.dateKey + 'T' + p.timeKey + ':' + pad(p.second) + p.offsetIso
	}

	/**
	 * Convert a `<input type="datetime-local">` value (`YYYY-MM-DDTHH:mm`) from the
	 * effective user/site timezone into Frappe's canonical site-timezone naive string
	 * (`YYYY-MM-DD HH:mm:ss`), avoiding naive-vs-aware comparison errors on backend.
	 */
	function datetimeLocalToServer(localValue, tz) {
		const text = String(localValue || '').trim()
		if (!text) return ''
		let ms = null
		if (/(?:Z|[+-]\d{2}:?\d{2})$/i.test(text)) {
			const parsed = parseServerDate(text)
			if (!parsed) return text
			ms = parsed.getTime()
		} else {
			const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$/.exec(text)
			if (!m) return text
			const zone = tz || effectiveTimeZone()
			const sec = Number(m[6] || 0)
			ms = tzWallClockToEpochMs(
				Number(m[1]),
				Number(m[2]),
				Number(m[3]),
				Number(m[4]),
				Number(m[5]),
				sec,
				zone
			)
		}
		const siteParts = partsAtEpochInTz(ms, siteTimeZone())
		const pad = function (n) {
			return String(n).padStart(2, '0')
		}
		return siteParts.dateKey + ' ' + siteParts.timeKey + ':' + pad(siteParts.second)
	}

	/**
	 * Classify a callback due timestamp/date strictly in the effective timezone,
	 * preserving 23:59 vs 00:00 midnight boundaries.
	 */
	function classifyDueAt(dueValue, taskStatus, refNow, tz) {
		const st = String(taskStatus || '').trim().toLowerCase()
		if (st === 'done' || st === 'closed' || st === 'completed') return 'done'
		if (st === 'cancelled' || st === 'canceled') return 'canceled'
		if (!dueValue) return ''

		const zone = tz || effectiveTimeZone()
		const nowDate = refNow ? parseServerDate(refNow) : serverNow()
		const nowMs = nowDate ? nowDate.getTime() : Date.now()
		const todayKey = partsAtEpochInTz(nowMs, zone).dateKey

		const rawText = String(dueValue).trim()
		if (/^\d{4}-\d{2}-\d{2}$/.test(rawText)) {
			if (rawText < todayKey) return 'overdue'
			if (rawText === todayKey) return 'due_today'
			return 'upcoming'
		}

		const dueDate = parseServerDate(dueValue)
		if (!dueDate) return ''
		if (dueDate.getTime() < nowMs) return 'overdue'
		const dueKey = partsAtEpochInTz(dueDate.getTime(), zone).dateKey
		if (dueKey <= todayKey) return 'due_today'
		return 'upcoming'
	}

	function pad2(n) {
		return String(n).padStart(2, '0')
	}

	function formatCivilDate(year, month, day) {
		return year + '-' + pad2(month) + '-' + pad2(day)
	}

	function addCivilDays(year, month, day, deltaDays) {
		const dt = new Date(Date.UTC(year, month - 1, day + deltaDays, 12, 0, 0))
		return {
			year: dt.getUTCFullYear(),
			month: dt.getUTCMonth() + 1,
			day: dt.getUTCDate(),
		}
	}

	function isoWeekMonday(isoYear, isoWeek) {
		const jan4 = new Date(Date.UTC(isoYear, 0, 4, 12, 0, 0))
		const jan4Dow = jan4.getUTCDay() || 7
		const week1Mon = new Date(Date.UTC(isoYear, 0, 4 - (jan4Dow - 1), 12, 0, 0))
		const target = new Date(week1Mon.getTime() + (isoWeek - 1) * 7 * 86400000)
		return {
			year: target.getUTCFullYear(),
			month: target.getUTCMonth() + 1,
			day: target.getUTCDate(),
		}
	}

	function civilDateToIsoWeekKey(year, month, day) {
		const dt = new Date(Date.UTC(year, month - 1, day, 12, 0, 0))
		const dow = dt.getUTCDay() || 7
		dt.setUTCDate(dt.getUTCDate() + 4 - dow)
		const isoYear = dt.getUTCFullYear()
		const yearStart = new Date(Date.UTC(isoYear, 0, 1, 12, 0, 0))
		const weekNo = Math.ceil(((dt.getTime() - yearStart.getTime()) / 86400000 + 1) / 7)
		return isoYear + '-W' + pad2(weekNo)
	}

	function normalizePeriodType(rawType) {
		const s = String(rawType || 'monthly').trim().toLowerCase()
		const aliases = {
			day: 'daily',
			daily: 'daily',
			week: 'weekly',
			weekly: 'weekly',
			month: 'monthly',
			monthly: 'monthly',
			quarter: 'quarterly',
			quarterly: 'quarterly',
			year: 'yearly',
			yearly: 'yearly',
			custom: 'custom',
		}
		return aliases[s] || 'monthly'
	}

	/**
	 * Compute authoritative half-open `[from, to_exclusive)` period boundaries
	 * and canonical `period_key` in `tz` (default `siteTimeZone()`, `Asia/Ho_Chi_Minh`
	 * per `docs/p3/report-api-contract.md` §1.1), independent of browser OS timezone.
	 *
	 * Supported `periodType`: `daily`, `weekly`, `monthly`, `quarterly`, `yearly`, `custom`
	 * (plus aliases `day`, `week`, `month`, `quarter`, `year`).
	 */
	function computePeriodBounds(periodType, anchorValue, customToValue, tz) {
		const zone = tz || siteTimeZone() || 'Asia/Ho_Chi_Minh'
		const nowParts = partsAtEpochInTz(serverNow().getTime(), zone)
		const pType = normalizePeriodType(periodType)
		const rawAnchor = String(anchorValue || '').trim()

		let startY = nowParts.year
		let startM = nowParts.month
		let startD = 1
		let endExcl = null
		let periodKey = ''

		if (pType === 'daily') {
			const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(rawAnchor)
			startY = m ? Number(m[1]) : nowParts.year
			startM = m ? Number(m[2]) : nowParts.month
			startD = m ? Number(m[3]) : nowParts.day
			endExcl = addCivilDays(startY, startM, startD, 1)
			periodKey = formatCivilDate(startY, startM, startD)
		} else if (pType === 'weekly') {
			const wm = /^(\d{4})-W(\d{2})$/i.exec(rawAnchor)
			if (wm) {
				const mon = isoWeekMonday(Number(wm[1]), Number(wm[2]))
				startY = mon.year
				startM = mon.month
				startD = mon.day
				periodKey = wm[1] + '-W' + wm[2]
			} else {
				const dm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(rawAnchor)
				const cy = dm ? Number(dm[1]) : nowParts.year
				const cm = dm ? Number(dm[2]) : nowParts.month
				const cd = dm ? Number(dm[3]) : nowParts.day
				const probe = new Date(Date.UTC(cy, cm - 1, cd, 12, 0, 0))
				const dow = probe.getUTCDay() || 7
				const mon = addCivilDays(cy, cm, cd, -(dow - 1))
				startY = mon.year
				startM = mon.month
				startD = mon.day
				periodKey = civilDateToIsoWeekKey(startY, startM, startD)
			}
			endExcl = addCivilDays(startY, startM, startD, 7)
		} else if (pType === 'quarterly') {
			const qm = /^(\d{4})-Q([1-4])$/i.exec(rawAnchor)
			let qNum = Math.floor((nowParts.month - 1) / 3) + 1
			if (qm) {
				startY = Number(qm[1])
				qNum = Number(qm[2])
			} else {
				const dm = /^(\d{4})-(\d{2})/.exec(rawAnchor)
				if (dm) {
					startY = Number(dm[1])
					qNum = Math.floor((Number(dm[2]) - 1) / 3) + 1
				}
			}
			startM = (qNum - 1) * 3 + 1
			startD = 1
			const nextQM = startM + 3
			endExcl =
				nextQM > 12
					? { year: startY + 1, month: nextQM - 12, day: 1 }
					: { year: startY, month: nextQM, day: 1 }
			periodKey = startY + '-Q' + qNum
		} else if (pType === 'yearly') {
			const ym = /^(\d{4})/.exec(rawAnchor)
			startY = ym ? Number(ym[1]) : nowParts.year
			startM = 1
			startD = 1
			endExcl = { year: startY + 1, month: 1, day: 1 }
			periodKey = String(startY)
		} else if (pType === 'custom') {
			const fm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(rawAnchor)
			const tm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(customToValue || '').trim())
			startY = fm ? Number(fm[1]) : nowParts.year
			startM = fm ? Number(fm[2]) : nowParts.month
			startD = fm ? Number(fm[3]) : 1
			const incY = tm ? Number(tm[1]) : nowParts.year
			const incM = tm ? Number(tm[2]) : nowParts.month
			const incD = tm ? Number(tm[3]) : nowParts.day
			endExcl = addCivilDays(incY, incM, incD, 1)
			periodKey = ''
		} else {
			// Default `monthly`
			const mm = /^(\d{4})-(\d{2})/.exec(rawAnchor)
			startY = mm ? Number(mm[1]) : nowParts.year
			startM = mm ? Number(mm[2]) : nowParts.month
			startD = 1
			endExcl =
				startM >= 12
					? { year: startY + 1, month: 1, day: 1 }
					: { year: startY, month: startM + 1, day: 1 }
			periodKey = startY + '-' + pad2(startM)
		}

		const fromEpochMs = tzWallClockToEpochMs(startY, startM, startD, 0, 0, 0, zone)
		const toExclEpochMs = tzWallClockToEpochMs(
			endExcl.year,
			endExcl.month,
			endExcl.day,
			0,
			0,
			0,
			zone
		)
		const incEnd = addCivilDays(endExcl.year, endExcl.month, endExcl.day, -1)
		const fromParts = partsAtEpochInTz(fromEpochMs, zone)
		const toExclParts = partsAtEpochInTz(toExclEpochMs, zone)
		const fromDate = formatCivilDate(startY, startM, startD)
		const toDateExclusive = formatCivilDate(endExcl.year, endExcl.month, endExcl.day)
		const toDateInclusive = formatCivilDate(incEnd.year, incEnd.month, incEnd.day)

		return {
			periodType: pType,
			periodKey: periodKey,
			timeZone: zone,
			fromDate: fromDate,
			toDateInclusive: toDateInclusive,
			toDateExclusive: toDateExclusive,
			fromServer: fromParts.dateKey + ' 00:00:00',
			toExclusiveServer: toExclParts.dateKey + ' 00:00:00',
			fromIso: fromParts.dateKey + 'T00:00:00' + fromParts.offsetIso,
			toExclusiveIso: toExclParts.dateKey + 'T00:00:00' + toExclParts.offsetIso,
			fromEpochMs: fromEpochMs,
			toExclusiveEpochMs: toExclEpochMs,
			label: '[' + fromParts.dateKey + ' 00:00, ' + toExclParts.dateKey + ' 00:00) ' + zone,
		}
	}

	/* ------------------------------------------------------------------ */
	/* Error codes, HTTP transport & Safe DOM                             */
	/* ------------------------------------------------------------------ */

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

	function errorCode(body, status) {
		if (body && body.p3_code) return body.p3_code
		if (body && body.p2_code) return body.p2_code
		if (body && body.p1_code) return body.p1_code
		if (body && body.error_code) return body.error_code
		if (body && body.code && typeof body.code === 'string') return body.code
		if (body && body.exc_type === 'CSRFTokenError') return 'CSRF_ERROR'
		if ((body && body.exc_type === 'PermissionError') || status === 403) return 'FORBIDDEN'
		if ((body && body.exc_type === 'DoesNotExistError') || status === 404) return 'NOT_FOUND'
		if (body && body.exc_type === 'ValidationError') return 'INVALID_REQUEST'
		if (body && body.exc_type) return body.exc_type
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
						} catch (_err2) {
							/* fall through */
						}
					}
					return String(first)
				}
			} catch (_err) {
				/* fall through */
			}
		}
		if (body.exception && body.exception.indexOf('crm_cskh') === 0) return ''
		return body.message || body.exc_type || ''
	}

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
		} catch (_err) {
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
			else if (key === 'checked') node.checked = !!attrs[key]
			else if (key === 'disabled') node.disabled = !!attrs[key]
			else if (key === 'hidden') node.hidden = !!attrs[key]
			else if (key === 'value' && (tag === 'input' || tag === 'textarea' || tag === 'select')) {
				node.value = attrs[key]
			} else if (key.indexOf('on') === 0 && typeof attrs[key] === 'function') {
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

	function stateBadge(kind) {
		const map = {
			assigned: 'is-assigned',
			in_progress: 'is-info',
			on_hold: 'is-queued',
			queued: 'is-queued',
			revoked: 'is-conflict',
			closed: 'is-invalid',
			completed: 'is-assigned',
			done: 'is-invalid',
			canceled: 'is-invalid',
			connected: 'is-assigned',
			not_connected: 'is-queued',
			attempted_not_connected: 'is-queued',
			not_contacted: 'is-info',
			due_today: 'is-queued',
			followup_due: 'is-queued',
			upcoming: 'is-info',
			overdue: 'is-conflict',
			followup_overdue: 'is-conflict',
			do_not_contact: 'is-conflict',
			dnc: 'is-conflict',
			self_reported: 'is-queued',
			verified: 'is-assigned',
			corrected: 'is-info',
			void: 'is-invalid',
			pending: 'is-queued',
			rejected: 'is-conflict',
			valid: 'is-assigned',
			duplicate_in_file: 'is-queued',
			duplicate_in_crm: 'is-queued',
			conflict_active_campaign: 'is-conflict',
			invalid: 'is-invalid',
			conflict: 'is-conflict',
			excluded: 'is-invalid',
			eligible: 'is-assigned',
			// P3 freshness / confidence & SLA obligation states
			fresh: 'is-assigned',
			ok: 'is-assigned',
			final: 'is-assigned',
			finalized: 'is-assigned',
			calculating: 'is-info',
			stale_recomputing: 'is-queued',
			empty_not_computed: 'is-invalid',
			provisional: 'is-queued',
			stale: 'is-queued',
			restated: 'is-info',
			insufficient_data: 'is-invalid',
			disabled: 'is-invalid',
			na: 'is-invalid',
			failed: 'is-conflict',
			open: 'is-info',
			fulfilled_on_time: 'is-assigned',
			fulfilled_late: 'is-queued',
			breached_open: 'is-conflict',
			breached_handoff: 'is-conflict',
			handoff_closed: 'is-invalid',
			waived: 'is-info',
			canceled_valid: 'is-invalid',
			canceled_validly: 'is-invalid',
			superseded: 'is-invalid',
			escalated: 'is-conflict',
			needs_review: 'is-queued',
			disputed: 'is-conflict',
			historical_attribution_unknown: 'is-queued',
			ready: 'is-assigned',
			running: 'is-info',
			expired: 'is-invalid',
		}
		return 'cskh-state ' + (map[kind] || '')
	}

	function workItemStateLabel(value) {
		const map = {
			assigned: t('Newly assigned'),
			in_progress: t('In progress'),
			on_hold: t('On hold'),
			queued: t('Queued'),
			revoked: t('Revoked'),
			closed: t('Completed care'),
			not_contacted: t('Not contacted'),
			attempted: t('Attempted'),
			attempted_not_connected: t('Attempted, not connected'),
			connected: t('Connected'),
			followup_due: t('Due today'),
			followup_overdue: t('Overdue'),
			do_not_contact: t('Do not contact'),
		}
		return map[value] || value || '—'
	}

	function dueStateLabel(value) {
		const map = {
			due_today: t('Due today'),
			upcoming: t('Upcoming'),
			overdue: t('Overdue'),
			done: t('Done'),
			canceled: t('Canceled'),
			open: t('Open'),
		}
		return map[value] || value || '—'
	}

	function channelLabel(value) {
		const map = {
			phone_call: t('Phone call'),
			call: t('Phone call'),
			zalo: t('Zalo channel'),
			sms: t('SMS message'),
			facebook: t('Facebook channel'),
			other: t('Other channel'),
		}
		return map[value] || value || '—'
	}

	function outcomeLabel(code, snapshotLabel) {
		if (snapshotLabel && hasTranslation(snapshotLabel)) return t(snapshotLabel)
		if (snapshotLabel && !/^[a-z0-9_]+$/.test(snapshotLabel)) return String(snapshotLabel)
		const map = {
			connected_interested: t('Interested (call)'),
			connected_needs_followup: t('Needs follow-up'),
			interested: t('Interested'),
			connected_not_interested: t('Not interested (call)'),
			not_interested: t('Not interested'),
			connected_callback_requested: t('Callback requested'),
			callback_requested: t('Callback requested'),
			connected_converted: t('Converted / closed'),
			converted: t('Converted / closed'),
			no_answer: t('No answer'),
			busy: t('Line busy'),
			unreachable: t('Unreachable'),
			wrong_number: t('Wrong number'),
			do_not_contact: t('Refused / Do not contact'),
			dnc: t('Refused / Do not contact'),
			message_sent_no_reply: t('Message sent, no reply'),
			message_replied_interested: t('Replied — interested'),
			message_replied_not_interested: t('Replied — not interested'),
			message_replied_callback: t('Replied — callback requested'),
			other: t('Other'),
		}
		return map[code] || snapshotLabel || code || '—'
	}

	function formatNumber(value) {
		if (value === undefined || value === null || value === '') return '—'
		return new Intl.NumberFormat('vi-VN').format(value)
	}

	function maskPhone(value) {
		if (!value) return ''
		const digits = String(value).replace(/\D/g, '')
		if (digits.length <= 4) return '*'.repeat(digits.length)
		return '*'.repeat(digits.length - 4) + digits.slice(-4)
	}

	function capacityLabel(value) {
		if (value === undefined) return '—'
		if (value === null) return t('Unlimited')
		if (Number(value) === 0) return t('Unlimited')
		return formatNumber(value)
	}

	function scopeLabel(value) {
		const normalized = String(value || '').trim().toLowerCase()
		const map = {
			system: t('Whole installation'),
			all: t('Whole installation'),
			company: t('Company-wide'),
			bod: t('Company-wide (BOD)'),
			crm_admin: t('CRM system administration'),
			team: t('Team scope'),
			team_leader: t('Managed team scope'),
			employee: t('Employee scope'),
			campaign: t('Campaign scope'),
			self: t('Only my own data'),
			staff: t('Only my own data'),
		}
		return map[normalized] ? map[normalized] : value || '—'
	}

	function campaignStatusLabel(value) {
		const map = {
			Draft: 'Campaign draft',
			Active: 'Campaign active',
			Paused: 'Campaign paused',
			Closed: 'Campaign closed',
		}
		return map[value] ? t(map[value]) : value || '—'
	}

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

	function alert(container, kind, message, extra, actionNodes) {
		if (!container) return
		clear(container)
		if (!message) return
		const textWrap = el('div', {}, [
			el('span', { text: message }),
			extra ? el('span', { class: 'cskh-muted', text: ' · ' + extra }) : null,
		])
		const children = [textWrap]
		if (actionNodes && actionNodes.length) {
			children.push(el('div', { class: 'cskh-actions', style: 'margin-top: 8px;' }, actionNodes))
		}
		container.appendChild(el('div', { class: 'cskh-alert is-' + kind }, children))
	}

	function toast(kind, message) {
		const host = document.getElementById('cskh-toast-host')
		if (!host || !message) return
		const item = el('div', { class: 'cskh-toast is-' + (kind || 'info') }, [
			el('span', { text: message }),
		])
		host.appendChild(item)
		setTimeout(function () {
			if (item.parentNode) item.parentNode.removeChild(item)
		}, 4200)
	}

	const CONFLICT_CODES = {
		CONFLICT: true,
		RULE_CHANGED: true,
		PREVIEW_EXPIRED: true,
		ALREADY_ASSIGNED: true,
		CAPACITY_FULL: true,
		CAPACITY_EXCEEDED: true,
		NO_ELIGIBLE_LEADS: true,
		PARTIAL_SUCCESS: true,
		OWNERSHIP_CHANGED: true,
		WORK_ITEM_NOT_ACTIVE: true,
		STALE_WORK_ITEM: true,
		ALREADY_LINKED: true,
		IDEMPOTENCY_CONFLICT: true,
		DO_NOT_CONTACT: true,
		POLICY_CHANGED: true,
		FORBIDDEN_SCOPE: true,
		SNAPSHOT_STALE: true,
		POLICY_CONFLICT: true,
		DATA_RECONCILIATION_FAILED: true,
		QA_DISABLED: true,
		QA_CONFLICT: true,
		RECOMPUTE_IN_PROGRESS: true,
		EXPORT_EXPIRED: true,
	}

	function renderError(container, error, recoveryActions) {
		const code = error && error.code ? error.code : 'ERROR'
		const rawMessage = error && error.message ? String(error.message) : ''
		const translated = rawMessage && hasTranslation(rawMessage) ? t(rawMessage) : ''
		const explanation = errorText(code)
		const message = translated || explanation || t('An error occurred while processing the request.')
		const label =
			translated && explanation && translated !== explanation
				? explanation + ' [' + code + ']'
				: '[' + code + ']'

		const actions = []
		if (recoveryActions) {
			if (code === 'STALE_WORK_ITEM' && typeof recoveryActions.onReloadWorkItem === 'function') {
				actions.push(
					el('button', {
						class: 'cskh-btn is-small is-primary',
						type: 'button',
						text: t('Reload latest work item'),
						onclick: recoveryActions.onReloadWorkItem,
					})
				)
			}
			if (code === 'POLICY_CHANGED' && typeof recoveryActions.onReloadPolicy === 'function') {
				actions.push(
					el('button', {
						class: 'cskh-btn is-small is-primary',
						type: 'button',
						text: t('Reload policy and form'),
						onclick: recoveryActions.onReloadPolicy,
					})
				)
			}
			if (
				(code === 'OWNERSHIP_CHANGED' || code === 'FORBIDDEN' || code === 'NOT_FOUND') &&
				recoveryActions.showBackLink
			) {
				actions.push(
					el('a', {
						class: 'cskh-btn is-small',
						href: '/cskh/my-customers',
						text: t('Back to my customers'),
					})
				)
			}
			if (
				(code === 'RETRYABLE_FAILURE' || (error && error.status >= 500)) &&
				typeof recoveryActions.onSafeRetry === 'function'
			) {
				actions.push(
					el('button', {
						class: 'cskh-btn is-small is-primary',
						type: 'button',
						text: t('Retry with same request key'),
						onclick: recoveryActions.onSafeRetry,
					})
				)
			}
		}

		if (code === 'FORBIDDEN' || CONFLICT_CODES[code]) {
			alert(container, 'warning', message, label, actions)
			return code
		}
		alert(container, 'error', message, label, actions)
		return code
	}

	async function copyPhone(phoneValue) {
		if (!phoneValue) return false
		try {
			if (navigator.clipboard && navigator.clipboard.writeText) {
				await navigator.clipboard.writeText(String(phoneValue))
				toast('success', t('Phone number copied to clipboard.'))
				return true
			}
		} catch (_err) {
			/* fall through */
		}
		toast('warning', t('Could not copy phone number in this browser.'))
		return false
	}

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
		} catch (_err) {
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
		uuid: uuid,
		stateBadge: stateBadge,
		workItemStateLabel: workItemStateLabel,
		dueStateLabel: dueStateLabel,
		channelLabel: channelLabel,
		outcomeLabel: outcomeLabel,
		timezoneInfo: timezoneInfo,
		effectiveTimeZone: effectiveTimeZone,
		siteTimeZone: siteTimeZone,
		partsAtEpochInTz: partsAtEpochInTz,
		tzWallClockToEpochMs: tzWallClockToEpochMs,
		computePeriodBounds: computePeriodBounds,
		parseServerDate: parseServerDate,
		serverNow: serverNow,
		formatDate: formatDate,
		formatDateOnly: formatDateOnly,
		dateKeyInTz: dateKeyInTz,
		toDatetimeLocalValue: toDatetimeLocalValue,
		datetimeLocalToIso: datetimeLocalToIso,
		datetimeLocalToServer: datetimeLocalToServer,
		classifyDueAt: classifyDueAt,
		formatNumber: formatNumber,
		capacityLabel: capacityLabel,
		scopeLabel: scopeLabel,
		campaignStatusLabel: campaignStatusLabel,
		maskPhone: maskPhone,
		copyPhone: copyPhone,
		alert: alert,
		toast: toast,
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
