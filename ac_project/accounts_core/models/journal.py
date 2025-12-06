import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from ..exceptions import AlreadyPostedDifferentPayload, UnbalancedJournalError
from ..managers import JournalLineCurrencyManager, TenantManager
from .account import Account
from .currency import Currency
from .entitymembership import Company
from .period import Period

JOURNAL_STATUS = [
    ("draft", "Draft"),  # still editable
    ("ready", "Ready"),  # validated but not yet posted
    ("posted", "Posted"),  # finalized
]


# ---------- Journal (Header) & JournalLine ----------
class JournalEntry(models.Model):  # Represents one accounting transaction
    # Header-level info
    # Multi-tenant: every entry belongs to a company
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    # Optional link to an accounting period (for reporting, closing)
    period = models.ForeignKey(
        Period,
        null=True,
        blank=True,
        on_delete=models.PROTECT,  # Prevent breaking historical ledger
    )
    # Business metadata
    date = models.DateField()
    reference = models.CharField(max_length=200, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=10,
        choices=JOURNAL_STATUS,
        default="draft"
        """ JournalEntry workflow:
                            ("Draft") # still editable
                            ("Ready") # validated but not yet posted
                            ("Posted") # finalized
                        """,
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    # Track user who created it
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True, on_delete=models.SET_NULL
    )
    # optional polymorphic source info
    # (invoice, bill, bank txn, fixed asset actions)
    source_type = models.CharField(
        max_length=50, null=True, blank=True
    )  # Helps trace back where the JE originated
    source_id = models.BigIntegerField(null=True, blank=True)
    # Fingerprint-based idempotency (safe to call twice if nothing has changed)
    posting_fingerprint = models.CharField(
        max_length=64, null=True, blank=True)

    # Enforce tenant scoping
    objects = TenantManager()

    class Meta:
        # Speed up listing & filtering
        # (e.g. show all posted entries this month)
        indexes = [
            models.Index(fields=["company", "date"]),
            models.Index(fields=["company", "status"]),
        ]

        constraints = [
            # Within one company, each journal entry must be unique
            # Across companies, duplicates are allowed
            models.UniqueConstraint(
                fields=["company", "reference"], name="uq_je_company_ref"
            )
        ]

    def __str__(self):
        return f"JE {self.pk} {self.date} [{self.status}]"

    # Aggregate all debit and credit amounts across entry’s lines
    def compute_totals(self):
        """Return debits, credits sums for lines"""
        aggs = self.lines.aggregate(
            total_debit=models.Sum("debit_local"),
            total_credit=models.Sum("credit_local"),
        )
        return (
            aggs["total_debit"] or Decimal("0.0"),
            aggs["total_credit"] or Decimal("0.0"),
        )

    # True if double-entry rule holds: total debits = total credits
    def is_balanced(self):
        debit, credit = self.compute_totals()
        return debit == credit

    def _posting_payload(self):
        """Deterministic representation of what matters for posting

        Deterministic = no matter when or how you call it,
        if the data hasn't changed,
        the JSON string will always look the same.

        Creates a consistent JSON "snapshot" of
        the important accounting data in a journal entry,
        so you can later check whether that exact version
        has already been posted.
        """
        lines = [
            {
                "acct": line.account_id,
                "debit": str(line.debit_original),
                "credit": str(line.credit_original),
                "desc": line.description or "",
            }
            # Get all lines for this journal entry,
            # always in the same order (id ascending)
            # For each line l, build a dict with fields that matter for posting
            for line in self.lines.order_by("id").all()
        ]

        # Build a dictionary for the whole journal
        payload = {
            "company": self.company_id,
            "date": self.date.isoformat(),
            "lines": lines,  # Lines (from above)
        }  # Date (always in ISO format like "2025-09-15")

        # Converts payload dict into a compact JSON string
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    def _fingerprint(self):
        # hash (sha256) JSON string
        # from _posting_payload() to produce a fingerprint
        return hashlib.sha256(self._posting_payload().encode()).hexdigest()

    # Post the entry safely inside a database transaction
    @transaction.atomic
    def post(self, user=None):
        """
        Safely post a journal entry
        with validations, idempotency, and snapshots.
        """

        # Lock row + lines to prevent concurrent modifications
        je = JournalEntry.objects.select_for_update().get(pk=self.pk)

        # Lock all journal lines to prevent race conditions
        # so no other transaction can modify them while posting is in progress
        lines = je.lines.select_for_update().all()

        # lazy import to avoid circular import at module load time
        from ..services import update_snapshots_for_journal

        """ Business validations """
        if not lines.exists():  # Prevent posting an empty entry
            raise ValidationError(
                "JournalEntry must have at least one JournalLine.")

        # Recompute totals fresh from DB & ignore any stale cached values
        total_debit = Decimal("0.00")
        total_credit = Decimal("0.00")
        total_debit, total_credit = self.compute_totals()

        # Enforce double-entry rule: debits = credits
        td = total_debit
        tc = total_credit
        if tc != td:
            # Use custom exception
            raise UnbalancedJournalError(
                f"Journal not balanced: debits={td}, credits={tc}"
            )

        # Enforce tenant consistency
        # every line must belong to same company as journal
        if lines.exclude(company=self.company).exists():
            raise ValidationError(
                "All journal lines must belong to same company as journal."
            )

        # Check period
        if je.period and je.period.company != je.company:
            raise ValidationError(
                "Period must belong to " "the same company as journal"
            )

        # Ensure periods open
        if je.period and je.period.is_closed:
            raise ValidationError("Period is closed")

        # Compute fingerprint
        fp = je._fingerprint()

        """ Idempotency & immutability """
        if je.status == "posted":
            if je.posting_fingerprint == fp:
                # Idempotent: safe to return without raising
                return je
            raise AlreadyPostedDifferentPayload(
                "Journal already posted with different payload."
            )

        """ Update state """
        je.status = "posted"  # Mark journal as posted
        je.posted_at = timezone.now()  # Timestamp
        if user:
            je.created_by = user
        je.posting_fingerprint = fp
        je.save(
            update_fields=[
                "status", "posted_at", "created_by", "posting_fingerprint"]
        )

        # mark all lines as posted (bulk update)
        lines.update(is_posted=True)

        # Service layer function to trigger snapshot update
        # Recalculate AccountBalanceSnapshot
        # for all accounts affected by this journal
        update_snapshots_for_journal(je)
        return je

    def clean(self):
        """Don't modify posted journals"""
        if (
            self.pk and self.status == "posted"
        ):  # If it has a primary key and status is posted, it's an update
            # Load original DB version before edits
            orig = JournalEntry.objects.get(pk=self.pk)
            # if attempting to change any core fields after posted
            if orig.status.posted:
                changed = False  # allow no changes if posted (strict)
                # compare changes in "description", "period_id" fields
                for f in ("description", "period_id"):
                    if getattr(orig, f) != getattr(self, f):
                        # If they differ,
                        # then user is trying to change something after posting
                        changed = True
                if changed:
                    # Block the save with a ValidationError
                    raise ValidationError(
                        "Cannot modify a posted JournalEntry. It is immutable."
                    )

        """ Don't allow journals in closed periods """
        if self.period and self.period.is_closed:
            # If journal is assigned to a period,
            # and the period is marked closed, reject save
            raise ValidationError(
                "Cannot create or edit journal inside a closed period."
            )

    def save(self, *args, **kwargs):
        if self.pk:  # Does this row already exist in DB?
            # Fetch "original" row to update
            orig = JournalEntry.objects.get(pk=self.pk)
            # Check if journal was already posted
            if orig.status == "posted" and self.status != "posted":
                # disallow toggling posted flag
                raise ValidationError("Cannot unpost a posted journal")
            """ If self.status != "posted"
            → the user is trying to change status
            back to "draft" (or anything else). """

        # If validation passes, continue with normal save
        super().save(*args, **kwargs)

    # Control status changes
    def transition_to(self, new_status, user=None):
        allowed = {
            "draft": ["ready", "posted"],
            "ready": ["posted"],
            "posted": [],
        }
        # if later you add `archived` or `void` states,
        # you just update the dictionary

        # prevent skipping validations
        if new_status not in allowed.get(self.status, []):
            raise ValidationError(
                f"Cannot go from {self.status} to {new_status}")

        if new_status == "posted":
            # call posting logic (validations, mark lines as is_posted, etc.)
            self.post(user=user)
        else:
            # just update the status
            self.status = new_status
            self.save(update_fields=["status"])


