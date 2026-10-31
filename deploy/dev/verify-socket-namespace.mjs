#!/usr/bin/env node
/**
 * verify-socket-namespace.mjs — kiểm chứng namespace Socket.IO thật của Frappe DEV.
 *
 * Handshake/polling HTTP 200 KHÔNG chứng minh namespace đã join. Script này
 * đăng nhập lấy cookie phiên, rồi nối tới namespace /<site> qua proxy Vite và
 * đòi bằng chứng round-trip thật:
 *
 *   1. sự kiện 'connect' với socket.id khác null
 *   2. socket.nsp === '/' + SITE_NAME
 *   3. transport = websocket
 *   4. emit('ping') -> nhận được 'pong' (server thật trả lời)
 *
 * Frappe realtime (apps/frappe/realtime/middlewares/authenticate.js) so sánh
 * hostname của header Host với hostname của header Origin và so sánh cổng
 * trong Origin với cổng publish trên host (request tự xác thực đi tới
 * `Origin + /api/...`). Vì vậy:
 *   - cần Cookie (session) để authenticate
 *   - cần Origin khớp hostname/cổng với URL truy cập
 *   - cổng Vite trong container PHẢI bằng cổng Vite trên host
 *
 * Usage (trong bench container):
 *   cd /workspace/frappe-bench/apps/crm/frontend
 *   node /src/deploy/dev/verify-socket-namespace.mjs \
 *        --origin http://dev.localhost:8091 --web http://dev.localhost:8010
 *
 * Exit code: 0 = PASS, 2 = FAIL.
 */

import { io } from 'socket.io-client'

function parseArgs(argv) {
	const args = { origin: null, web: null, site: null, timeout: 10000 }
	for (let i = 2; i < argv.length; i++) {
		const key = argv[i]
		if (!key.startsWith('--')) continue
		const name = key.slice(2)
		const value = argv[i + 1]
		if (value === undefined || value.startsWith('--')) continue
		args[name] = value
		i++
	}
	return args
}

const args = parseArgs(process.argv)
const site = args.site || process.env.SITE_NAME || 'dev.localhost'
const origin = args.origin || process.env.VITE_ORIGIN || 'http://dev.localhost:8080'
const web = args.web || process.env.WEB_ORIGIN || origin.replace(/:\d+$/, ':8000')
const adminPassword = process.env.ADMIN_PASSWORD

function fail(message, extra = {}) {
	console.log(
		JSON.stringify({
			schema: 'verify-socket-namespace-v1',
			result: 'FAIL',
			reason: message,
			site,
			origin,
			web,
			...extra,
		}),
	)
	process.exit(2)
}

if (!adminPassword) {
	fail('ADMIN_PASSWORD khong co trong moi truong')
}

const u = new URL(origin)
const hostHeader = u.host

async function login() {
	let response
	try {
		response = await fetch(`${web}/api/method/login`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ usr: 'Administrator', pwd: adminPassword }),
		})
	} catch (error) {
		fail('Khong ket noi duoc web backend', { cause: String(error?.cause ?? error) })
	}
	if (!response.ok) {
		fail('Dang nhap that bai', { httpStatus: response.status })
	}
	const raw = response.headers.get('set-cookie') || ''
	const sid = raw
		.split(/,(?=[^;]+?=)/)
		.map((c) => c.split(';')[0].trim())
		.find((c) => c.startsWith('sid='))
	if (!sid) {
		fail('Khong lay duoc cookie sid sau dang nhap')
	}
	return sid
}

const cookie = await login()

const result = {
	schema: 'verify-socket-namespace-v1',
	result: 'FAIL',
	site,
	origin,
	web,
	connected: false,
	id: null,
	nsp: null,
	transport: null,
	pong: false,
	error: null,
}

const socket = io(`${origin}/${site}`, {
	transports: ['websocket'],
	extraHeaders: { Cookie: cookie, Origin: origin, Host: hostHeader },
	reconnection: false,
	timeout: args.timeout,
	path: '/socket.io',
})

const finish = (code) => {
	console.log(JSON.stringify(result))
	try {
		socket.close()
	} catch {
		/* ignore */
	}
	process.exit(code)
}

socket.on('connect', () => {
	result.connected = true
	result.id = socket.id
	result.nsp = socket.nsp
	result.transport = socket.io?.engine?.transport?.name ?? null
	socket.emit('ping')
})

socket.on('pong', () => {
	result.pong = true
	result.result = 'PASS'
	finish(0)
})

socket.on('connect_error', (error) => {
	result.error = error?.message ?? String(error)
	finish(2)
})

setTimeout(() => {
	if (!result.error) {
		result.error = 'Timeout: khong nhan duoc phan hoi trong thoi han'
	}
	finish(2)
}, args.timeout + 2000)
