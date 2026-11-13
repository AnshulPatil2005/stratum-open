# What `review.quiet` may leave out, tried on the marked findings

1102 model findings in all: 215 marked wrong, 220 marked right, 25 unsure, 642 not marked.

| Pattern | Looks at | Wrong removed | In PRs | Right lost | Unsure | Not marked | Ships |
| --- | --- | --- | --- | --- | --- | --- | --- |
| asks for docs: `\b(missing\|lacks?\|lacking\|needs?\|add\|adding\|no\|without) (\w+ )?(docstrings?\|documentation\|docs)\b` | title | 0 | 0 | 0 | 0 | 0 | yes: nothing marked matches, so the benchmark is silent on it; the prompt rules these out, and Stratum's review of its own PR #55 had "Missing docstring for new parameter" |
| style, naming or readability: `\b(naming\|misleading (\w+ )?names?\|unclear\|confusing\|readability\|large constants?\|magic (numbers?\|constants?\|values?))\b` | title | 2 | 2 | 0 | 0 | 1 | yes |
| praises the change: `^(improv\w+\|optimi[sz]\w+\|efficient\|better\|enhanc\w+)\b` | title | 4 | 2 | 0 | 0 | 0 | yes |
| a guess: `^(potential(ly)?\|possibl[ey])\b` | title (not in security findings) | 35 | 19 | 0 | 0 | 118 | yes |
| mentions docs or a docstring: `\b(docstrings?\|documentation\|documented\|undocumented)\b` | either | 0 | 0 | 5 | 0 | 3 | no: loses 5 marked right |
| mentions a comment: `\bcomments?\b` | either | 1 | 1 | 4 | 0 | 5 | no: loses 4 marked right |
| mentions a name: `\bnames?\b` | title | 2 | 1 | 17 | 1 | 17 | no: loses 17 marked right |
| redundant or unnecessary: `\b(redundant\|unnecessary\|unneeded\|unused\|duplicate)\b` | title | 3 | 3 | 1 | 0 | 12 | no: loses 1 marked right |
| says could, may or might: `\b(could\|may\|might)\b` | detail | 59 | 21 | 14 | 2 | 206 | no: loses 14 marked right |
| about tests: `\btests?\b` | title | 9 | 6 | 1 | 0 | 19 | no: loses 1 marked right |
| about a deprecation: `\bdeprecat\w+` | title | 8 | 5 | 1 | 0 | 33 | no: loses 1 marked right |
| starts with Deprecated or Deprecation: `^deprecat\w+` | title | 6 | 5 | 0 | 0 | 24 | no: it can't tell a PR that deprecates something, which is all its matches are, from code that calls something deprecated |
| says consider: `\bconsider\b` | detail | 1 | 1 | 0 | 0 | 2 | no: its one marked match uses the word in another sense ("should also consider the case where") |
| starts with Consider or Suggest: `^(consider\|suggest\w*\|recommend\w*)\b` | title | 0 | 0 | 0 | 0 | 0 | no: no finding in the benchmark has such a title |
| type hints: `\btype (hints?\|hinting\|annotations?)\b` | title | 0 | 0 | 0 | 0 | 9 | no: nothing marked matches, and code that reads annotations when it runs can break on a wrong one |
| formatting: `\b(indentation\|whitespace\|formatting\|blank lines?)\b` | title | 5 | 3 | 2 | 0 | 6 | no: loses 2 marked right |
| says may, might or could: `\b(may\|might\|could)\b` | title | 3 | 3 | 0 | 0 | 6 | no: three marked findings are too few to tell, and the prompt doesn't rule the wording out |
| missing timeout: `\btimeout\b` | title | 18 | 12 | 0 | 0 | 19 | no: a missing timeout is a defect the prompt asks for; the benchmark plants logic bugs, so it can't say how often one is right |
| category is performance: `^performance$` | category | 8 | 7 | 0 | 0 | 4 | no: a kind of defect, not a kind of comment; the benchmark plants logic bugs, so it can't say how often one is right |

**The shipped patterns together** remove 41 of the 215 findings marked wrong (in 20 PRs) and 0 of the 220 marked right. They also match 0 marked unsure and 119 of the 642 nobody marked.

One title on several files, in the 643 reviews that have findings:

| One title on | Times | Findings it would fold away | Copies marked wrong | Copies marked right |
| --- | --- | --- | --- | --- |
| 2 files or more | 57 | 80 | 44 | 1 |
| 3 files or more (what ships) | 15 | 38 | 16 | 0 |
| 4 files or more | 7 | 22 | 13 | 0 |
