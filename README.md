# Binance Market Console

Ứng dụng PyQt6 hiển thị giá Binance USD-M Futures cho tài sản có market cap trên
100 triệu USD. Hợp đồng, giá và nến lấy ưu tiên từ Binance Futures. Binance không cung
cấp market cap trong API Futures, nên ứng dụng lấy market cap từ CoinLore trước,
CoinGecko làm nguồn dự phòng; ticker trùng bị loại để tránh ghép nhầm tài sản.

## Cài đặt và chạy trên Windows

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python src\main.py
```

## Build ứng dụng Windows

Mở PowerShell tại thư mục dự án và chạy lệnh sau. Cách này chỉ bypass execution policy
cho tiến trình build, không thay đổi policy của máy:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1
```

Script tự tạo `.venv`, cài các gói trong `requirements-build.txt` và đóng gói bằng
PyInstaller. Mặc định tạo bản **onedir** để chạy ổn định hơn với PyQt:
`dist\BinanceMarketConsole\BinanceMarketConsole.exe`. Khi gửi cho máy Windows khác,
hãy gửi cả thư mục `dist\BinanceMarketConsole`, không chỉ file `.exe`.
Có thể nhấp đúp `build_windows.bat` để chạy build và xem kết quả; hãy đóng ứng dụng
BinanceMarketConsole nếu đang chạy trước khi build lại.

