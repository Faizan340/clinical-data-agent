from evals.run_evals import results_match


def test_scalar_match_ignores_type():
    assert results_match([(412,)], [[412.0]])


def test_breakdown_is_order_insensitive():
    assert results_match([("female", 10), ("male", 12)], [["male", 12], ["female", 10]])


def test_extra_agent_columns_are_ok():
    assert results_match([("female", 10)], [["female", 10, 45.5]])


def test_wrong_number_fails():
    assert not results_match([(412,)], [[411]])


def test_row_count_must_match():
    assert not results_match([("a", 1)], [["a", 1], ["b", 2]])
