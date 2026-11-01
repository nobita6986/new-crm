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


def check_js_syntax(path: Path) -> None:
    """Fail fast when a patched JS file does not parse.

    The Vite config is edited by string replacement, so a mistake can produce
    syntactically invalid JavaScript that only blows up when the dev server
    tries to load it.  Node is always available in the bench image, so use it
    as a cheap syntax gate right after the edits.
    """
    try:
        result = subprocess.run(
            ["node", "--check", str(path)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        # Node is not installed: skip the gate rather than fail the patch.
        return
    if result.returncode != 0:
        raise RuntimeError(
            f"Patched file {path} is not valid JavaScript:\n{result.stderr.strip()}"
        )


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
    index_html = crm_root / "frontend/index.html"
    main_js = crm_root / "frontend/src/main.js"
    translation_js = crm_root / "frontend/src/translation.js"
    if (
        not config.is_file()
        or not index_html.is_file()
        or not main_js.is_file()
        or not translation_js.is_file()
    ):
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
        crm_root / "frontend/src/components/SidePanelLayout.vue": (
            (
                '                          :placeholder="\n'
                "                            __('Select') + ' ' + field.label + '...'\n"
                '                          "',
                '                          :placeholder="\n'
                "                            __('Select {0}...', [__(field.label)])\n"
                '                          "',
            ),
        ),
    }
    for path, replacements in static_label_patches.items():
        for old, new in replacements:
            changed = patch_once(path, old, new)
            print(
                f"{'patched' if changed else 'already patched'}: {path.relative_to(crm_root)} ({new})"
            )

    side_panel_layout = crm_root / "frontend/src/components/SidePanelLayout.vue"
    side_panel_text = side_panel_layout.read_text(encoding="utf-8")
    translated_placeholder = (
        ':placeholder="field.placeholder ? __(field.placeholder) : field.placeholder"'
    )
    placeholder_changed = False
    for placeholder_source in (
        ':placeholder="field.placeholder"',
        ':placeholder="__(field.placeholder)"',
    ):
        if placeholder_source in side_panel_text:
            side_panel_text = side_panel_text.replace(
                placeholder_source, translated_placeholder
            )
            placeholder_changed = True
    if placeholder_changed:
        side_panel_layout.write_text(side_panel_text, encoding="utf-8", newline="\n")
    print(
        f"Side-panel field placeholders: {'patched' if placeholder_changed else 'already patched'}"
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

    # frappe-ui derives the Vite dev port from `webserver_port` (8080 + offset)
    # and that derived value overrides the CLI `--port` flag.  The realtime
    # self-authentication request is sent to `Origin + /api/...`, i.e. to the
    # browser-facing Vite origin, so the Vite port must be reachable INSIDE the
    # container under the very same port number the browser uses.  Allow the
    # operator to pin it through VITE_DEV_PORT; compose publishes that same
    # number on the host (host port == container port) to keep the two equal.
    proxy_new = (
        "      frappeProxy: {\n"
        "        source: '^/(desk|app|login|api|assets|files|private|cskh)',\n"
        "        port: Number(process.env.VITE_DEV_PORT || 8080),\n"
        "      },"
    )
    # Upgrade a proxy block that was patched without the explicit port option.
    proxy_previous = (
        "      frappeProxy: {\n"
        "        source: '^/(desk|app|login|api|assets|files|private|cskh)',\n"
        "      },"
    )
    config_text = config.read_text(encoding="utf-8")
    if proxy_new in config_text:
        proxy_changed = False
    elif proxy_previous in config_text:
        proxy_changed = patch_once(config, proxy_previous, proxy_new)
    else:
        proxy_changed = patch_once(config, "      frappeProxy: true,", proxy_new)
    config_text = config.read_text(encoding="utf-8")
    if not proxy_changed and proxy_new not in config_text:
        raise RuntimeError("Pinned Vite proxy block differs from the expected source")

    # frappe-ui's built-in proxy correctly sends HTTP application routes to the
    # Frappe web server on :8000, but the realtime Socket.IO server is a
    # separate process on :9000.  Do not add `/socket.io` to `frappeProxy`:
    # that would route Engine.IO handshakes to the web server and Vite would
    # eventually return the SPA document.  The explicit proxy keeps the
    # browser same-origin in DEV and supports both polling and WebSocket
    # upgrade.
    #
    # `changeOrigin` MUST stay false.  Frappe's realtime authentication
    # middleware compares the Host header hostname against the Origin header
    # hostname and rejects the handshake with "Invalid origin" when they
    # differ (apps/frappe/realtime/middlewares/authenticate.js).  With
    # `changeOrigin: true` Vite rewrites Host to the proxy target
    # (127.0.0.1:9000) while the browser Origin stays on dev.localhost, so
    # every namespace join fails even though the Engine.IO handshake itself
    # returns HTTP 200.  Measured 2026-10-31 (T1A): changeOrigin=true ->
    # connect_error "Invalid origin"; changeOrigin=false -> namespace
    # /dev.localhost connected=true over websocket with a pong reply.
    socket_proxy_new = """    server: {
      proxy: {
        '/socket.io': {
          target: 'http://127.0.0.1:9000',
          changeOrigin: false,
          ws: true,
        },
      },
      fs: {"""
    # Upgrade the earlier repository-owned DEV proxy as well as a clean pin.
    socket_proxy_previous = socket_proxy_new.replace(
        "changeOrigin: false", "changeOrigin: true"
    )
    socket_proxy_upgraded = False
    if socket_proxy_previous in config.read_text(encoding="utf-8"):
        socket_proxy_upgraded = patch_once(
            config, socket_proxy_previous, socket_proxy_new
        )
    socket_proxy_changed = patch_once(
        config,
        "    server: {\n      fs: {",
        socket_proxy_new,
    )
    config_text = config.read_text(encoding="utf-8")
    if not socket_proxy_changed and socket_proxy_new not in config_text:
        raise RuntimeError(
            "Pinned Vite Socket.IO proxy block differs from the expected source"
        )

    # The anchor is the closing bracket of `optimizeDeps.include`, followed by
    # the closing brace of `optimizeDeps` and the `server` key.  The
    # replacement MUST re-emit that closing bracket: dropping it leaves the
    # `include:` array unterminated and Vite then fails to parse the config
    # with "Expected `,` or `]` but found `:`" at the injected `exclude:` key.
    # That parse error only surfaces when the dev server starts, i.e. long
    # after the patch that caused it, so it is easy to miss.
    optimizer_new = (
        "      ],\n"
        "      exclude: [\n"
        "        'frappe-ui/editor',\n"
        "      ],\n"
        "    },\n"
        "    server: {"
    )
    optimizer_changed = patch_once(
        config,
        "      ],\n    },\n    server: {",
        optimizer_new,
    )
    config_text = config.read_text(encoding="utf-8")
    if not optimizer_changed and optimizer_new not in config_text:
        raise RuntimeError(
            "Pinned Vite optimizer block differs from the expected source"
        )
    changed_config = (
        proxy_changed
        or socket_proxy_changed
        or socket_proxy_upgraded
        or optimizer_changed
    )

    socket_js = crm_root / "frontend/src/socket.js"
    socket_boot_old = """export function initSocket() {
  let socketio_port = window.socketio_port || 9000
  let host = window.location.hostname
  let siteName = window.site_name
  let port = window.location.port ? `:${socketio_port}` : ''
  let protocol = port ? 'http' : 'https'
  let url = `${protocol}://${host}${port}/${siteName}`
"""
    socket_boot_new = """export function initSocket() {
  let siteName = window.site_name
  // During Vite development, go through its same-origin `/socket.io` proxy.
  // The proxy above forwards Engine.IO polling and WebSocket upgrades to the
  // separate Frappe realtime process on :9000.  Production keeps the
  // upstream direct-realtime behavior.
  let url
  if (import.meta.env.DEV) {
    url = `${window.location.origin}/${siteName}`
  } else {
    let socketio_port = window.socketio_port || 9000
    let host = window.location.hostname
    let port = window.location.port ? `:${socketio_port}` : ''
    let protocol = port ? 'http' : 'https'
    url = `${protocol}://${host}${port}/${siteName}`
  }
"""
    socket_boot_changed = patch_once(socket_js, socket_boot_old, socket_boot_new)

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
  // token from the authenticated same-site Desk page before the first POST;
  // the API boot method itself requires that token.
  fetch('/desk', { credentials: 'same-origin', cache: 'no-store' })
    .then((response) => {
      if (!response.ok) throw new Error(`CSRF bootstrap returned ${response.status}`)
      return response.text()
    })
    .then((html) => {
      const match = html.match(/frappe\\.csrf_token\\s*=\\s*(['\\\"])(.*?)\\1/)
      if (!match || !match[2]) throw new Error('CSRF token missing from Desk response')
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
  // Desk boot is independent of business permission to read a CRM Lead.
  // Guest sessions are redirected to login before any CSRF-protected POST.
  const loginUrl = '/login?redirect-to=' + encodeURIComponent(
    window.location.pathname + window.location.search + window.location.hash,
  )
  fetch('/desk', { credentials: 'same-origin', cache: 'no-store' })
    .then((response) => {
      if (new URL(response.url, window.location.origin).pathname === '/login') {
        window.location.replace(loginUrl)
        return null
      }
      if (!response.ok) throw new Error(`CSRF bootstrap returned ${response.status}`)
      return response.text()
    })
    .then((html) => {
      if (html === null) return null
      const match = html.match(/frappe\\.csrf_token\\s*=\\s*(['\\\"])(.*?)\\1/)
      if (!match || !match[2] || match[2] === 'None') {
        throw new Error('CSRF token missing from Desk response')
      }
      window.csrf_token = match[2]
      return frappeRequest({ url: '/api/method/crm.www.crm.get_context_for_dev' })
    })
    .then((values) => {
      if (!values) return
      for (const key in values) window[key] = values[key]
      initializeApp()
    })
    .catch((error) => {
      console.error('CRM Vite boot failed', error)
      const root = document.getElementById('app')
      if (!root) return
      const heading = document.createElement('h1')
      heading.textContent = 'Không thể mở CRM'
      const message = document.createElement('p')
      message.textContent = 'Không thể khởi tạo phiên. Vui lòng tải lại trang hoặc đăng nhập lại.'
      const loginLink = document.createElement('a')
      loginLink.href = loginUrl
      loginLink.textContent = 'Đăng nhập lại'
      root.replaceChildren(heading, message, loginLink)
    })
} else {
  initializeApp()
}"""
    if new_main_suffix not in current_main:
        # The first application removes the upstream comment. Later upgrades
        # must replace our existing initializer rather than require that comment.
        initialization_marker = main_marker
        if current_main.count(initialization_marker) == 0:
            initialization_marker = "function initializeApp() {"
        if current_main.count(initialization_marker) != 1:
            raise RuntimeError(
                "Expected one CRM main initialization marker, "
                f"found {current_main.count(initialization_marker)}"
            )
        source_prefix, source_suffix = current_main.split(initialization_marker, 1)
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
            source_prefix + new_main_suffix,
            encoding="utf-8",
            newline="\n",
        )
        changed_main = True

    # CRM modules evaluate translated labels while their ESM imports load.
    # Install the translator before importing router/App so module-level
    # `__('...')` calls do not crash the Vite entry point.
    translator_prelude = """if (typeof window !== 'undefined' && typeof window.__ !== 'function') {
  window.__ = translate
}

export default function translationPlugin(app) {"""
    changed_translation = patch_once(
        translation_js,
        "export default function translationPlugin(app) {",
        translator_prelude,
    )

    # Some Frappe-served CRM chunks call the global __ before the Vite module
    # graph installs the real translator. Provide an identity fallback in the
    # document head; translationPlugin replaces it after boot context loads.
    shim_line = (
        "    <script>var __ = window.__ = window.__ || ((message) => message)</script>"
    )
    current_index = index_html.read_text(encoding="utf-8")
    index_lines = [
        line
        for line in current_index.splitlines()
        if not (
            "window.__ = window.__ || ((message) => message)" in line
            or "var __ = window.__ = window.__ || ((message) => message)" in line
        )
    ]
    head_index = index_lines.index("  <head>") if "  <head>" in index_lines else -1
    if head_index < 0:
        raise RuntimeError("Pinned CRM Vite index.html head marker is missing")
    index_lines.insert(head_index + 1, shim_line)
    normalized_index = "\n".join(index_lines) + "\n"
    changed_index = normalized_index != current_index
    if changed_index:
        index_html.write_text(normalized_index, encoding="utf-8", newline="\n")

    # In Vite mode the CRM onboarding helper can pass a dotted Frappe method
    # name to frappe-ui's generic request() utility. A relative URL then
    # resolves under the current CRM route and receives the SPA HTML fallback.
    # Normalize only bare dotted method names to the standard RPC endpoint.
    request_ts = crm_root / "frontend/node_modules/frappe-ui/src/utils/request.ts"
    if not request_ts.is_file():
        raise RuntimeError("Pinned frappe-ui request utility is missing")
    changed_request = patch_once(
        request_ts,
        "  let url = options.url\n",
        "  let url = options.url\n"
        "  if (/^[A-Za-z0-9_]+(?:\\.[A-Za-z0-9_]+)+$/.test(url)) {\n"
        "    url = `/api/method/${url}`\n"
        "  }\n",
    )

    current_main = main_js.read_text(encoding="utf-8")
    translation_import = "import translationPlugin from './translation'\n"
    if current_main.count(translation_import) != 1:
        raise RuntimeError("Expected one translation plugin import in CRM main.js")
    if not current_main.startswith(translation_import):
        current_main = current_main.replace(translation_import, "", 1)
        if not current_main.startswith("import './index.css'\n"):
            raise RuntimeError(
                "Pinned CRM main.js import prefix differs from expected source"
            )
        current_main = translation_import + current_main
        main_js.write_text(current_main, encoding="utf-8", newline="\n")
        changed_main = True

    vueuse_core = crm_root / "frontend/node_modules/@vueuse/core/index.mjs"
    if not vueuse_core.is_file():
        raise RuntimeError("Pinned VueUse core source is missing")
    vueuse_old_targets = """const targets = computed(() => Array.isArray(target) ? target.map((el) => unrefElement(el)) : [unrefElement(target)]);"""
    vueuse_new_targets = """const targets = computed(() => {
    const value = unref(target);
    const inputTargets = Array.isArray(value) ? value : [value];
    return inputTargets.flatMap((item) => {
      const element = unrefElement(item);
      return Array.isArray(element) ? element.map(unrefElement) : [element];
    }).filter((element) => element && element.nodeType === 1);
  });"""
    vueuse_observer_changed = patch_once(
        vueuse_core, vueuse_old_targets, vueuse_new_targets
    )

    # Fail fast if any edited JavaScript file does not parse.  This catches
    # malformed string replacements (e.g. an unbalanced bracket) at patch time
    # instead of much later when the Vite dev server refuses to start.
    for js_path in (config, socket_js, main_js, translation_js):
        if js_path.is_file():
            check_js_syntax(js_path)

    print(f"Vite config: {'patched' if changed_config else 'already patched'}")
    print(
        f"Same-origin Socket.IO bootstrap: {'patched' if socket_boot_changed else 'already patched'}"
    )
    print(f"Dev bootstrap: {'patched' if changed_main else 'already patched'}")
    print(
        f"Early translation global: {'patched' if changed_translation else 'already patched'}"
    )
    print(
        f"Computed element-array observer normalization: {'patched' if vueuse_observer_changed else 'already patched'}"
    )
    print(
        f"HTML translation bootstrap: {'patched' if changed_index else 'already patched'}"
    )
    print(
        f"Vite RPC request normalization: {'patched' if changed_request else 'already patched'}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
