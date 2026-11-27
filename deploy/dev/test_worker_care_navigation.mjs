import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import vm from 'node:vm'
import { fileURLToPath } from 'node:url'
import { createCrmDom, makeNativeSidebarItem } from './test_crm_sidebar_bridge.mjs'

const __filename = fileURLToPath(import.meta.url)
const REPO_ROOT = path.resolve(path.dirname(__filename), '..', '..')
const OVERVIEW_JS_PATH = path.join(
	REPO_ROOT,
	'apps',
	'crm_cskh',
	'crm_cskh',
	'public',
	'cskh',
	'worker-overview.js'
)
const OVERVIEW_JS_SOURCE = fs.readFileSync(OVERVIEW_JS_PATH, 'utf-8')

test('Static DEV script stays inert until authenticated capability is enabled', () => {
	const { win, navigations, allViewsNav, flushRaf } = createCrmDom({ pathname: '/crm', workerCare: false })
	assert.equal(allViewsNav.querySelector('[data-cskh-sidebar-item="1"]'), null)
	assert.deepEqual(navigations, [])
	assert.equal(win.__cskhSidebarBridge.activateWorkerCare({ enabled: false }), false)
	assert.deepEqual(navigations, [])
	assert.equal(win.__cskhSidebarBridge.activateWorkerCare({ enabled: true, href: 'https://attacker.test' }), true)
	flushRaf()
	assert.deepEqual(navigations, ['/cskh'])
})

test('Direct old sales routes navigate once to a fixed worker-care destination', () => {
	for (const pathname of [
		'/crm',
		'/crm/',
		'/crm/dashboard/',
		'/crm/%64ashboard',
		'/crm/%64eals',
		'/crm//deals',
		'/crm/deals/DEAL-001',
		'/crm/deals/DEAL%20001',
		'/crm/deals/DEAL%3F001',
		'/crm/deals/DEAL%23001',
		'/crm/deals/DEAL%5C001',
		'/crm/deals/Giao%20d%E1%BB%8Bch-01',
		'/crm/revenue',
		'/crm/data-import/doctype/CRM Deal',
		'/crm/data-import/doctype/CRM%20Deal',
		'/crm/data-import/doctype/CRM%20Deal/IMP-001',
		'/crm/data-import/doctype/CRM%20Deal/IMP%3F001',
	]) {
		const { win, navigations, flushRaf } = createCrmDom({ pathname })
		win.__cskhSidebarBridge.guardCurrentRoute()
		win.dispatch('popstate')
		flushRaf()
		assert.deepEqual(navigations, ['/cskh'], `Expected redirect for ${pathname}`)
	}
})

test('SPA history cannot reopen commercial dashboards; helper history stays functional', () => {
	for (const method of ['pushState', 'replaceState']) {
		const { win, navigations, historyCalls } = createCrmDom()
		win.history[method]({}, '', '/crm/leads/LEAD-001?next=/crm/deals')
		win.history[method]({}, '', '/crm/deals/view/list?redirect=https://attacker.test')
		assert.equal(historyCalls.length, 1)
		assert.deepEqual(navigations, ['/cskh'])
	}
})

test('External navigation, API paths and lookalike route names are preserved', () => {
	const { win, navigations, historyCalls } = createCrmDom()
	for (const url of [
		'https://external.test/crm/deals',
		'/api/method/crm.api.dashboard',
		'/crm/deals-archive',
		'/crm/dashboard-tools',
		'/crm/leads/LEAD%20001',
		'/crm/tasks',
		'/crm/call-logs',
		'/crm/data-import/doctype/CRM Lead',
	]) {
		win.history.pushState({}, '', url)
	}
	assert.equal(historyCalls.length, 8)
	assert.deepEqual(navigations, [])
})

test('Back/Forward to an old sales page is redirected without an observer loop', () => {
	const { win, navigations, flushRaf } = createCrmDom()
	win.location.pathname = '/crm/deals/DEAL-001/'
	win.dispatch('popstate')
	win.dispatch('pageshow')
	flushRaf()
	assert.deepEqual(navigations, ['/cskh'])
})

