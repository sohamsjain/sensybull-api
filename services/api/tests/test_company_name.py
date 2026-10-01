"""Pins app/utils/company_name.py to the same cases as sensybull-web's company-name tests."""

import pytest

from app.utils.company_name import case_company_name, display_company_name


@pytest.mark.parametrize('raw, cased', [
    ('PURE CYCLE CORP', 'Pure Cycle Corp'),
    ('TWO HARBORS INVESTMENT CORP', 'Two Harbors Investment Corp'),
    ('APPLIED OPTOELECTRONICS, INC.', 'Applied Optoelectronics, Inc.'),
    ('Eos Energy Enterprises, Inc.', 'Eos Energy Enterprises, Inc.'),
    ('eBay Inc.', 'eBay Inc.'),
    ('CARLYLE SECURED LENDING LLC', 'Carlyle Secured Lending LLC'),
    ('GRUPO TELEVISA SAB', 'Grupo Televisa SAB'),
    ('PBF ENERGY INC', 'PBF Energy Inc'),
    ('U.S. BANCORP', 'U.S. Bancorp'),
    ('AT&T INC.', 'AT&T Inc.'),
    ('ALIBABA GROUP HOLDING LTD', 'Alibaba Group Holding Ltd'),
    ('BANK OF AMERICA CORP', 'Bank of America Corp'),
    ('MCKESSON CORP', 'McKesson Corp'),
    ('3M CO', '3M Co'),
    ('', ''),
    (None, ''),
])
def test_case_company_name(raw, cased):
    assert case_company_name(raw) == cased


@pytest.mark.parametrize('raw, shown', [
    ('MICRON TECHNOLOGY INC', 'Micron Technology'),
    ('Tesla, Inc.', 'Tesla'),
    ('Braemar Hotels & Resorts Inc.', 'Braemar Hotels & Resorts'),
    ('ACME CORP /DE/', 'Acme'),
    ('Booking Holdings Inc.', 'Booking Holdings'),
    ('Linde plc', 'Linde'),
    ('JPMorgan Chase & Co.', 'JPMorgan Chase'),
    ('PURE CYCLE CORP', 'Pure Cycle'),
    ('eBay Inc.', 'eBay'),
    ('Fifth Third Bancorp', 'Fifth Third Bancorp'),
    ('Johnson & Johnson', 'Johnson & Johnson'),
    ('Energy Transfer LP', 'Energy Transfer'),
    ('Inc', 'Inc'),
    ('', ''),
])
def test_display_company_name(raw, shown):
    assert display_company_name(raw) == shown
