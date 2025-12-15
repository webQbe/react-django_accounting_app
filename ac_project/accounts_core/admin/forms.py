from django import forms
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.contrib.auth.forms import (
    UserChangeForm as DjangoUserChangeForm,
    UserCreationForm as DjangoUserCreationForm)
from accounts_core.models import Invoice, InvoiceLine, User, JournalLine, Account, FixedAsset

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

# Inline form for JournalLine (admin)
class JournalLineInlineForm(forms.ModelForm):
    class Meta:
        model = JournalLine
        fields = "__all__"
        exclude = ("company",)  # hide company from inline

    # Limit the account dropdown to company accounts in admin
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Skip if account queryset already set via formfield_for_foreignkey
        if "account" in self.fields and not self.fields["account"].queryset.exists():
            # try to infer company from instance or its journal
            company = None
            if getattr(self.instance, "company_id", None):
                company = self.instance.company
            elif getattr(self.instance, "journal_id", None):
                try:
                    company = self.instance.journal.company
                except Exception:
                    company = None
            if company:
                self.fields["account"].queryset = Account.objects.filter(company=company)
            else:
                # Show all accounts so user can pick
                self.fields["account"].queryset = Account.objects.all()

    def clean(self):
        cleaned = super().clean()

        # Ensure either debit_original or credit_original is provided (> 0)
        debit = cleaned.get("debit_original") or Decimal("0.00")
        credit = cleaned.get("credit_original") or Decimal("0.00")
        if (debit == Decimal("0.00")) and (credit == Decimal("0.00")):
            raise ValidationError("Either debit or credit must be > 0 for a JournalLine.")

        # Copy company_id from parent JournalEntry if available (admin inline case)
        journal = cleaned.get("journal") or getattr(self.instance, "journal", None)
        if journal and not cleaned.get("company"):
            # use journal.company_id to avoid dereferencing heavy objects
            cleaned["company"] = journal.company

        # If account chosen, ensure its company matches the line's company (if known)
        account = cleaned.get("account")
        company = cleaned.get("company") or getattr(self.instance, "company", None)
        if account and company and account.company_id != company.id:
            raise ValidationError({"account": "Selected account does not belong to the same company."})

        return cleaned


class FixedAssetAdminForm(forms.ModelForm):
    class Meta:
        model = FixedAsset
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # name of FK to account 
        account_field_name = "account"

        # try to determine company in descending order:
        company = None
        # 1) change form: instance has company
        if getattr(self.instance, "pk", None) and getattr(self.instance, "company", None):
            company = self.instance.company
        # 2) initial data passed to form (e.g. admin add with ?company=1)
        elif self.initial.get("company"):
            company = self.initial.get("company")
        # 3) try cleaned initial in kwargs (admin sometimes passes initial kwarg)
        elif kwargs.get("initial") and kwargs["initial"].get("company"):
            company = kwargs["initial"].get("company")

        if account_field_name in self.fields:
            if company:
                self.fields[account_field_name].queryset = Account.objects.filter(company=company)
            else:
                # fallback: show all accounts instead of an empty set
                self.fields[account_field_name].queryset = Account.objects.all()

