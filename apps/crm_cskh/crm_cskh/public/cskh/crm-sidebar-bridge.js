/* Mounts the `CSKH` navigation item directly onto Frappe CRM's left sidebar (`/crm/*`).
 *
 * Design invariants (`ADR-UI-001` / `Containerfile` clean-upstream rule):
 *   1. Zero modifications to `apps/crm` Git-tracked Vue source files.
 *   2. Exact DOM structure and Tailwind utility classes of `frappe-ui@1.0.0-beta.29`
 *      `SidebarItem.vue` so expanded, hover, focus, and collapsed-rail states match
 *      native Frappe CRM items pixel-for-pixel across desktop and mobile drawers.
 *   3. Targets ONLY the primary `All Views` `<nav>` (never `Public Views` or
 *      `Pinned Views` sections whose links carry `?view=...`).
 *   4. Uses pure DOM APIs (`createElement` / `createElementNS` / `textContent`) with
 *      mutation-free idempotent class checks so `MutationObserver` never loops.
 */
(function () {
	'use strict'

	const SVG_NS = 'http://www.w3.org/2000/svg'
	const ITEM_ATTR = 'data-cskh-sidebar-item'
	const LINK_ATTR = 'data-cskh-nav-link'

	const ITEM_CLASS =
		'group/sidebar-item flex h-7 items-center rounded transition text-ink-gray-6 hover:bg-surface-gray-2'
	const LINK_BASE_CLASS =
		'flex h-full min-w-0 flex-1 items-center focus:outline-none focus-visible:ring-0 '
	const ICON_BASE_CLASS = 'grid shrink-0 place-items-center'
	const LABEL_BASE_CLASS = 'min-w-0 flex-1 transition-all ease-in-out '
	const SUFFIX_BASE_CLASS = 'flex shrink-0 items-center transition-all ease-in-out '

	function readBridgeConfig(doc) {
		const script =
			doc.currentScript || doc.querySelector('script[data-cskh-sidebar-bridge]')
		const rawHref = (script && script.getAttribute('data-cskh-href')) || '/cskh'
		const rawLabel = (script && script.getAttribute('data-cskh-label')) || 'CSKH'
		return {
			href: rawHref.trim() || '/cskh',
			label: rawLabel.trim() || 'CSKH',
		}
	}

	function isCrmPath(win) {
		if (win.__cskhForceSidebarMount === true) {
			return true
		}
		const pathname = (win.location && win.location.pathname) || ''
		return pathname === '/crm' || pathname.indexOf('/crm/') === 0
	}

	function parseHref(win, rawHref) {
		if (!rawHref) return null
		try {
			const base = (win.location && win.location.origin) || 'http://localhost'
			return new win.URL(rawHref, base)
		} catch (_err) {
			return null
		}
	}

	function isPrimaryLeadsAnchor(win, anchor) {
		if (!anchor) return false
		const parsed = parseHref(win, anchor.getAttribute('href'))
		if (!parsed) return false
		const path = parsed.pathname || ''
		if (path !== '/crm/leads' && path.indexOf('/crm/leads/') !== 0) {
			return false
		}
		return !parsed.searchParams.has('view')
	}

	function findPrimaryLeadsItem(win, nav) {
		const anchors = nav.querySelectorAll('a[href]')
		for (let i = 0; i < anchors.length; i += 1) {
			const anchor = anchors[i]
			if (!isPrimaryLeadsAnchor(win, anchor)) continue
			const slotItem = anchor.closest('[data-slot="sidebar-item"]')
			if (slotItem && slotItem.parentElement === nav) {
				return slotItem
			}
		}
		return null
	}

	function findPrimaryNavs(win, root) {
		const scope = root || win.document
		const result = []
		const seen = new Set()

		const navNodes = scope.querySelectorAll('nav')
		for (let i = 0; i < navNodes.length; i += 1) {
			const nav = navNodes[i]
			if (seen.has(nav)) continue
			if (findPrimaryLeadsItem(win, nav)) {
				seen.add(nav)
				result.push(nav)
			}
		}

		if (result.length > 0) {
			return result
		}

		const notifButtons = scope.querySelectorAll('[id="notifications-btn"]')
		for (let i = 0; i < notifButtons.length; i += 1) {
			const firstSection = notifButtons[i].nextElementSibling
			if (!firstSection) continue
			const nav = firstSection.querySelector('nav')
			if (nav && !seen.has(nav) && nav.querySelector('[data-slot="sidebar-item"]')) {
				seen.add(nav)
				result.push(nav)
			}
		}
		return result
	}

	function detectCollapsedState(nav) {
		const siblings = nav.querySelectorAll('[data-slot="sidebar-item"]')
		for (let i = 0; i < siblings.length; i += 1) {
			const sib = siblings[i]
			if (sib.hasAttribute(ITEM_ATTR)) continue
			const trigger = sib.querySelector('a, button')
			if (trigger && trigger.classList && trigger.classList.contains('justify-center')) {
				return true
			}
			const iconSlot = trigger && trigger.querySelector('span.grid')
			if (iconSlot && iconSlot.classList && iconSlot.classList.contains('size-7')) {
				return true
			}
			return false
		}
		return false
	}

	function createHeadsetIcon(doc) {
		const svg = doc.createElementNS(SVG_NS, 'svg')
		svg.setAttribute('viewBox', '0 0 16 16')
		svg.setAttribute('fill', 'none')
		svg.setAttribute('aria-hidden', 'true')
		svg.setAttribute('class', 'size-4 text-ink-gray-7')

		const arch = doc.createElementNS(SVG_NS, 'path')
		arch.setAttribute('d', 'M3 8.5V7.5a5 5 0 0 1 10 0v1')
		arch.setAttribute('stroke', 'currentColor')
		arch.setAttribute('stroke-width', '1.35')
		arch.setAttribute('stroke-linecap', 'round')
		arch.setAttribute('stroke-linejoin', 'round')

		const leftEar = doc.createElementNS(SVG_NS, 'rect')
		leftEar.setAttribute('x', '2')
		leftEar.setAttribute('y', '7.75')
		leftEar.setAttribute('width', '2.25')
		leftEar.setAttribute('height', '3.75')
		leftEar.setAttribute('rx', '1')
		leftEar.setAttribute('stroke', 'currentColor')
		leftEar.setAttribute('stroke-width', '1.35')

		const rightEar = doc.createElementNS(SVG_NS, 'rect')
		rightEar.setAttribute('x', '11.75')
		rightEar.setAttribute('y', '7.75')
		rightEar.setAttribute('width', '2.25')
		rightEar.setAttribute('height', '3.75')
		rightEar.setAttribute('rx', '1')
		rightEar.setAttribute('stroke', 'currentColor')
		rightEar.setAttribute('stroke-width', '1.35')

		const mic = doc.createElementNS(SVG_NS, 'path')
		mic.setAttribute('d', 'M13 11.5a2 2 0 0 1-2 2H9.25')
		mic.setAttribute('stroke', 'currentColor')
		mic.setAttribute('stroke-width', '1.35')
		mic.setAttribute('stroke-linecap', 'round')

		svg.appendChild(arch)
		svg.appendChild(leftEar)
		svg.appendChild(rightEar)
		svg.appendChild(mic)
		return svg
	}

	function setClassIfChanged(el, nextClass) {
		if (el && el.className !== nextClass) {
			el.className = nextClass
		}
	}

	function setAttrIfChanged(el, attr, value) {
		if (!el) return
		if (value === null || value === '') {
			if (el.hasAttribute(attr)) {
				el.removeAttribute(attr)
			}
			return
		}
		if (el.getAttribute(attr) !== value) {
			el.setAttribute(attr, value)
		}
	}

	function syncCollapsedClasses(itemEl, collapsed, labelText) {
		const linkEl = itemEl.querySelector('a[' + LINK_ATTR + '="1"]')
		const iconWrapEl = itemEl.querySelector('[data-cskh-slot="icon"]')
		const labelWrapEl = itemEl.querySelector('[data-cskh-slot="label"]')
		const suffixEl = itemEl.querySelector('[data-slot="sidebar-item-suffix"]')

		setClassIfChanged(
			linkEl,
			LINK_BASE_CLASS + (collapsed ? 'justify-center' : 'pl-2')
		)
		setAttrIfChanged(linkEl, 'title', collapsed ? labelText : '')
		setClassIfChanged(
			iconWrapEl,
			ICON_BASE_CLASS + (collapsed ? ' size-7' : '')
		)
		setClassIfChanged(
			labelWrapEl,
			LABEL_BASE_CLASS +
				(collapsed ? 'ml-0 w-0 overflow-hidden opacity-0' : 'ml-2 w-auto opacity-100')
		)
		setClassIfChanged(
			suffixEl,
			SUFFIX_BASE_CLASS + (collapsed ? 'w-0 overflow-hidden opacity-0' : 'opacity-100')
		)
	}

	function buildSidebarItem(doc, config, collapsed) {
		const itemEl = doc.createElement('div')
		itemEl.setAttribute('data-slot', 'sidebar-item')
		itemEl.setAttribute('data-state', 'inactive')
		itemEl.setAttribute(ITEM_ATTR, '1')
		itemEl.className = ITEM_CLASS

		const linkEl = doc.createElement('a')
		linkEl.setAttribute('href', config.href)
		linkEl.setAttribute(LINK_ATTR, '1')
		linkEl.setAttribute('aria-label', config.label)

		const iconWrapEl = doc.createElement('span')
		iconWrapEl.setAttribute('data-cskh-slot', 'icon')
		iconWrapEl.appendChild(createHeadsetIcon(doc))

		const labelWrapEl = doc.createElement('span')
		labelWrapEl.setAttribute('data-cskh-slot', 'label')

		const labelInnerEl = doc.createElement('span')
		labelInnerEl.className = 'flex min-w-0 items-center'

		const labelTextEl = doc.createElement('span')
		labelTextEl.className = 'truncate text-sm'
		labelTextEl.textContent = config.label

		labelInnerEl.appendChild(labelTextEl)
		labelWrapEl.appendChild(labelInnerEl)

		linkEl.appendChild(iconWrapEl)
		linkEl.appendChild(labelWrapEl)

		const suffixEl = doc.createElement('div')
		suffixEl.setAttribute('data-slot', 'sidebar-item-suffix')

		itemEl.appendChild(linkEl)
		itemEl.appendChild(suffixEl)

		syncCollapsedClasses(itemEl, collapsed, config.label)
		return itemEl
	}

	function ensureSidebarItem(win, nav, config) {
		const collapsed = detectCollapsedState(nav)
		const existing = nav.querySelector('[' + ITEM_ATTR + '="1"]')
		if (existing) {
			syncCollapsedClasses(existing, collapsed, config.label)
			return existing
		}

		const itemEl = buildSidebarItem(win.document, config, collapsed)
		const leadsItem = findPrimaryLeadsItem(win, nav)
		if (leadsItem && leadsItem.parentElement === nav) {
			nav.insertBefore(itemEl, leadsItem.nextSibling)
		} else {
			nav.insertBefore(itemEl, nav.firstChild)
		}
		return itemEl
	}

	function installSidebarBridge(win) {
		if (!win || !win.document) return null
		if (win.__cskhSidebarBridge) return win.__cskhSidebarBridge

		const doc = win.document
		const config = readBridgeConfig(doc)

		function ensureThemeStylesheet() {
			if (!isCrmPath(win) || !doc.head || typeof doc.createElement !== 'function') {
				return
			}
			if (
				typeof doc.querySelector === 'function' &&
				doc.querySelector('link[data-cskh-crm-theme]')
			) {
				return
			}
			const linkEl = doc.createElement('link')
			linkEl.setAttribute('rel', 'stylesheet')
			linkEl.setAttribute('href', '/assets/crm_cskh/cskh/crm-theme.css')
			linkEl.setAttribute('data-cskh-crm-theme', '1')
			doc.head.appendChild(linkEl)
		}

		function syncAll(root) {
			if (!isCrmPath(win)) return 0
			ensureThemeStylesheet()
			const navs = findPrimaryNavs(win, root || doc)
			for (let i = 0; i < navs.length; i += 1) {
				ensureSidebarItem(win, navs[i], config)
			}
			return navs.length
		}

		let scheduled = false
		function scheduleSync() {
			if (scheduled) return
			scheduled = true
			const runner = function () {
				scheduled = false
				syncAll(doc)
			}
			if (typeof win.requestAnimationFrame === 'function') {
				win.requestAnimationFrame(runner)
			} else {
				win.setTimeout(runner, 0)
			}
		}

		let observer = null
		function startObserver() {
			if (observer || typeof win.MutationObserver !== 'function') return
			const target = doc.getElementById('app') || doc.body || doc.documentElement
			if (!target) return
			observer = new win.MutationObserver(function () {
				scheduleSync()
			})
			observer.observe(target, {
				childList: true,
				subtree: true,
				attributes: true,
				attributeFilter: ['class'],
			})
		}

		if (doc.readyState === 'loading') {
			doc.addEventListener('DOMContentLoaded', function () {
				syncAll(doc)
				startObserver()
			})
		} else {
			syncAll(doc)
			startObserver()
		}

		const api = {
			config: config,
			syncAll: syncAll,
			findPrimaryNavs: function (root) {
				return findPrimaryNavs(win, root || doc)
			},
			detectCollapsedState: detectCollapsedState,
		}
		win.__cskhSidebarBridge = api
		return api
	}

	if (typeof window !== 'undefined') {
		installSidebarBridge(window)
	}
})()
