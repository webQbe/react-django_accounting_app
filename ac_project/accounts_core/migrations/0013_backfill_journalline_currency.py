from django.db import migrations

def backfill_currency(apps, schema_editor):
    JournalLine = apps.get_model("accounts_core", "JournalLine")
    # Loop over all existing JournalLine rows 
    for jl in JournalLine.objects.all():
        # fill in their currency
        jl.currency = jl.journal.company.default_currency
        jl.save()

class Migration(migrations.Migration):

    dependencies = [
        ("accounts_core", "0012_mv_balance_sheet_running"),  # adjust if your last migration has a different name
    ]

    operations = [
        migrations.RunPython(backfill_currency, reverse_code=migrations.RunPython.noop),
    ]

