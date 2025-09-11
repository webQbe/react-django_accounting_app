from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('accounts_core', '0008_mv_trial_balance_period'),
    ]

    """ Running Trial Balance Up to date:
        This materialized view gives you the running trial balance:
        - One row per account per company.
        - Shows total debits, total credits, and net balance from 
          the beginning of time up to now.
        - Unlike the period trial balance, this doesn't reset each month/period.
    """

    operations = [
        migrations.RunSQL(
            """  
            CREATE MATERIALIZED VIEW mv_trial_balance_running AS
            SELECT
                jl.company_id,
                a.id AS account_id,
                a.code AS account_code,
                a.name AS account_name,
                a.ac_type AS account_type,
                SUM(jl.debit) AS total_debit_to_date,
                SUM(jl.credit) AS total_credit_to_date,
                SUM(jl.debit) - SUM(jl.credit) AS balance_to_date
            FROM journal_line jl
            JOIN journal_entry je ON je.id = jl.journal_entry_id
            JOIN account a ON a.id = jl.account_id
            WHERE je.posted = TRUE
            GROUP BY jl.company_id, a.id, a.code, a.name, a.ac_type;

            CREATE UNIQUE INDEX ux_mv_trial_balance_running_company_account
                ON mv_trial_balance_running (company_id, account_id);
            """,
            reverse_sql="DROP MATERIALIZED VIEW mv_trial_balance_running;"
        ),
        
        """ SQL schema definition:
        - Creates  'mv_trial_balance_running' a stored, precomputed view in the database
        - It is a snapshot of balances up to the present, without slicing by period.
        - There's no period_id here which means it's cumulative — a full trial balance as of now.
        - Starts from journal lines (jl), join with journal entries (je) to get transaction info, and
          join with account (a) to know which account the line belongs to.
        - Only include posted entries (finalized transactions).
        - Groups by company + account, the SUM() values are totals per account (per company).
        - The unique index ensures each (company_id, account_id) combination is unique &
           makes lookups by company/account faster
        """
        
     ]