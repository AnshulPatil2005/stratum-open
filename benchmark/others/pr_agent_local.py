"""PR-Agent's command line with the pull request's own title and description.

Run by PR-Agent's Python, not stratum-open's: pr_agent.py starts it in the
repository it built for a case.

PR-Agent's `local` git provider reviews the checked-out branch of a local
repository against another branch, with no hosted pull request. It has no
title or description to read, so it uses the branch name and the commit
messages. The benchmark's runner writes the real PR's title and description
as the head commit's message, and this provider hands them over as such, so
PR-Agent is told what stratum-open is told. Nothing else of PR-Agent changes:
`register_git_provider` is its documented way to add a provider from outside.
"""

import sys
from pathlib import Path

# pr_agent.py, the runner, sits in this folder and a script's folder goes first on the path:
# without this, `import pr_agent` would find the runner, not the package.
sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != Path(__file__).resolve().parent]

from pr_agent import cli  # noqa: E402
from pr_agent.git_providers import register_git_provider  # noqa: E402
from pr_agent.git_providers.local_git_provider import LocalGitProvider  # noqa: E402

PROVIDER = "local-pr"


class LocalPullRequest(LocalGitProvider):
    def _message(self) -> tuple[str, str]:
        title, _, body = self.repo.head.commit.message.partition("\n\n")
        return title.strip(), body

    def get_pr_title(self) -> str:
        return self._message()[0]

    def get_pr_description_full(self) -> str:
        return self._message()[1]


register_git_provider(PROVIDER, LocalPullRequest)

if __name__ == "__main__":
    raise SystemExit(cli.run())