test('Newly rendered sales links and exact conversion actions are blocked VDOM-safely while Save stays', () => {
	const { doc, allViewsNav, publicViewsNav, flushRaf } = createCrmDom()
	for (const href of ['/crm/deals?view=VIEW-001', '/crm/forecast', '/crm/revenue']) {
		allViewsNav.appendChild(makeNativeSidebarItem(doc, { href, label: 'Sales' }))
	}
	publicViewsNav.appendChild(makeNativeSidebarItem(doc, { href: '/crm/deals?view=PUBLIC-001', label: 'Deal view' }))
	const save = doc.createElement('button')
	save.textContent = 'Save'
	doc.body.appendChild(save)
	const blockedButtons = []
	for (const label of ['Convert to Deal', 'Chuyển thành Giao dịch CRM', 'Tạo Giao dịch CRM']) {
		const button = doc.createElement('button')
		button.textContent = label
		doc.body.appendChild(button)
		blockedButtons.push(button)
	}
	flushRaf()
	assert.ok(save.parentElement)
	assert.equal(save.getAttribute('hidden'), null)
	for (const btn of blockedButtons) {
		assert.equal(btn.parentElement, doc.body, 'Vue anchor stays mounted')
		assert.equal(btn.getAttribute('data-cskh-sales-blocked'), '1')
		assert.equal(btn.getAttribute('hidden'), 'hidden')
	}
	const visibleButtons = doc.querySelectorAll('button').filter((b) => !b.closest('[data-cskh-sales-blocked="1"]'))
	assert.equal(visibleButtons.length, 1)
	const visibleSalesAnchors = doc
		.querySelectorAll('a[href]')
		.filter(
			(a) =>
				!a.closest('[data-cskh-sales-blocked="1"]') &&
				/\/crm\/(deals|forecast|revenue)/.test(a.getAttribute('href'))
		)
	assert.equal(visibleSalesAnchors.length, 0)
	const mutations = doc.mutations
	flushRaf()
	assert.equal(doc.mutations, mutations)
})

test('Capture guard blocks a conversion click before native handlers and preserves external actions', () => {
	const { doc, navigations } = createCrmDom()
	const convert = doc.createElement('button')
	convert.setAttribute('aria-label', 'Convert Lead to Deal')
	let prevented = false, stopped = false
	doc.dispatch('click', { target: convert, preventDefault() { prevented = true }, stopImmediatePropagation() { stopped = true } })
	assert.ok(prevented && stopped)
	assert.deepEqual(navigations, ['/cskh'])
	const external = doc.createElement('a')
	external.setAttribute('href', 'https://external.test/crm/deals')
	external.textContent = 'New Deal'
	doc.dispatch('click', { target: external, preventDefault() { assert.fail('External link was blocked') }, stopImmediatePropagation() {} })
})

test('Command palette role="option" items and Enter key block lead-convert and sales commands while preserving Lead records', () => {
	const { doc, navigations, flushRaf } = createCrmDom({ pathname: '/crm/leads/LEAD-001' })

	const dialog = doc.createElement('div')
	dialog.setAttribute('role', 'dialog')
	const palette = doc.createElement('div')
	palette.className = 'crm-command-palette'
	const input = doc.createElement('input')
	input.setAttribute('role', 'combobox')
	input.setAttribute('aria-activedescendant', 'opt-lead-convert')
	palette.appendChild(input)

	function makeOption(id, title, subtitle = '') {
		const opt = doc.createElement('div')
		opt.setAttribute('role', 'option')
		opt.setAttribute('id', id)
		const row = doc.createElement('div')
		const titleSpan = doc.createElement('span')
		titleSpan.textContent = title
		row.appendChild(titleSpan)
		if (subtitle) {
			const subSpan = doc.createElement('span')
			subSpan.textContent = subtitle
			row.appendChild(subSpan)
		}
		opt.appendChild(row)
		return opt
	}

	const groupContextual = doc.createElement('div')
	const optConvert = makeOption('opt-lead-convert', 'Convert to deal')
	const optStatus = makeOption('opt-lead-status', 'Change status')
	groupContextual.appendChild(optConvert)
	groupContextual.appendChild(optStatus)

	const groupNav = doc.createElement('div')
	const optDash = makeOption('opt-nav-dash', 'Dashboard')
	const optDeals = makeOption('opt-nav-deals', 'Deals')
	const optCreateDeal = makeOption('opt-create-deal', 'Create Deal')
	groupNav.appendChild(optDash)
	groupNav.appendChild(optDeals)
	groupNav.appendChild(optCreateDeal)

	const groupRecords = doc.createElement('div')
	const optLeadNamedDash = makeOption('opt-rec-lead', 'Dashboard', 'Lead')
	const optDealRecord = makeOption('opt-rec-deal', 'ABC Manpower', 'Deal')
	groupRecords.appendChild(optLeadNamedDash)
	groupRecords.appendChild(optDealRecord)

	palette.appendChild(groupContextual)
	palette.appendChild(groupNav)
	palette.appendChild(groupRecords)
	dialog.appendChild(palette)
	doc.body.appendChild(dialog)
	flushRaf()

	// Sales options are blocked & hidden; legitimate commands and Lead records stay visible
	assert.equal(optConvert.getAttribute('hidden'), 'hidden')
	assert.equal(optConvert.getAttribute('aria-disabled'), 'true')
	assert.equal(optDash.getAttribute('hidden'), 'hidden')
	assert.equal(optDeals.getAttribute('hidden'), 'hidden')
	assert.equal(optCreateDeal.getAttribute('hidden'), 'hidden')
	assert.equal(optDealRecord.getAttribute('hidden'), 'hidden')
	assert.equal(groupNav.getAttribute('hidden'), 'hidden', 'All-sales group is hidden cleanly')

	assert.equal(optStatus.getAttribute('hidden'), null)
	assert.equal(optLeadNamedDash.getAttribute('hidden'), null, 'Lead titled Dashboard with subtitle Lead is preserved')

	// Pressing Enter while combobox active-descendant points at lead-convert is blocked before Vue handler
	let enterPrevented = false
	let enterStopped = false
	doc.dispatch('keydown', {
		key: 'Enter',
		target: input,
		preventDefault() { enterPrevented = true },
		stopImmediatePropagation() { enterStopped = true },
	})
	assert.ok(enterPrevented && enterStopped)
	assert.deepEqual(navigations, ['/cskh'])
})

