# T1A — Một việc: hoàn tất hồ sơ rollback đúng Live N-1 để T1C review

Review và hoàn thiện [phụ lục Tier 3](tier3-live-nminus1-annex.md), đối chiếu với source patch và báo cáo chỉ đọc đã có. Không truy vấn lại Live chỉ để phát hành readiness giống cũ.

Chuẩn bị snapshot/change-set/restore checklist cho CRM Form Script, System Settings.default_app và các User thực bị patch thay đổi. Bảo toàn NULL, missing values và admin overrides; không blanket UPDATE. Bổ sung ma trận clone upgrade → rollback → upgrade lại, including Patch Log và after_migrate.

Ghi rõ target N-1 `152d057` khác baseline CI `4678301`; giữ hook/sidebar/theme hợp lệ của N-1. Chuẩn bị kế hoạch capacity/headroom cho Config B+ và phương án UAT hiện hữu, không coi đề xuất stop UAT là quyền đã được cấp.

Đầu ra: runbook/checklist có thể review, các prerequisite còn thiếu và criteria đóng finding. Không migrate, restore, tạo clone/stack, stop UAT, chạy G18 hoặc deploy trong phạm vi chỉ đọc hiện tại. Không ký thay người trực/UAT/Owner.
