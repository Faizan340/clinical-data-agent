from app.verify import check_result_shape, verify_answer


def test_how_many_with_several_rows_is_rejected():
    issue = check_result_shape("How many patients had more than one emergency visit?", ["count"], [[1], [1], [1]])
    assert issue and "3 rows" in issue


def test_how_many_single_row_is_fine():
    assert check_result_shape("How many patients do we have?", ["count"], [[699]]) is None


def test_breakdowns_may_return_several_rows():
    assert check_result_shape("How many patients by gender?", ["g", "n"], [["male", 1], ["female", 2]]) is None
    assert check_result_shape("Break down asthma by gender", ["g", "n"], [["male", 1], ["female", 2]]) is None

Q = "How many diabetic patients over 60 haven't had an HbA1c test in the last 6 months?"


def test_number_from_result_passes():
    ok, _ = verify_answer("There are 57 such patients.", Q, ["count"], [[57]])
    assert ok


def test_numbers_from_question_pass():
    ok, _ = verify_answer("57 diabetic patients over 60 have no HbA1c in 6 months.", Q, ["count"], [[57]])
    assert ok


def test_hallucinated_number_fails():
    ok, issues = verify_answer("There are 75 such patients.", Q, ["count"], [[57]])
    assert not ok and "75" in issues


def test_rounding_and_thousands_separator():
    assert verify_answer("Average HbA1c is 8.6%.", "avg?", ["avg"], [[8.57]])[0]
    assert verify_answer("1,204 patients.", "count?", ["count"], [[1204]])[0]


def test_column_total_and_row_count_allowed():
    rows = [["female", 174], ["male", 179]]
    ok, _ = verify_answer("353 patients across 2 genders: 174 female, 179 male.", "breakdown?", ["g", "n"], rows)
    assert ok
