import pytest

from locallib.json_extractor import JsonExtractor

"""
Test: jq style json extraction
"""

DOCUMENT = {
    "application": {"name": "WIN.FieldCapacity"},
    "properties": {"count": 3, "msg": "hello", "tags": ["a", "b"]},
    "items": [{"name": "one", "value": 1}, {"name": "two", "value": 2}],
    "odd key": {"inner": "yes"},
}


@pytest.fixture()
def extractor():
    return JsonExtractor()


@pytest.mark.unit
def test_nested_keys(extractor):
    assert extractor.extract_first(DOCUMENT, ".properties.count") == 3
    assert extractor.extract_first(DOCUMENT, ".application.name") == "WIN.FieldCapacity"


@pytest.mark.unit
def test_case_insensitive_fallback(extractor):
    assert (
        extractor.extract_first({"Properties": {"Count": 7}}, ".properties.count") == 7
    )


@pytest.mark.unit
def test_list_indexes(extractor):
    assert extractor.extract_first(DOCUMENT, ".items[0].name") == "one"
    assert extractor.extract_first(DOCUMENT, ".items[-1].name") == "two"
    assert extractor.extract_first(DOCUMENT, ".properties.tags[1]") == "b"


@pytest.mark.unit
def test_wildcards_return_every_match(extractor):
    assert extractor.extract_all(DOCUMENT, ".items[].value") == [1, 2]


@pytest.mark.unit
def test_key_over_a_list_maps(extractor):
    assert extractor.extract_all(DOCUMENT, ".items.name") == ["one", "two"]


@pytest.mark.unit
def test_quoted_keys(extractor):
    assert extractor.extract_first(DOCUMENT, '."odd key".inner') == "yes"
    assert extractor.extract_first(DOCUMENT, '.["odd key"].inner') == "yes"


@pytest.mark.unit
def test_missing_paths_return_the_default(extractor):
    assert extractor.extract_first(DOCUMENT, ".properties.missing") is None
    assert extractor.extract_first(DOCUMENT, ".nope.deeper", default=0) == 0
    assert extractor.extract_all(DOCUMENT, ".items[9].name") == []


@pytest.mark.unit
def test_identity_path(extractor):
    assert extractor.extract_first(DOCUMENT, ".") is DOCUMENT


@pytest.mark.unit
def test_leading_dot_is_optional(extractor):
    assert extractor.extract_first(DOCUMENT, "properties.count") == 3


@pytest.mark.unit
def test_unparseable_path_raises(extractor):
    with pytest.raises(ValueError):
        extractor.extract_first(DOCUMENT, ".items[")
