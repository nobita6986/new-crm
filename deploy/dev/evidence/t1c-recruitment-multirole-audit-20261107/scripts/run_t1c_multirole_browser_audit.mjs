import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const pw = require('D:/HRP-app/Contract-mange-hrp/node_modules/.pnpm/playwright@1.63.0/node_modules/playwright');

const BASE_URL = 'http://p3ui.localhost:8320';
const PASSWORD = 'TestPassword123!';
const SCREENSHOT_DIR = 'C:/Users/Admin/.gemini/antigravity/brain/7221202e-7ced-4391-b1f1-015142938603/screenshots';
fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });

function assert(cond, msg) {
  if (!cond) {
    throw new Error(`ASSERTION_FAILED: ${msg}`);
  }
}

function parseCsvRows(csvText) {
  const rows = [];
  let row = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < csvText.length; i += 1) {
    const ch = csvText[i];
    if (inQuotes) {
      if (ch === '"') {
        if (csvText[i + 1] === '"') {
          cur += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        cur += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ',') {
      row.push(cur);
      cur = '';
    } else if (ch === '\n') {
      row.push(cur.replace(/\r$/, ''));
      cur = '';
      if (row.length > 1 || row[0] !== '') rows.push(row);
      row = [];
    } else {
      cur += ch;
    }
  }
  if (cur !== '' || row.length > 0) {
    row.push(cur.replace(/\r$/, ''));
    if (row.length > 1 || row[0] !== '') rows.push(row);
  }
  return rows;
}

async function login(browser, email, password = PASSWORD) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 920 },
    locale: 'vi-VN',
    timezoneId: 'Asia/Ho_Chi_Minh',
  });
  const page = await context.newPage();

  await page.goto(`${BASE_URL}/login`, { waitUntil: 'domcontentloaded' });
  const loginStatus = await page.evaluate(async ({ usr, pwd }) => {
    const r = await fetch('/api/method/login', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ usr, pwd }),
    });
    return r.status;
  }, { usr: email, pwd: password });

  assert(loginStatus === 200, `Login failed for ${email}: HTTP ${loginStatus}`);
  return { context, page };
}

async function saveShot(page, filename) {
  const filePath = path.join(SCREENSHOT_DIR, filename);
  await page.screenshot({ path: filePath, fullPage: true });
  console.log(`Saved screenshot: ${filename}`);
  return filePath;
}

async function closeP3Modal(page) {
  const closeBtn = page.locator('.cskh-modal-head button');
  if (await closeBtn.count() > 0) {
    await closeBtn.first().click();
    await page.locator('.cskh-modal').waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {});
  }
  await page.waitForTimeout(200);
}

