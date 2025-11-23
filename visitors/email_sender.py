import os
import smtplib
import csv
import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from django.template.loader import render_to_string
import django
from django.conf import settings

# Django 環境初期化
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'visitor_schedule.settings')
django.setup()

def read_mailing_list():
    path = os.path.join(settings.BASE_DIR, 'visitors', 'mailing_list.csv')
    recipients = []
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            if row and row[0].strip():
                recipients.append(row[0].strip())
    return recipients

def is_holiday(today):
    holiday_file = os.path.join(settings.BASE_DIR, 'visitors', 'holidays.csv')
    if not os.path.exists(holiday_file):
        return False
    with open(holiday_file, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            if row and row[0].strip() == today.strftime('%Y-%m-%d'):
                return True
    return False

def read_visitors(today):
    path = os.path.join(os.path.dirname(__file__), '..', 'visitor_list.csv')
    path = os.path.abspath(path)  # フルパスに変換して安全に

    visitors = []
    if not os.path.exists(path):
        print(f"[ERROR] visitor_list.csv が見つかりません: {path}")
        return visitors

    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        print("[DEBUG] ヘッダー:", reader.fieldnames)
        for row in reader:
            print("[DEBUG] 行データ:", row)
            date_str = row.get('visit_date', '').strip()
            if not date_str:
                continue
            try:
                if '/' in date_str:
                    visit_date = datetime.datetime.strptime(date_str, '%Y/%m/%d').date()
                else:
                    visit_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
                if visit_date >= today:
                    row['visit_date'] = visit_date
                    row['is_cancelled'] = row.get('cancelled', '').strip().lower() == 'true'
                    visitors.append(row)
            except Exception as e:
                print(f"[ERROR] 日付変換失敗: {row} → {e}")
                continue
    
    visitors.sort(key=lambda x: (x.get('visit_date', ''), x.get('visit_time', '00:00')))

    print(f"[DEBUG] 読み込まれた来訪予定件数: {len(visitors)}")
    return visitors

def send_daily_email():
    today = datetime.date.today()
    if is_holiday(today):
        print("今日は休日のため、送信しません。")
        return

    recipients = read_mailing_list()
    if not recipients:
        print("メーリングリストが空です。")
        return

    visitors = read_visitors(today)
    print("[DEBUG] 来訪予定データ:", visitors)
    html_content = render_to_string('visitors/email_template.html', {'visitors': visitors})

    msg = MIMEMultipart('alternative')
    msg['Subject'] = '【来訪予定通知】本日以降の来訪一覧'
    #msg['From'] = 'NGLS-CS-INFO <cs_info@ngls.sh.cn>'
    msg['From'] = 'cs_info@ngls.sh.cn'
    msg['To'] = ", ".join(recipients)

    part = MIMEText(html_content, 'html', 'utf-8')
    msg.attach(part)

    try:
        print("[DEBUG] SMTP接続開始")
        server = smtplib.SMTP('smtp.qiye.aliyun.com', 587)
        server.set_debuglevel(1)
        server.starttls()
        print("[DEBUG] ログイン中")
        server.login('cs_info@ngls.sh.cn', 'NGLScs9981')
        print("[DEBUG] メール送信中")
        server.sendmail(msg['From'], recipients, msg.as_string())
        server.quit()
        print("[DEBUG] メール送信完了")
    except Exception as e:
        print("[DEBUG] メール送信失敗:", e)

if __name__ == '__main__':
    today = datetime.date.today()
    visitors = read_visitors(today)
    print(f"本日: {today}")
    print("読み込まれた来訪予定:")
    for v in visitors:
        print(v)
