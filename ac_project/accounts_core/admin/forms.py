from django import forms
from django.contrib.auth.forms import (
    UserChangeForm as DjangoUserChangeForm,
    UserCreationForm as DjangoUserCreationForm)
from accounts_core.models import Invoice, InvoiceLine, User

# -----------------------------
# Register custom admin forms
# ----------------------------


# Subclass `DjangoUserCreationForm` (form used when adding a new user)
class UserAdminCreationForm(DjangoUserCreationForm):
    class Meta(DjangoUserCreationForm.Meta):
        model = User  # Points `model` to custom User model
        fields = ("username", "email", "default_company")


# Subclass `DjangoUserChangeForm` (form used when editing an existing user)
class UserAdminChangeForm(DjangoUserChangeForm):
    # override `Meta` to include custom model & any extra fields
    class Meta(DjangoUserChangeForm.Meta):
        model = User
        fields = (
            "username",
            "email",
            "is_active",
            "is_staff",
            "is_superuser",
            "default_company",
        )


class InvoiceLineForm(forms.ModelForm):
    class Meta:
        model = InvoiceLine
        exclude = ("company",)  # hide company from inline form

    def clean(self):
        # set company_id if possible
        # (form.instance.invoice may have pk after admin saves parent)
        if getattr(self.instance, "invoice_id", None) and not getattr(
            self.instance, "company_id", None
        ):
            self.instance.company_id = (
                Invoice.objects.only("company_id")
                .get(pk=self.instance.invoice_id)
                .company_id
            )
        return super().clean()
