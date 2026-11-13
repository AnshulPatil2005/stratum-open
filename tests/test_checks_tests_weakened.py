import pytest

from stratum_open.checks import run_checks, tests_weakened
from stratum_open.diff import parse_diff
from stratum_open.findings import assign_fingerprints, dedupe, placeable
from stratum_open.policy import parse_policy
from stratum_open.profiles import apply

ON = parse_policy("checks:\n  weakened_tests: true\n")


def patch(path, body, *, header="", start=10):
    """One hunk of a git diff, written as a diff: each line of `body` starts
    with a space (unchanged), `-` or `+`. A blank line is an unchanged one."""
    lines = body.strip("\n").split("\n")
    old = sum(1 for line in lines if not line.startswith("+"))
    new = sum(1 for line in lines if not line.startswith("-"))
    head = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}", f"@@ -{start},{old} +{start},{new} @@ {header}".rstrip()]
    return "\n".join(head + lines) + "\n"


def found(text):
    return [(f.rule, f.line_start, f.title) for f in tests_weakened.check(parse_diff(text))]


def rules(text):
    return [rule for rule, _, _ in found(text)]


def marked(marker, path="tests/test_orders.py", test=" def test_total():\n     assert total() == 42"):
    """A marker added above a test that was already there."""
    return patch(path, f" import pytest\n \n+{marker}\n{test}\n")


# ── a test switched off ──────────────────────────────────────────────────


def test_a_skip_marker_added_to_a_test_that_was_there_is_reported_on_its_line():
    [finding] = tests_weakened.check(parse_diff(marked('@pytest.mark.skip(reason="fails on CI")')))
    assert (finding.rule, finding.line_start, finding.title) == ("tests/skipped", 12, "`test_total` is switched off")
    assert (finding.severity, finding.category, finding.source, finding.needs_human) == ("medium", "tests", "check", False)
    assert '`@pytest.mark.skip(reason="fails on CI")`' in finding.detail and "no longer runs" in finding.detail
    assert "why" in finding.suggestion


@pytest.mark.parametrize("marker", [
    "@pytest.mark.skip",
    '@pytest.mark.skip("flaky")',
    "@pytest.mark.skip()",
    '@pytest.mark.skipif(True, reason="flaky")',
    '@unittest.skip("later")',
    '@mark.skip(reason="later")',
])
def test_python_markers_that_switch_a_test_off(marker):
    assert found(marked(marker)) == [("tests/skipped", 12, "`test_total` is switched off")]


@pytest.mark.parametrize("marker", [
    "@pytest.mark.xfail",
    '@pytest.mark.xfail(reason="see #12", strict=True)',
    "@pytest.mark.xfail(\n+    reason=\"see #12\",\n+)",
    "@unittest.expectedFailure",
])
def test_an_expected_failure_is_a_test_allowed_to_fail(marker):
    [finding] = tests_weakened.check(parse_diff(marked(marker)))
    assert (finding.rule, finding.line_start, finding.title) == ("tests/skipped", 12, "`test_total` is allowed to fail")
    assert "can now fail without failing the run" in finding.detail


@pytest.mark.parametrize("marker", [
    '@pytest.mark.skipif(sys.platform == "win32", reason="no fork")',
    '@pytest.mark.skipif(not HAS_NUMPY, reason="needs numpy")',
    '@pytest.mark.skipif(\n+    sys.version_info < (3, 12),\n+    reason="needs 3.12",\n+)',
    '@pytest.mark.xfail(sys.platform == "win32", reason="no fork")',
    '@pytest.mark.xfail(condition=WIN, reason="no fork")',
    '@unittest.skipIf(WIN, "no fork")',
    '@unittest.skipUnless(HAS_NUMPY, "needs numpy")',
    "@pytest.mark.slow",
    '# @pytest.mark.skip(reason="was flaky")',
])
def test_a_skip_that_something_decides_is_left_alone(marker):
    assert found(marked(marker)) == []


