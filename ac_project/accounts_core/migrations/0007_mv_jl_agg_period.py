from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('accounts_core', '0006_alter_user_options_and_more'),
    ]

    """ Base aggregation matview :
        - Base, fine-grained aggregation by company/period/account. 
        - This materialized view creates a fast lookup table of account balances 
        per company and period, only using posted journal entries. 
        - Instead of recalculating debits/credits every time, the database stores them 
        ready to query. 
    """

    operations = [
        migrations.RunSQL(
            """
            CREATE MATERIALIZED VIEW mv_jl_agg_period AS
            SELECT
                jl.company_id,
                je.period_id,
                je.date::date AS last_txn_date,
                a.id AS account_id,
                a.code AS account_code, 
                a.name AS account_name,
                a.ac_type AS account_type,
                SUM(jl.debit_amount) AS total_debit,
                SUM(jl.credit_amount) AS total_credit,
                SUM(jl.debit_amount) - SUM(jl.credit_amount) AS net_amount
            FROM journal_line jl
            JOIN journal_entry je ON je.id = jl.journal_id
            JOIN account a ON a.id = jl.account_id
            WHERE je.posted = TRUE
            GROUP BY jl.company_id, je.period_id, je.date::date, a.id, a.code, a.name, a.ac_type;

            CREATE UNIQUE INDEX ux_mv_jl_agg_period_company_period_account
                ON mv_jl_agg_period (company_id, period_id, account_id);

            CREATE INDEX ix_mv_jl_agg_period_company_account
                ON mv_jl_agg_period (company_id, account_id);
            """,
            # SQL to undo view if you roll back migration (dropping the view)
            reverse_sql="DROP MATERIALIZED VIEW mv_jl_agg_period;"
        ),

        """ SQL schema definition:
            - Builds a summarized “reporting table” called mv_jl_agg_period 
            - Aggregates journal entries by company, period, and account
                - jl.company_id: Which tenant/company the journal line belongs to.
                - je.period_id: The accounting period (e.g. Jan 2025).
                - je.date::date AS last_txn_date: The date of the journal entry, cast to a pure date (without time).
                - a.id, a.code, a.name, a.ac_type: Identifiers and properties of the account.
                - SUM(jl.debit) and SUM(jl.credit): Total debits and credits across lines.
                - SUM(jl.debit) - SUM(jl.credit) AS net_amount: The net effect (like a balance) for that account in that period.
        
            - Each line knows which entry it belongs to and which account it affects
                - Start from journal_line (the detailed lines)
                - Join with journal_entry (the header/transaction info).
                - Join with account (to get account details).
            
            - Only include posted journal entries (finalized)

            - One row per account per period per company
                - Groups all rows by company + period + account + transaction date
                - For each group, it calculates the sums

            - Indexes
                - First index enforces uniqueness: you can’t have two rows for the same (company, period, account).
                - Second index is for fast lookups when filtering by company and account 
                  (e.g., “show me this account’s history for a company”).
        """
    ]