test('Mobile Lead detail removes Convert / Chuyển đổi button while preserving Make Call and Save', () => {
	const { doc, flushRaf } = createCrmDom({ pathname: '/crm/leads/LEAD-009' })
	const makeCall = doc.createElement('button')
	makeCall.textContent = 'Make a Call'
	const saveBtn = doc.createElement('button')
	saveBtn.textContent = 'Lưu'
	const mobileConvertEn = doc.createElement('button')
	mobileConvertEn.textContent = 'Convert'
	const mobileConvertVi = doc.createElement('button')
	mobileConvertVi.textContent = 'Chuyển đổi'

	doc.body.appendChild(makeCall)
	doc.body.appendChild(saveBtn)
	doc.body.appendChild(mobileConvertEn)
	doc.body.appendChild(mobileConvertVi)
	flushRaf()

	assert.ok(makeCall.parentElement)
	assert.ok(saveBtn.parentElement)
	assert.equal(makeCall.getAttribute('hidden'), null)
	assert.equal(saveBtn.getAttribute('hidden'), null)
	assert.equal(mobileConvertEn.parentElement, doc.body, 'Mobile Convert stays mounted for Vue VDOM')
	assert.equal(mobileConvertVi.parentElement, doc.body, 'Mobile Chuyển đổi stays mounted for Vue VDOM')
	assert.equal(mobileConvertEn.getAttribute('data-cskh-sales-blocked'), '1')
	assert.equal(mobileConvertVi.getAttribute('data-cskh-sales-blocked'), '1')
	assert.equal(mobileConvertEn.getAttribute('hidden'), 'hidden')
	assert.equal(mobileConvertVi.getAttribute('hidden'), 'hidden')
})

test('Organization Deals tab auto-switches once without stealing input focus and Contact renders worker-care helper', () => {
	// 1. Organization.vue: Deals (selected) + Contacts -> clicks Contacts once, hides Deals tab, keeps input focus
	const orgEnv = createCrmDom({ pathname: '/crm/organizations/ORG-001' })
	const searchInput = orgEnv.doc.createElement('input')
	let inputRestoredFocus = 0
	searchInput.focus = () => {
		inputRestoredFocus += 1
		orgEnv.doc.activeElement = searchInput
	}
	orgEnv.doc.body.appendChild(searchInput)
	orgEnv.doc.activeElement = searchInput

	const orgTabsWrap = orgEnv.doc.createElement('div')
	const orgTablist = orgEnv.doc.createElement('div')
	orgTablist.setAttribute('role', 'tablist')
	const orgDealsTab = orgEnv.doc.createElement('button')
	orgDealsTab.setAttribute('role', 'tab')
	orgDealsTab.setAttribute('aria-selected', 'true')
	orgDealsTab.textContent = 'Deals 2'
	const orgContactsTab = orgEnv.doc.createElement('button')
	orgContactsTab.setAttribute('role', 'tab')
	orgContactsTab.setAttribute('aria-selected', 'false')
	orgContactsTab.textContent = 'Contacts 5'
	let contactsClicked = 0
	orgContactsTab.addEventListener('click', () => {
		contactsClicked += 1
		orgEnv.doc.activeElement = orgContactsTab
		orgDealsTab.setAttribute('aria-selected', 'false')
		orgContactsTab.setAttribute('aria-selected', 'true')
	})
	orgTablist.appendChild(orgDealsTab)
	orgTablist.appendChild(orgContactsTab)
	orgTabsWrap.appendChild(orgTablist)
	orgEnv.doc.body.appendChild(orgTabsWrap)
	orgEnv.flushRaf()

	assert.equal(contactsClicked, 1)
	assert.equal(inputRestoredFocus, 1, 'Focused input must not lose focus when Deals tab switches')
	assert.equal(orgEnv.doc.activeElement, searchInput)
	assert.equal(orgDealsTab.getAttribute('hidden'), 'hidden')
	assert.equal(orgDealsTab.getAttribute('tabindex'), '-1')
	assert.equal(orgContactsTab.getAttribute('hidden'), null)

	// Re-syncing must never click Contacts a second time even if attributes mutate
	orgDealsTab.setAttribute('aria-selected', 'true')
	orgEnv.win.__cskhSidebarBridge.syncAll()
	assert.equal(contactsClicked, 1, 'Tab auto-switch runs at most once per tablist')

	// 2. Contact.vue desktop: sole Deals tab -> hides tablist/tabpanel and injects worker-care panel idempotently
	const contactEnv = createCrmDom({ pathname: '/crm/contacts/CONT-001' })
	const contactTabsWrap = contactEnv.doc.createElement('div')
	const contactTablist = contactEnv.doc.createElement('div')
	contactTablist.setAttribute('role', 'tablist')
	const contactDealsTab = contactEnv.doc.createElement('button')
	contactDealsTab.setAttribute('role', 'tab')
	contactDealsTab.textContent = 'Giao dịch CRM 0'
	contactTablist.appendChild(contactDealsTab)
	const contactPanel = contactEnv.doc.createElement('div')
	contactPanel.setAttribute('role', 'tabpanel')
	contactPanel.textContent = 'No Deals Found'
	contactTabsWrap.appendChild(contactTablist)
	contactTabsWrap.appendChild(contactPanel)
	contactEnv.doc.body.appendChild(contactTabsWrap)
	contactEnv.flushRaf()

	assert.equal(contactTablist.getAttribute('hidden'), 'hidden')
	assert.equal(contactPanel.getAttribute('hidden'), 'hidden')
	const carePanels = contactTabsWrap.querySelectorAll('[data-cskh-contact-care-panel="1"]')
	assert.equal(carePanels.length, 1)

	const mutBefore = contactEnv.doc.mutations
	contactEnv.win.__cskhSidebarBridge.syncAll()
	assert.equal(contactEnv.doc.mutations, mutBefore, 'Contact helper panel is strictly idempotent')
})