class JournalLine(models.Model):  # Stores Lines ( credits / debits )
    """
    Each line belongs to a journal entry and to a GL account.
    Optional foreign keys to
    invoice/bill/banktransaction/fixedasset for traceability.
    """

    # Belongs to company & a journal entry
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    journal = models.ForeignKey(
        JournalEntry,
        on_delete=models.CASCADE,
        related_name="lines",  # default reverse name
    )

    # Must point to one Account (can’t delete account if lines exist → PROTECT)
    account = models.ForeignKey(Account, on_delete=models.PROTECT)

    # Description
    description = models.CharField(max_length=400, null=True, blank=True)

    # Original currency amounts & the debit/credit split
    currency = models.ForeignKey(
        Currency, on_delete=models.PROTECT, default="USD"
    )  # force a default
    debit_original = models.DecimalField(
        max_digits=18, decimal_places=2, default=0)
    credit_original = models.DecimalField(
        max_digits=18, decimal_places=2, default=0)

    # Conversion
    fx_rate = models.DecimalField(
        max_digits=18, decimal_places=6, default=1
    )

    # Local (functional currency) amounts
    debit_local = models.DecimalField(
        max_digits=18, decimal_places=2, default=0)
    credit_local = models.DecimalField(
        max_digits=18, decimal_places=2, default=0)

    # Link each posting line back to the business object that caused it
    invoice = models.ForeignKey(
        "Invoice", null=True, blank=True, on_delete=models.SET_NULL
    )
    bill = models.ForeignKey(
        "Bill", null=True, blank=True, on_delete=models.SET_NULL)
    bank_transaction = models.ForeignKey(
        "BankTransaction",
        null=True,
        blank=True,
        # on deleting a bank transaction
        on_delete=models.PROTECT,  # prevent breaking audit trails
    )
    fixed_asset = models.ForeignKey(
        "FixedAsset",
        null=True,
        blank=True,
        # on deleting a fixed_asset
        on_delete=models.PROTECT,  # do not remove posted journal entries
    )

    # audit / immutability marker (populated when journal posted)
    is_posted = models.BooleanField(default=False)  # prevents edits later

    # Custom managers
    objects = TenantManager()  # Enforce tenant scoping
    with_currency = JournalLineCurrencyManager()  # autofill right currency

    class Meta:
        # For fast queries like “all lines for this account” /
        # “all lines in this JE.”
        indexes = [
            models.Index(fields=["company", "account"]),
            models.Index(fields=["company", "journal"]),
        ]

        # Enforce debits and credits must be non-negative
        """ You can optionally add a CHECK constraint in Postgres
        to prevent both debit & credit > 0
        and at least one of them non-zero.
        Django 3.2+ supports CheckConstraint. """
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(debit_original__gte=0) &
                    models.Q(credit_original__gte=0)
                ),
                name="jl_non_negative_original_amounts",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(debit_local__gte=0) &
                    models.Q(credit_local__gte=0)
                ),
                name="jl_non_negative_local_amounts",
            ),
            models.CheckConstraint(
                condition=~(models.Q(debit_original=0) &
                            models.Q(credit_original=0)),
                name="debit_xor_credit_nonzero_original",
            ),
            models.CheckConstraint(
                condition=~(models.Q(debit_local=0) &
                            models.Q(credit_local=0)),
                name="debit_xor_credit_nonzero_local",
            ),
            models.CheckConstraint(
                condition=models.Q(fx_rate__isnull=True) |
                models.Q(fx_rate__gt=0),
                name="fx_rate_null_or_positive",
            ),
        ]

    # Show journal, account, and amounts in admin dropdowns and debug logs
    def __str__(self):
        jid = self.journal_id
        acc = self.account.code
        acn = self.account.name
        db = self.debit_original or 0
        cr = self.credit_original or 0
        return f"{jid} | {acc} {acn} | D:{db} C:{cr}"

    # Business logic validation:
    # - Debit/credit should always be non-negative
    # - Prevent mixing payable and receivable logic on one line
    # - Prevent “cross-company” contamination
    def clean(self):
        # Ensure no negative values sneak in
        # (redundant with CheckConstraint but useful at app-level)
        if self.debit_original < 0 or self.credit_original < 0:
            raise ValidationError("Debit and credit must be >= 0")

        # ensure debit xor credit or both allowed?
        # Usually one is zero.
        if (self.debit_original > 0) and (self.credit_original > 0):
            raise ValidationError(
                "JournalLine should not have both debit and credit > 0"
            )
        if (self.debit_original == 0) and (self.credit_original == 0):
            raise ValidationError(
                "JournalLine requires a non-0 amount on either debit or credit"
            )

        # Derive effective company_id safely 
        company_id = getattr(self, "company_id", None)

        # If inline has journal_id (JE saved), query it
        if company_id is None and getattr(self, "journal_id", None):
            company_id = JournalEntry.objects.only("company_id").filter(pk=self.journal_id).values_list("company_id", flat=True).first()

        # If JE is present but unsaved, maybe form set journal.company already
        if company_id is None and getattr(self, "journal", None):
            company_id = getattr(self.journal, "company_id", None) or (self.journal.company.id if getattr(self.journal, "company", None) else None)

        # Enforce cross company checks only if company_id available
        if self.account and company_id is not None and self.account.company_id != company_id:
            raise ValidationError("JournalLine.account must belong to the same company.")

        # If company_id is still None, company-based checks are skipped for now.
        # They will be enforced later in save() when we can copy company from parent.

        # Invoice and Bill cannot both be set
        # A journal line can link to either an invoice or a bill,
        # but never both
        if self.invoice and self.bill:
            raise ValidationError(
                "JournalLine cannot reference " "both invoice and bill."
            )

        # Company consistency
        # Every line must belong to same company as its parent journal
        if self.journal_id and self.company_id != self.journal.company_id:
            raise ValidationError(
                "JournalLine.company must" " equal JournalEntry.company"
            )
        
        # Ensure invoice chosen belongs to the same company
        if self.invoice_id and self.invoice.company_id != self.company_id:
            raise ValidationError(
                "JournalLine.invoice must belong to the same company."
            )
        # Ensure bill chosen belongs to the same company
        if self.bill_id and self.bill.company_id != self.company_id:
            raise ValidationError(
                "JournalLine.bill must " "belong to the same company."
            )       
        # Ensure bank transaction chosen belongs to the same company
        bt = self.bank_transaction
        if bt and bt.company_id != self.company_id:
            raise ValidationError(
                "JournalLine.bank_transaction must belong to the same company."
            )
        # Ensure fixed asset chosen belongs to the same company
        if self.fixed_asset_id and self.fixed_asset.company_id != self.company_id:
            raise ValidationError(
                "JournalLine.fixed_asset must belong to the same company."
            )

        # Check if an Invoice/Bill/Asset
        # references a non-control account → block it
        if self.invoice_id and not self.account.is_control_account:
            raise ValidationError(
                "Invoice postings must " "use a control AR account.")
        if self.bill_id and not self.account.is_control_account:
            raise ValidationError(
                "Bill postings must " "use a control AP account.")
        if self.fixed_asset_id and not self.account.is_control_account:
            raise ValidationError(
                "Fixed asset postings " "must use a control account.")

        # Check if fx_rate valid
        if self.currency_id != self.journal.company.default_currency_id:
            if self.fx_rate is None or self.fx_rate <= 0:
                raise ValidationError(
                    "fx_rate must be > 0 " "when currency differs")
        elif self.fx_rate not in (None, 1.0):
            raise ValidationError(
                "fx_rate must be None " "or 1.0 for default currency")

        # If there's a parent journal set, check DB for its posted flag.
        if self.journal_id:
            # Query DB for posted state (one small query)
            posted = JournalEntry.objects.filter(
                pk=self.journal_id, status="posted"
            ).exists()
            if posted:
                # If this is an existing line being updated
                if self.pk:
                    # Compare persisted values to attempted values.
                    # If anything changed, forbid it.
                    try:
                        orig = JournalLine.objects.get(pk=self.pk)
                    except JournalLine.DoesNotExist:
                        # unlikely, but be safe
                        raise ValidationError(
                            "Cannot modify JournalLine: "
                            "parent JournalEntry is posted."
                        )
                    # decide which fields
                    # constitute a "modification" for your domain:
                    changed = (
                        orig.debit_original != self.debit_original
                        or orig.credit_original != self.credit_original
                        or orig.account_id
                        != (self.account.id if self.account else None)
                        or orig.invoice_id
                        != (self.invoice.id if self.invoice else None)
                        or orig.bill_id !=
                        (self.bill.id if self.bill else None)
                    )
                    if changed:
                        raise ValidationError(
                            "Cannot modify JournalLine: "
                            "parent JournalEntry is posted."
                        )
                else:
                    # Trying to create a line on a posted journal — block it.
                    raise ValidationError(
                        "Cannot add JournalLine: parent journal is posted."
                    )

    def delete(self, *args, **kwargs):
        # Prevent deletion if parent journal is posted
        if self.journal_id:
            if JournalEntry.objects.filter(
                pk=self.journal_id, status="posted"
            ).exists():
                raise ValidationError(
                    "Cannot delete JournalLine: parent JournalEntry is posted."
                )
        return super().delete(*args, **kwargs)

    """ Treat fx_rate as 1.0 when it is NULL """

    @property
    def effective_fx_rate(self):
        # if fx_rate is NULL, pretend it's 1.0
        return self.fx_rate if self.fx_rate is not None else Decimal("1.0")

    @property
    def amount_local_computed(self):
        # always safe, because we fallback to 1.0
        return self.amount_original * self.effective_fx_rate

    # save() override
    def save(self, *args, **kwargs):
        # If company not set but JE is known, get company_id from JE
        if not getattr(self, "company_id", None) and getattr(self, "journal_id", None):
            # JE saved
            self.company_id = JournalEntry.objects.only("company_id").get(pk=self.journal_id).company_id
        elif not getattr(self, "company_id", None) and getattr(self, "journal", None):
            # JE maybe unsaved but its company field could be set on JE model instance
            self.company = getattr(self.journal, "company", None)

        """Calculate debit_local / credit_local:
        - Local amounts are always stored,
        ready for reporting without recomputation.
        - If fx_rate or original amounts change, local fields are updated.
        """
        rate = self.fx_rate or Decimal("1.0")  # treat None as 1.0
        # round to 2 decimal places before assigning
        self.debit_local = (self.debit_original * rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.credit_local = (self.credit_original * rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # clean()+field validation always run whenever
        # you save a JournalLine programmatically
        self.full_clean()
        return super().save(*args, **kwargs)
