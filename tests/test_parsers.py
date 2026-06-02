from datetime import date

from paisa.parsers.hdfc import parse_hdfc_tables
from paisa.parsers.icici import parse_icici_tables, parse_icici_text


def test_parse_hdfc_tables_merges_multiline_rows():
    tables = [
        [
            ["Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"],
            ["01/03/2026", "UPI/P2M/123/ZOMATO", "450.00", "", "1000.00"],
            ["", "LIMITED", "", "", ""],
            ["02/03/2026", "SALARY CREDIT", "", "50000.00", "51000.00"],
        ]
    ]

    transactions = parse_hdfc_tables(tables)

    assert len(transactions) == 2
    assert transactions[0].bank == "HDFC"
    assert transactions[0].date == date(2026, 3, 1)
    assert transactions[0].type == "DEBIT"
    assert transactions[0].amount == 450.00
    assert transactions[0].running_balance == 1000.00
    assert transactions[0].merchant_name == "Zomato Limited"
    assert transactions[1].running_balance == 51000.00
    assert transactions[1].type == "CREDIT"


def test_parse_icici_tables_reads_debit_and_credit_columns():
    tables = [
        [
            ["Transaction Date", "Remarks", "Debit", "Credit", "Balance"],
            ["03-04-2026", "INF/INB/SWIGGY 99887766", "300.00", "", "900.00"],
            ["04-04-2026", "INTEREST CREDIT", "", "10.00", "910.00"],
        ]
    ]

    transactions = parse_icici_tables(tables)

    assert len(transactions) == 2
    assert transactions[0].bank == "ICICI"
    assert transactions[0].date == date(2026, 4, 3)
    assert transactions[0].type == "DEBIT"
    assert transactions[0].running_balance == 900.00
    assert transactions[0].merchant_name == "Swiggy"
    assert transactions[1].running_balance == 910.00
    assert transactions[1].type == "CREDIT"


def test_parse_icici_text_reads_multiline_statement_layout():
    text = """Statement of Transactions in Savings Account XXXXXXXX5192 in INR for the period April 01, 2026 - April 30, 2026
DATE MODE PARTICULARS DEPOSITS WITHDRAWALS BALANCE
01-04-2026 B/F 9,452.39
UPI/sbipmopad/sbipmopad.02pl/UPI/SBI
01-04-2026 PAYMEN/609116534517/ICI0f4208c597d148e784ec311f9968 50.00 9,402.39
UPI/RISHABH FO/paytm.s1xwebu@/UPI/YES BANK
01-04-2026 40.00 9,362.39
NEFT-SCBLH09100580136-ULAGAMMAI MEYYAPPAN-AMMA
01-04-2026 2,400.00 11,762.39
RD-42610538221-SCBL0036001
Total: 2,400.00 90.00 11,762.39
Account Related Other Information
"""

    transactions = parse_icici_text(text)

    assert len(transactions) == 3
    assert transactions[0].date == date(2026, 4, 1)
    assert transactions[0].type == "DEBIT"
    assert transactions[0].amount == 50.00
    assert transactions[0].running_balance == 9402.39
    assert transactions[1].narration == "UPI/RISHABH FO/paytm.s1xwebu@/UPI/YES BANK"
    assert transactions[1].type == "DEBIT"
    assert transactions[1].running_balance == 9362.39
    assert transactions[2].type == "CREDIT"
    assert transactions[2].running_balance == 11762.39
    assert transactions[2].narration == (
        "NEFT-SCBLH09100580136-ULAGAMMAI MEYYAPPAN-AMMA "
        "RD-42610538221-SCBL0036001"
    )
