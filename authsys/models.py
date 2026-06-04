# authsys/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager, AbstractBaseUser, PermissionsMixin


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        """
        実際のユーザー作成処理（共通）
        """
        if not email:
            raise ValueError('メールアドレスは必須です。')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    
    def create_user(self, email, password=None, **extra_fields):
        """
        一般ユーザー作成
        """
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        """
        スーパーユーザー作成(createsuperuser コマンド用）
        """
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self._create_user(email, password, **extra_fields)



class User(AbstractUser):
    # username フィールドは使わない
    username = None

    # ログインIDにするメールアドレス
    email = models.EmailField('メールアドレス', unique=True)

    must_change_password = models.BooleanField(
        '初回ログイン時にパスワード変更が必要',
        default=False,
    )

    # ユーザー管理画面でのドラッグ&ドロップ表示順（小さいほど上）。
    # 0 のままなら氏名順にフォールバックする。
    display_order = models.PositiveIntegerField('表示順', default=0)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    # ここがポイント：上で定義した UserManager を使う
    objects = UserManager()

    def __str__(self):
        return self.email
    