def test_a_skip_called_in_a_tests_own_body_counts_and_one_under_a_condition_does_not():
    assert found(patch("tests/test_orders.py", """
 def test_total():
+    pytest.skip("broken since the upgrade")
     assert total() == 42
""")) == [("tests/skipped", 11, "`test_total` is switched off")]
    assert rules(patch("tests/test_orders.py", """
 class TestOrders(unittest.TestCase):
     def test_total(self):
+        self.skipTest("broken")
         self.assertEqual(total(), 42)
""")) == ["tests/skipped"]
    assert found(patch("tests/test_orders.py", """
 def test_total():
+    if sys.platform == "win32":
+        pytest.skip("no fork on Windows")
+    try:
+        import numpy
+    except ImportError:
+        pytest.skip("needs numpy")
     assert total() == 42
""")) == []


def test_a_test_the_pr_adds_switched_off_from_the_start_is_left_alone():
    assert found(patch("tests/test_orders.py", """
 import pytest

+@pytest.mark.skip(reason="needs the new API")
+def test_refund():
+    assert refund() == 42
+
+def test_other():
+    pytest.skip("later")
""")) == []
    assert found(patch("src/lib.rs", "+#[test]\n+#[ignore]\n+fn slow() {}\n")) == []
    assert found(patch("test/orders.test.js", "+  it.skip('refunds', () => {})\n+  it.todo('adds tax')\n")) == []
    assert found(patch("pkg/orders_test.go", "+func TestRefund(t *testing.T) {\n+\tt.Skip(\"later\")\n+}\n")) == []


def test_a_marker_on_a_test_whose_first_line_the_pr_also_rewrites_still_counts():
    assert found(patch("tests/test_orders.py", """
+@pytest.mark.skip
-def test_total():
+def test_total(client):
     assert total() == 42
""")) == [("tests/skipped", 10, "`test_total` is switched off")]


def test_a_whole_class_a_whole_module_and_one_parametrized_case():
    assert found(marked("@pytest.mark.skip", test=" class TestOrders:\n     def test_total(self):\n         assert 1")) == [
        ("tests/skipped", 12, "`TestOrders` is switched off")]
    assert rules(patch("tests/test_orders.py", " import pytest\n+pytestmark = pytest.mark.skip(reason=\"broken\")\n")) == ["tests/skipped"]
    assert rules(patch("tests/test_orders.py", " import pytest\n+pytestmark = pytest.mark.skipif(WIN, reason=\"no fork\")\n")) == []
    assert found(patch("tests/test_orders.py", """
 @pytest.mark.parametrize("amount, total", [
-    (1, 2),
+    pytest.param(1, 2, marks=pytest.mark.xfail),
     (3, 4),
+    pytest.param(5, 6, marks=pytest.mark.skip(reason="not yet")),
 ])
""")) == [("tests/skipped", 11, "A test is allowed to fail")]  # the second case is new: nothing that ran stops


def test_a_javascript_test_switched_off_is_one_whose_title_ran_before():
    assert found(patch("test/orders.test.js", """
 describe('orders', () => {
-  it('adds tax', () => {
+  it.skip('adds tax', () => {
     expect(total()).toBe(42)
   })
-  test("rounds", () => {
+  xtest("rounds", () => {
   })
-  it('refunds', () => { expect(refund()).toBe(1) })
+  it.todo('refunds')
 })
""")) == [("tests/skipped", 11, "`adds tax` is switched off"), ("tests/skipped", 14, "`rounds` is switched off"),
          ("tests/skipped", 16, "`refunds` is switched off")]
    assert rules(patch("test/orders.test.ts", "-describe('orders', () => {\n+describe.skip('orders', () => {\n")) == ["tests/skipped"]


@pytest.mark.parametrize("line", [
    "  test.skip(isWindows, 'no fork')",  # Playwright: a condition comes first
    "  it.skipIf(isWindows)('adds tax', () => {",
    "  test.runIf(isBuild)('adds tax', () => {",
    "  (isWindows ? it.skip : it)('adds tax', () => {",
    "  // it.skip('adds tax', () => {",
])
def test_a_javascript_skip_with_a_condition_is_left_alone(line):
    assert found(patch("test/orders.test.js", f"-  it('adds tax', () => {{\n+{line}\n")) == []


@pytest.mark.parametrize("line", ["  it.only('adds tax', () => {", "  describe.only('orders', () => {", "  test.only(`adds tax`, async () => {", "  fit('adds tax', () => {"])
def test_an_added_only_leaves_the_other_tests_out(line):
    [finding] = tests_weakened.check(parse_diff(patch("src/__tests__/orders.spec.ts", f" import {{ total }} from '../orders'\n+{line}\n")))
    assert (finding.rule, finding.line_start, finding.severity) == ("tests/only", 11, "medium")
    assert "none of the other tests in the file" in finding.detail
    assert found(patch("src/orders.ts", f"+{line}\n")) == []  # not a test file


