# Security

Stratum Open reads untrusted pull requests, so its main promise is that no
code from a PR ever runs. The Action and the App read a PR only through
GitHub's API, take their rules from the default branch, and trust hidden
state only in comments Stratum wrote itself.

If you find a way around any of that, or any other security problem, please
report it privately through
[GitHub's private vulnerability reporting](https://github.com/AnshulPatil2005/stratum-open/security/advisories/new)
rather than in a public issue. You'll get a reply within a week.
