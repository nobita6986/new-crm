#!/usr/bin/env node
/**
 * Unit tests for `apps/crm_cskh/crm_cskh/public/cskh/setup-wizard.js` (synced from 5d05066).
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import vm from 'node:vm'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const REPO_ROOT = path.resolve(path.dirname(__filename), '..', '..')
const SETUP_WIZARD_JS = fs.readFileSync(
	path.join(REPO_ROOT, 'apps', 'crm_cskh', 'crm_cskh', 'public', 'cskh', 'setup-wizard.js'),
	'utf-8'
)

test('Defaults Setup Wizard welcome slide country to Vietnam, populates VND & timezone, and hides country field', () => {
	let countryHidden = false
	let origOnloadCalled = false
	let origInitCalled = false

	const welcomeSlide = {
		name: 'welcome',
		onload(slide) {
			origOnloadCalled = Boolean(slide)
			return 'onload-ok'
		},
		initialize_fields(slide) {
			origInitCalled = Boolean(slide)
			return 'init-ok'
		},
	}

	const frappe = {
		setup: {
			slides_settings: [welcomeSlide],
			data: {
				regional_data: {
					country_info: {
						Vietnam: {
							currency: 'VND',
							timezones: ['Asia/Ho_Chi_Minh'],
						},
					},
				},
			},
		},
		wizard: {
			values: {},
		},
	}

	const ctx = { frappe }
	ctx.window = ctx
	vm.runInNewContext(SETUP_WIZARD_JS, ctx)

	const slideObj = {
		get_field(name) {
			assert.equal(name, 'country')
			return {
				$wrapper: {
					hide() {
						countryHidden = true
					},
				},
			}
		},
	}

	assert.equal(welcomeSlide.onload(slideObj), 'onload-ok')
	assert.equal(countryHidden, true)
	assert.equal(origOnloadCalled, true)

	assert.equal(welcomeSlide.initialize_fields(slideObj), 'init-ok')
	assert.equal(origInitCalled, true)
	assert.equal(frappe.wizard.values.country, 'Vietnam')
	assert.equal(frappe.wizard.values.currency, 'VND')
	assert.equal(frappe.wizard.values.timezone, 'Asia/Ho_Chi_Minh')
})

test('No-ops cleanly when frappe.setup slides are absent', () => {
	const ctx = { frappe: {} }
	ctx.window = ctx
	assert.doesNotThrow(() => vm.runInNewContext(SETUP_WIZARD_JS, ctx))
})
