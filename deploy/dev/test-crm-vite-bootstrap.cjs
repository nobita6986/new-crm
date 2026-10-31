// Execute the JavaScript actually emitted by the pinned-source patcher.
// These are auxiliary DEV regressions, outside the 99-method security inventory.
const assert = require('node:assert/strict')
const { execFileSync } = require('node:child_process')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')

const extract = `import ast,json,sys
tree=ast.parse(open(sys.argv[1],encoding='utf-8').read())
for node in ast.walk(tree):
 if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='new_main_suffix' for t in node.targets):
  print(json.dumps(ast.literal_eval(node.value)));break
else:raise RuntimeError('Missing generated bootstrap')`
const suffix = JSON.parse(execFileSync(
  process.platform === 'win32' ? 'python' : 'python3',
  ['-c', extract, path.join(__dirname, 'apply-crm-vite-dev-patch.py')],
  { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } },
))
const source = suffix.slice(suffix.lastIndexOf('\nif (import.meta.env.DEV)'))
  .replaceAll('import.meta.env.DEV', 'true')

async function runBoot(response, bootError) {
  const seen = { mounted: 0, requests: [], errors: [], children: [], redirect: null }
  const context = {
    URL,
    window: {
      location: {
        origin: 'http://dev.localhost:8091',
        pathname: '/crm/leads/view/list', search: '?view=mine', hash: '#activity',
        replace(url) { seen.redirect = url },
      },
    },
    document: {
      getElementById(id) {
        assert.equal(id, 'app')
        return { replaceChildren(...children) { seen.children = children } }
      },
      createElement(tag) { return { tag } },
    },
    console: { error(...args) { seen.errors.push(args) } },
    fetch: async (url, options) => {
      seen.requests.push({ url, options })
      assert.equal(url, '/desk')
      assert.equal(options.credentials, 'same-origin')
      assert.equal(options.cache, 'no-store')
      return response
    },
    frappeRequest: async ({ url }) => {
      assert.equal(context.window.csrf_token, 'session-csrf-test')
      assert.equal(seen.mounted, 0, 'App must wait for CSRF and context')
      seen.requests.push({ url })
      if (bootError) throw bootError
      return { site_name: 'dev.localhost' }
    },
    initializeApp() { seen.mounted++ },
  }
  vm.runInNewContext(source, context)
  await new Promise(setImmediate)
  return { ...seen, window: context.window }
}

const desk = (html = `frappe.csrf_token = "session-csrf-test";`) => ({
  ok: true, status: 200, url: 'http://dev.localhost:8091/desk',
  text: async () => html,
})

test('Guest goes to login with the original CRM route and makes no boot POST', async () => {
  const result = await runBoot({
    ...desk(), url: 'http://dev.localhost:8091/login?redirect-to=%2Fapp',
  })
  assert.equal(result.redirect,
    '/login?redirect-to=%2Fcrm%2Fleads%2Fview%2Flist%3Fview%3Dmine%23activity')
  assert.equal(result.requests.length, 1)
  assert.equal(result.mounted, 0)
  assert.equal(result.errors.length, 0)
})

test('Authenticated Desk session supplies CSRF before booting the app', async () => {
  const result = await runBoot(desk())
  assert.equal(result.mounted, 1)
  assert.equal(result.requests.length, 2)
  assert.equal(result.window.site_name, 'dev.localhost')
  assert.equal(result.errors.length, 0)
})

for (const token of ['', 'None']) {
  test(`Missing/invalid CSRF (${JSON.stringify(token)}) shows recovery UI without a POST`, async () => {
    const result = await runBoot(desk(`frappe.csrf_token = "${token}";`))
    assert.equal(result.mounted, 0)
    assert.equal(result.requests.length, 1)
    assert.equal(result.children[0].textContent, 'Không thể mở CRM')
    assert.equal(result.children[2].textContent, 'Đăng nhập lại')
  })
}

test('Desk permission denial shows recovery UI without an automatic login loop', async () => {
  const result = await runBoot({ ...desk(), ok: false, status: 403 })
  assert.equal(result.mounted, 0)
  assert.equal(result.requests.length, 1)
  assert.equal(result.redirect, null)
  assert.equal(result.children.length, 3)
})

test('Context request failure leaves a usable recovery message', async () => {
  const result = await runBoot(desk(), new Error('Context request denied'))
  assert.equal(result.mounted, 0)
  assert.equal(result.children.length, 3)
  assert.equal(result.errors.length, 1)
})
