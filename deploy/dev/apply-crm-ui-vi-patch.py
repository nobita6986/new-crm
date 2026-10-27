#!/usr/bin/env python3
"""Apply the small Vietnamese-label patch to the pinned CRM DEV dependency.

This edits only frappe-ui under the DEV bench's node_modules. The reproducible
patch lives in this repo; no CRM/Frappe source checkout is modified.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


PINNED_FRAPPE_UI = "1.0.0-beta.29"

REPLACEMENTS = {
	"src/components/ListView/ListFooter.vue": {
		'label="Load More"': ':label="__(\'Load More\')"',
		"<div>of</div>": "<div>{{ __('of') }}</div>",
	},
	"frappe/DataImport/DataImportList.vue": {
		"\t\t\t\t\tData Import\n\t\t\t\t</div>": "\t\t\t\t\t{{ __('Data Import') }}\n\t\t\t\t</div>",
		"\t\t\t\t\tImport data into your system using CSV files.\n\t\t\t\t</div>": "\t\t\t\t\t{{ __('Import data into your system using CSV files.') }}\n\t\t\t\t</div>",
		"\n                Import\n            </Button>": "\n                {{ __('Import') }}\n            </Button>",
		'placeholder="Search imported files"': ':placeholder="__(\'Search imported files\')"',
		"\n                        Name\n                    </div>": "\n                        {{ __('Name') }}\n                    </div>",
		"\n                        Status\n                    </div>": "\n                        {{ __('Status') }}\n                    </div>",
		"\n                    Load More\n                </Button>": "\n                    {{ __('Load More') }}\n                </Button>",
		"No data imports found.": "{{ __('No data imports found.') }}",
		"title: 'New Data Import'": "title: __('New Data Import')",
		"label: 'Continue'": "label: __('Continue')",
		'label="Choose a Document Type to import"': ':label="__(\'Choose a Document Type to import\')"',
		"return options.map(option => ({ label: option, value: option }))": "return options.map(option => ({ label: __(option), value: option }))",
	},
	"frappe/DataImport/PreviewStep.vue": {
		"{ label: 'All', value: 'all' }": "{ label: __('All'), value: 'all' }",
		"{ label: 'Successful', value: 'successful' }": "{ label: __('Successful'), value: 'successful' }",
		"{ label: 'Failed', value: 'failed' }": "{ label: __('Failed'), value: 'failed' }",
	},
	"frappe/DataImport/DataImport.vue": {
		"label: 'Data Import'": "label: __('Data Import')",
		"label: `Importing ${doctypeTitle.value}`": "label: __('Importing {0}', [doctypeTitle.value])",
	},
	"frappe/Onboarding/GettingStartedBanner.vue": {
		"{{ 'Getting started' }}": "{{ __('Getting started') }}",
		'{{ `${stepsCompleted}/${totalSteps} steps` }}': "{{ __('{0}/{1} steps', [stepsCompleted, totalSteps]) }}",
		"{{ 'You are all set' }}": "{{ __('You are all set') }}",
		"{{ 'All steps are completed successfully' }}": "{{ __('All steps are completed successfully') }}",
		':label="stepsCompleted == 0 ? \'Start now\' : \'Continue\'"':
			':label="__(stepsCompleted == 0 ? \'Start now\' : \'Continue\')"',
	},
	"frappe/Onboarding/OnboardingSteps.vue": {
		"{{ 'Welcome to ' + title }}": "{{ __('Welcome to {0}', [title]) }}",
		'{{ `${stepsCompleted}/${totalSteps} steps completed` }}': "{{ __('{0}/{1} steps completed', [stepsCompleted, totalSteps]) }}",
		':label="`${completedPercentage}% completed`"': ':label="__(\'{0}% completed\', [completedPercentage])"',
		':label="\'Reset all\'"': ':label="__(\'Reset all\')"',
		':label="\'Skip all\'"': ':label="__(\'Skip all\')"',
		':label="\'Skip\'"': ':label="__(\'Skip\')"',
		':label="\'Reset\'"': ':label="__(\'Reset\')"',
		'return `You need to complete "${dependsOnStep.title}" first.`':
			'return __(\'You need to complete "{0}" first.\', [dependsOnStep.title])',
	},
	"frappe/Help/HelpModal.vue": {
		"return 'Getting started'": "return __('Getting started')",
		"return 'Help center'": "return __('Help center')",
		"label: 'Reset onboarding steps'": "label: __('Reset onboarding steps')",
		"label: 'Help centre'": "label: __('Help centre')",
		"label: 'Getting started'": "label: __('Getting started')",
	},
	"src/components/Charts/donutChartOptions.ts": {
		"name: config.categoryColumn,": "name: config.categoryLabel || config.categoryColumn,",
	},
}


def patch_file(path: Path, replacements: dict[str, str]) -> bool:
	text = path.read_text(encoding="utf-8")
	changed = False
	for index, (old, new) in enumerate(replacements.items()):
		# Protect already-patched text while checking for any remaining source
		# anchor. Some replacements intentionally contain their original text.
		placeholder = f"\x00T1B_PATCH_{index}\x00"
		protected = text.replace(new, placeholder)
		count = protected.count(old)
		if count == 0:
			if placeholder in protected:
				text = protected.replace(placeholder, new)
				continue
			raise RuntimeError(f"Pinned source no longer matches expected text: {path} :: {old!r}")
		if count != 1:
			raise RuntimeError(f"Expected one match, found {count}: {path} :: {old!r}")
		text = protected.replace(old, new, 1).replace(placeholder, new)
		changed = True
	if changed:
		path.write_text(text, encoding="utf-8", newline="\n")
	return changed


def main() -> int:
	if len(sys.argv) != 2:
		print("Usage: apply-crm-ui-vi-patch.py <crm-frontend-dir>", file=sys.stderr)
		return 2

	frontend = Path(sys.argv[1]).resolve()
	package_path = frontend / "node_modules/frappe-ui/package.json"
	if not package_path.is_file():
		print(f"frappe-ui dependencies are not installed: {package_path}", file=sys.stderr)
		return 2

	version = json.loads(package_path.read_text(encoding="utf-8"))["version"]
	if version != PINNED_FRAPPE_UI:
		print(f"Refusing unreviewed frappe-ui version {version}; expected {PINNED_FRAPPE_UI}", file=sys.stderr)
		return 2

	changed_any = False
	for relative, replacements in REPLACEMENTS.items():
		path = frontend / "node_modules/frappe-ui" / relative
		if not path.is_file():
			print(f"Missing pinned frappe-ui file: {path}", file=sys.stderr)
			return 2
		changed = patch_file(path, replacements)
		changed_any = changed_any or changed
		print(f"{'patched' if changed else 'already patched'}: {relative}")

	print("Vietnamese CRM UI labels are ready." if changed_any else "Vietnamese CRM UI patch already applied.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
