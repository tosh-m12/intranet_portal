# working_schedule/utils.py
from datetime import date
from .models import HolidayDate, SpecialWorkingDay


def is_holiday(day: date) -> bool:
    """
    会社の稼働カレンダーにおける「休日」判定。
    ルール:
      1) 特別稼働日に登録されていれば → 休日ではない（必ず False）
      2) 上記以外で、土日なら → 休日（True）
      3) 平日でも HolidayDate に登録されていれば → 休日（True）
      4) それ以外 → 稼働日（False）
    """
    # 1) 特別稼働日に登録されていたら、休日扱いにしない
    if SpecialWorkingDay.objects.filter(date=day).exists():
        return False

    # 2) 土日判定（Monday=0 ... Sunday=6）
    if day.weekday() >= 5:  # 5=土, 6=日
        return True

    # 3) 平日の追加休日
    if HolidayDate.objects.filter(date=day).exists():
        return True

    # 4) それ以外は稼働日
    return False


def is_working_day(day: date) -> bool:
    """
    稼働日かどうかの判定。
    """
    return not is_holiday(day)