test('Bridge fetch & localStorage guards preserve headers, errors, non-Deal requests and Lead/Contact history', async () => {
	const { doc, win, navigations, flushRaf } = createCrmDom({ pathname: '/crm/leads', workerCare: false })

	// Seed localStorage with Lead/Contact/Deal recent entries AND unrelated preferences
	const store = new Map([
		[
			'crm-command-palette-recent:staff@dev.local',
			JSON.stringify([
				{ doctype: 'CRM Lead', name: 'LEAD-1', title: 'Nguyen Van A' },
				{ doctype: 'CRM Deal', name: 'DEAL-1', title: 'Old Sales Deal' },
				{ doctype: 'Contact', name: 'CONT-1', title: 'Tran Thi B' },
				{ doctype: 'CRM Task', name: 'TASK-1', title: 'Gọi lại' },
			]),
		],
		['firstDealCreated', 'true'],
		['lastLeadTab', 'Activities'],
		['crm-user-theme', 'light'],
	])
	win.localStorage = {
		get length() { return store.size },
		key(i) { return Array.from(store.keys())[i] || null },
		getItem(k) { return store.has(k) ? store.get(k) : null },
		setItem(k, v) { store.set(k, String(v)) },
		removeItem(k) { store.delete(k) },
	}
	win.Headers = Headers
	win.Response = Response

	const cleanViewsResp = new Response(
		JSON.stringify({ message: [{ name: 'V-LEAD-ONLY', dt: 'CRM Lead', route_name: 'Leads' }] }),
		{ status: 200, statusText: 'OK', headers: { 'Content-Type': 'application/json', 'X-Frappe-Site': 'cskh.local' } }
	)
	const error403Resp = new Response(
		JSON.stringify({ exc_type: 'PermissionError' }),
		{ status: 403, statusText: 'FORBIDDEN', headers: { 'Content-Type': 'application/json', 'X-Error-Code': 'FORBIDDEN' } }
	)
	const saveOkResp = new Response(
		JSON.stringify({ message: { name: 'LEAD-1' } }),
		{ status: 200, headers: { 'Content-Type': 'application/json', 'X-Custom': 'kept' } }
	)

	let nextViewsMode = 'mixed'
	win.fetch = async (input, init) => {
		const url = typeof input === 'string' ? input : (input.url || input.href)
		if (url.includes('network-fail')) {
			throw new TypeError('NetworkError when attempting to fetch resource')
		}
		if (url.includes('command_palette.search')) {
			if (url.includes('forbidden=1')) return error403Resp
			return new Response(
				JSON.stringify({
					message: {
						matches: [
							{ doctype: 'CRM Lead', name: 'LEAD-1', title: 'Nguyen Van A', route: 'Lead' },
							{ doctype: 'CRM Deal', name: 'DEAL-1', title: 'Commercial Deal', route: 'Deal' },
						],
						recent: [{ doctype: 'CRM Deal', name: 'DEAL-1', title: 'Commercial Deal', route: 'Deal' }],
						upcoming: [
							{ kind: 'task', name: 'TASK-1', route: 'Lead', route_name: 'LEAD-1' },
							{ kind: 'sla', name: 'DEAL-1', route: 'Deal', route_name: 'DEAL-1' },
						],
					},
				}),
				{
					status: 200,
					statusText: 'OK',
					headers: {
						'Content-Type': 'application/json; charset=utf-8',
						'X-Frappe-Request-Id': 'req-search-123',
						'Cache-Control': 'private, no-store',
					},
				}
			)
		}
		if (url.includes('views.get_views')) {
			if (nextViewsMode === 'clean') return cleanViewsResp
			return new Response(
				JSON.stringify({
					message: [
						{ name: 'V-LEAD', dt: 'CRM Lead', route_name: 'Leads', pinned: 1 },
						{ name: 'V-DEAL', dt: 'CRM Deal', route_name: 'Deals', pinned: 1, is_default: 1 },
					],
				}),
				{
					status: 200,
					statusText: 'OK',
					headers: {
						'Content-Type': 'application/json',
						'X-Frappe-Request-Id': 'req-views-456',
					},
				}
			)
		}
		return Object.assign(saveOkResp, { _receivedInit: init })
	}

	win.__cskhSidebarBridge.activateWorkerCare({ enabled: true, label: 'Chăm sóc người lao động' })

	// 1. localStorage preserves Lead/Contact/Task history and all unrelated preference keys
	const scrubbedRecent = JSON.parse(store.get('crm-command-palette-recent:staff@dev.local'))
	assert.deepEqual(
		scrubbedRecent.map((r) => r.doctype),
		['CRM Lead', 'Contact', 'CRM Task']
	)
	assert.equal(store.get('firstDealCreated'), 'true')
	assert.equal(store.get('lastLeadTab'), 'Activities')
	assert.equal(store.get('crm-user-theme'), 'light')

	// 2. Settings Dashboard item is hidden while General stays visible
	const settingsGeneral = doc.createElement('div')
	settingsGeneral.setAttribute('data-page', 'General')
	const settingsDashboard = doc.createElement('div')
	settingsDashboard.setAttribute('data-page', 'Dashboard')
	doc.body.appendChild(settingsGeneral)
	doc.body.appendChild(settingsDashboard)
	flushRaf()
	assert.equal(settingsGeneral.getAttribute('hidden'), null)
	assert.equal(settingsDashboard.getAttribute('hidden'), 'hidden')

	// 3. Filtered search response preserves custom headers, status, statusText & charset
	const searchRes = await win.fetch('/api/method/crm.api.command_palette.search?query=ng')
	assert.equal(searchRes.status, 200)
	assert.equal(searchRes.headers.get('x-frappe-request-id'), 'req-search-123')
	assert.equal(searchRes.headers.get('cache-control'), 'private, no-store')
	assert.match(searchRes.headers.get('content-type'), /application\/json/)
	const searchBody = await searchRes.json()
	assert.equal(searchBody.message.matches.length, 1)
	assert.equal(searchBody.message.matches[0].doctype, 'CRM Lead')
	assert.equal(searchBody.message.recent.length, 0)
	assert.equal(searchBody.message.upcoming.length, 1)

	// 4. Filtered views response preserves headers (both string and URL instance); clean views returns exact Response
	const viewsRes = await win.fetch('/api/method/crm.api.views.get_views')
	assert.equal(viewsRes.headers.get('x-frappe-request-id'), 'req-views-456')
	const viewsBody = await viewsRes.json()
	assert.deepEqual(viewsBody.message.map((v) => v.name), ['V-LEAD'])

	const viewsUrlObjRes = await win.fetch(
		new URL('/api/method/crm.api.views.get_views', win.location.origin)
	)
	const viewsUrlObjBody = await viewsUrlObjRes.json()
	assert.deepEqual(viewsUrlObjBody.message.map((v) => v.name), ['V-LEAD'])

	nextViewsMode = 'clean'
	const untouchedViewsRes = await win.fetch('/api/method/crm.api.views.get_views')
	assert.equal(untouchedViewsRes, cleanViewsResp, 'Unchanged payload returns exact native Response object')

	// 5. Non-2xx error responses, network errors, and write RPCs pass through 100% untouched (backend owns authz)
	const errRes = await win.fetch('/api/method/crm.api.command_palette.search?forbidden=1')
	assert.equal(errRes, error403Resp)
	assert.equal(errRes.status, 403)
	assert.equal(errRes.headers.get('x-error-code'), 'FORBIDDEN')

	const saveInit = { method: 'POST', headers: { 'X-Frappe-CSRF-Token': 'tok' }, body: '{}' }
	const saveRes = await win.fetch('/api/method/frappe.client.set_value', saveInit)
	assert.equal(saveRes, saveOkResp)
	assert.equal(saveRes._receivedInit, saveInit)

	await assert.rejects(
		win.fetch('/api/method/crm.api.views.get_views?network-fail=1'),
		/NetworkError/
	)
	assert.deepEqual(navigations, [])
})

