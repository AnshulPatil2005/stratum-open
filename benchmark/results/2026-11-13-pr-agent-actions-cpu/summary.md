# Planted-bug benchmark

19 real merged PRs, each with one bug planted on a line it adds. Each model reviewed
them as the Action does: the `small` profile and an 8,192-token window, unless a variant says
otherwise. Only the model's own findings count. A finding is near the bug when it's on that file
within 3 lines; found means it was read and it describes the bug. Where the PRs were also
reviewed as merged (the clean control), *near only with the bug* counts the cases whose clean
version got no finding on that line: a model that comments on every changed line doesn't score.

| Model | Variant | PRs | Near the bug | Near it when clean | Near only with the bug | Found (dev + test) | Other findings | Other findings marked right | Findings on the clean PRs | Failed calls | Median time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gpt-oss:20b` | pr-agent-0.50.0-reasoning-low | 19 | 12 of 19 | 9 of 19 | 6 of 19 | 10 of 19 (10 + 0) | 15 | 0 of 0 marked | 20 | 0 of 108 | 599s |
| `gpt-oss:20b` | reasoning-low-verify-strict | 19 | 6 of 19 | 2 of 19 | 4 of 19 | 6 of 19 (6 + 0) | 4 | 0 of 0 marked | 8 | 1 of 62 | 219s |

Found, by kind of bug:

| Model | Variant | flipped-comparison | inverted-return | missing-await | negated-condition | not-in | off-by-one | wrong-boolean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gpt-oss:20b` | pr-agent-0.50.0-reasoning-low | 5 of 6 | 0 of 2 | 1 of 1 | 1 of 4 | 1 of 1 | 0 of 2 | 2 of 3 |
| `gpt-oss:20b` | reasoning-low-verify-strict | 2 of 6 | 0 of 2 | 1 of 1 | 0 of 4 | 1 of 1 | 0 of 2 | 2 of 3 |

**19 finding(s) not marked yet**, so Found and the marked counts are incomplete.

Left out: encode/httpx#3670. Not a bug: the stream's file comes from the builtin open(), so close() is synchronous and dropping its await fixes the line rather than breaking it. The merged PR awaits that file's read() and close() (lines 199, 205, 217), which raises TypeError on httpx's v1 branch.
