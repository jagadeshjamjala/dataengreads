Review the uncommitted changes (`git diff` plus new files) as a senior data engineer would.
Check: idempotency and resumability, crawler politeness (robots.txt, rate limits, caching),
copyright (no article text reaches site/), missing tests, HTTP error handling, SQL correctness,
and anything that would break the nightly GitHub Action.
List problems by severity with file and line. Don't change code unless I ask.