def test_a_function_of_the_projects_own_called_fit_is_not_a_focused_test():
    assert found(patch("test/curves.test.js", " it('fits a line', () => {\n+  fit('linear', xs, ys)\n+  fit(model)\n })\n")) == []


def test_a_go_skip_at_the_top_of_a_test_and_not_one_under_a_condition():
    assert found(patch("pkg/orders_test.go", """
 func TestTotal(t *testing.T) {
+	t.Skip("flaky on CI")
 	if got := Total(); got != 42 {
 		t.Fatalf("got %d", got)
 	}
 }

 func TestRefund(t *testing.T) {
+	if runtime.GOOS == "windows" {
+		t.Skip("no fork")
+	}
+	if testing.Short() {
+		t.Skipf("slow")
+	}
 	eventually(func() {
+		t.Skip("not ready")
 	})
 	t.Run("partial", func(t *testing.T) {
+		t.SkipNow()
 	})
 }
""")) == [("tests/skipped", 11, "`TestTotal` is switched off"), ("tests/skipped", 28, "A test is switched off")]


def test_a_go_function_named_only_by_the_hunk_header_counts_one_tab_in():
    below = patch("pkg/orders_test.go", " \tgot := Total()\n+\tt.Skip(\"flaky\")\n \twant := 42\n", header="func TestTotal(t *testing.T) {")
    assert found(below) == [("tests/skipped", 11, "`TestTotal` is switched off")]
    deeper = patch("pkg/orders_test.go", " \t\tgot := Total()\n+\t\tt.Skip(\"flaky\")\n", header="func TestTotal(t *testing.T) {")
    assert found(deeper) == []  # under a block the hunk doesn't show, maybe an `if`


def test_a_rust_ignore_counts_wherever_the_test_is_and_cfg_attr_does_not():
    assert found(patch("src/orders.rs", """
     #[test]
+    #[ignore]
     fn adds_tax() {
         assert_eq!(total(), 42);
     }

     #[tokio::test]
+    #[ignore = "flaky"]
     async fn refunds() {}

     #[test]
+    #[cfg_attr(miri, ignore)]
+    #[cfg_attr(target_os = "windows", ignore = "no fork")]
     fn forks() {}
""")) == [("tests/skipped", 11, "`adds_tax` is switched off"), ("tests/skipped", 17, "`refunds` is switched off")]


def test_two_tests_switched_off_by_the_same_line_stay_two_findings():
    files = parse_diff(patch("tests/test_orders.py", """
+@pytest.mark.skip
 def test_total():
     assert total() == 42

+@pytest.mark.skip
 def test_refund():
     assert refund() == 1
"""))
    findings = tests_weakened.check(files)
    assign_fingerprints(findings, files)
    assert len(dedupe(findings)) == 2 and len({f.fingerprint for f in findings}) == 2


# ── assertions removed ───────────────────────────────────────────────────


def test_an_assertion_removed_with_nothing_in_its_place_is_reported():
    files = parse_diff(patch("tests/test_orders.py", """
 def test_total():
     order = make_order()
-    assert order.total == 42
     assert order.items
"""))
    [finding] = tests_weakened.check(files)
    assert (finding.rule, finding.title, finding.severity, finding.category) == (
        "tests/assertions-removed", "Assertions removed from `test_total`", "medium", "tests")
    assert "removes an assertion" in finding.detail and "`assert order.total == 42`" in finding.detail
    # A removed line has no place in the new file: the finding sits on the unchanged line above, where GitHub takes a comment.
    assert finding.line_start == 11 and placeable([finding], files) == [finding]


def test_several_are_one_finding_for_the_function():
    [finding] = tests_weakened.check(parse_diff(patch("tests/test_orders.py", """
 def test_total():
     order = make_order()
-    assert order.total == 42
-    assert order.tax == 7
     order.close()
-    with pytest.raises(Closed):
-        order.add(item)
""")))
    assert "removes 3 assertions" in finding.detail and "`assert order.total == 42` and 2 more" in finding.detail


