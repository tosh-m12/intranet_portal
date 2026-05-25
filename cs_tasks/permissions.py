# cs_tasks/permissions.py
"""cs_tasks の権限判定ヘルパ。"""


def is_admin(user):
    """上長（管理者）判定。superuser または is_staff。"""
    return user.is_authenticated and (user.is_superuser or user.is_staff)


def can_edit_task(user, task):
    """
    課題の編集可否。
    ・上長は常にOK
    ・完了済みは上長のみ（再開しないと一般は触れない）
    ・それ以外は登録者(owner) または 担当者(assignee)
    """
    if not user.is_authenticated:
        return False
    if is_admin(user):
        return True
    if task.is_closed:
        return False
    return task.owner_id == user.id or task.assignee_id == user.id


def can_cancel_task(user, task):
    """中止（論理削除）可否。上長 または 登録者本人。"""
    if not user.is_authenticated:
        return False
    if is_admin(user):
        return True
    return task.owner_id == user.id


def can_close_task(user):
    """完了・再開の判断は上長のみ。"""
    return is_admin(user)


def can_comment(user):
    """上長コメントの付与は上長のみ。"""
    return is_admin(user)