function createOverviewHarness({ caps = { pii: true, staff: true }, apiResponses = [] } = {}) {
	const { doc } = createCrmDom({ pathname: '/cskh', workerCare: false })
	const root = doc.createElement('section')
	root.setAttribute('id', 'care-overview')
	for (const id of [
		'care-status',
		'care-data',
		'care-refresh',
		'care-role',
		'care-scope',
		'care-day',
		'care-updated',
		'care-tz-note',
		'care-bod-banner',
		'care-empty',
		'care-metrics',
		'care-priority-eyebrow',
		'care-priority-title',
		'care-open-summary',
		'care-priorities',
		'care-coverage',
		'care-bottlenecks-card',
		'care-bottlenecks',
		'care-dnc',
		'care-sla',
	]) {
		const node = doc.createElement(id === 'care-refresh' ? 'button' : 'div')
		node.setAttribute('id', id)
		if (id === 'care-data' || id === 'care-bottlenecks-card' || id === 'care-bod-banner' || id === 'care-empty') {
			node.hidden = true
		}
		root.appendChild(node)
	}
	doc.body.appendChild(root)

	let callIdx = 0
	let intervalFn = null
	const cskhStub = {
		t: (msg, fb) => (fb !== undefined && msg === 'HTTP 500' ? fb : msg),
		el(tag, attrs = {}, children = []) {
			const node = doc.createElement(tag)
			node.style = {}
			for (const [k, v] of Object.entries(attrs)) {
				if (k === 'class') node.className = v
				else if (k === 'text') node.textContent = String(v)
				else if (k === 'onclick') node.addEventListener('click', v)
				else if (v !== null && v !== undefined) node.setAttribute(k, String(v))
			}
			for (const ch of children) {
				if (!ch) continue
				if (typeof ch === 'string') {
					const span = doc.createElement('span')
					span.textContent = ch
					node.appendChild(span)
				} else {
					node.appendChild(ch)
				}
			}
			return node
		},
		clear(node) {
			while (node.children.length) node.removeChild(node.children[0])
			node.textContent = ''
		},
		formatNumber: (v) => String(v),
		formatDate: (v) => String(v),
		formatDateOnly: (v) => String(v),
		scopeLabel: (s) => ({ self: 'Chỉ dữ liệu của tôi', team: 'Phạm vi đội', all: 'Toàn hệ thống' }[s] || s),
		errorText: (code) => ({ FORBIDDEN: 'Không đủ quyền truy cập', HTTP_500: 'Lỗi máy chủ' }[code] || ''),
		capabilities: () => caps,
		get: async () => {
			const next = apiResponses[Math.min(callIdx++, apiResponses.length - 1)]
			if (next instanceof Error) throw next
			return next
		},
	}

	const win = {
		document: doc,
		cskh: cskhStub,
		setInterval(fn) { intervalFn = fn; return 1 },
		addEventListener() {},
	}
	vm.runInNewContext(OVERVIEW_JS_SOURCE, { window: win, document: doc, cskh: cskhStub, Date })
	return { doc, root, win, triggerInterval: () => intervalFn && intervalFn() }
}