def test_a_rewritten_assertion_is_not_a_finding():
    assert found(patch("tests/test_orders.py", """
 def test_total():
     order = make_order()
-    assert order.total == 42
+    assert order.total == 43
-    assert order.items
+    check_items(order)
     order.close()
-    self.assertEqual(order.state, "closed")
+    assert order.state == "closed"
""")) == []


def test_code_written_in_its_place_may_be_another_way_to_check():
    helper = patch("test/orders.test.js", """
   it('warns once', async () => {
-    const onWarning = () => {
-      assert.fail('should not warn')
-    }
-    process.on('warning', onWarning)
+    assertNoWarning(t)
     await run()
   })
""")
    assert found(helper) == []


def test_an_assertion_commented_out_or_swapped_for_pass_is_reported_on_the_added_line():
    commented = patch("tests/test_orders.py", """
 def test_total():
     order = make_order()
-    assert order.total == 42
+    # assert order.total == 42  (fails since the rounding change)
""")
    assert found(commented) == [("tests/assertions-removed", 12, "Assertions removed from `test_total`")]
    passed = patch("tests/test_orders.py", " def test_total():\n-    assert total() == 42\n+    pass\n")
    assert found(passed) == [("tests/assertions-removed", 11, "Assertions removed from `test_total`")]


def test_an_assertion_moved_is_not_removed():
    within = patch("tests/test_orders.py", """
 def test_total():
+    assert make_order().total == 42
     order = make_order()
     order.close()
-    assert order.total == 42
     assert order.closed
""")
    assert found(within) == []
    to_another_test = patch("tests/test_orders.py", """
 def test_total():
     order = make_order()
-    assert order.total == 42
     assert order.items


+def test_total_is_kept():
+    order = make_order()
+    assert order.total == 42
""")
    assert found(to_another_test) == []


def test_a_test_deleted_outright_or_renamed_is_not_reported():
    # A rule for deleted tests was measured and left out: nearly always the PR had removed what the test tested.
    deleted = patch("app/orders.py", " def total():\n-    return 42\n+    return 43\n") + patch("tests/test_orders.py", """
 def test_total():
     assert total() == 42


-def test_refund():
-    order = make_order()
-    assert order.refund() == 42
-
-
 def test_other():
     assert other()
""")
    assert found(deleted) == []
    renamed = patch("tests/test_orders.py", """
-def test_refund():
+def test_refund_returns_the_total():
     order = make_order()
     assert order.refund() == 42
""")
    assert found(renamed) == []


def test_a_test_whose_first_line_changed_can_still_lose_assertions():
    assert found(patch("tests/test_orders.py", """
-def test_total():
+def test_total(client):
     order = make_order()
-    assert order.total == 42
     order.close()
""")) == [("tests/assertions-removed", 11, "Assertions removed from `test_total`")]


@pytest.mark.parametrize("path, body, title", [
    ("test/orders.test.js", """
   it('adds tax', async () => {
     const res = await request(app).get('/')
-    expect(res.status).toBe(200)
     expect(res.body).toEqual({})
   })
""", "Assertions removed from `adds tax`"),
    ("test/orders.js", """
   it('adds tax', function (done) {
     request(app)
       .get('/')
-      .expect('Content-Type', /json/)
       .expect(200, done)
   })
""", "Assertions removed from `adds tax`"),
    ("test/orders.test.js", """
 test('adds tax', async (t) => {
-  t.plan(2)
-  t.assert.strictEqual(total(), 42)
   await run()
 })
""", "Assertions removed from `adds tax`"),
    ("pkg/orders_test.go", """
 func TestTotal(t *testing.T) {
 	got := Total()
-	if got != 42 {
-		t.Errorf("got %d, want 42", got)
-	}
-	require.NoError(t, err)
 	cleanup()
 }
""", "Assertions removed from `TestTotal`"),
    ("tests/orders.rs", """
 #[test]
 fn adds_tax() {
     let order = make_order();
-    assert_eq!(order.total(), 42);
     order.close();
 }
""", "Assertions removed from `adds_tax`"),
])
def test_each_languages_assertions(path, body, title):
    assert [(rule, name) for rule, _, name in found(patch(path, body))] == [("tests/assertions-removed", title)]