Muốn tạo một file `.exe` duy nhất, chạy:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1 -OneFile
```

File sẽ nằm tại `dist\BinanceMarketConsole.exe`; lần mở đầu có thể chậm hơn. Máy chạy
ứng dụng cần Windows tương thích và kết nối Internet để lấy dữ liệu Binance/CoinLore/
CoinGecko.
Nên build trên Windows để tạo gói Windows.

Đăng nhập bằng tài khoản ban đầu đã được cấu hình trong ứng dụng. Xác thực dùng
PBKDF2-HMAC-SHA256 với salt; mật khẩu không được lưu dưới dạng văn bản thuần.
Danh sách tài khoản nằm trong `src/app/backend/auth.py`; dùng
`create_password_record()` để tạo salt/hash khi thêm tài khoản, không lưu mật khẩu rõ.
Đây là lớp đăng nhập cục bộ cho giao diện desktop, không thay thế bảo vệ hệ điều hành
hay cơ chế xác thực máy chủ.

Nhấn **Bắt đầu quét** để cập nhật danh sách giá với thời gian chờ khoảng 1 giây giữa
các vòng quét (thời gian phản hồi API có thể làm vòng quét lâu hơn). Nhấn **Chart** ở dòng hợp
đồng trong bảng thị trường hoặc vị thế trong bảng lệnh đang mở để mở cửa sổ biểu đồ riêng.
Chart có nến xanh/đỏ, volume, trục giá, crosshair, đường giá mới nhất; hỗ trợ khung
1m, 3m, 5m, 15m, 1h, 4h, 1d và tự cập nhật. Chart giá phủ thêm EMA 20 và Bollinger
Bands 20 kỳ với 2 độ lệch chuẩn.
Chỉ báo **Mạng** hiển thị độ trễ phản hồi Binance Futures: xanh ≤ 500 ms, vàng
501–1500 ms, đỏ > 1500 ms hoặc khi không có phản hồi.

Nút ngày/đêm ở màn hình đăng nhập, dashboard hoặc chart sẽ đổi giao diện đồng bộ.
Lựa chọn được lưu cục bộ và áp dụng lại ở lần chạy sau.

Giao diện cho phép chọn riêng chiến thuật **short_p** hoặc **long_p** và bật/tắt các
khung nến 1m, 3m, 5m, 15m, 1h, 4h (mặc định bật cả sáu). Tín hiệu trên bất kỳ khung
đang bật nào cũng có thể vào lệnh. Chiến thuật kiểm tra nến đang chạy mỗi vòng quét;
nến trước đã đóng chỉ dùng làm mốc so sánh. Sau khi phát tín hiệu, cùng một nến không
phát tín hiệu lần nữa. **short_p** yêu cầu giá đã tăng vượt giá mở cửa trong nến hiện tại
rồi đảo chiều đỏ; đỉnh nến hiện tại cao hơn đỉnh nến trước, giá hiện tại thấp hơn giá đóng
cửa nến trước, râu trên ít nhất gấp đôi râu dưới, râu dưới không dài hơn thân nến, và giá
hiện tại nằm trong 25% thấp nhất của biên độ; nến hiện tại hoặc nến đã đóng ngay trước đó
có đỉnh chạm/vượt Upper Bollinger Band (20, 2). **long_p** yêu cầu giá đã giảm dưới giá
mở cửa trong nến hiện tại rồi đảo chiều xanh; đáy nến hiện tại thấp hơn đáy nến trước,
giá hiện tại cao hơn giá đóng cửa nến trước, râu dưới ít nhất gấp đôi râu trên, râu trên
không dài hơn thân nến, và giá hiện tại nằm trong 25% cao nhất; nến hiện tại hoặc nến đã
đóng ngay trước đó có đáy chạm/vượt Lower Bollinger Band (20, 2). Telegram ghi chiến thuật
và khung nến của tín hiệu vào lệnh; thông báo bắt đầu quét cũng liệt kê các khung đang bật.
Khi vị thế do chiến thuật mở, ứng dụng theo dõi thêm mẫu hình đối hướng trên cùng khung
nến. Tín hiệu đảo chiều chỉ đóng vị thế sau khi nến vào lệnh đã kết thúc và nến kế tiếp
đang chạy thỏa mẫu hình đối hướng; không cần chờ nến kế tiếp đóng. Lệnh mới không được
mở lại theo chính tín hiệu đã đóng lệnh, mà chờ một tín hiệu vào lệnh mới.
Trước khi vào lệnh, bot cũng kiểm tra nến đã đóng ngay trước đó trên cùng khung: không
SHORT nếu râu dưới ít nhất gấp đôi râu trên và lớn hơn thân nến; không LONG nếu râu trên
ít nhất gấp đôi thân nến và giá đóng nằm trong 25% biên độ phía dưới. Nến thân bằng 0
hoặc thiếu dữ liệu không bị xem là mẫu hình cản lệnh.

Tab **Thống kê chiến thuật** tổng hợp riêng theo chiến thuật, khung nến và
Simulation/Testnet trong phiên ứng dụng hiện tại; mỗi dòng cho biết `short_p` hoặc
`long_p` phát sinh giao dịch trên khung nào. Dữ liệu reset khi thoát ứng dụng.
Lệnh mở/đóng chỉ tính
khi vị thế được xác nhận mở/đóng, không tính lệnh LIMIT còn chờ. Kết quả thắng/thua và
tổng tiền dùng PnL ròng sau phí; hòa vốn hoặc kết quả đóng ngoài ứng dụng chưa xác định
được hiển thị riêng và không tính vào tỉ lệ thắng. Lệnh mở thủ công qua Telegram được
thống kê ở nhóm **Thủ công/Telegram**. Bảng **Lệnh đang mở** hiển thị thêm chiến thuật
đã mở vị thế, tỉ lệ râu thuận hướng/thân của nến đã đóng gần nhất trên khung vị thế,
và tự sắp xếp tỉ lệ cao hơn lên trước. LONG dùng râu dưới, SHORT dùng râu trên; vị thế
không có đủ dữ liệu hoặc nến thân bằng 0 hiển thị `—` và xếp sau.

Dashboard cho phép chọn **Simulation** hoặc **Binance Testnet**, nhập ký quỹ tối đa
mỗi lệnh, số vị thế mở đồng thời, % chốt lời và % dừng lỗ. Cả hai chế độ dùng đòn bẩy
cố định x2: tối đa 50 USDT ký quỹ tạo vị thế tối đa 100 USDT/lệnh. Simulation mặc định
có vốn mô phỏng 1.000 USDT và tối đa 20 vị thế; số dư khả dụng trừ ký quỹ đang sử dụng,
còn PnL và phí được tính trên giá trị vị thế. Testnet mặc định tối đa 3 vị thế và đặt
đòn bẩy x2 cho từng mã trước khi gửi lệnh. Cả hai chế độ mặc định chốt lời 1% và dừng
lỗ 1%. Ngưỡng lãi/lỗ được tính trên giá trị vị thế ban đầu, theo PnL ròng ước tính sau
phí vào/ra (0.04% mỗi chiều), chưa gồm funding. Khi chạm ngưỡng, ứng dụng đóng vị thế
và cộng PnL ước tính vào PnL đã chốt. Các giới hạn được kiểm tra lại trong backend.
Ngoài TP/SL từng lệnh, ứng dụng tự đóng toàn bộ vị thế đang quản lý khi tổng PnL ròng
chưa chốt vượt 0.5% vốn: vốn ban đầu Simulation hoặc số dư ví Testnet cập nhật gần nhất.
Khi kích hoạt trên Testnet, các lệnh chờ do ứng dụng đặt cũng bị hủy; vị thế ngoài phiên
không bị ảnh hưởng. Lệnh vào mới được giữ lại đến vòng quét tiếp theo sau khi chốt mục tiêu.
Thanh trạng thái đầu dashboard hiển thị số dư ví và số dư khả dụng; Simulation dùng vốn
mô phỏng, còn Testnet lấy số liệu tài khoản Binance khi bắt đầu quét và cập nhật định kỳ.

Simulation mô phỏng vào/đóng lệnh. Testnet chỉ gửi lệnh khi có tín hiệu, không dùng tiền
thật, và bị khóa trừ khi cấu hình đủ biến môi trường sau trong file `.env` ở thư mục dự án:

```text
BINANCE_TESTNET_TRADING_ENABLED=true
BINANCE_TESTNET_API_KEY=
BINANCE_TESTNET_API_SECRET=
BINANCE_TESTNET_MAX_NOTIONAL_USDT=100
```

Chỉ dùng API key Binance Futures **Testnet**; không đưa key/secret vào mã nguồn hoặc
commit file `.env`. Giá trị vị thế sau khi nhân đòn bẩy không được vượt
`BINANCE_TESTNET_MAX_NOTIONAL_USDT` (mặc định 100 USDT). Ứng dụng không cung cấp chế độ đặt lệnh Mainnet. Market cap được cache trong phiên
chạy 30 phút; nếu CoinLore lỗi, CoinGecko được dùng dự phòng. CoinGecko được gọi qua
`/coins/markets` theo thứ hạng vốn hóa, không gọi `/coins/list`. Lỗi 429 của
một nguồn được ghi cảnh báo và không ngăn sử dụng dữ liệu đã có từ nguồn còn lại.

Telegram là tùy chọn; thông báo hoạt động cả trong Simulation và Testnet. Lệnh truy
vấn/điều khiển được xử lý khi đang quét. Trong Simulation, bot hỗ trợ `/account`,
`/positions`, `/pnl`, mở lệnh MARKET, `/close` và `/close_all` trên số dư mô phỏng;
ký quỹ mỗi lệnh không vượt 50 USDT (vị thế tối đa 100 USDT ở đòn bẩy x2). Lệnh LIMIT, hủy lệnh và xác nhận đóng toàn bộ
chỉ khả dụng trong Testnet. Khi Telegram đã cấu hình, ứng dụng gửi thông báo sau khi
dashboard mở thành công và mỗi lần nhấn **Bắt đầu quét** hoặc **Dừng**. Hãy gửi
`/start` cho bot trước để Telegram cho phép bot nhắn riêng. Sao chép `.env.example`
thành `.env` tại thư mục gốc khi chạy
mã nguồn, hoặc đặt `.env` cạnh `.exe` khi dùng bản đóng gói. Cấu hình `TELEGRAM_BOT_TOKEN` và
`TELEGRAM_ALLOWED_USER_IDS` trong `.env`; bot chỉ nhận lệnh trong chat riêng từ các ID
được cho phép. Thanh trạng thái Telegram trên giao diện sẽ chuyển sang **đã kết nối**
khi gửi thông báo thành công. Hãy thu hồi và tạo token mới nếu token từng được chia sẻ trong chat hoặc
nơi không an toàn. Cách cài đặt, cú pháp lệnh, giới hạn notional và xác nhận
`/close_all` được ghi trong
[examples/telegram_commands.md](./examples/telegram_commands.md).

Console log được lưu dưới dạng UTF-8 theo ngày trong thư mục `applog` cạnh thư mục dự án
khi chạy source, hoặc cạnh `.exe` khi chạy bản đóng gói.

Bảng **Lệnh đang mở** nhận vị thế từ executor và hiển thị PnL chưa chốt; Testnet chỉ đóng
vị thế do phiên ứng dụng hiện tại quản lý, không tự đóng vị thế tồn tại trước khi chạy.
PnL đã chốt được ghi nhận trong phạm vi phiên chạy; Testnet hiển thị giá trị ước tính,
không đồng bộ lịch sử PnL/funding từ Binance. Nếu dừng hoặc thoát ứng dụng khi còn vị thế
Testnet, vị thế vẫn mở trên sàn và việc theo dõi TP/SL tự động dừng. Long hiển thị xanh,
Short đỏ.

## Kiểm thử

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Để gửi thử **một tin nhắn Telegram thật**, cấu hình `TELEGRAM_BOT_TOKEN` và
`TELEGRAM_ALLOWED_USER_IDS` trong `.env`, rồi bật riêng bài test tích hợp:

```powershell
$env:PYTHONPATH = "src"
$env:RUN_TELEGRAM_LIVE_TEST = "1"
python -m unittest discover -s tests -p "test_telegram_live.py" -v
```

Test gửi tới user ID đầu tiên trong danh sách cho phép. Mặc định test bị bỏ qua,
không gọi Telegram; không dán token vào mã nguồn.
