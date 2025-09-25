from django.apps import AppConfig


class AccountsCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts_core"

    # ensure receivers are registered
    def ready(self):
        import accounts_core.signals