def test_a_hunk_that_starts_inside_a_function_names_it_only_when_its_header_is_sure_to():
    removal = " \torder := MakeOrder()\n-\trequire.Equal(t, 42, order.Total)\n \torder.Close()\n"
    assert found(patch("pkg/orders_test.go", removal, header="func TestTotal(t *testing.T) {")) == [
        ("tests/assertions-removed", 10, "Assertions removed from `TestTotal`")]
    removal = "     order = make_order()\n-    assert order.total == 42\n     order.close()\n"
    assert found(patch("tests/test_orders.py", removal, header="def test_total():")) == [
        ("tests/assertions-removed", 10, "Assertions removed from `test_total`")]
    # A Python header can be a method of a class the test defines, so it doesn't name the test.
    assert found(patch("tests/test_orders.py", removal, header="def on_mount(self):")) == [
        ("tests/assertions-removed", 10, "Assertion removed: `assert order.total == 42`")]


def test_a_function_inside_the_test_is_counted_with_it():
    assert found(patch("tests/test_orders.py", """
 def test_total():
     class App:
         def on_mount(self):
             self.ready = True

-    assert App().ready is None
+    def check(app):
+        assert app.ready

     check(App())
""")) == []


def test_hunks_under_the_same_header_may_be_one_function():
    removal = "     order.close()\n-    expect(order.total).toBe(42)\n     done()\n"
    addition = "     const order = makeOrder()\n+    expect(order.items).toHaveLength(2)\n     order.pay()\n"

    def two_hunks(first_header, second_header, first_body=addition):
        first = patch("test/orders.test.js", first_body, header=first_header)
        second = patch("test/orders.test.js", removal, header=second_header, start=80)
        return first + "\n".join(second.split("\n")[3:])

    assert found(two_hunks("describe('orders', () => {", "describe('orders', () => {")) == []
    assert rules(two_hunks("describe('orders', () => {", "describe('refunds', () => {")) == ["tests/assertions-removed"]
    # The test the first hunk renames is not the same test still running: what it loses further down isn't this rule's.
    renamed = "-  it('totals an order', () => {\n+  it('totals a paid order', () => {\n     const order = makeOrder()\n"
    assert found(two_hunks("describe('orders', () => {", "describe('orders', () => {", renamed)) == []


def test_with_no_unchanged_line_near_it_the_finding_is_about_the_file():
    text = ("diff --git a/tests/test_orders.py b/tests/test_orders.py\n--- a/tests/test_orders.py\n+++ b/tests/test_orders.py\n"
            "@@ -1,2 +0,0 @@ def test_total():\n-    assert total() == 42\n-    assert tax() == 7\n")
    [finding] = tests_weakened.check(parse_diff(text))
    assert finding.rule == "tests/assertions-removed" and finding.line_start is None and finding.path == "tests/test_orders.py"


def test_only_test_files_of_known_languages_are_read():
    removal = " def check_total():\n     order = make_order()\n-    assert order.total == 42\n     order.close()\n"
    assert rules(patch("tests/test_orders.py", removal)) == ["tests/assertions-removed"]
    assert found(patch("app/orders.py", removal)) == []
    assert found(patch("tests/data/orders.txt", removal)) == []
    assert found(patch("src/orders.rs", " fn total() {\n-    assert!(ready());\n     42\n }\n")) == []  # Rust beside the code: only `#[ignore]`
    gone = ("diff --git a/tests/test_orders.py b/tests/test_orders.py\ndeleted file mode 100644\n--- a/tests/test_orders.py\n+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n-def test_total():\n-    assert total() == 42\n")
    assert found(gone) == []


# ── a tolerance loosened ─────────────────────────────────────────────────


