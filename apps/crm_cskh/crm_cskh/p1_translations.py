"""Generate the Vietnamese translation rows for the P1 UI strings.

Run with: `bench --site <site> execute crm_cskh.p1_strings.build_translation_rows`
or directly with `python apps/crm_cskh/crm_cskh/p1_strings.py` (it prints CSV).

The rows are appended to `crm_cskh/translations/vi.csv` so the Frappe i18n
pipeline serves the P1 pages in Vietnamese (P1 §9.6).
"""

import csv
import io

# English message id -> Vietnamese translation.
TRANSLATIONS = {
	# --- Shell ---------------------------------------------------------------
	"CSKH": "CSKH",
	"Customer pool": "Kho khách hàng",
	"Import list": "Nhập danh sách",
	"Campaigns and rules": "Chiến dịch và quy tắc",
	"My customers": "Khách được giao",
	"Distribution dashboard": "Bảng điều khiển phân phối",
	"Signed in as": "Đã đăng nhập với tư cách",
	"This page uses the same Frappe site session as CRM.": "Trang này dùng cùng phiên đăng nhập Frappe với CRM.",
	"Check session and CSRF": "Kiểm tra phiên đăng nhập và CSRF",
	"Request denied": "Yêu cầu bị từ chối",
	"Session verified for": "Phiên đăng nhập hợp lệ cho",
	"Return to CRM Leads": "Về danh sách khách hàng tiềm năng",
	"Loading...": "Đang tải...",
	"Refresh": "Làm mới",
	"Apply filters": "Áp dụng bộ lọc",
	"Previous": "Trang trước",
	"Next": "Trang sau",
	"All": "Tất cả",
	"Close": "Đóng",
	"Cancel": "Hủy",
	"Confirm": "Xác nhận",
	"Save": "Lưu",
	"Search": "Tìm kiếm",
	"No data": "Không có dữ liệu",
	"Error": "Lỗi",
	# --- Titles --------------------------------------------------------------
	"Campaigns and distribution rules": "Chiến dịch và quy tắc phân phối",
	"Customers assigned to me": "Khách hàng được giao cho tôi",
	"Leads in your scope, with the derived distribution state.": "Khách hàng tiềm năng trong phạm vi của bạn, kèm trạng thái phân phối.",
	"Upload a private file, map the phone column, preview, then confirm.": "Tải lên tệp riêng tư, ánh xạ cột số điện thoại, xem trước rồi xác nhận.",
	"Set up the target set and the weights, then preview before distributing.": "Thiết lập tập mục tiêu và trọng số, rồi xem trước trước khi phân phối.",
	"Only the customers that are currently assigned to you.": "Chỉ những khách hàng đang được giao cho bạn.",
	"Total pool, assigned, unassigned, per team, per user and per campaign.": "Tổng kho, đã giao, chưa giao, theo đội, theo người và theo chiến dịch.",
	"Customer pool, imports, campaigns and distribution.": "Kho khách hàng, nhập danh sách, chiến dịch và phân phối.",
	"Customers assigned to me.": "Khách hàng được giao cho tôi.",
	"Customers currently assigned to you.": "Những khách hàng hiện đang được giao cho bạn.",
	"Operational distribution figures for your scope.": "Số liệu phân phối trong phạm vi của bạn.",
	"Current owners are counted from work items; assignment events come from the audit trail.": "Người đang phụ trách được đếm từ công việc; sự kiện phân công lấy từ nhật ký kiểm toán.",
	"Log in to access this page.": "Hãy đăng nhập để truy cập trang này.",
	"You do not have permission to access this page.": "Bạn không có quyền truy cập trang này.",
	# --- Pool ----------------------------------------------------------------
	"Distribution state": "Trạng thái phân phối",
	"Not distributed": "Chưa phân phối",
	"Assigned": "Đã giao",
	"Queued": "Trong hàng đợi",
	"Revoked": "Đã thu hồi",
	"Closed": "Đã đóng",
	"Conflict": "Xung đột",
	"Campaign": "Chiến dịch",
	"Customer": "Khách hàng",
	"Phone number": "Số điện thoại",
	"Source": "Nguồn",
	"Owner": "Người phụ trách",
	"Created at": "Thời điểm tạo",
	"Name or phone number": "Tên hoặc số điện thoại",
	"No customers match the current filters.": "Không có khách hàng nào khớp với bộ lọc hiện tại.",
	"Work item": "Công việc",
	"Open in CRM": "Mở trong CRM",
	"Scope": "Phạm vi",
	"Unique customers": "Số khách hàng duy nhất",
	"Work items": "Số công việc",
	# --- Import --------------------------------------------------------------
	"1. Upload a private file": "1. Tải lên tệp riêng tư",
	"2. Preview and confirm": "2. Xem trước và xác nhận",
	"3. Follow the progress": "3. Theo dõi tiến độ",
	"CSV or XLSX, up to 10 MB. Keep the phone column as text so the leading zero is not lost.": "CSV hoặc XLSX, tối đa 10 MB. Giữ cột số điện thoại ở dạng văn bản để không mất số 0 ở đầu.",
	"Preview": "Xem trước",
	"Confirm import": "Xác nhận nhập",
	"Start over": "Bắt đầu lại",
	"Import history": "Lịch sử nhập",
	"File": "Tệp",
	"Column mapping": "Ánh xạ cột",
	"Phone column": "Cột số điện thoại",
	"Total rows": "Tổng số dòng",
	"Valid rows": "Số dòng hợp lệ",
	"New rows": "Số dòng mới",
	"Duplicate rows": "Số dòng trùng",
	"Error rows": "Số dòng lỗi",
	"Row": "Dòng",
	"Classification": "Phân loại",
	"Reason": "Lý do",
	"Status": "Trạng thái",
	"Batch": "Lô",
	"Committed at": "Thời điểm ghi nhận",
	"No import batch yet.": "Chưa có lô nhập nào.",
	"The phone column was not found. Map the column that holds the phone number.": "Không tìm thấy cột số điện thoại. Hãy ánh xạ cột chứa số điện thoại.",
	"The file must be CSV or XLSX.": "Tệp phải là CSV hoặc XLSX.",
	"The upload failed.": "Tải lên thất bại.",
	"Preview is ready. Review the numbers before confirming.": "Đã sẵn sàng xem trước. Hãy rà soát các con số trước khi xác nhận.",
	# --- Campaigns and rules -------------------------------------------------
	"Campaigns": "Chiến dịch",
	"Assignment rules": "Quy tắc phân công",
	"Distribution": "Phân phối",
	"Distribution runs": "Đợt phân phối",
	"Code": "Mã",
	"Title": "Tiêu đề",
	"Description": "Mô tả",
	"Started at": "Bắt đầu lúc",
	"Ended at": "Kết thúc lúc",
	"Rule version": "Phiên bản quy tắc",
	"Team scope": "Phạm vi đội",
	"Create campaign": "Tạo chiến dịch",
	"New campaign": "Chiến dịch mới",
	"Assignee": "Người được giao",
	"Team": "Đội",
	"Weight": "Trọng số",
	"Capacity": "Sức chứa",
	# `capacity_limit = 0` means "no limit" (P1 §7.2 / ADR-P1-04 §2.6). The
	# contract of T1B sends `null` for the remaining capacity of an unlimited
	# rule, so the UI has one dedicated label for it.
	"Unlimited": "Không giới hạn",
	"Normalized share": "Tỷ lệ chuẩn hóa",
	"Remaining capacity": "Sức chứa còn lại",
	"Eligibility": "Đủ điều kiện",
	"Eligible": "Đủ điều kiện",
	"Excluded": "Bị loại",
	"Preview distribution": "Xem trước phân phối",
	"Confirm distribution": "Xác nhận phân phối",
	"Preview first. Executing is a separate, explicitly confirmed action.": "Phải xem trước trước. Thực thi là hành động riêng biệt và phải được xác nhận rõ ràng.",
	"Preview ID": "Mã xem trước",
	"Selection fingerprint": "Vân tay lựa chọn",
	"Expires at": "Hết hạn lúc",
	"Eligible customers": "Khách hàng đủ điều kiện",
	"Excluded customers": "Khách hàng bị loại",
	"Planned": "Dự kiến",
	"Assigned now": "Đang được giao",
	"Assigned in this run": "Giao trong đợt này",
	"Assigned in the run": "Giao trong đợt phân phối",
	"Failed": "Thất bại",
	"No campaign yet.": "Chưa có chiến dịch nào.",
	"No assignment rule yet.": "Chưa có quy tắc phân công nào.",
	"The preview has expired. Run the preview again.": "Bản xem trước đã hết hạn. Hãy chạy lại xem trước.",
	"The rules changed after the preview. Run the preview again.": "Quy tắc đã thay đổi sau khi xem trước. Hãy chạy lại xem trước.",
	"The distribution state changed after the preview. Run the preview again.": "Trạng thái phân phối đã thay đổi sau khi xem trước. Hãy chạy lại xem trước.",
	"The campaign is already distributed by another executor.": "Chiến dịch đã được một bộ thực thi khác phân phối.",
	"Distribution completed.": "Đã phân phối xong.",
	"Distribution completed with errors.": "Đã phân phối xong nhưng có lỗi.",
	"Run ID": "Mã đợt chạy",
	"Created": "Đã tạo",
	"Errors": "Số lỗi",
	# --- My customers --------------------------------------------------------
	"Display name": "Tên hiển thị",
	"Assigned at": "Giao lúc",
	"Work item status": "Trạng thái công việc",
	"You have not been assigned any customer yet.": "Anh/chị chưa được giao khách hàng nào.",
	"Search by name or phone number.": "Tìm theo tên hoặc số điện thoại.",
	# --- Dashboard -----------------------------------------------------------
	"Unique customers in scope": "Khách hàng duy nhất trong phạm vi",
	"Eligible in the pool": "Đủ điều kiện trong kho",
	"Assigned work items": "Công việc đã giao",
	"Unassigned or conflicted work items": "Công việc chưa giao hoặc xung đột",
	"Active campaigns": "Chiến dịch đang hoạt động",
	"Failed batches": "Lô nhập lỗi",
	"Assignment events": "Số sự kiện phân công",
	"Current owners": "Người đang phụ trách",
	"Per assignee": "Theo người được giao",
	"Share": "Tỷ lệ",
	"Currently assigned": "Đang được giao",
	"Remaining capacity": "Sức chứa còn lại",
	"No data in the current scope.": "Không có dữ liệu trong phạm vi hiện tại.",
	"Per campaign": "Theo chiến dịch",
	"Data as of": "Dữ liệu tính đến",
	# --- Error codes (machine readable, kept as-is) -------------------------
	"INVALID_PHONE": "INVALID_PHONE",
	"DUPLICATE": "DUPLICATE",
	"ALREADY_ASSIGNED": "ALREADY_ASSIGNED",
	"RULE_CHANGED": "RULE_CHANGED",
	"CAPACITY_EXCEEDED": "CAPACITY_EXCEEDED",
	"CONFLICT": "CONFLICT",
	"FORBIDDEN": "FORBIDDEN",
	"PARTIAL_SUCCESS": "PARTIAL_SUCCESS",
	"RETRYABLE_FAILURE": "RETRYABLE_FAILURE",
	"UNSUPPORTED_FORMAT": "UNSUPPORTED_FORMAT",
	"FILE_TOO_LARGE": "FILE_TOO_LARGE",
	"TOO_MANY_ROWS": "TOO_MANY_ROWS",
	"INVALID_REQUEST": "INVALID_REQUEST",
	"NOT_FOUND": "NOT_FOUND",
	"PREVIEW_EXPIRED": "PREVIEW_EXPIRED",
	"NO_ELIGIBLE_LEADS": "NO_ELIGIBLE_LEADS",
	"CAPACITY_FULL": "CAPACITY_FULL",
	# --- Eligibility reasons -------------------------------------------------
	"no_canonical_identity": "Chưa có định danh chuẩn",
	"work_item_exists_in_campaign": "Đã có công việc trong chiến dịch",
	"active_claim_in_other_campaign": "Đang được giữ bởi chiến dịch khác",
	"owned_outside_cskh": "Được quản lý ngoài phạm vi CSKH",
	"no_assignment_rule": "Chưa có quy tắc phân công",
	"user_disabled": "Tài khoản đã bị vô hiệu hóa",
	"user_out_of_team_scope": "Ngoài phạm vi đội được phép",
	"user_is_not_sales_user": "Không phải nhân viên bán hàng",
	"no_positive_weight": "Trọng số không dương",
	"capacity_full": "Đã đầy sức chứa",
	"no_eligible_assignee": "Không có người nhận đủ điều kiện",
	"assignment_failed": "Phân công thất bại",
	# --- Import row classifications -----------------------------------------
	"valid": "Hợp lệ",
	"duplicate_in_file": "Trùng trong tệp",
	"duplicate_in_crm": "Trùng trong CRM",
	"conflict_active_campaign": "Xung đột chiến dịch đang hoạt động",
	"invalid": "Không hợp lệ",
	# --- Backend contract messages -------------------------------------------
	"A queued, revoked or closed work item must not have an assignee.": "Công việc đang chờ, đã thu hồi hoặc đã đóng không được có người phụ trách.",
	"A revocation event must not have a target user.": "Sự kiện thu hồi không được có người nhận.",
	"An assigned work item requires an assignee.": "Công việc đã giao bắt buộc có người phụ trách.",
	"An assigned work item requires an assignment timestamp.": "Công việc đã giao bắt buộc có thời điểm phân công.",
	"An assignment event requires a target user.": "Sự kiện phân công bắt buộc có người nhận.",
	"Assignment history is append-only; use a compensating event.": "Lịch sử phân công chỉ được ghi thêm; hãy dùng sự kiện bù trừ.",
	"Column mapping must be a JSON object.": "Ánh xạ cột phải là một đối tượng JSON.",
	"Import counters must not be negative.": "Bộ đếm nhập dữ liệu không được âm.",
	"Import source files must be private.": "Tệp nguồn nhập dữ liệu phải là riêng tư.",
	"Only an active work item may hold an active claim.": "Chỉ công việc đang hoạt động mới được giữ quyền sở hữu đang hoạt động.",
	"Resolve the active work items before closing the campaign; nothing is released implicitly.": "Hãy xử lý các công việc đang hoạt động trước khi đóng chiến dịch; không có gì được giải phóng ngầm.",
	"The active claim must reference the same lead and campaign as its work item.": "Quyền sở hữu đang hoạt động phải trỏ tới cùng khách hàng và chiến dịch với công việc của nó.",
	"The actual payload must be valid JSON.": "Dữ liệu kết quả phải là JSON hợp lệ.",
	"The allocator state must be a JSON object.": "Trạng thái bộ phân bổ phải là một đối tượng JSON.",
	"The allocator state must be valid JSON.": "Trạng thái bộ phân bổ phải là JSON hợp lệ.",
	"The assignee must be an enabled Sales User.": "Người được giao phải là nhân viên bán hàng đang hoạt động.",
	"The canonical lead must have a mobile number.": "Khách hàng chuẩn phải có số điện thoại di động.",
	"The distribution run": "Đợt phân phối",
	"The end date must not precede the start date.": "Ngày kết thúc không được trước ngày bắt đầu.",
	"The event id is required so the event stays idempotent.": "Cần có mã sự kiện để sự kiện giữ tính idempotent.",
	"The import batch": "Lô nhập dữ liệu",
	"The phone identity must be a canonical Vietnamese mobile number.": "Định danh số điện thoại phải là số di động Việt Nam chuẩn hóa.",
	"The phone identity must match the canonical lead mobile number.": "Định danh số điện thoại phải khớp với số di động của khách hàng chuẩn.",
	"The preview payload must be valid JSON.": "Dữ liệu xem trước phải là JSON hợp lệ.",
	"The rule version must be positive.": "Phiên bản quy tắc phải là số dương.",
	"The selection fingerprint is required.": "Cần có vân tay lựa chọn.",
	"The selection must be a JSON object.": "Tập lựa chọn phải là một đối tượng JSON.",
	"This campaign, assignee and version already has a rule.": "Chiến dịch, người được giao và phiên bản này đã có quy tắc.",
	"Weight and version must be positive; capacity must not be negative.": "Trọng số và phiên bản phải là số dương; sức chứa không được âm.",
	"{0} cannot move from {1} to {2}.": "{0} không thể chuyển từ {1} sang {2}.",
}


def translation_rows():
	"""Return the CSV rows of the P1 translations."""
	buffer = io.StringIO()
	writer = csv.writer(buffer, lineterminator="\n")
	for message in sorted(TRANSLATIONS):
		writer.writerow([message, TRANSLATIONS[message]])
	return buffer.getvalue()


def build_translation_rows():
	"""Append the missing P1 rows to `translations/vi.csv`."""
	import os

	import frappe

	path = os.path.join(
		os.path.dirname(__file__), "translations", "vi.csv"
	)
	existing = set()
	if os.path.exists(path):
		with open(path, "r", encoding="utf-8", newline="") as handle:
			for row in csv.reader(handle):
				if row:
					existing.add(row[0])

	missing = [message for message in TRANSLATIONS if message not in existing]
	if not missing:
		return {"added": 0, "path": path}

	with open(path, "a", encoding="utf-8", newline="") as handle:
		writer = csv.writer(handle, lineterminator="\n")
		for message in sorted(missing):
			writer.writerow([message, TRANSLATIONS[message]])

	return {"added": len(missing), "path": path}


if __name__ == "__main__":
	print(translation_rows())
