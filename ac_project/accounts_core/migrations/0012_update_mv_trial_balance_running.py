from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('accounts_core', '0011_mv_trial_balance_period'),
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
            DROP MATERIALIZED VIEW IF EXISTS mv_trial_balance_running;
            CREATE MATERIALIZED VIEW mv_trial_balance_running AS

            -- `WITH jl_norm` is above everything, so it can be reused in final query
            WITH jl_norm AS (
                SELECT
                    jl.*,
                    COALESCE(jl.fx_rate, 1.0) AS fxr
                FROM accounts_core_journalline jl
                JOIN accounts_core_journalentry je 
                    ON je.id = jl.journal_id
                -- filter early
                WHERE je.status = 'posted' 
            )
            -- only one final SELECT (Postgres requires this)
            SELECT
                jl_norm.company_id,
                a.id   AS account_id,
                a.code AS account_code,
                a.name AS account_name,
                a.ac_type AS account_type,
                
                -- Original & local totals in the same projection
                -- Original currency amounts
                SUM(jl_norm.debit_original)  AS total_debit_to_date_original,
                SUM(jl_norm.credit_original) AS total_credit_to_date_original,
                SUM(jl_norm.debit_original) - SUM(jl_norm.credit_original) AS balance_to_date_original,

                -- Local currency amounts
                SUM(jl_norm.debit_original * jl_norm.fxr)  AS total_debit_local,
                SUM(jl_norm.credit_original * jl_norm.fxr) AS total_credit_local,
                SUM(jl_norm.debit_original * jl_norm.fxr) 
                - SUM(jl_norm.credit_original * jl_norm.fxr) AS balance_to_date_local

            FROM jl_norm
            JOIN accounts_core_account a ON a.id = jl_norm.account_id
            GROUP BY jl_norm.company_id, a.id, a.code, a.name, a.ac_type;

            CREATE UNIQUE INDEX ux_mv_trial_balance_running_company_account
                ON mv_trial_balance_running (company_id, account_id);
            """,
            reverse_sql="DROP MATERIALIZED VIEW mv_trial_balance_running;"
        ),  
        
     ]
    
    """ SQL schema definition:
        - Creates  'mv_trial_balance_running' a stored, precomputed view in the database
        - It is a snapshot of balances up to the present, without slicing by period.
        - There's no period_id here which means it's cumulative — a full trial balance as of now.
        - Starts from journal lines (jl), join with journal entries (je) to get transaction info, and
          join with account (a) to know which account the line belongs to.
        - Each row is multiplied by its FX rate (or 1.0 if NULL).
        - Only include posted entries (finalized transactions).
        - Groups by company + account, the SUM() values are totals per account (per company).
        - The unique index ensures each (company_id, account_id) combination is unique &
           makes lookups by company/account faster
    """