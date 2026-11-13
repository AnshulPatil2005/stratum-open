from stratum_open.util import count


def test_count_is_singular_only_for_one():
    assert count(1, "file") == "1 file"
    assert [count(n, "serious finding") for n in (0, 2)] == ["0 serious findings", "2 serious findings"]
