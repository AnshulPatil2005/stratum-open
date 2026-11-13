"""What a checkout of the default branch and its history say about a PR's
files: who imports them, who knows them, how often they change, which
module rules they cross, and whether the names they import are there. No
model is involved.

`repo_dir` is always a trusted checkout of the default branch, never the PR's
code. It may be a shallow clone, so short or missing history means fewer
notes, never an error.
"""
