from app.data.celestrak import parse_tle_text
from tests.conftest import ISS_LINE1, ISS_LINE2


def test_parses_crlf_and_padded_names() -> None:
    text = f"ISS (ZARYA)             \r\n{ISS_LINE1}\r\n{ISS_LINE2}\r\n"
    [rec] = parse_tle_text(text)
    assert rec.norad_id == 25544
    assert rec.name == "ISS (ZARYA)"
    assert rec.line1 == ISS_LINE1


def test_skips_bad_checksum_and_resyncs() -> None:
    bad_line2 = ISS_LINE2[:-1] + str((int(ISS_LINE2[-1]) + 1) % 10)
    text = "\n".join(["BROKEN", ISS_LINE1, bad_line2, "junk line", "ISS", ISS_LINE1, ISS_LINE2])
    records = parse_tle_text(text)
    assert [r.name for r in records] == ["ISS"]


def test_notice_text_yields_nothing() -> None:
    assert parse_tle_text("No GP data found") == []
