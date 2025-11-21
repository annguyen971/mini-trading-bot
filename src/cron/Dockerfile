# Sử dụng base image Alpine nhỏ gọn
FROM alpine:latest

# Cài đặt "dcron" (một trình cron nhẹ)
RUN apk add --no-cache dcron docker-cli

# Tạo thư mục custom scripts (nơi volume của bạn sẽ mount vào)
RUN mkdir -p /etc/periodic/custom

# Copy crontab file
COPY crontab /etc/crontabs/root

# Lệnh để chạy cron ở chế độ foreground
CMD ["crond", "-f", "-l", "8"]