test('Worker overview never turns initial API errors into fake zero metrics and marks failed refreshes stale', async () => {
	const err500 = Object.assign(new Error('HTTP 500'), { status: 500, code: 'HTTP_500' })
	const samplePayload = {
		scope: 'team',
		as_of: '2026-11-26T09:30:00+07:00',
		timezone: { site: 'Asia/Ho_Chi_Minh', effective: 'Asia/Tokyo' },
		day: { date: '2026-11-26' },
		workload: { active: 18, contact_records: 15, unattempted: 6, attempted: 12, connected: 7, attempted_unconnected: 5 },
		callbacks: { open: 5, today: 3, overdue: 2 },
		bottlenecks: { on_hold: 4, active_assignees: 3, assignees_with_unattempted: 2, assignees_with_overdue: 1 },
		do_not_contact: 2,
		sla: null,
	}

	const { doc } = createOverviewHarness({
		caps: { pii: true, leader: true, p3_enabled: true },
		apiResponses: [err500, samplePayload, err500],
	})
	await new Promise((r) => setTimeout(r, 10))

	// 1. Initial load failure: care-data stays hidden, no 0 metrics shown, error code surfaced
	assert.equal(doc.getElementById('care-data').hidden, true)
	assert.equal(doc.getElementById('care-metrics').children.length, 0)
	assert.match(doc.getElementById('care-status').className, /is-error/)
	assert.match(doc.getElementById('care-status').querySelector('.care-notice-text').textContent, /\[HTTP_500\]/)

	// 2. Retry succeeds: metrics, units, distinct workers vs Work Items, TZ difference & Leader bottlenecks shown
	doc.getElementById('care-refresh').click()
	await new Promise((r) => setTimeout(r, 10))
	assert.equal(doc.getElementById('care-data').hidden, false)
	assert.equal(doc.getElementById('care-metrics').children.length, 4)
	assert.equal(doc.getElementById('care-bottlenecks-card').hidden, false)
	assert.equal(doc.getElementById('care-bottlenecks').children.length, 4)
	assert.equal(doc.getElementById('care-tz-note').hidden, false)
	assert.match(doc.getElementById('care-sla').textContent, /disabled/)

	// 3. Subsequent refresh fails: previous snapshot retained and visibly marked stale
	doc.getElementById('care-refresh').click()
	await new Promise((r) => setTimeout(r, 10))
	assert.equal(doc.getElementById('care-data').hidden, false)
	assert.equal(doc.getElementById('care-data').getAttribute('data-stale'), '1')
	assert.match(doc.getElementById('care-status').className, /is-error/)
})

