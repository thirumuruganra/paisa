from paisa.parsers.merchant_cleaner import clean_merchant_name


def test_clean_merchant_name_removes_bank_noise():
    result = clean_merchant_name("UPI/P2M/1234567890/ZOMATO LIMITED/REFNO:99887766")

    assert result == "Zomato Limited"


def test_clean_merchant_name_handles_empty_result():
    assert clean_merchant_name("123456789012") == "Unknown"
