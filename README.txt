FB AUTO POST WEB - HƯỚNG DẪN CÀI ĐẶT

1. Giải nén toàn bộ thư mục.
2. Cài Python 3.12 hoặc 3.13 (khuyến nghị Python 3.12), tích chọn Add Python to PATH.
3. Nhấp đúp CHAY_WEB_WINDOWS.bat.
4. Chờ cài Flask, Playwright và Chromium. Trình duyệt sẽ mở tại:
   http://127.0.0.1:5000
5. Trong web, vào Cài đặt > Mở Facebook. Đăng nhập thủ công trên cửa sổ Chromium.
6. Quay lại web, soạn nội dung, thêm ảnh, dán nhóm và bấm Đăng bài ngay.

LƯU Ý QUAN TRỌNG
- Đây là web cục bộ (localhost), backend phải luôn chạy trong cửa sổ CMD.
- Không đóng cửa sổ CMD trong khi đăng hoặc chờ lịch.
- Lịch đăng chỉ chạy khi máy tính không ngủ, backend đang chạy và phiên Facebook còn đăng nhập.
- Không nhập mật khẩu Facebook vào web. CAPTCHA/2FA cần tự xử lý.
- Kết quả "Cần đối chiếu" không có nghĩa là đăng thất bại. Hãy kiểm tra Facebook trước khi đăng lại.
- Dữ liệu SQLite và ảnh lưu trong thư mục FB_Auto_Post_V4 ở thư mục người dùng.
- Web localhost không được public ra Internet. Đừng mở cổng router hoặc chia sẻ thư mục dữ liệu.
- Chỉ đăng vào nhóm mà tài khoản có quyền và tuân thủ quy định Facebook.

CẤU TRÚC
app.py       Backend Flask + API
core.py      Logic đăng Facebook, SQLite, chống trùng, lịch sử, ảnh, lịch hẹn
static/      Giao diện web
requirements.txt
CHAY_WEB_WINDOWS.bat