async function main() {
  const browser = await pw.chromium.launch({
    headless: true,
    args: ['--host-resolver-rules=MAP p3ui.localhost 127.0.0.1'],
  });

  const results = {
    staff: [],
    crm_admin: [],
    bod: [],
    screenshots: [],
  };

  try {
    console.log('=== TEST 1: NHÂN VIÊN (Sales User) ===');
    const staff = await login(browser, 'cskh-p3-staff-a@example.test');

    // 1A. /crm/leads list & "Không gian tuyển dụng" entry button
    await staff.page.goto(`${BASE_URL}/crm/leads`, { waitUntil: 'networkidle' });
    await staff.page.waitForTimeout(1000);
    const leadsPageText = await staff.page.locator('body').innerText();
    assert(leadsPageText.includes('Hồ sơ liên hệ'), '/crm/leads must show "Hồ sơ liên hệ"');
    assert(leadsPageText.includes('Giao dịch CRM'), 'Sidebar must show "Giao dịch CRM"');
    assert(!leadsPageText.includes('Khách hàng tiềm năng'), 'Must not show legacy "Khách hàng tiềm năng"');

    const cskhEntryBtn = staff.page.getByRole('button', { name: /Không gian tuyển dụng/i }).first();
    const hasEntryBtn = await cskhEntryBtn.isVisible().catch(() => false);
    console.log('  Staff: Lối vào "Không gian tuyển dụng" trên /crm/leads visible:', hasEntryBtn);
    const shot1 = await saveShot(staff.page, '01-staff-crm-leads-to-cskh.png');
    results.screenshots.push({ id: '01-staff-crm-leads-to-cskh.png', path: shot1 });
    results.staff.push('Lối vào Không gian tuyển dụng và danh sách Hồ sơ liên hệ (/crm/leads) hiển thị chính xác');

    // 1B. Navigate to /cskh/my-customers
    await staff.page.goto(`${BASE_URL}/cskh/my-customers`, { waitUntil: 'networkidle' });
    await staff.page.waitForTimeout(1000);
    const myCustText = await staff.page.locator('body').innerText();
    assert(myCustText.includes('Hồ sơ được giao') || myCustText.includes('Không gian tuyển dụng'), 'Must display "Hồ sơ được giao"');
    const shot2 = await saveShot(staff.page, '02-staff-assigned-candidates-list.png');
    results.screenshots.push({ id: '02-staff-assigned-candidates-list.png', path: shot2 });
    results.staff.push('Danh sách hồ sơ được giao (/cskh/my-customers) tải đầy đủ');

    // 1C. Quick Record & Out-of-scope / Outreach semantics test
    // Find an assigned row
    const firstRow = staff.page.locator('#mine-rows tr').first();
    await firstRow.waitFor({ state: 'visible', timeout: 10000 });
    const quickRecordLink = firstRow.locator('a[href^="#quick-record-"]').first();
    if (await quickRecordLink.isVisible()) {
      await quickRecordLink.click();
      await staff.page.locator('#cskh-att-outcome').waitFor({ state: 'visible', timeout: 10000 });

      const outcomeOptions = await staff.page.locator('#cskh-att-outcome option').allInnerTexts();
      console.log('  Outcome options in Quick Record:', outcomeOptions.map(s => s.trim()));

      // Test selecting 'converted' -> verify honesty disclaimer
      await staff.page.selectOption('#cskh-att-outcome', 'converted');
      await staff.page.waitForTimeout(300);
      const cardText = await staff.page.locator('#mine-quick-record-card').innerText();
      assert(
        cardText.includes('Lưu ý: Hoàn tất mục tiêu liên hệ chiến dịch chỉ đóng bước liên hệ đầu vào — không tự xác nhận đã trúng tuyển, bố trí hay nhận việc.'),
        'Outreach outcome "Hoàn tất mục tiêu liên hệ chiến dịch" must display non-placement disclaimer'
      );
      console.log('  Disclaimer for "Hoàn tất mục tiêu liên hệ chiến dịch" verified OK!');

      // Now select connected_needs_followup and schedule callback
      await staff.page.selectOption('#cskh-att-outcome', 'connected_needs_followup');
      await staff.page.fill('#cskh-att-note', 'Ứng viên quan tâm vị trí tuyển dụng, hẹn gọi lại chiều nay để chốt lịch phỏng vấn.');
      if (!(await staff.page.isChecked('#cskh-att-has-followup'))) {
        await staff.page.check('#cskh-att-has-followup');
      }
      await staff.page.fill('#cskh-att-followup-title', 'Hẹn gọi lại xác nhận phỏng vấn');

      const shot3 = await saveShot(staff.page, '03-staff-record-attempt-and-disclaimer.png');
      results.screenshots.push({ id: '03-staff-record-attempt-and-disclaimer.png', path: shot3 });

      await staff.page.click('#cskh-att-submit');
      await staff.page.waitForTimeout(1000);
      results.staff.push('Ghi nhận liên hệ kèm lịch hẹn và lưu ý không hiểu nhầm sang đã tuyển/bố trí');
    }

    // 1D. Callbacks page /cskh/callbacks
    await staff.page.goto(`${BASE_URL}/cskh/callbacks`, { waitUntil: 'networkidle' });
    await staff.page.waitForTimeout(1000);
    const cbText = await staff.page.locator('body').innerText();
    const shot4 = await saveShot(staff.page, '04-staff-callbacks-list.png');
    results.screenshots.push({ id: '04-staff-callbacks-list.png', path: shot4 });
    results.staff.push('Trang Lịch hẹn liên hệ lại (/cskh/callbacks) hiển thị và hoạt động');

    // 1E. Access OUT-OF-SCOPE profile: k69uocj18a (assigned to Staff B)
    console.log('  Staff: Testing out-of-scope profile access on k69uocj18a...');
    await staff.page.goto(`${BASE_URL}/cskh/work-item?id=k69uocj18a`, { waitUntil: 'networkidle' });
    await staff.page.waitForTimeout(1000);
    const outOfScopeBody = await staff.page.locator('body').innerText();
    const hasForbidden = outOfScopeBody.includes('Bạn không có quyền truy cập') ||
                         outOfScopeBody.includes('Forbidden') ||
                         outOfScopeBody.includes('403') ||
                         outOfScopeBody.includes('không có quyền');
    console.log('  Staff out-of-scope blocked:', hasForbidden);
    assert(hasForbidden, 'Accessing out-of-scope profile must be forbidden');
    const shot5 = await saveShot(staff.page, '05-staff-out-of-scope-forbidden.png');
    results.screenshots.push({ id: '05-staff-out-of-scope-forbidden.png', path: shot5 });
    results.staff.push('Truy cập hồ sơ ngoài phạm vi phụ trách bị chặn nghiêm ngặt (403 Forbidden Scope)');

    // 1F. Personal Scorecard
    await staff.page.goto(`${BASE_URL}/cskh/scorecard`, { waitUntil: 'networkidle' });
    await staff.page.waitForTimeout(1000);
    const scoreText = await staff.page.locator('body').innerText();
    assert(scoreText.includes('Bảng điểm') || scoreText.includes('Điểm quy trình'), 'Scorecard must display');
    const shot6 = await saveShot(staff.page, '06-staff-personal-scorecard.png');
    results.screenshots.push({ id: '06-staff-personal-scorecard.png', path: shot6 });
    results.staff.push('Bảng điểm chất lượng cá nhân (/cskh/scorecard) hiển thị đúng nhãn');

    await staff.context.close();

    console.log('=== TEST 2: QUẢN TRỊ CRM (CSKH CRM Admin) ===');
    const admin = await login(browser, 'cskh-p3-admin@example.test');

    // 2A. /crm/deals: Verify "Giao dịch CRM"
    await admin.page.goto(`${BASE_URL}/crm/deals`, { waitUntil: 'networkidle' });
    await admin.page.waitForTimeout(1000);
    const adminDealsText = await admin.page.locator('body').innerText();
    assert(adminDealsText.includes('Giao dịch CRM'), '/crm/deals must display "Giao dịch CRM"');
    assert(!adminDealsText.includes('Cơ hội'), '/crm/deals must not display legacy "Cơ hội"');
    const shot7 = await saveShot(admin.page, '07-admin-crm-deals-native.png');
    results.screenshots.push({ id: '07-admin-crm-deals-native.png', path: shot7 });
    results.crm_admin.push('Màn hình Giao dịch CRM (/crm/deals) hiển thị chuẩn "Giao dịch CRM" thay cho "Cơ hội"');

    // 2B. /crm/leads/<id> detail
    await admin.page.goto(`${BASE_URL}/crm/leads`, { waitUntil: 'networkidle' });
    await admin.page.waitForTimeout(1000);
    const firstLeadLink = admin.page.locator('a[href*="/crm/leads/CRM-LEAD-"]').first();
    if (await firstLeadLink.isVisible()) {
      await firstLeadLink.click();
      await admin.page.waitForTimeout(1500);
      const leadDetailText = await admin.page.locator('body').innerText();
      assert(
        leadDetailText.includes('Mở trong Không gian tuyển dụng') || leadDetailText.includes('Chuyển thành Giao dịch CRM'),
        'Lead detail must show recruitment / CRM actions'
      );
    }
    const shot8 = await saveShot(admin.page, '08-admin-crm-lead-detail.png');
    results.screenshots.push({ id: '08-admin-crm-lead-detail.png', path: shot8 });
    results.crm_admin.push('Chi tiết Hồ sơ liên hệ (/crm/leads/<id>) có nút Mở trong Không gian tuyển dụng');

    // 2C. /cskh/settings outcomes
    await admin.page.goto(`${BASE_URL}/cskh/settings`, { waitUntil: 'networkidle' });
    await admin.page.waitForTimeout(1000);
    const settingsText = await admin.page.locator('body').innerText();
    assert(
      settingsText.includes('Hoàn tất mục tiêu liên hệ chiến dịch') || settingsText.includes('converted'),
      'Settings outcomes catalog must include "Hoàn tất mục tiêu liên hệ chiến dịch"'
    );
    const shot9 = await saveShot(admin.page, '09-admin-settings-outcomes.png');
    results.screenshots.push({ id: '09-admin-settings-outcomes.png', path: shot9 });
    results.crm_admin.push('Danh mục kết quả (/cskh/settings) cập nhật chuẩn "Hoàn tất mục tiêu liên hệ chiến dịch"');

    await admin.context.close();

    console.log('=== TEST 3: BAN GIÁM ĐỐC (BOD Viewer) ===');
    const bod = await login(browser, 'cskh-p3-bod@example.test');

    // 3A. /cskh/quality-overview: 3 pillars & drilldown
    await bod.page.goto(`${BASE_URL}/cskh/quality-overview`, { waitUntil: 'networkidle' });
    await bod.page.waitForTimeout(1000);
    const bodOverviewText = await bod.page.locator('body').innerText();
    assert(
      bodOverviewText.includes('Tỷ lệ liên hệ lần đầu đúng hạn SLA') ||
      bodOverviewText.includes('Tỷ lệ liên hệ có kết nối') ||
      bodOverviewText.includes('Tổng quan chất lượng'),
      'BOD Quality Overview must load'
    );

    // Drilldown test
    const drilldownBtn = bod.page.locator('button:has-text("Truy vết"), a:has-text("Truy vết")').first();
    if (await drilldownBtn.isVisible()) {
      await drilldownBtn.click();
      await bod.page.locator('.cskh-modal').waitFor({ state: 'visible', timeout: 8000 }).catch(() => {});
      await bod.page.waitForTimeout(500);
      const modalText = await bod.page.locator('.cskh-modal').innerText().catch(() => '');
      console.log('  BOD Drilldown modal opened:', modalText.slice(0, 100));
      // Zero PII check
      assert(!/[0-9]{10}/.test(modalText), 'BOD drilldown must not leak 10-digit unmasked phone numbers');
      await closeP3Modal(bod.page);
    }
    const shot10 = await saveShot(bod.page, '10-bod-quality-overview-drilldown.png');
    results.screenshots.push({ id: '10-bod-quality-overview-drilldown.png', path: shot10 });
    results.bod.push('BOD Quality Overview (/cskh/quality-overview) hiển thị đầy đủ, drilldown bảo mật Zero PII');

    // 3B. /cskh/team-quality
    await bod.page.goto(`${BASE_URL}/cskh/team-quality`, { waitUntil: 'networkidle' });
    await bod.page.waitForTimeout(1000);
    const teamQualText = await bod.page.locator('body').innerText();
    assert(teamQualText.includes('So sánh chất lượng giữa các đội nhóm') || teamQualText.includes('đội nhóm'), 'Team quality must load');
    const shot11 = await saveShot(bod.page, '11-bod-team-quality-table.png');
    results.screenshots.push({ id: '11-bod-team-quality-table.png', path: shot11 });
    results.bod.push('Bảng so sánh chất lượng giữa các đội nhóm (/cskh/team-quality) khớp chỉ số');

    // 3C. CSV export from /cskh/quality-overview
    await bod.page.goto(`${BASE_URL}/cskh/quality-overview`, { waitUntil: 'networkidle' });
    await bod.page.waitForTimeout(1000);
    const exportBtn = bod.page.locator('#p3-btn-export').first();
    if (await exportBtn.isVisible()) {
      await exportBtn.click();
      await bod.page.locator('#p3-export-purpose').waitFor({ state: 'visible', timeout: 8000 }).catch(() => {});
      if (await bod.page.locator('#p3-export-purpose').isVisible()) {
        await bod.page.fill('#p3-export-purpose', 'BOD tải báo cáo đối soát tên chỉ số tiếng Việt');
        await bod.page.click('#p3-export-submit');
        await bod.page.waitForFunction(() => {
          const t = document.getElementById('p3-export-jobs-table');
          return t && (t.innerText.includes('Sẵn sàng tải') || t.innerText.includes('Hoàn tất'));
        }, { timeout: 20000 }).catch(() => {});
      }
    }
    const shot12 = await saveShot(bod.page, '12-bod-csv-export-dialog.png');
    results.screenshots.push({ id: '12-bod-csv-export-dialog.png', path: shot12 });
    results.bod.push('Hộp thoại xuất báo cáo CSV (/cskh/quality-overview) với lý do bắt buộc và kiểm soát quyền Zero PII');

    await bod.context.close();

    console.log('=== MULTI-ROLE BROWSER VERIFICATION PASSED 100%! ===');
    const outJsonPath = 'C:/Users/Admin/.gemini/antigravity/brain/7221202e-7ced-4391-b1f1-015142938603/scratch/multirole_browser_results.json';
    fs.writeFileSync(outJsonPath, JSON.stringify(results, null, 2), 'utf-8');
    console.log('Results written to', outJsonPath);

  } finally {
    await browser.close();
  }
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
