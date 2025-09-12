from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('accounts_core', '0013_mv_pl_period'),
    ]

    """  Balance Sheet (snapshot):
    This materialized view is a running Balance Sheet snapshot:
       -  Shows each company's Asset, Liability, and Equity accounts.
       -  Tracks their balances up to date.
       -  Enforces uniqueness and query speed with an index.
    """


    operations = [
            migrations.RunSQL(
                """
                CREATE MATERIALIZED VIEW mv_balance_sheet_running AS
                SELECT
                    t.company_id,
                    t.account_id,
                    t.account_code,
                    t.account_name,
                    t.account_type,
                    t.balance_to_date_original,
                    t.balance_to_date_local
                FROM mv_trial_balance_running t
                WHERE t.account_type IN ('Asset','Liability','Equity');

                CREATE UNIQUE INDEX ux_mv_balance_sheet_running_company_account
                    ON mv_balance_sheet_running (company_id, account_id);
                """,
                reverse_sql="DROP MATERIALIZED VIEW mv_balance_sheet_running;"
            ),
            
        ]
    

    """ SQL schema definition:
        - Creating a materialized view called mv_balance_sheet_running
            - “Running” means it accumulates balances up to date (not just per period).
            - It's based on mv_trial_balance_running view.
        - Each row represents one account's balance for a company
        - Income and Expense accounts are excluded because they flow into Equity at
           period-end, not shown directly on the Balance Sheet.
        - UNIQUE INDEX ensures uniqueness on (company_id, account_id)
            - Prevents duplicate rows for the same account in a company.
            - Speeds up lookups
    """     