@pytest.mark.parametrize("path, before, after", [
    ("tests/test_rates.py", "    assert rate() == pytest.approx(0.3, abs=1e-9)", "    assert rate() == pytest.approx(0.3, abs=1e-2)"),
    ("tests/test_rates.py", "    assert rate() == approx(0.3, rel=1e-6, abs=1e-12)", "    assert rate() == approx(0.3, rel=0.05, abs=1e-12)"),
    ("tests/test_rates.py", "        self.assertAlmostEqual(rate(), 0.3, places=7)", "        self.assertAlmostEqual(rate(), 0.3, places=2)"),
    ("tests/test_rates.py", "        self.assertAlmostEqual(rate(), 0.3, delta=0.001)", "        self.assertAlmostEqual(rate(), 0.3, delta=0.1)"),
    ("tests/test_rates.py", "    assert math.isclose(rate(), 0.3, rel_tol=1e-9)", "    assert math.isclose(rate(), 0.3, rel_tol=1e-3)"),
    ("tests/test_rates.py", "    np.testing.assert_allclose(rates, want, rtol=1e-7, atol=0)", "    np.testing.assert_allclose(rates, want, rtol=1e-2, atol=0)"),
    ("test/rates.test.ts", "    expect(rate()).toBeCloseTo(0.3, 5)", "    expect(rate()).toBeCloseTo(0.3, 1)"),
    ("pkg/rates_test.go", "\tassert.InDelta(t, 0.3, rate(), 0.0001)", "\tassert.InDelta(t, 0.3, rate(), 0.1)"),
    ("tests/rates.rs", "    assert_relative_eq!(rate(), 0.3, epsilon = 1e-9);", "    assert_relative_eq!(rate(), 0.3, epsilon = 1e-2);"),
])
def test_the_same_assertion_with_a_wider_tolerance_is_reported(path, before, after):
    [finding] = tests_weakened.check(parse_diff(patch(path, f" setup()\n-{before}\n+{after}\n")))
    assert (finding.rule, finding.line_start, finding.severity) == ("tests/tolerance-loosened", 11, "medium")
    assert before.strip() in finding.detail and after.strip() in finding.detail
    assert found(patch(path, f" setup()\n-{after}\n+{before}\n")) == []  # tightened


@pytest.mark.parametrize("before, after", [
    ("    assert rate() == pytest.approx(0.3, abs=1e-9)", "    assert rate() == pytest.approx(0.25, abs=1e-2)"),  # another expected value too
    ("    assert rate() == pytest.approx(0.3, abs=1e-9)", "    assert rate() == pytest.approx(0.3, abs=1e-9)  # exact"),
    ("    assert rate() == pytest.approx(0.3, rel=1e-3, abs=1e-9)", "    assert rate() == pytest.approx(0.3, rel=1e-6, abs=1e-2)"),  # one wider, one tighter
    ("    assert rate() == 0.3", "    assert rate() == pytest.approx(0.3, abs=1e-2)"),  # exact to approximate: often right
    ("    sleep(timeout=0.1)", "    sleep(timeout=5)"),
])
def test_another_assertion_or_a_tolerance_not_only_wider_is_left_alone(before, after):
    assert "tests/tolerance-loosened" not in rules(patch("tests/test_rates.py", f" def test_rate():\n-{before}\n+{after}\n"))


# ── the policy ───────────────────────────────────────────────────────────


def test_it_runs_only_when_the_policy_turns_it_on():
    files = parse_diff(marked("@pytest.mark.skip"))
    assert [f.rule for f in run_checks(files, ON) if f.rule.startswith("tests/")] == ["tests/skipped"]
    for policy in (parse_policy(None), parse_policy("checks:\n  heuristics: true\n"),
                   apply(parse_policy(None), "small"), apply(parse_policy(None), "large")):
        assert not [f for f in run_checks(files, policy) if f.rule.startswith("tests/")]
    assert parse_policy("checks:\n  weakened_tests: yes please\n").errors == ("checks.weakened_tests must be true or false.",)


def test_the_file_level_heuristic_is_as_it_was():
    files = parse_diff(patch("tests/test_orders.py", " def test_total():\n-    assert total() == 42\n-    assert tax() == 7\n+    pass\n"))
    both = parse_policy("checks:\n  weakened_tests: true\n  heuristics: true\n")
    assert sorted(f.rule for f in run_checks(files, both)) == ["heuristic/tests-weakened", "tests/assertions-removed"]
    assert [f.rule for f in run_checks(files, parse_policy("checks:\n  heuristics: true\n"))] == ["heuristic/tests-weakened"]


def test_many_markers_and_one_long_run_of_changes_stay_quick():
    skips = "".join(f"-  it('case {i}', () => {{}})\n+  it.skip('case {i}', () => {{}})\n" for i in range(4000))
    assert len(found(patch("test/orders.test.js", skips))) == 4000
    removed = "".join(f"-    assert total({i}) == {i}\n" for i in range(4000)) + "".join(f"+    value_{i} = {i}\n" for i in range(4000))
    assert found(patch("tests/test_orders.py", " def test_total():\n" + removed)) == []
