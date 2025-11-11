#!/bin/bash
# scripts/backup.sh
set -e

DB_USER="${POSTGRES_USER:-hunter}"
DB_NAME="${POSTGRES_DB:-hunter}"
BACKUP_DIR="/backups"
DATE=$(date +%Y-%m-%d_%H-%M-%S)

# Đảm bảo thư mục sao lưu tồn tại
mkdir -p $BACKUP_DIR

# Tạo backup (sử dụng biến môi trường POSTGRES_PASSWORD)
echo "Starting backup of database: $DB_NAME..."
pg_dump -U $DB_USER -d $DB_NAME -F c -b -f $BACKUP_DIR/hunter_db_$DATE.dump

# Xóa các bản sao lưu cũ hơn 7 ngày (giữ 7 bản)
echo "Cleaning up old backups..."
find $BACKUP_DIR -name 'hunter_db_*.dump' -mtime +7 -delete

echo "Backup complete."
