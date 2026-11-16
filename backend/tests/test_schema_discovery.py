"""Unit tests for the type-inference rules (no API, no storage)."""
import pandas as pd
import pytest

from app.ingestion.parsers import ParseError, ParseOptions, parse
from app.ingestion.schema import discover, infer_type, normalise_names, snake_case


def t(values):
    return infer_type(pd.Series(values, dtype=object).astype(str))[0]


@pytest.mark.parametrize("values,expected", [
    (["1", "-2", "+3"], "integer"),
    (["1", "2.5", "1e3"], "double"),
    (["1,5", "2"], "double"),
    (["true", "FALSE"], "boolean"),
    (["001", "002"], "string"),  # codes keep their leading zeros
    (["2024-01-31", "2024-02-29"], "date"),
    (["2024-02-30"], "string"),  # not a real date
    (["31/12/2024", "01/02/2024"], "date"),
    (["2024-01-01T10:00:00Z", "2024-01-01 11:00"], "timestamp"),
    (["1", "two"], "string"),
    (["99999999999999999999"], "double"),  # too big for a 64-bit integer
])
def test_infer_type(values, expected):
    assert t(values) == expected


def test_names_are_normalised_and_unique():
    assert snake_case("Date de Création") == "date_de_creation"
    assert snake_case("customerID") == "customer_id"
    assert snake_case("2024 sales") == "c_2024_sales"
    assert normalise_names(["A", "a", "", "a "]) == ["a", "a_2", "column_3", "a_3"]


def test_null_tokens_and_profile():
    raw = parse(b"id,score\n1,NA\n2,\n3,7\n", "csv", "x.csv")
    d = discover(raw)
    score = d.schema[1]
    assert score["type"] == "integer" and score["null_count"] == 2 and score["min"] == 7
    assert d.table.column("score").to_pylist() == [None, None, 7]


def test_latin1_file_is_read_with_a_warning():
    raw = parse("nom\nCréteil\n".encode("cp1252"), "csv", "x.csv")
    assert raw.rows == [["Créteil"]]
    assert any("Windows-1252" in n for n in raw.notes)


def test_parse_errors():
    with pytest.raises(ParseError):
        parse(b"\x00\x01binary", "csv", "x.csv")
    with pytest.raises(ParseError):
        parse(b"not an excel file", "excel", "x.xlsx")
    with pytest.raises(ParseError):
        parse(b'"just a string"', "json", "x.json")
    with pytest.raises(ParseError):
        parse(b"a,b\n", "csv", "x.csv", ParseOptions(delimiter=";;"))
