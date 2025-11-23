import os
import sys
import django

# プロジェクトルートをパスに追加（manage.pyと同じ場所）
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# settings モジュールを指定
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'visitor_schedule.settings')

# Djangoのセットアップ
django.setup()

# Django 初期化の後にインポートすること！
from visitors.email_sender import send_daily_email

# メール送信処理を実行
send_daily_email()

# from visitors.email_sender import send_daily_email

# if __name__ == '__main__':
#     send_daily_email()