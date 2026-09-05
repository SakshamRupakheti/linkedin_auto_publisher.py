# World & Human news publisher

A replacement for the original LinkedIn script, configured for company Page **143606150**.
It collects dated RSS headlines, creates source-linked news digests, publishes through
LinkedIn's company-Page API, and exports posting history and organic engagement statistics.

## What is included

- 12 RSS feeds from BBC News, The Guardian, NASA and UN News.
- Broad English-language coverage: world affairs, business, technology, science, health,
  sport, environment and culture. This is curated source coverage, not every event worldwide.
- Two scheduled checks daily, at **08:17 and 20:17 UTC**, with a maximum of two posts per UTC day
  and a minimum six-hour interval. GitHub can delay scheduled runs; timing is not guaranteed.
- Up to three attributed headlines from distinct publishers and categories per digest.
  At least two are required; unavailable or stale feeds can cause the run to skip publishing.
- Stories must have a publication timestamp within the past 30 hours.
- URL and similar-title duplicate filtering, category rotation, and durable publishing history.
- Markdown report and CSV exports in every GitHub Actions run's downloadable artifact.
- No OpenAI key, external Python packages, or always-on local computer required.

The first version deliberately produces **headline digests**, not AI-written analysis.
Headlines are quoted and attributed to their publishers. It does not independently fact-check
the reporting, reconcile conflicting accounts, or invent statistics. Similarity filtering can
miss differently worded stories about the same event. Each headline links to the source.

## Status and prerequisites

The rebuild is installed in GitHub. The cloud preview and 12 tests passed on September 5, 2026.
**Live publishing is disabled while the saved LinkedIn token is being validated.**

You need:

1. Permission to update this repository's default branch and Actions configuration.
2. A LinkedIn developer app with the necessary approved API access.
3. A member access token with **w_organization_social**, issued to a member with a suitable
   role on company Page 143606150. A token supporting only personal-profile posting is insufficient.
4. For organic Page aggregate analytics, **rw_organization_admin** and the Page administrator role.
   Missing analytics permissions do not stop an otherwise valid post.

The human-readable LinkedIn Page slug was not independently verified. The numeric ID above
is the one supplied by the Page owner. Confirm it before enabling publishing.

## Install into the existing repository

1. Replace `linkedin_auto_publisher.py` and `.github/workflows/daily_post.yml` with these versions.
   Add `config.json`, `state.json`, `.gitignore`, `tests/test_publisher.py`, and this README.
   Preserve the `.github/workflows/` folder structure. The old workflow should be replaced,
   not left running alongside this workflow.
2. In GitHub **Settings → Actions → General → Workflow permissions**, allow repository contents
   writes if your repository policy permits this. The workflow must be able to commit `state.json`
   to the default branch. If branch protection prohibits this, use a permitted state-storage design
   before going live; do not bypass protection blindly.
3. Under **Settings → Secrets and variables → Actions → Secrets**, set `LINKEDIN_ACCESS_TOKEN` (the existing secret name `LINKEDIN` is also accepted).
   Enter the token directly in GitHub, never in the code, a commit, an issue, or a chat.
   Existing `OPENAI_API_KEY` and `LINKEDIN_PERSON_URN` secrets are unused by this replacement.
4. Leave the repository variable `PUBLISHING_ENABLED` unset initially.
5. Open **Actions → World and Human News Publisher → Run workflow → preview**.
   Inspect the generated `draft.txt`, `feed-health.json`, and report artifact.
6. Once the target Page and access are correct, create the repository Actions variable
   `PUBLISHING_ENABLED` with the exact value `true`. Run the workflow in **publish** mode.
   Confirm that a post appears on the correct Page and the workflow records its outcome.
   Scheduled runs will then publish automatically. Manual publish also respects daily limits.
7. To pause posting, set `PUBLISHING_ENABLED` to `false`. Scheduled runs will create previews.
   Disable the workflow itself to stop all checks.

The workflow runs the default branch's code even when manually dispatched from another branch.
Keep only one production workflow/runner using this Page and history.

## Reports

Open a workflow run to see its Markdown job summary. Download the artifact for:

| File | Contents |
|---|---|
| draft.txt / draft.json | Proposed digest and its source metadata, when a new draft was created |
| feed-health.json | Fresh story counts and availability by source/category |
| report.md | Publishing counts, topic coverage, unresolved attempts, and available metrics |
| posts.csv | One row per story and publishing attempt, with outcome and LinkedIn post ID |
| analytics.json / metrics.csv | Organic organization aggregate impressions, clicks, likes, comments, shares and engagement, when authorized |
| state.json | Recovery copy of publishing history |

Metrics are the API's organization organic aggregate with its rolling 12-month availability,
not a daily delta. Unavailable metrics are labeled unavailable, never shown as zero.
Follower growth, per-post rankings and a hosted dashboard are not implemented in this version.
GitHub artifacts are retained for 30 days; download them to keep a longer reporting archive.
Because the supplied repository is public, committed history, drafts in artifacts, and reports
may be accessible to other users. Use a private repository if the reports must be private.

## Local preview and tests

Requires Python 3.11 or later. No pip installation is needed.

```sh
python -m unittest discover -s tests -v
python linkedin_auto_publisher.py preview
```

The preview uses real RSS feeds and never changes publishing history or calls the posting API.
Do not manually run `publish` as an unattended service without durable state persistence and
exclusive execution equivalent to the included workflow.

## Recovery and maintenance

Before sending a post, the workflow commits a reservation to `state.json`. After sending, it
commits the outcome. If the runner crashes or LinkedIn times out after accepting the request,
the reservation blocks future posts rather than blindly resending it. This sacrifices availability
when necessary to reduce duplicates; it is not an exactly-once delivery guarantee.

For an unresolved attempt, inspect the LinkedIn Page and the failed run's artifacts first.
Then use a freshly updated checkout with the latest `state.json`:

```sh
python linkedin_auto_publisher.py resolve --attempt ATTEMPT_ID --outcome published --post-id urn:li:share:POST_ID
# Or, only after confirming the post is absent:
python linkedin_auto_publisher.py resolve --attempt ATTEMPT_ID --outcome not_published
```

Commit and push the corrected state before resuming. Never clear history simply to unstick a run.
If the outcome commit failed, use the artifact and Page to reconcile the persisted reservation.
If a reservation commit fails, publishing is not attempted.

HTTP 401 generally needs a renewed token. HTTP 403 needs the correct API access and Page role.
HTTP 426 needs an API-version update. HTTP 429 waits for a later run. Tokens and version support
need ongoing maintenance; there is no automatic OAuth renewal in this version.

The API version is `202608`, configurable in `config.json` or with the `LINKEDIN_VERSION`
repository variable. GitHub can disable scheduled public-repository workflows after inactivity;
check Actions when scheduled runs stop.

## Original diagnosis

The last five publicly visible runs failed in `Execute script`. Public step metadata did not
reveal the Python exception, so the precise original runtime failure is not confirmed.
Independent defects in the original code were:

- Used `urn:li:person:...` instead of your company organization URN.
- Used the retired `202401` LinkedIn API version.
- Generated generic weekly topics without fetching any news.
- Printed LinkedIn posting failures without exiting with an error.
- Had no history, duplicate prevention, reports, HTTP timeout or ambiguous-outcome recovery.

## API and scheduler references

- [LinkedIn Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api)
- [LinkedIn organization share statistics](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/organizations/share-statistics)
- [LinkedIn version support](https://learn.microsoft.com/en-us/linkedin/marketing/versioning)
- [GitHub scheduled workflow behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [GitHub workflow enable/disable](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)
