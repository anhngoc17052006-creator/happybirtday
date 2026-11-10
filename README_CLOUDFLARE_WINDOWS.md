# FB Auto Post — Windows + Cloudflare Tunnel (miễn phí)

## Gói này giữ lại những gì?

Giữ nguyên `app.py`, `core.py`, `static/`, API và cơ sở dữ liệu SQLite của gói FB Auto Post gốc. Các chức năng soạn bài, đăng bài qua Playwright, đăng nhập Facebook, quản lý nhóm, ảnh, thư viện, lịch đăng, lịch sử, nhật ký và cài đặt vẫn dùng backend cũ.

Bổ sung: xác thực website bằng tài khoản/mật khẩu riêng và các file chạy Windows/Cloudflare. Không thay đổi mật khẩu Facebook hay tự động đăng nhập Facebook.

## 1. Cài Python

- Cài Python **3.12 hoặc 3.13** bản Windows 64-bit.
- Khi cài, tích `Add Python to PATH`.
- Không cần cài Docker, không cần VPS, không cần mua tên miền.

## 2. Đặt mật khẩu cho website (bắt buộc)

1. Sao chép `fb_web_config.bat.example` thành `fb_web_config.bat`.
2. Mở `fb_web_config.bat` bằng Notepad.
3. Đổi `FB_WEB_USER` và `FB_WEB_PASSWORD` thành tên đăng nhập và mật khẩu riêng. Mật khẩu nên dài ít nhất 20 ký tự, không dùng lại mật khẩu Facebook.
4. Không gửi hoặc tải `fb_web_config.bat` lên GitHub công khai.

## 3. Cài và chạy backend

Nhấp đúp `CHAY_WEB_WINDOWS.bat`. Lần đầu chương trình cài Flask, Playwright và Chromium; cần Internet và có thể mất vài phút. Sau đó trình duyệt mở `http://127.0.0.1:5000`. Đăng nhập bằng thông tin ở bước 2.

Giữ cửa sổ backend đang chạy. Không đóng cửa sổ này khi dùng ứng dụng hoặc khi đang chờ lịch đăng.

## 4. Cài Cloudflare Tunnel

Tải `cloudflared` cho Windows tại:
https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

Cài đặt theo hướng dẫn của Cloudflare hoặc đặt file thực thi tên `cloudflared.exe` ngay trong thư mục `FB_Auto_Post_Web`. Có thể kiểm tra bằng lệnh `cloudflared --version` trong CMD.

## 5. Tạo link HTTPS để truy cập từ xa

1. Đảm bảo backend ở bước 3 vẫn chạy.
2. Nhấp đúp `CHAY_CLOUDFLARE_TUNNEL.bat`.
3. Chờ Cloudflare in ra một địa chỉ dạng `https://ten-ngau-nhien.trycloudflare.com`.
4. Mở địa chỉ đó trên máy tính/điện thoại khác và đăng nhập bằng tài khoản/mật khẩu website đã tạo.
5. Muốn truy cập từ nơi khác, gửi link này cho chính bạn. Ai có link vẫn cần mật khẩu website.

Có thể chạy `CHAY_CA_HAI.bat` để mở backend và tunnel tự động trong hai cửa sổ. Nếu lần đầu cài thư viện chưa xong hoặc backend khởi động chậm, nên chạy từng bước riêng như trên.

## 6. Đăng nhập Facebook

Trong ứng dụng chọn **Cài đặt → Mở Facebook** (hoặc nút đăng nhập tương ứng). Chromium sẽ mở trên máy Windows đang chạy backend. Đăng nhập thủ công và tự xử lý CAPTCHA/2FA nếu Facebook yêu cầu. Không nhập mật khẩu Facebook vào form của ứng dụng.

## Dữ liệu và lịch đăng

- SQLite, ảnh tải lên và hồ sơ trình duyệt Facebook vẫn được lưu trên máy Windows theo đường dẫn dữ liệu mà `core.py` hiện dùng (mặc định `FB_Auto_Post_V4` trong thư mục người dùng Windows).
- Không xóa thư mục dữ liệu này nếu muốn giữ lịch sử, ảnh và phiên Facebook.
- Lịch đăng chỉ chạy khi máy Windows bật, không ngủ, backend đang chạy, Internet ổn định và phiên Facebook còn hiệu lực.
- Cloudflare chỉ tạo đường truyền HTTPS; nó không chạy ứng dụng thay cho máy tính.

## Dừng và mở lại

- Dừng tunnel: nhấn `Ctrl+C` trong cửa sổ tunnel.
- Dừng backend: nhấn `Ctrl+C` trong cửa sổ backend.
- Lần sau chạy lại backend rồi tunnel. Quick Tunnel có thể cấp **đường dẫn mới** mỗi lần khởi động; hãy dùng URL mới được in ra.

## Lưu ý bảo mật và giới hạn

- Backend chỉ lắng nghe `127.0.0.1:5000`, không mở cổng 5000 trên router và không đổi host thành `0.0.0.0`.
- Lớp đăng nhập website dùng HTTP Basic Auth; trình duyệt sẽ hiện hộp thoại đăng nhập. Không dùng chung mật khẩu với Facebook.
- Không chia sẻ file cấu hình, thư mục dữ liệu, ảnh bằng chứng hoặc hồ sơ Chromium. Hồ sơ Chromium có thể chứa phiên Facebook.
- Quick Tunnel là dịch vụ miễn phí cho thử nghiệm/phát triển, không có cam kết uptime; URL ngẫu nhiên và thay đổi khi khởi tạo lại. Không phù hợp nếu cần website luôn trực tuyến 24/7.
- Nếu muốn lịch đăng chạy cả khi PC tắt, phải chuyển backend sang máy chủ luôn bật (thường có chi phí).
- Chỉ đăng vào nhóm mà tài khoản có quyền và tuân thủ quy định Facebook. Facebook có thể thay đổi giao diện/cơ chế chống tự động hóa; không có bảo đảm mọi bài đăng đều thành công.

## Khắc phục nhanh

- **401 / hộp thoại đăng nhập lặp:** kiểm tra lại user/password trong `fb_web_config.bat`; thử cửa sổ ẩn danh.
- **Không mở được link:** kiểm tra cả hai cửa sổ backend và tunnel còn chạy; kiểm tra máy có Internet.
- **Tunnel báo không tìm thấy cloudflared:** cài cloudflared hoặc đặt `cloudflared.exe` cạnh các file BAT.
- **Lỗi Playwright/Chromium:** chạy lại `CHAY_WEB_WINDOWS.bat` để cài dependencies; dùng Python 3.12/3.13.
- **Lịch không chạy:** không để máy ngủ; không đóng backend; kiểm tra phiên Facebook trong Chromium.
