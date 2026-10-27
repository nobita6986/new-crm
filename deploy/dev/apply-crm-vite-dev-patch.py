#!/usr/bin/env python3
"""Apply pinned, development-only fixes to CRM's Vite dev server.

The CRM source itself is fetched at a pinned revision by bootstrap.sh. This
script keeps local development behavior reproducible without changing the
upstream Git checkout or weakening Frappe's CSRF checks.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PINNED_CRM_SHA = "0d3910b456ef4fb913e77b29db011b3debc4f778"


def patch_once(path: Path, old: str, new: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return False
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one patch anchor in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: apply-crm-vite-dev-patch.py <crm-app-dir>", file=sys.stderr)
        return 2

    crm_root = Path(sys.argv[1]).resolve()
    actual_sha = subprocess.check_output(
        ["git", "-C", str(crm_root), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual_sha != PINNED_CRM_SHA:
        print(
            f"Refusing unreviewed CRM revision {actual_sha}; expected {PINNED_CRM_SHA}",
            file=sys.stderr,
        )
        return 2

    config = crm_root / "frontend/vite.config.js"
    main_js = crm_root / "frontend/src/main.js"
    if not config.is_file() or not main_js.is_file():
        print("Pinned CRM Vite source files are missing", file=sys.stderr)
        return 2

    translations: dict[Path, tuple[str, str]] = {
        crm_root / "frontend/src/components/ListViews/LeadsListView.vue": (
            """function getLabel(label, column) {
  if (column.type === 'Duration') return formatDuration(label)
  if (column.options && isTranslatable(column.options)) return __(label)
  return label
}""",
            """function getLabel(label, column) {
  if (column.type === 'Duration') return formatDuration(label)
  if (column.key === 'status') return __(label)
  if (column.options && isTranslatable(column.options)) return __(label)
  return label
}""",
        ),
        crm_root / "frontend/src/components/ListViews/DealsListView.vue": (
            """function getLabel(label, column) {
  if (column.type === 'Duration') return formatDuration(label)
  if (column.options && isTranslatable(column.options)) return __(label)
  return label
}""",
            """function getLabel(label, column) {
  if (column.type === 'Duration') return formatDuration(label)
  if (column.key === 'status') return __(label)
  if (column.options && isTranslatable(column.options)) return __(label)
  return label
}""",
        ),
        crm_root / "frontend/src/components/ViewControls.vue": (
            "label = `${routeName} - ${label}`",
            "label = `${__(routeName)} - ${label}`",
        ),
        crm_root / "frontend/src/components/ListViews/TasksListView.vue": (
            """function getLabel(label, column) {
  if (column.type === 'Duration') return formatDuration(label)
  if (column.options && isTranslatable(column.options)) return __(label)
  return label
}""",
            """function getLabel(label, column) {
  if (column.type === 'Duration') return formatDuration(label)
  // These are fixed CRM enum values; keep database values in English and
  // translate only their presentation in the Vietnamese UI.
  if (['status', 'priority'].includes(column.key)) return __(label)
  if (column.options && isTranslatable(column.options)) return __(label)
  return label
}""",
        ),
        crm_root / "frontend/src/components/ListViews/CallLogsListView.vue": (
            """function getLabel(label, column) {
  if (column.options && isTranslatable(column.options)) return __(label)
  return label
}""",
            """function getLabel(label, column) {
  // Call direction is an enum; translate its display without changing stored data.
  if (column.key === 'type') return __(label)
  if (column.options && isTranslatable(column.options)) return __(label)
  return label
}""",
        ),
        crm_root / "frontend/src/components/Dashboard/DashboardItem.vue": (
            """:config="item.data""",
            """:config="chartConfig""",
        ),
    }
    for path, (old, new) in translations.items():
        if path.name == "DashboardItem.vue":
            text = path.read_text(encoding="utf-8")
            old_count = text.count(old)
            new_count = text.count(new)
            if new_count == 3 and old_count == 0:
                has_chart_patch = (
                    "function localizeChartData(data, config)" in text
                    and "suffix: localizeChartSuffix(data.suffix)" in text
                    and "categoryLabel: localizeChartLabel(data.categoryColumn)" in text
                )
                old_label_helper = """function localizeChartLabel(value) {
  return typeof value === 'string' ? __(value) : value
}"""
                previous_label_helper = """function localizeChartLabel(value) {
  if (typeof value !== 'string') return value
  const percentSuffix = value.match(/^(.*?)\\s+(\\(\\d+%\\))$/)
  if (percentSuffix) return `${__(percentSuffix[1])} ${percentSuffix[2]}`
  return __(value)
}"""
                new_label_helper = """function localizeChartLabel(value) {
  if (typeof value !== 'string') return value
  const percentSuffix = value.match(/^(.*?)\\s+(\\(\\d+%\\))$/)
  const label = percentSuffix ? percentSuffix[1] : value
  const translations = {
    stage: 'Giai đoạn',
    source: 'Nguồn',
    'Web Form': 'Biểu mẫu web',
    Exhibition: 'Triển lãm',
    'Mass Mailing': 'Gửi thư hàng loạt',
    'Walk In': 'Khách đến trực tiếp',
    Advertisement: 'Quảng cáo',
  }
  const translated = translations[label] || __(label)
  return percentSuffix ? `${translated} ${percentSuffix[2]}` : translated
}"""
                if has_chart_patch and new_label_helper in text:
                    upgraded = text.replace(
                        "{ ...row, [categoryKey]: __(value) }",
                        "{ ...row, [categoryKey]: localizeChartLabel(value) }",
                    )
                    if upgraded != text:
                        path.write_text(upgraded, encoding="utf-8", newline="\n")
                    print(f"already patched: {path.relative_to(crm_root)}")
                    continue
                if has_chart_patch and (
                    old_label_helper in text or previous_label_helper in text
                ):
                    helper_to_replace = (
                        previous_label_helper
                        if previous_label_helper in text
                        else old_label_helper
                    )
                    text = text.replace(helper_to_replace, new_label_helper, 1)
                    text = text.replace(
                        "{ ...row, [categoryKey]: __(value) }",
                        "{ ...row, [categoryKey]: localizeChartLabel(value) }",
                    )
                    path.write_text(text, encoding="utf-8", newline="\n")
                    print(
                        f"upgraded chart label localization: {path.relative_to(crm_root)}"
                    )
                    continue
                partial_chart_config = """function localizeChartLabel(value) {
  if (typeof value !== 'string') return value
  const percentSuffix = value.match(/^(.*?)\\s+(\\(\\d+%\\))$/)
  const label = percentSuffix ? percentSuffix[1] : value
  const translations = {
    stage: 'Giai đoạn',
    source: 'Nguồn',
    'Web Form': 'Biểu mẫu web',
    Exhibition: 'Triển lãm',
    'Mass Mailing': 'Gửi thư hàng loạt',
    'Walk In': 'Khách đến trực tiếp',
    Advertisement: 'Quảng cáo',
  }
  const translated = translations[label] || __(label)
  return percentSuffix ? `${translated} ${percentSuffix[2]}` : translated
}

const chartConfig = computed(() => {
  const data = props.item.data
  if (!data) return data
  return {
    ...data,
    title: localizeChartLabel(data.title),
    subtitle: localizeChartLabel(data.subtitle),
    xAxis: data.xAxis
      ? { ...data.xAxis, title: localizeChartLabel(data.xAxis.title) }
      : data.xAxis,
    yAxis: data.yAxis
      ? { ...data.yAxis, title: localizeChartLabel(data.yAxis.title) }
      : data.yAxis,
    series: data.series?.map((series) => ({
      ...series,
      name: localizeChartLabel(series.name),
    })),
  }
})"""
                full_chart_config = """function localizeChartLabel(value) {
  if (typeof value !== 'string') return value
  const percentSuffix = value.match(/^(.*?)\\s+(\\(\\d+%\\))$/)
  const label = percentSuffix ? percentSuffix[1] : value
  const translations = {
    stage: 'Giai đoạn',
    source: 'Nguồn',
    'Web Form': 'Biểu mẫu web',
    Exhibition: 'Triển lãm',
    'Mass Mailing': 'Gửi thư hàng loạt',
    'Walk In': 'Khách đến trực tiếp',
    Advertisement: 'Quảng cáo',
  }
  const translated = translations[label] || __(label)
  return percentSuffix ? `${translated} ${percentSuffix[2]}` : translated
}

function localizeChartData(data, config) {
  const categoryKey = config.categoryColumn || config.xAxis?.key
  if (!categoryKey || !Array.isArray(data)) return data
  return data.map((row) => {
    const value = row?.[categoryKey]
    return typeof value === 'string' ? { ...row, [categoryKey]: localizeChartLabel(value) } : row
  })
}

function localizeChartSuffix(value) {
  return value === ' days' ? ' ngày' : value
}

const chartConfig = computed(() => {
  const data = props.item.data
  if (!data) return data
  return {
    ...data,
    data: localizeChartData(data.data, data),
    title: localizeChartLabel(data.title),
    subtitle: localizeChartLabel(data.subtitle),
    tooltip: localizeChartLabel(data.tooltip),
    categoryLabel: localizeChartLabel(data.categoryColumn),
    suffix: localizeChartSuffix(data.suffix),
    deltaSuffix: localizeChartSuffix(data.deltaSuffix),
    xAxis: data.xAxis
      ? { ...data.xAxis, title: localizeChartLabel(data.xAxis.title) }
      : data.xAxis,
    yAxis: data.yAxis
      ? { ...data.yAxis, title: localizeChartLabel(data.yAxis.title) }
      : data.yAxis,
    series: data.series?.map((series) => ({
      ...series,
      name: localizeChartLabel(series.name),
    })),
  }
})"""
                if partial_chart_config not in text:
                    raise RuntimeError(
                        f"Partially patched chart config differs from expected source: {path}"
                    )
                text = text.replace(partial_chart_config, full_chart_config, 1)
                path.write_text(text, encoding="utf-8", newline="\n")
                print(f"completed partial patch: {path.relative_to(crm_root)}")
                continue
            if old_count != 3 or new_count:
                raise RuntimeError(
                    f"Expected three chart config bindings in {path}, found old={old_count}, new={new_count}"
                )
            text = text.replace(old, new)
            chart_import = "import { AxisChart, DonutChart, NumberChart, Tooltip } from 'frappe-ui'"
            chart_import_new = chart_import + "\nimport { computed } from 'vue'"
            if chart_import not in text:
                raise RuntimeError(
                    f"Pinned DashboardItem import anchor missing: {path}"
                )
            text = text.replace(chart_import, chart_import_new, 1)
            script_anchor = """defineProps({
  index: { type: Number, required: true },
  item: { type: Object, required: true },
  editing: { type: Boolean, default: false },
})"""
            script_replacement = """const props = defineProps({
  index: { type: Number, required: true },
  item: { type: Object, required: true },
  editing: { type: Boolean, default: false },
})
const index = computed(() => props.index)
const item = computed(() => props.item)
const editing = computed(() => props.editing)

function localizeChartLabel(value) {
  if (typeof value !== 'string') return value
  const percentSuffix = value.match(/^(.*?)\\s+(\\(\\d+%\\))$/)
  const label = percentSuffix ? percentSuffix[1] : value
  const translations = {
    stage: 'Giai đoạn',
    source: 'Nguồn',
    'Web Form': 'Biểu mẫu web',
    Exhibition: 'Triển lãm',
    'Mass Mailing': 'Gửi thư hàng loạt',
    'Walk In': 'Khách đến trực tiếp',
    Advertisement: 'Quảng cáo',
  }
  const translated = translations[label] || __(label)
  return percentSuffix ? `${translated} ${percentSuffix[2]}` : translated
}

function localizeChartData(data, config) {
  const categoryKey = config.categoryColumn || config.xAxis?.key
  if (!categoryKey || !Array.isArray(data)) return data
  return data.map((row) => {
    const value = row?.[categoryKey]
    return typeof value === 'string' ? { ...row, [categoryKey]: localizeChartLabel(value) } : row
  })
}

function localizeChartSuffix(value) {
  return value === ' days' ? ' ngày' : value
}

const chartConfig = computed(() => {
  const data = props.item.data
  if (!data) return data
  return {
    ...data,
    data: localizeChartData(data.data, data),
    title: localizeChartLabel(data.title),
    subtitle: localizeChartLabel(data.subtitle),
    tooltip: localizeChartLabel(data.tooltip),
    categoryLabel: localizeChartLabel(data.categoryColumn),
    suffix: localizeChartSuffix(data.suffix),
    deltaSuffix: localizeChartSuffix(data.deltaSuffix),
    xAxis: data.xAxis
      ? { ...data.xAxis, title: localizeChartLabel(data.xAxis.title) }
      : data.xAxis,
    yAxis: data.yAxis
      ? { ...data.yAxis, title: localizeChartLabel(data.yAxis.title) }
      : data.yAxis,
    series: data.series?.map((series) => ({
      ...series,
      name: localizeChartLabel(series.name),
    })),
  }
})"""
            if script_anchor not in text:
                raise RuntimeError(
                    f"Pinned DashboardItem script anchor missing: {path}"
                )
            text = text.replace(script_anchor, script_replacement, 1)
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"patched: {path.relative_to(crm_root)}")
        else:
            changed = patch_once(path, old, new)
            print(
                f"{'patched' if changed else 'already patched'}: {path.relative_to(crm_root)}"
            )

    data_import = crm_root / "frontend/src/pages/DataImport.vue"
    for old, new in (
        ("title: 'Leads',", "title: __('Leads'),"),
        ("title: 'Deals',", "title: __('Deals'),"),
    ):
        changed = patch_once(data_import, old, new)
        print(
            f"{'patched' if changed else 'already patched'}: {data_import.relative_to(crm_root)} ({new})"
        )

    # These static labels bypass the translation helper in the pinned CRM
    # source. Keep their stored route/field values unchanged and translate only
    # the display labels.
    static_label_patches = {
        crm_root / "frontend/src/utils/navigation.js": (
            (
                "{ label: 'Deals', icon: DealsIcon, route: 'Deals' },",
                "{ label: __('Deals'), icon: DealsIcon, route: 'Deals' },",
            ),
            (
                "{ label: 'Call Logs', icon: PhoneIcon, route: 'Call Logs' },",
                "{ label: __('Call Logs'), icon: PhoneIcon, route: 'Call Logs' },",
            ),
        ),
        crm_root / "frontend/src/utils/model.js": (
            ("label: 'Last Modified',", "label: __('Last Modified'),"),
        ),
        crm_root / "frontend/src/pages/Contact.vue": (
            ("label: 'Deals',", "label: __('Deals'),"),
        ),
        crm_root / "frontend/src/pages/Organization.vue": (
            ("label: 'Deals',", "label: __('Deals'),"),
        ),
    }
    for path, replacements in static_label_patches.items():
        for old, new in replacements:
            changed = patch_once(path, old, new)
            print(
                f"{'patched' if changed else 'already patched'}: {path.relative_to(crm_root)} ({new})"
            )

    donut_options = (
        crm_root
        / "frontend/node_modules/frappe-ui/src/components/Charts/donutChartOptions.ts"
    )
    chart_label_anchor = "import { DonutChartConfig } from './types'\n"
    chart_label_patch = """import { DonutChartConfig } from './types'

const vietnameseChartLabels: Record<string, string> = {
  stage: 'Giai đoạn',
  source: 'Nguồn',
  'Web Form': 'Biểu mẫu web',
  Exhibition: 'Triển lãm',
  'Mass Mailing': 'Gửi thư hàng loạt',
  'Walk In': 'Khách đến trực tiếp',
  Advertisement: 'Quảng cáo',
}

function localizeChartLabel(value: string) {
  return vietnameseChartLabels[value] || value
}
"""
    patch_once(donut_options, chart_label_anchor, chart_label_patch)
    donut_text = donut_options.read_text(encoding="utf-8")
    donut_text_before_formatters = donut_text
    old_percentage = "return `${name} (${percentage.toFixed(0)}%)`"
    new_percentage = "return `${localizeChartLabel(name)} (${percentage.toFixed(0)}%)`"
    percentage_count = donut_text.count(old_percentage)
    if percentage_count not in (0, 2):
        raise RuntimeError(
            f"Expected two donut label formatters, found {percentage_count}"
        )
    donut_text = donut_text.replace(old_percentage, new_percentage)
    if donut_text.count(new_percentage) != 2:
        raise RuntimeError("Donut chart percentage label patch is incomplete")
    old_tooltip = "<div>${p.name}</div>"
    new_tooltip = "<div>${localizeChartLabel(p.name)}</div>"
    if old_tooltip in donut_text:
        donut_text = donut_text.replace(old_tooltip, new_tooltip, 1)
    elif new_tooltip not in donut_text:
        raise RuntimeError("Donut chart tooltip label patch is incomplete")
    if donut_text != donut_text_before_formatters:
        donut_options.write_text(donut_text, encoding="utf-8", newline="\n")
        print(f"patched chart label display: {donut_options.relative_to(crm_root)}")
    else:
        print(
            f"already patched chart label display: {donut_options.relative_to(crm_root)}"
        )

    proxy_new = (
        "      frappeProxy: {\n"
        "        source: '^/(desk|app|login|api|assets|files|private|cskh)',\n"
        "      },"
    )
    proxy_changed = patch_once(config, "      frappeProxy: true,", proxy_new)
    config_text = config.read_text(encoding="utf-8")
    if not proxy_changed and proxy_new not in config_text:
        raise RuntimeError("Pinned Vite proxy block differs from the expected source")

    optimizer_new = "      exclude: [\n        'frappe-ui/editor',\n      ],"
    optimizer_changed = patch_once(
        config,
        "      ],\n    },\n    server: {",
        optimizer_new + "\n    },\n    server: {",
    )
    config_text = config.read_text(encoding="utf-8")
    if not optimizer_changed and optimizer_new not in config_text:
        raise RuntimeError(
            "Pinned Vite optimizer block differs from the expected source"
        )
    changed_config = proxy_changed or optimizer_changed

    old_boot = """if (import.meta.env.DEV) {
  frappeRequest({ url: '/api/method/crm.www.crm.get_context_for_dev' }).then(
    (values) => {
      for (let key in values) {
        window[key] = values[key]
      }
      socket = initSocket()
      app.config.globalProperties.$socket = socket
      app.mount('#app')
    },
  )
} else {"""
    new_boot = """if (import.meta.env.DEV) {
  // Vite serves index.html without Frappe's Jinja boot data. Read the CSRF
  // token from the authenticated same-site /cskh page before the first POST;
  // the API boot method itself requires that token.
  fetch('/cskh', { credentials: 'same-origin' })
    .then((response) => {
      if (!response.ok) throw new Error(`CSRF bootstrap returned ${response.status}`)
      return response.text()
    })
    .then((html) => {
      const match = html.match(/frappe\\.csrf_token\\s*=\\s*(['\\\"])(.*?)\\1/)
      if (!match || !match[2]) throw new Error('CSRF token missing from /cskh response')
      window.csrf_token = match[2]
      return frappeRequest({ url: '/api/method/crm.www.crm.get_context_for_dev' })
    })
    .then((values) => {
      for (let key in values) window[key] = values[key]
      socket = initSocket()
      app.config.globalProperties.$socket = socket
      app.mount('#app')
    })
    .catch((error) => console.error('CRM Vite boot failed', error))
} else {"""
    current_main = main_js.read_text(encoding="utf-8")
    changed_main = (
        False
        if "function initializeApp()" in current_main
        else patch_once(main_js, old_boot, new_boot)
    )

    current_main = main_js.read_text(encoding="utf-8")
    main_marker = "// create a pinia instance"
    new_main_suffix = """function initializeApp() {
  // Install request-producing plugins only after the DEV CSRF token and boot
  // context are ready. Router resources and telemetry can POST during install.
  const pinia = createPinia()
  const app = createApp(App)

  const globalComponents = {
    Button,
    TextInput,
    Input,
    FormControl,
    ErrorMessage,
    Dialog,
    Alert,
    Badge,
    FeatherIcon,
  }

  setConfig('resourceFetcher', frappeRequest)
  app.use(FrappeUI)
  app.use(spritePlugin)
  app.use(pinia)
  app.use(router)
  app.use(translationPlugin)
  for (const key in globalComponents) {
    app.component(key, globalComponents[key])
  }
  app.use(telemetryPlugin, { app_name: 'crm' })

  app.config.globalProperties.$dialog = createDialog
  const socket = initSocket()
  app.config.globalProperties.$socket = socket
  app.mount('#app')

  if (import.meta.env.DEV) {
    window.$dialog = createDialog
  }
}

if (import.meta.env.DEV) {
  // Vite serves index.html without Frappe's Jinja boot data. Read the CSRF
  // token from the authenticated same-site /cskh page before installing the
  // router, resources, or telemetry that can issue POST requests.
  fetch('/cskh', { credentials: 'same-origin' })
    .then((response) => {
      if (!response.ok) throw new Error(`CSRF bootstrap returned ${response.status}`)
      return response.text()
    })
    .then((html) => {
      const match = html.match(/frappe\\.csrf_token\\s*=\\s*(['\\\"])(.*?)\\1/)
      if (!match || !match[2]) throw new Error('CSRF token missing from /cskh response')
      window.csrf_token = match[2]
      return frappeRequest({ url: '/api/method/crm.www.crm.get_context_for_dev' })
    })
    .then((values) => {
      for (const key in values) window[key] = values[key]
      initializeApp()
    })
    .catch((error) => console.error('CRM Vite boot failed', error))
} else {
  initializeApp()
}"""
    if new_main_suffix not in current_main:
        if current_main.count(main_marker) != 1:
            raise RuntimeError(
                f"Expected one CRM main initialization marker, found {current_main.count(main_marker)}"
            )
        source_suffix = current_main.split(main_marker, 1)[1]
        required = (
            "app.use(router)",
            "app.use(telemetryPlugin, { app_name: 'crm' })",
            "crm.www.crm.get_context_for_dev",
        )
        if any(marker not in source_suffix for marker in required):
            raise RuntimeError(
                "Pinned CRM main.js initialization block differs from expected source"
            )
        main_js.write_text(
            current_main.split(main_marker, 1)[0] + new_main_suffix,
            encoding="utf-8",
            newline="\n",
        )
        changed_main = True

    print(f"Vite config: {'patched' if changed_config else 'already patched'}")
    print(f"Dev bootstrap: {'patched' if changed_main else 'already patched'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