test('BOD overview enforces zero-PII banner and never links to PII callback or record workflows', async () => {
	const bodPayload = {
		scope: 'all',
		as_of: '2026-11-26T09:30:00+07:00',
		timezone: { site: 'Asia/Ho_Chi_Minh', effective: 'Asia/Ho_Chi_Minh' },
		day: { date: '2026-11-26' },
		workload: { active: 0, contact_records: 0, unattempted: 0, attempted: 0, connected: 0, attempted_unconnected: 0 },
		callbacks: { open: 0, today: 0, overdue: 0 },
		bottlenecks: { on_hold: 0, active_assignees: 0, assignees_with_unattempted: 0, assignees_with_overdue: 0 },
		do_not_contact: 5,
		sla: { pending: 4, overdue: 1 },
	}
	const { doc, root } = createOverviewHarness({
		caps: { pii: false, bod: true, p3_enabled: true },
		apiResponses: [bodPayload],
	})
	await new Promise((r) => setTimeout(r, 10))

	assert.equal(doc.getElementById('care-bod-banner').hidden, false)
	assert.equal(doc.getElementById('care-empty').hidden, false, 'Empty active workload is reported honestly')
	const allHrefs = root.querySelectorAll('a').map((a) => a.getAttribute('href'))
	for (const href of allHrefs) {
		assert.ok(!href.startsWith('/cskh/callbacks'), `BOD must not link to PII callbacks: ${href}`)
		assert.ok(!href.startsWith('/cskh/my-customers'), `BOD must not link to PII my-customers: ${href}`)
		assert.ok(!href.startsWith('/crm'), `BOD must not link to CRM: ${href}`)
	}
})

test('Onboarding widget div steps for Deal conversion and Deal status are blocked without detaching v-for siblings', () => {
	const { doc, flushRaf } = createCrmDom({ pathname: '/crm/leads/LEAD-002' })
	const stepsList = doc.createElement('div')

	function makeStepRow(label, withSkip = false) {
		const row = doc.createElement('div')
		const inner = doc.createElement('div')
		const textSpan = doc.createElement('span')
		textSpan.textContent = label
		inner.appendChild(textSpan)
		row.appendChild(inner)
		if (withSkip) {
			const skipBtn = doc.createElement('button')
			skipBtn.textContent = 'Bỏ qua'
			row.appendChild(skipBtn)
		}
		stepsList.appendChild(row)
		return row
	}

	const stepPassword = makeStepRow('Thiết lập mật khẩu của bạn', true)
	const stepFirstLead = makeStepRow('Tạo hồ sơ liên hệ đầu tiên của bạn', true)
	const stepConvertDeal = makeStepRow('Chuyển hồ sơ liên hệ thành Giao dịch CRM')
	const stepFirstTask = makeStepRow('Tạo công việc đầu tiên của bạn', true)
	const stepDealStatus = makeStepRow('Thay đổi trạng thái Giao dịch CRM', true)

	doc.body.appendChild(stepsList)
	flushRaf()

	assert.equal(stepConvertDeal.getAttribute('data-cskh-sales-blocked'), '1')
	assert.equal(stepConvertDeal.getAttribute('hidden'), 'hidden')
	assert.equal(stepDealStatus.getAttribute('data-cskh-sales-blocked'), '1')
	assert.equal(stepDealStatus.getAttribute('hidden'), 'hidden')
	assert.equal(stepConvertDeal.parentElement, stepsList, 'v-for node stays mounted for Vue patching')

	assert.equal(stepPassword.getAttribute('hidden'), null)
	assert.equal(stepFirstLead.getAttribute('hidden'), null)
	assert.equal(stepFirstTask.getAttribute('hidden'), null)
	assert.equal(stepsList.getAttribute('hidden'), null)
})

