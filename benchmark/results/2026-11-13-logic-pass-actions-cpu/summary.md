# Planted-bug benchmark

39 real merged PRs, each with one bug planted on a line it adds. Each model reviewed
them as the Action does: the `small` profile and an 8,192-token window, unless a variant says
otherwise. Only the model's own findings count. A finding is near the bug when it's on that file
within 3 lines; found means it was read and it describes the bug. Where the PRs were also
reviewed as merged (the clean control), *near only with the bug* counts the cases whose clean
version got no finding on that line: a model that comments on every changed line doesn't score.

| Model | Variant | PRs | Near the bug | Near it when clean | Near only with the bug | Found (dev + test) | Other findings | Other findings marked right | Findings on the clean PRs | Failed calls | Median time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gpt-oss:20b` | logic-context10-verify-strict | 39 | 29 of 39 | 1 of 39 | 29 of 39 | 27 of 39 (14 + 13) | 12 | 0 of 1 marked | 15 | 3 of 276 | 676s |
| `gpt-oss:20b` | logic-verify-strict | 19 | 11 of 19 | 2 of 19 | 11 of 19 | 11 of 19 (11 + 0) | 12 | 1 of 3 marked | 5 | 1 of 118 | 433s |
| `gpt-oss:20b` | reasoning-low-verify-strict | 39 | 19 of 39 | 2 of 39 | 19 of 39 | 18 of 39 (9 + 9) | 9 | 1 of 3 marked | 8 | 3 of 129 | 223s |
| `gpt-oss:20b` | reasoning-low-verify-strict-context10 | 19 | 10 of 19 | 0 of 19 | 10 of 19 | 10 of 19 (10 + 0) | 4 | 0 of 0 marked | 5 | 0 of 69 | 249s |
| `gpt-oss:20b` | samples2-logic-verify-strict | 19 | 12 of 19 | 2 of 19 | 11 of 19 | 11 of 19 (11 + 0) | 7 | 0 of 3 marked | 6 | 0 of 162 | 497s |
| `gpt-oss:20b` | samples2-verify-strict | 19 | 10 of 19 | 1 of 19 | 9 of 19 | 9 of 19 (9 + 0) | 5 | 0 of 3 marked | 6 | 1 of 109 | 252s |

Found, by kind of bug:

| Model | Variant | flipped-comparison | inverted-return | missing-await | negated-condition | not-in | off-by-one | wrong-boolean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gpt-oss:20b` | logic-context10-verify-strict | 9 of 11 | 2 of 4 | 1 of 3 | 5 of 9 | 1 of 1 | 2 of 4 | 7 of 7 |
| `gpt-oss:20b` | logic-verify-strict | 4 of 6 | 0 of 2 | 0 of 1 | 2 of 4 | 1 of 1 | 1 of 2 | 3 of 3 |
| `gpt-oss:20b` | reasoning-low-verify-strict | 8 of 11 | 0 of 4 | 1 of 3 | 5 of 9 | 1 of 1 | 0 of 4 | 3 of 7 |
| `gpt-oss:20b` | reasoning-low-verify-strict-context10 | 4 of 6 | 0 of 2 | 1 of 1 | 2 of 4 | 1 of 1 | 0 of 2 | 2 of 3 |
| `gpt-oss:20b` | samples2-logic-verify-strict | 5 of 6 | 0 of 2 | 0 of 1 | 2 of 4 | 1 of 1 | 0 of 2 | 3 of 3 |
| `gpt-oss:20b` | samples2-verify-strict | 4 of 6 | 0 of 2 | 1 of 1 | 1 of 4 | 1 of 1 | 0 of 2 | 2 of 3 |

**31 finding(s) not marked yet**, so Found and the marked counts are incomplete.

Left out: encode/httpx#3670. Not a bug: the stream's file comes from the builtin open(), so close() is synchronous and dropping its await fixes the line rather than breaking it. The merged PR awaits that file's read() and close() (lines 199, 205, 217), which raises TypeError on httpx's v1 branch.
