/* Promotes worker care while keeping native CRM contact/task/call helpers usable.
 *
 * Design invariants (`ADR-UI-001` / `Containerfile` clean-upstream rule):
 *   1. Zero modifications to `apps/crm` Git-tracked Vue source files.
 *   2. Exact DOM structure and Tailwind utility classes of `frappe-ui@1.0.0-beta.29`
 *      `SidebarItem.vue` so expanded, hover, focus, and collapsed-rail states match
 *      native Frappe CRM items pixel-for-pixel across desktop and mobile drawers.
 *   3. Adds worker care only to primary navigation. Sales destinations, deal tabs,
 *      command-palette deal actions, settings/onboarding deal entries, and explicit
 *      deal-conversion controls are blocked from native helpers while preserving
 *      ordinary saved contact views, phone calls, tasks, Save, and API contracts.
 *   4. Uses pure DOM APIs (`createElement` / `createElementNS` / `textContent`) with
 *      mutation-free idempotent checks so `MutationObserver` never loops.
 */
(function () {
	'use strict'

	const SVG_NS = 'http://www.w3.org/2000/svg'
	const ITEM_ATTR = 'data-cskh-sidebar-item'
	const LINK_ATTR = 'data-cskh-nav-link'
	const BLOCKED_ATTR = 'data-cskh-sales-blocked'
	const GROUP_HIDDEN_ATTR = 'data-cskh-group-hidden'
	const CARE_PANEL_ATTR = 'data-cskh-contact-care-panel'
	const TAB_SWITCHED_ATTR = 'data-cskh-tab-switched'

	const SALES_SECTIONS = new Set(['dashboard', 'deals', 'forecast', 'forecasts', 'revenue'])
	// Exact action labels only: generic Save and unrelated form actions stay intact.
	const SALES_ACTIONS = new Set([
		'Convert to Deal',
		'Convert to deal',
		'Convert Lead to Deal',
		'Convert lead to deal',
		'Create Deal',
		'Create deal',
		'New Deal',
		'New deal',
		'Change deal status',
		'Change Deal Status',
		'Create your first deal',
		'Chuyển thành Giao dịch CRM',
		'Chuyển Hồ sơ liên hệ thành Giao dịch CRM',
		'Chuyển hồ sơ liên hệ thành Giao dịch CRM',
		'Tạo Giao dịch CRM',
		'Giao dịch CRM mới',
		'Thay đổi trạng thái Giao dịch CRM',
		'Tạo Giao dịch CRM đầu tiên',
	])
	const LEAD_MOBILE_CONVERT_LABELS = new Set(['Convert', 'Chuyển đổi'])
	const SALES_OPTION_TITLES = new Set([
		'Dashboard',
		'Trang tổng quan',
		'Deals',
		'Deal',
		'CRM Deal',
		'Giao dịch CRM',
		'Forecast',
		'Forecasts',
		'Dự báo',
		'Revenue',
		'Doanh thu',
	])
	const SALES_SUBTITLES = new Set(['Deal', 'CRM Deal', 'Giao dịch CRM'])
	const SALES_SETTINGS_PAGES = new Set(['Dashboard', 'Trang tổng quan'])
	const DEAL_TAB_PREFIXES = ['Deals', 'Giao dịch CRM']
	const DEAL_DIALOG_TITLES = new Set([
		'Convert to Deal',
		'Convert to deal',
		'Chuyển thành Giao dịch CRM',
	])

	const ITEM_CLASS =
		'group/sidebar-item flex h-7 items-center rounded transition text-ink-gray-6 hover:bg-surface-gray-2'
	const LINK_BASE_CLASS =
		'flex h-full min-w-0 flex-1 items-center focus:outline-none focus-visible:ring-0 '
	const ICON_BASE_CLASS = 'grid shrink-0 place-items-center'
	const LABEL_BASE_CLASS = 'min-w-0 flex-1 transition-all ease-in-out '
	const SUFFIX_BASE_CLASS = 'flex shrink-0 items-center transition-all ease-in-out '

	function normalizeText(raw) {
		return String(raw || '').replace(/\s+/g, ' ').trim()
	}

	function readBridgeConfig(doc) {
		const script =
			doc.currentScript || doc.querySelector('script[data-cskh-sidebar-bridge]')
		const rawHref = (script && script.getAttribute('data-cskh-href')) || '/cskh'
		const rawLabel = (script && script.getAttribute('data-cskh-label')) || 'Chăm sóc người lao động'
		return {
			href: rawHref.trim() || '/cskh',
			label: rawLabel.trim() || 'Chăm sóc người lao động',
			workerCare: !!(script && script.getAttribute('data-cskh-worker-care') === '1'),
		}
	}

	function isCrmPath(win) {
		if (win.__cskhForceSidebarMount === true) {
			return true
		}
		const pathname = (win.location && win.location.pathname) || ''
		return pathname === '/crm' || pathname.indexOf('/crm/') === 0
	}

	function isLeadDetailPath(pathname) {
		if (typeof pathname !== 'string') return false
		const clean = pathname.replace(/\/+$/, '')
		const parts = clean.split('/')
		if (parts.length !== 4 || parts[0] !== '' || parts[1] !== 'crm' || parts[2] !== 'leads') {
			return false
		}
		return parts[3] !== '' && parts[3] !== 'view' && parts[3] !== 'list'
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

	function isSalesPath(path) {
		if (typeof path !== 'string' || /[?#\\]/.test(path)) return false
		if (path.indexOf('%') !== -1) {
			return path.replace(/\/+$/, '') === '/crm/data-import/doctype/CRM%20Deal'
		}
		if (path.replace(/\/+$/, '') === '/crm') return true
		const parts = path.split('/')
		if (parts[0] !== '' || parts[1] !== 'crm') return false
		if (SALES_SECTIONS.has(parts[2])) return true
		return parts[2] === 'data-import' && parts[3] === 'doctype' && parts[4] === 'CRM Deal'
	}

	function isSalesUrl(win, href) {
		const url = parseHref(win, href)
		return !!(url && url.origin === win.location.origin && isSalesPath(url.pathname))
	}

	function isSalesAction(win, el) {
		if (!el) return false
		const labels = [
			el.textContent,
			el.getAttribute('aria-label'),
			el.getAttribute('title'),
		].map(normalizeText)
		if (labels.some(function (label) { return SALES_ACTIONS.has(label) })) {
			return true
		}
		// MobileLead.vue (<768px) renders header action button labeled `Convert` / `Chuyển đổi`.
		if (
			el.tagName === 'BUTTON' &&
			isLeadDetailPath((win.location && win.location.pathname) || '') &&
			!el.closest('[role="dialog"]') &&
			!el.closest('[role="tablist"]') &&
			labels.some(function (label) { return LEAD_MOBILE_CONVERT_LABELS.has(label) })
		) {
			return true
		}
		return false
	}

	function isSalesControl(win, control) {
		if (!control || control.getAttribute('role') === 'tab') return false
		const href = control.getAttribute('href')
		const url = href && parseHref(win, href)
		if (url && url.origin !== win.location.origin) return false
		return isSalesUrl(win, href) || isSalesAction(win, control)
	}

	function extractOptionParts(optionEl) {
		const spans = optionEl.querySelectorAll('span')
		if (spans.length > 0) {
			const primary = normalizeText(spans[0].textContent)
			const subtitle = spans.length > 1 ? normalizeText(spans[1].textContent) : ''
			return { primary: primary, subtitle: subtitle }
		}
		return {
			primary: normalizeText(
				optionEl.textContent || optionEl.getAttribute('aria-label') || optionEl.getAttribute('title')
			),
			subtitle: '',
		}
	}

	function isSalesOption(optionEl) {
		if (!optionEl) return false
		if (optionEl.getAttribute(BLOCKED_ATTR) === '1') return true
		if (SALES_ACTIONS.has(normalizeText(optionEl.getAttribute('aria-label')))) return true
		const parts = extractOptionParts(optionEl)
		if (SALES_ACTIONS.has(parts.primary)) return true
		if (SALES_SUBTITLES.has(parts.subtitle)) return true
		// Preserve non-Deal records whose title happens to match a section word (e.g. a Lead named "Dashboard").
		if (parts.subtitle && !SALES_SUBTITLES.has(parts.subtitle)) return false
		return SALES_OPTION_TITLES.has(parts.primary)
	}

	function isDealTab(tabEl) {
		if (!tabEl) return false
		if (tabEl.getAttribute(BLOCKED_ATTR) === '1') return true
		const label = normalizeText(
			tabEl.textContent || tabEl.getAttribute('aria-label') || tabEl.getAttribute('title')
		)
		for (let i = 0; i < DEAL_TAB_PREFIXES.length; i += 1) {
			const prefix = DEAL_TAB_PREFIXES[i]
			if (label === prefix || new RegExp('^' + prefix + '(?:\\s+\\d+)?$').test(label)) {
				return true
			}
		}
		return false
	}

	function isTabSelected(tabEl) {
		if (!tabEl) return false
		if (tabEl.getAttribute('aria-selected') === 'true') return true
		const state = tabEl.getAttribute('data-headlessui-state') || ''
		return state.split(/\s+/).indexOf('selected') !== -1
	}

	function isOptionActive(optionEl, activeId) {
		if (!optionEl) return false
		const id = optionEl.getAttribute('id')
		if (activeId && id && id === activeId) return true
		if (optionEl.getAttribute('aria-selected') === 'true') return true
		const state = optionEl.getAttribute('data-headlessui-state') || ''
		return state.split(/\s+/).indexOf('active') !== -1
	}

	function removeElement(el) {
		if (el && el.parentElement) el.parentElement.removeChild(el)
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

	function markElementBlocked(el) {
		setAttrIfChanged(el, BLOCKED_ATTR, '1')
		setAttrIfChanged(el, 'aria-disabled', 'true')
		setAttrIfChanged(el, 'aria-hidden', 'true')
		setAttrIfChanged(el, 'tabindex', '-1')
		setAttrIfChanged(el, 'hidden', 'hidden')
	}

	function removeSalesControls(win, root) {
		const scope = root || win.document
		const controls = scope.querySelectorAll('a[href], button, [role="menuitem"]')
		for (let i = 0; i < controls.length; i += 1) {
			const control = controls[i]
			if (control.getAttribute('role') === 'tab') continue
			if (!isSalesControl(win, control)) continue
			const slotItem = control.closest('[data-slot="sidebar-item"]')
			if (slotItem && slotItem.hasAttribute('data-page')) {
				markElementBlocked(slotItem)
				continue
			}
			// Remove structural sidebar items or action buttons cleanly.
			removeElement(slotItem || control)
		}
	}

	function syncSettingsItems(scope) {
		const settingsItems = scope.querySelectorAll('[data-page]')
		for (let i = 0; i < settingsItems.length; i += 1) {
			const item = settingsItems[i]
			const page = normalizeText(item.getAttribute('data-page'))
			if (SALES_SETTINGS_PAGES.has(page)) {
				markElementBlocked(item)
			}
		}
	}

	function syncSalesOptions(win, scope) {
		const options = scope.querySelectorAll('[role="option"]')
		const touchedGroups = []
		for (let i = 0; i < options.length; i += 1) {
			const opt = options[i]
			if (isSalesOption(opt)) {
				markElementBlocked(opt)
			}
			const parent = opt.parentElement
			if (parent && touchedGroups.indexOf(parent) === -1 && opt.closest('.crm-command-palette')) {
				touchedGroups.push(parent)
			}
		}

		for (let g = 0; g < touchedGroups.length; g += 1) {
			const groupEl = touchedGroups[g]
			const groupOpts = groupEl.querySelectorAll('[role="option"]')
			if (!groupOpts.length) continue
			let hasVisible = false
			for (let j = 0; j < groupOpts.length; j += 1) {
				if (groupOpts[j].getAttribute(BLOCKED_ATTR) !== '1') {
					hasVisible = true
					break
				}
			}
			if (!hasVisible) {
				setAttrIfChanged(groupEl, GROUP_HIDDEN_ATTR, '1')
				setAttrIfChanged(groupEl, 'hidden', 'hidden')
			} else if (groupEl.getAttribute(GROUP_HIDDEN_ATTR) === '1') {
				setAttrIfChanged(groupEl, GROUP_HIDDEN_ATTR, null)
				setAttrIfChanged(groupEl, 'hidden', null)
			}
		}
	}

	function findActivePaletteContext(scope) {
		const palettes = scope.querySelectorAll('.crm-command-palette')
		if (!palettes.length) return null
		const palette = palettes[0]
		const input = palette.querySelector('[role="combobox"]') || palette.querySelector('input')
		const activeId = (input && input.getAttribute('aria-activedescendant')) || ''
		const options = palette.querySelectorAll('[role="option"]')
		let activeOption = null
		for (let i = 0; i < options.length; i += 1) {
			if (isOptionActive(options[i], activeId)) {
				activeOption = options[i]
				break
			}
		}
		return { palette: palette, input: input, options: options, activeOption: activeOption }
	}

	let skippingOption = false
	function nudgePaletteOffBlockedOption(win, scope, keyDirection) {
		if (skippingOption) return
		const ctx = findActivePaletteContext(scope)
		if (!ctx || !ctx.activeOption || !isSalesOption(ctx.activeOption)) return
		if (!ctx.input || typeof ctx.input.dispatchEvent !== 'function' || typeof win.KeyboardEvent !== 'function') {
			return
		}
		let hasAllowed = false
		for (let i = 0; i < ctx.options.length; i += 1) {
			if (!isSalesOption(ctx.options[i])) {
				hasAllowed = true
				break
			}
		}
		if (!hasAllowed) return
		skippingOption = true
		try {
			const maxSteps = ctx.options.length
			for (let step = 0; step < maxSteps; step += 1) {
				ctx.input.dispatchEvent(
					new win.KeyboardEvent('keydown', {
						key: keyDirection || 'ArrowDown',
						bubbles: true,
						cancelable: true,
					})
				)
				const nextCtx = findActivePaletteContext(scope)
				if (!nextCtx || !nextCtx.activeOption || !isSalesOption(nextCtx.activeOption)) {
					break
				}
			}
		} catch (_err) {
			/* ignore synthetic event unsupported in minimal DOM */
		} finally {
			skippingOption = false
		}
	}

	function buildContactCarePanel(doc, config) {
		const panel = doc.createElement('section')
		panel.setAttribute(CARE_PANEL_ATTR, '1')
		panel.className = 'cskh-contact-care-panel'

		const heading = doc.createElement('h3')
		heading.className = 'cskh-contact-care-title'
		heading.textContent = config.label

		const desc = doc.createElement('p')
		desc.className = 'cskh-contact-care-desc'
		desc.textContent =
			'Lịch sử chăm sóc người lao động, kết quả liên hệ và lịch hẹn chăm sóc được quản lý tập trung tại không gian Chăm sóc người lao động.'

		const actions = doc.createElement('div')
		actions.className = 'cskh-contact-care-actions'

		const overviewLink = doc.createElement('a')
		overviewLink.setAttribute('href', config.href)
		overviewLink.className = 'cskh-contact-care-btn is-primary'
		overviewLink.textContent = 'Mở tổng quan chăm sóc'

		const assignedLink = doc.createElement('a')
		assignedLink.setAttribute('href', '/cskh/my-customers')
		assignedLink.className = 'cskh-contact-care-btn'
		assignedLink.textContent = 'Người lao động phụ trách'

		actions.appendChild(overviewLink)
		actions.appendChild(assignedLink)
		panel.appendChild(heading)
		panel.appendChild(desc)
		panel.appendChild(actions)
		return panel
	}

	function isInteractiveFocusElement(el) {
		if (!el || !el.tagName) return false
		if (
			el.tagName === 'INPUT' ||
			el.tagName === 'TEXTAREA' ||
			el.tagName === 'SELECT'
		) {
			return true
		}
		const role = typeof el.getAttribute === 'function' ? el.getAttribute('role') : ''
		if (role === 'combobox' || role === 'textbox' || role === 'searchbox') return true
		return typeof el.getAttribute === 'function' && el.getAttribute('contenteditable') === 'true'
	}

	function syncDealTabs(doc, scope, config) {
		const tablists = scope.querySelectorAll('[role="tablist"]')
		for (let i = 0; i < tablists.length; i += 1) {
			const tablist = tablists[i]
			const tabs = tablist.querySelectorAll('[role="tab"]')
			if (!tabs.length) continue
			const dealTabs = []
			const otherTabs = []
			for (let j = 0; j < tabs.length; j += 1) {
				if (isDealTab(tabs[j])) {
					dealTabs.push(tabs[j])
				} else {
					otherTabs.push(tabs[j])
				}
			}
			if (!dealTabs.length) continue

			if (otherTabs.length > 0) {
				// Organization.vue / MobileContact.vue / MobileOrganization.vue:
				// switch away from Deals once on mount without stealing active input focus.
				const alreadySwitched = tablist.getAttribute(TAB_SWITCHED_ATTR) === '1'
				for (let d = 0; d < dealTabs.length; d += 1) {
					if (!alreadySwitched && isTabSelected(dealTabs[d]) && typeof otherTabs[0].click === 'function') {
						const prevActive = doc.activeElement
						setAttrIfChanged(tablist, TAB_SWITCHED_ATTR, '1')
						otherTabs[0].click()
						if (
							isInteractiveFocusElement(prevActive) &&
							prevActive !== dealTabs[d] &&
							typeof prevActive.focus === 'function'
						) {
							try {
								prevActive.focus({ preventScroll: true })
							} catch (_err) {
								prevActive.focus()
							}
						}
					}
					markElementBlocked(dealTabs[d])
				}
			} else {
				// Contact.vue desktop has Deals as its sole tab in the right pane.
				for (let d = 0; d < dealTabs.length; d += 1) {
					markElementBlocked(dealTabs[d])
				}
				setAttrIfChanged(tablist, 'hidden', 'hidden')
				const container = tablist.parentElement
				if (container) {
					const panels = container.querySelectorAll('[role="tabpanel"]')
					for (let p = 0; p < panels.length; p += 1) {
						setAttrIfChanged(panels[p], 'hidden', 'hidden')
					}
					if (!container.querySelector('[' + CARE_PANEL_ATTR + '="1"]')) {
						container.appendChild(buildContactCarePanel(doc, config))
					}
				}
			}
		}
	}

	function syncSalesDialogs(scope, openWorkerCare) {
		const dialogs = scope.querySelectorAll('[role="dialog"]')
		for (let i = 0; i < dialogs.length; i += 1) {
			const dialog = dialogs[i]
			if (dialog.getAttribute(BLOCKED_ATTR) === '1') continue
			const headings = dialog.querySelectorAll('h1, h2, h3')
			for (let h = 0; h < headings.length; h += 1) {
				if (DEAL_DIALOG_TITLES.has(normalizeText(headings[h].textContent))) {
					markElementBlocked(dialog)
					openWorkerCare()
					return
				}
			}
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
			if (sib.hasAttribute(ITEM_ATTR) || sib.getAttribute(BLOCKED_ATTR) === '1') continue
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
		nav.insertBefore(itemEl, nav.firstChild)
		return itemEl
	}

	function scrubRecentDealsFromStorage(win) {
		try {
			const storage = win.localStorage
			if (!storage || typeof storage.length !== 'number') return
			const keys = []
			for (let i = 0; i < storage.length; i += 1) {
				const key = storage.key(i)
				if (key && key.indexOf('crm-command-palette-recent:') === 0) {
					keys.push(key)
				}
			}
			for (let i = 0; i < keys.length; i += 1) {
				const key = keys[i]
				const raw = storage.getItem(key)
				if (!raw) continue
				const parsed = JSON.parse(raw)
				if (Array.isArray(parsed)) {
					const filtered = parsed.filter(function (item) {
						return !item || item.doctype !== 'CRM Deal'
					})
					if (filtered.length !== parsed.length) {
						storage.setItem(key, JSON.stringify(filtered))
					}
				}
			}
		} catch (_err) {
			/* storage may be unavailable in sandboxed contexts */
		}
	}

	function isNonDealPaletteRecord(item) {
		if (!item || typeof item !== 'object') return false
		if (item.doctype === 'CRM Deal') return false
		if (item.route === 'Deal' || item.route === 'Deals') return false
		return true
	}

	function cloneResponseHeaders(win, sourceRes) {
		const sourceHeaders = sourceRes && sourceRes.headers
		if (typeof win.Headers === 'function') {
			const headers = new win.Headers(sourceHeaders || undefined)
			headers.delete('content-length')
			headers.delete('content-encoding')
			if (!headers.has('content-type')) {
				headers.set('content-type', 'application/json')
			}
			return headers
		}
		const plain = {}
		if (sourceHeaders && typeof sourceHeaders.forEach === 'function') {
			sourceHeaders.forEach(function (val, key) {
				const lower = String(key).toLowerCase()
				if (lower !== 'content-length' && lower !== 'content-encoding') {
					plain[key] = val
				}
			})
		} else if (sourceHeaders && typeof sourceHeaders === 'object') {
			Object.keys(sourceHeaders).forEach(function (key) {
				const lower = String(key).toLowerCase()
				if (lower !== 'content-length' && lower !== 'content-encoding') {
					plain[key] = sourceHeaders[key]
				}
			})
		}
		const hasContentType = Object.keys(plain).some(function (k) {
			return k.toLowerCase() === 'content-type'
		})
		if (!hasContentType) {
			plain['content-type'] = 'application/json'
		}
		return plain
	}

	function buildJsonResponse(win, bodyObj, sourceRes) {
		const jsonText = JSON.stringify(bodyObj)
		const headers = cloneResponseHeaders(win, sourceRes)
		const status = (sourceRes && typeof sourceRes.status === 'number') ? sourceRes.status : 200
		const statusText = (sourceRes && typeof sourceRes.statusText === 'string') ? sourceRes.statusText : 'OK'
		if (typeof win.Response === 'function') {
			return new win.Response(jsonText, {
				status: status,
				statusText: statusText,
				headers: headers,
			})
		}
		return {
			ok: status >= 200 && status < 300,
			status: status,
			statusText: statusText,
			headers: headers,
			clone: function () { return buildJsonResponse(win, bodyObj, sourceRes) },
			json: function () { return Promise.resolve(bodyObj) },
			text: function () { return Promise.resolve(jsonText) },
		}
	}

	function installSidebarBridge(win) {
		if (!win || !win.document) return null
		if (win.__cskhSidebarBridge) return win.__cskhSidebarBridge

		const doc = win.document
		const config = readBridgeConfig(doc)
		let navigatingAway = false
		function openWorkerCare() {
			if (navigatingAway) return
			navigatingAway = true
			// Never forward the old route's query/hash or accept an external target.
			win.location.assign('/cskh')
		}

		function guardCurrentRoute() {
			if (isSalesPath(win.location.pathname || '')) {
				openWorkerCare()
				return true
			}
			return false
		}

		let dataGuardsInstalled = false
		function installDataGuards() {
			if (dataGuardsInstalled || !config.workerCare || !isCrmPath(win)) return
			dataGuardsInstalled = true
			scrubRecentDealsFromStorage(win)
			if (typeof win.fetch !== 'function') return
			const originalFetch = win.fetch
			win.fetch = function (input) {
				if (!config.workerCare || !isCrmPath(win)) {
					return originalFetch.apply(this, arguments)
				}
				const rawUrl =
					typeof input === 'string'
						? input
						: (input && typeof input.url === 'string' ? input.url : '')
				const parsed = parseHref(win, rawUrl)
				const apiPath = parsed && parsed.origin === win.location.origin ? parsed.pathname : ''

				if (
					apiPath !== '/api/method/crm.api.command_palette.search' &&
					apiPath !== '/api/method/crm.api.views.get_views'
				) {
					return originalFetch.apply(this, arguments)
				}

				return originalFetch.apply(this, arguments).then(function (res) {
					if (!res || !res.ok || typeof res.json !== 'function') return res
					if (res.headers && typeof res.headers.get === 'function') {
						const ct = String(res.headers.get('content-type') || '').toLowerCase()
						if (ct && ct.indexOf('json') === -1) return res
					}
					const clone = typeof res.clone === 'function' ? res.clone() : res
					return clone.json().then(function (payload) {
						if (!payload || typeof payload !== 'object') return res
						if (
							apiPath === '/api/method/crm.api.command_palette.search' &&
							payload.message &&
							typeof payload.message === 'object'
						) {
							const msg = payload.message
							let changed = false
							const nextMsg = Object.assign({}, msg)
							;['matches', 'recent', 'upcoming'].forEach(function (bucket) {
								if (Array.isArray(msg[bucket])) {
									const filtered = msg[bucket].filter(isNonDealPaletteRecord)
									if (filtered.length !== msg[bucket].length) {
										changed = true
										nextMsg[bucket] = filtered
									}
								}
							})
							if (!changed) return res
							return buildJsonResponse(win, Object.assign({}, payload, { message: nextMsg }), res)
						}
						if (apiPath === '/api/method/crm.api.views.get_views' && Array.isArray(payload.message)) {
							const nextViews = payload.message.filter(function (v) {
								return !v || (v.dt !== 'CRM Deal' && v.route_name !== 'Deals')
							})
							if (nextViews.length === payload.message.length) return res
							return buildJsonResponse(win, Object.assign({}, payload, { message: nextViews }), res)
						}
						return res
					}).catch(function () {
						return res
					})
				})
			}
		}

		function interceptPointerSalesTarget(event) {
			if (!event || !event.target || typeof event.target.closest !== 'function') return
			const optionTarget = event.target.closest('[role="option"]')
			if (optionTarget && isSalesOption(optionTarget)) {
				event.preventDefault()
				event.stopImmediatePropagation()
				openWorkerCare()
				return
			}
			const tabTarget = event.target.closest('[role="tab"]')
			if (tabTarget && isDealTab(tabTarget)) {
				event.preventDefault()
				event.stopImmediatePropagation()
				return
			}
			const pageTarget = event.target.closest('[data-page]')
			if (pageTarget && SALES_SETTINGS_PAGES.has(normalizeText(pageTarget.getAttribute('data-page')))) {
				event.preventDefault()
				event.stopImmediatePropagation()
				return
			}
			const controlTarget = event.target.closest('a[href], button, [role="menuitem"]')
			if (!controlTarget || !isSalesControl(win, controlTarget)) return
			event.preventDefault()
			event.stopImmediatePropagation()
			openWorkerCare()
		}

		function interceptKeyboardSalesTarget(event) {
			if (!event || skippingOption) return
			const key = event.key
			if (key === 'Enter') {
				const target = event.target
				if (target && typeof target.closest === 'function') {
					const opt = target.closest('[role="option"]')
					if (opt && isSalesOption(opt)) {
						event.preventDefault()
						event.stopImmediatePropagation()
						openWorkerCare()
						return
					}
					const ctrl = target.closest('a[href], button, [role="menuitem"]')
					if (ctrl && isSalesControl(win, ctrl)) {
						event.preventDefault()
						event.stopImmediatePropagation()
						openWorkerCare()
						return
					}
				}
				const ctx = findActivePaletteContext(doc)
				if (ctx && ctx.activeOption && isSalesOption(ctx.activeOption)) {
					event.preventDefault()
					event.stopImmediatePropagation()
					openWorkerCare()
				}
				return
			}
			if (key === 'ArrowDown' || key === 'ArrowUp') {
				if (typeof win.setTimeout === 'function') {
					win.setTimeout(function () {
						nudgePaletteOffBlockedOption(win, doc, key)
					}, 0)
				}
			}
		}

		// Router links use pushState/replaceState without a network request. Install
		// before Vue boots, and also cover browser Back/Forward and direct old links.
		let guardsInstalled = false
		function installNavigationGuards() {
			if (guardsInstalled || !config.workerCare || !isCrmPath(win)) return
			guardsInstalled = true
			installDataGuards()
			if (win.history) {
				;['pushState', 'replaceState'].forEach(function (method) {
					const original = win.history[method]
					if (typeof original !== 'function') return
					win.history[method] = function (state, title, url) {
						if (url !== undefined && url !== null && isSalesUrl(win, String(url))) {
							openWorkerCare()
							return
						}
						return original.apply(this, arguments)
					}
				})
			}
			if (typeof win.addEventListener === 'function') {
				win.addEventListener('popstate', guardCurrentRoute)
				win.addEventListener('pageshow', guardCurrentRoute)
			}
			doc.addEventListener('click', interceptPointerSalesTarget, true)
			doc.addEventListener('mousedown', interceptPointerSalesTarget, true)
			doc.addEventListener('keydown', interceptKeyboardSalesTarget, true)
			guardCurrentRoute()
		}
		installNavigationGuards()

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
			if (!config.workerCare || !isCrmPath(win) || navigatingAway || guardCurrentRoute()) return 0
			const scope = root || doc
			ensureThemeStylesheet()
			removeSalesControls(win, scope)
			syncSettingsItems(scope)
			syncSalesOptions(win, scope)
			syncDealTabs(doc, scope, config)
			syncSalesDialogs(scope, openWorkerCare)
			const navs = findPrimaryNavs(win, scope)
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
		function isSelfOwnedNode(node) {
			if (!node || typeof node.closest !== 'function') return false
			return !!(
				node.closest('[' + ITEM_ATTR + '="1"]') ||
				node.closest('[' + CARE_PANEL_ATTR + '="1"]')
			)
		}
		function startObserver() {
			if (observer || typeof win.MutationObserver !== 'function') return
			// Observe document.body so portals (CommandPalette, MobileSidebar, Settings, Modals) are covered.
			const target = doc.body || doc.documentElement
			if (!target) return
			observer = new win.MutationObserver(function (mutations) {
				if (Array.isArray(mutations) && mutations.length > 0) {
					let relevant = false
					for (let i = 0; i < mutations.length; i += 1) {
						const m = mutations[i]
						if (m.type === 'childList') {
							if (m.addedNodes && m.addedNodes.length > 0 && !isSelfOwnedNode(m.target)) {
								relevant = true
								break
							}
						} else if (m.type === 'attributes' && !isSelfOwnedNode(m.target)) {
							relevant = true
							break
						}
					}
					if (!relevant) return
				}
				scheduleSync()
			})
			observer.observe(target, {
				childList: true,
				subtree: true,
				attributes: true,
				attributeFilter: [
					'class',
					'href',
					'title',
					'aria-label',
					'aria-selected',
				],
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
			isSalesPath: isSalesPath,
			isSalesOption: isSalesOption,
			guardCurrentRoute: guardCurrentRoute,
			activateWorkerCare: function (capability) {
				if (!capability || capability.enabled !== true) return false
				config.workerCare = true
				config.href = '/cskh'
				if (capability.label) config.label = String(capability.label)
				installNavigationGuards()
				syncAll(doc)
				return navigatingAway
			},
		}
		win.__cskhSidebarBridge = api
		return api
	}

	if (typeof window !== 'undefined') {
		installSidebarBridge(window)
	}
})()