test('Command palette nudges across consecutive blocked options one async Vue microtask at a time without overshooting', async () => {
	const { doc, win, navigations } = createCrmDom({ pathname: '/crm/leads/LEAD-077' })
	const timers = []
	win.setTimeout = (fn) => {
		timers.push(fn)
		return timers.length
	}
	win.KeyboardEvent = class KeyboardEvent {
		constructor(type, init = {}) {
			this.type = type
			this.key = init.key || ''
			this.bubbles = !!init.bubbles
			this.cancelable = !!init.cancelable
			this.defaultPrevented = false
		}
		preventDefault() {
			this.defaultPrevented = true
		}
	}

	const dialog = doc.createElement('div')
	dialog.setAttribute('role', 'dialog')
	const palette = doc.createElement('div')
	palette.className = 'crm-command-palette'
	const input = doc.createElement('input')
	input.setAttribute('role', 'combobox')
	palette.appendChild(input)

	function makeOpt(id, title) {
		const opt = doc.createElement('div')
		opt.setAttribute('role', 'option')
		opt.setAttribute('id', id)
		const sp = doc.createElement('span')
		sp.textContent = title
		opt.appendChild(sp)
		return opt
	}

	const opts = [
		makeOpt('opt-0-convert', 'Convert to deal'),
		makeOpt('opt-1-dash', 'Dashboard'),
		makeOpt('opt-2-status', 'Change status'),
		makeOpt('opt-3-deals', 'Deals'),
		makeOpt('opt-4-task', 'Create task'),
	]
	const group = doc.createElement('div')
	for (const o of opts) group.appendChild(o)
	palette.appendChild(group)
	dialog.appendChild(palette)

	// Simulate Vue 3 + HeadlessUI Combobox: `keydown` updates `activeIdx` synchronously,
	// but flushes `aria-activedescendant` asynchronously on `Promise.resolve()` microtask (`nextTick`)!
	let activeIdx = 0
	input.setAttribute('aria-activedescendant', opts[0].getAttribute('id'))
	input.addEventListener('keydown', (ev) => {
		if (ev.key === 'ArrowDown') {
			activeIdx = (activeIdx + 1) % opts.length
		} else if (ev.key === 'ArrowUp') {
			activeIdx = (activeIdx - 1 + opts.length) % opts.length
		} else {
			return
		}
		const targetId = opts[activeIdx].getAttribute('id')
		Promise.resolve().then(() => {
			input.setAttribute('aria-activedescendant', targetId)
		})
	})

	async function drainAsyncPalette() {
		for (let round = 0; round < 12; round += 1) {
			await Promise.resolve()
			await Promise.resolve()
			if (!timers.length) break
			const fn = timers.shift()
			fn()
		}
	}

	doc.body.appendChild(dialog)
	win.__cskhSidebarBridge.syncAll()
	await drainAsyncPalette()

	// Opening Ctrl+K when index 0 & 1 are blocked lands cleanly on index 2 ('Change status'), NOT overshooting!
	assert.equal(input.getAttribute('aria-activedescendant'), 'opt-2-status')

	// User presses ArrowDown -> HeadlessUI moves to opt-3-deals -> bridge nudges once onto opt-4-task!
	input.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowDown' }))
	doc.dispatch('keydown', { key: 'ArrowDown', target: input, preventDefault() {}, stopImmediatePropagation() {} })
	await drainAsyncPalette()
	assert.equal(input.getAttribute('aria-activedescendant'), 'opt-4-task')

	// User presses ArrowUp from opt-4-task -> HeadlessUI moves to opt-3-deals, and even if rAF `syncAll()`
	// runs BEFORE setTimeout, `lastPaletteDirection` keeps moving upward onto opt-2-status!
	doc.dispatch('keydown', { key: 'ArrowUp', target: input, preventDefault() {}, stopImmediatePropagation() {} })
	input.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowUp' }))
	await Promise.resolve()
	win.__cskhSidebarBridge.syncAll()
	await drainAsyncPalette()
	assert.equal(input.getAttribute('aria-activedescendant'), 'opt-2-status')

	// Pressing ArrowUp again from opt-2-status traverses across BOTH blocked opt-1-dash & opt-0-convert
	// upward (wrapping to opt-4-task) without bouncing back down!
	doc.dispatch('keydown', { key: 'ArrowUp', target: input, preventDefault() {}, stopImmediatePropagation() {} })
	input.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowUp' }))
	await Promise.resolve()
	win.__cskhSidebarBridge.syncAll()
	await drainAsyncPalette()
	assert.equal(input.getAttribute('aria-activedescendant'), 'opt-4-task')
	assert.deepEqual(navigations, [])
})
