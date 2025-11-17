# Sử dụng base image Alpine nhỏ gọn
FROM alpine:latest

# Cài đặt "dcron" (một trình cron nhẹ)
RUN apk add --no-cache dcron

# Tạo thư mục custom scripts (nơi volume của bạn sẽ mount vào)
RUN mkdir -p /etc/periodic/custom

# Lệnh để chạy cron ở chế độ foreground
CMD ["crond", "-f", "-l", "8"]
