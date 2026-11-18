#!/usr/bin/env node
/**
 * Unit & DOM contract tests for `apps/crm_cskh/crm_cskh/public/cskh/crm-sidebar-bridge.js`.
 *
 * Verifies against the exact `AppSidebar.vue` + `SidebarItem.vue` (`frappe-ui@1.0.0-beta.29`)
 * DOM structure:
 *   1. Inserts `CSKH` (`href="/cskh"`) into `All Views` `<nav>` immediately below `Leads` (`/crm/leads`).
 *   2. Never injects into `Public Views` or `Pinned Views` sections (`?view=...`).
 *   3. Remains strictly idempotent across repeated syncs / mutations.
 *   4. Synchronizes collapsed (`justify-center`, `size-7`, `opacity-0`, `title="CSKH"`)
 *      and expanded (`pl-2`, `ml-2 w-auto opacity-100`, no `title`) rail classes.
 *   5. Ignores non-`/crm` paths (`/cskh`, `/login`).
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import vm from 'node:vm'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const REPO_ROOT = path.resolve(path.dirname(__filename), '..', '..')
const BRIDGE_PATH = path.join(
	REPO_ROOT,
	'apps',
	'crm_cskh',
	'crm_cskh',
	'public',
	'cskh',
	'crm-sidebar-bridge.js'
)
const BRIDGE_SOURCE = fs.readFileSync(BRIDGE_PATH, 'utf-8')

class MiniClassList {
	constructor(el) {
		this._el = el
	}
	_tokens() {
		return (this._el.className || '').split(/\s+/).filter(Boolean)
	}
	contains(token) {
		return this._tokens().includes(token)
	}
}

class MiniElement {
	constructor(tagName, ownerDocument) {
		this.tagName = String(tagName || 'div').toUpperCase()
		this.nodeType = 1
		this.ownerDocument = ownerDocument
		this.parentElement = null
		this.children = []
		this.attributes = new Map()
		this.textContent = ''
		this.classList = new MiniClassList(this)
	}

	get className() {
		return this.attributes.get('class') || ''
	}

	set className(val) {
		this.setAttribute('class', String(val || ''))
	}

	get firstChild() {
		return this.children[0] || null
	}

	get nextSibling() {
		if (!this.parentElement) return null
		const idx = this.parentElement.children.indexOf(this)
		return idx >= 0 ? this.parentElement.children[idx + 1] || null : null
	}

	get nextElementSibling() {
		return this.nextSibling
	}

	setAttribute(name, value) {
		this.attributes.set(name, String(value))
		if (this.ownerDocument) this.ownerDocument._notifyMutation()
	}

	getAttribute(name) {
		return this.attributes.has(name) ? this.attributes.get(name) : null
	}

	hasAttribute(name) {
		return this.attributes.has(name)
	}

	removeAttribute(name) {
		this.attributes.delete(name)
		if (this.ownerDocument) this.ownerDocument._notifyMutation()
	}

	appendChild(child) {
		return this.insertBefore(child, null)
	}

	insertBefore(child, refNode) {
		if (child.parentElement) {
			const oldIdx = child.parentElement.children.indexOf(child)
			if (oldIdx >= 0) child.parentElement.children.splice(oldIdx, 1)
		}
		child.parentElement = this
		if (!refNode) {
			this.children.push(child)
		} else {
			const idx = this.children.indexOf(refNode)
			if (idx < 0) this.children.push(child)
			else this.children.splice(idx, 0, child)
		}
		if (this.ownerDocument) this.ownerDocument._notifyMutation()
		return child
	}

	matches(selector) {
		const sel = selector.trim()
		if (sel.includes(',')) {
			return sel.split(',').some((part) => this.matches(part))
		}
		// Match optional tag prefix + zero or more `[attr="val"]` / `[attr]` + optional `.class`
		let rest = sel
		const tagMatch = rest.match(/^[a-zA-Z0-9_-]+/)
		if (tagMatch) {
			if (this.tagName !== tagMatch[0].toUpperCase()) return false
			rest = rest.slice(tagMatch[0].length)
		}
		const attrRe = /\[([a-zA-Z0-9_-]+)(?:="([^"]*)")?\]/g
		let m
		while ((m = attrRe.exec(rest)) !== null) {
			const [, attrName, attrVal] = m
			if (!this.hasAttribute(attrName)) return false
			if (attrVal !== undefined && this.getAttribute(attrName) !== attrVal) return false
		}
		rest = rest.replace(attrRe, '')
		const classRe = /\.([a-zA-Z0-9_-]+)/g
		while ((m = classRe.exec(rest)) !== null) {
			if (!this.classList.contains(m[1])) return false
		}
		return true
	}

	closest(selector) {
		let cur = this
		while (cur) {
			if (cur.matches && cur.matches(selector)) return cur
			cur = cur.parentElement
		}
		return null
	}

	querySelectorAll(selector) {
		const found = []
		function walk(node) {
			for (const child of node.children) {
				if (child.matches(selector)) found.push(child)
				walk(child)
			}
		}
		walk(this)
		return found
	}

	querySelector(selector) {
		return this.querySelectorAll(selector)[0] || null
	}
}

class MiniDocument {
	constructor() {
		this.readyState = 'complete'
		this.mutations = 0
		this._observers = []
		this.documentElement = new MiniElement('html', this)
		this.body = new MiniElement('body', this)
		this.documentElement.appendChild(this.body)
		this.currentScript = null
	}
	_notifyMutation() {
		this.mutations += 1
		for (const cb of this._observers) cb()
	}
	createElement(tag) {
		return new MiniElement(tag, this)
	}
	createElementNS(_ns, tag) {
		return new MiniElement(tag, this)
	}
	getElementById(id) {
		return this.documentElement.querySelector(`[id="${id}"]`)
	}
	querySelector(sel) {
		return this.documentElement.querySelector(sel)
	}
	querySelectorAll(sel) {
		return this.documentElement.querySelectorAll(sel)
	}
	addEventListener() {}
}

function makeNativeSidebarItem(doc, { href, label, collapsed = false }) {
	const item = doc.createElement('div')
	item.setAttribute('data-slot', 'sidebar-item')
	item.setAttribute('data-state', 'inactive')
	item.className =
		'group/sidebar-item flex h-7 items-center rounded transition text-ink-gray-6 hover:bg-surface-gray-2'

	const a = doc.createElement('a')
	a.setAttribute('href', href)
	a.className =
		'flex h-full min-w-0 flex-1 items-center focus:outline-none focus-visible:ring-0 ' +
		(collapsed ? 'justify-center' : 'pl-2')

	const iconSpan = doc.createElement('span')
	iconSpan.className = 'grid shrink-0 place-items-center' + (collapsed ? ' size-7' : '')
	a.appendChild(iconSpan)

	const labelSpan = doc.createElement('span')
	labelSpan.textContent = label
	a.appendChild(labelSpan)

	item.appendChild(a)
	return item
}

function createCrmDom({ pathname = '/crm/leads', collapsed = false } = {}) {
	const doc = new MiniDocument()
	const app = doc.createElement('div')
	app.setAttribute('id', 'app')
	doc.body.appendChild(app)

	const scrollBox = doc.createElement('div')
	app.appendChild(scrollBox)

	const notifBtn = makeNativeSidebarItem(doc, { href: '/crm/notifications', label: 'Thông báo', collapsed })
	notifBtn.setAttribute('id', 'notifications-btn')
	scrollBox.appendChild(notifBtn)

	// Section 1: All Views
	const allViewsSec = doc.createElement('div')
	const allViewsNav = doc.createElement('nav')
	allViewsNav.className = 'flex flex-col gap-1'
	allViewsNav.appendChild(
		makeNativeSidebarItem(doc, { href: '/crm/dashboard', label: 'Trang tổng quan', collapsed })
	)
	allViewsNav.appendChild(
		makeNativeSidebarItem(doc, { href: '/crm/leads', label: 'Hồ sơ liên hệ', collapsed })
	)
	allViewsNav.appendChild(
		makeNativeSidebarItem(doc, { href: '/crm/deals', label: 'Giao dịch CRM', collapsed })
	)
	allViewsNav.appendChild(
		makeNativeSidebarItem(doc, { href: '/crm/tasks', label: 'Nhiệm vụ', collapsed })
	)
	allViewsSec.appendChild(allViewsNav)
	scrollBox.appendChild(allViewsSec)

	// Section 2: Public Views (must NEVER receive CSKH item)
	const publicViewsSec = doc.createElement('div')
	const publicViewsNav = doc.createElement('nav')
	publicViewsNav.className = 'flex flex-col gap-1'
	publicViewsNav.appendChild(
		makeNativeSidebarItem(doc, {
			href: '/crm/leads/list?view=VIEW-001',
			label: 'Hồ sơ ưu tiên',
			collapsed,
		})
	)
	publicViewsSec.appendChild(publicViewsNav)
	scrollBox.appendChild(publicViewsSec)

	const scriptTag = doc.createElement('script')
	scriptTag.setAttribute('data-cskh-sidebar-bridge', '1')
	scriptTag.setAttribute('data-cskh-href', '/cskh')
	scriptTag.setAttribute('data-cskh-label', 'CSKH')
	doc.body.appendChild(scriptTag)
	doc.currentScript = scriptTag

	const pendingRaf = []
	const win = {
		URL,
		location: {
			origin: 'https://crm.hrpartner.vn',
			pathname,
		},
		document: doc,
		requestAnimationFrame(fn) {
			pendingRaf.push(fn)
			return pendingRaf.length
		},
		setTimeout(fn) {
			pendingRaf.push(fn)
			return pendingRaf.length
		},
		MutationObserver: class {
			constructor(cb) {
				this._cb = cb
			}
			observe() {
				doc._observers.push(this._cb)
			}
		},
	}

	vm.runInNewContext(BRIDGE_SOURCE, { window: win })

	function flushRaf() {
		while (pendingRaf.length) {
			const fn = pendingRaf.shift()
			fn()
		}
	}

	return { doc, win, allViewsNav, publicViewsNav, flushRaf }
}

test('Inserts CSKH directly after Leads in All Views nav and skips Public Views', () => {
	const { allViewsNav, publicViewsNav, win } = createCrmDom({ pathname: '/crm/leads' })

	const allLinks = allViewsNav.children.map((item) => item.querySelector('a')?.getAttribute('href'))
	assert.deepEqual(allLinks, ['/crm/dashboard', '/crm/leads', '/cskh', '/crm/deals', '/crm/tasks'])

	const cskhItem = allViewsNav.querySelector('[data-cskh-sidebar-item="1"]')
	assert.ok(cskhItem, 'CSKH item must exist in All Views nav')
	const cskhAnchor = cskhItem.querySelector('a[data-cskh-nav-link="1"]')
	assert.equal(cskhAnchor.getAttribute('href'), '/cskh')
	assert.equal(cskhAnchor.getAttribute('aria-label'), 'CSKH')
	assert.equal(cskhAnchor.getAttribute('title'), null)
	assert.ok(cskhAnchor.classList.contains('pl-2'))

	// Public Views nav must remain untouched
	assert.equal(publicViewsNav.querySelector('[data-cskh-sidebar-item="1"]'), null)

	// Repeated syncAll calls are idempotent and produce zero extra DOM mutations
	const beforeMutations = win.document.mutations
	win.__cskhSidebarBridge.syncAll()
	win.__cskhSidebarBridge.syncAll()
	assert.equal(allViewsNav.querySelectorAll('[data-cskh-sidebar-item="1"]').length, 1)
	assert.equal(win.document.mutations, beforeMutations, 'Zero redundant mutations when unchanged')
})

test('Mirrors collapsed and expanded sidebar states accurately', () => {
	const { allViewsNav, flushRaf } = createCrmDom({ pathname: '/crm/dashboard', collapsed: false })
	const cskhItem = allViewsNav.querySelector('[data-cskh-sidebar-item="1"]')
	const cskhAnchor = cskhItem.querySelector('a[data-cskh-nav-link="1"]')
	const iconSlot = cskhItem.querySelector('[data-cskh-slot="icon"]')
	const labelSlot = cskhItem.querySelector('[data-cskh-slot="label"]')

	assert.ok(cskhAnchor.classList.contains('pl-2'))
	assert.ok(!iconSlot.classList.contains('size-7'))
	assert.ok(labelSlot.classList.contains('opacity-100'))

	// Simulate user clicking "Thu gọn" (Collapse) on Frappe CRM sidebar
	const firstSiblingAnchor = allViewsNav.children[0].querySelector('a')
	firstSiblingAnchor.className =
		'flex h-full min-w-0 flex-1 items-center focus:outline-none focus-visible:ring-0 justify-center'
	flushRaf()

	assert.ok(cskhAnchor.classList.contains('justify-center'))
	assert.equal(cskhAnchor.getAttribute('title'), 'CSKH')
	assert.ok(iconSlot.classList.contains('size-7'))
	assert.ok(labelSlot.classList.contains('opacity-0'))

	// Simulate user clicking "Mở rộng" (Expand)
	firstSiblingAnchor.className =
		'flex h-full min-w-0 flex-1 items-center focus:outline-none focus-visible:ring-0 pl-2'
	flushRaf()

	assert.ok(cskhAnchor.classList.contains('pl-2'))
	assert.equal(cskhAnchor.getAttribute('title'), null)
	assert.ok(!iconSlot.classList.contains('size-7'))
	assert.ok(labelSlot.classList.contains('opacity-100'))
})

test('Does not mount on non-/crm routes', () => {
	const { allViewsNav } = createCrmDom({ pathname: '/cskh' })
	assert.equal(allViewsNav.querySelector('[data-cskh-sidebar-item="1"]'), null)
})
