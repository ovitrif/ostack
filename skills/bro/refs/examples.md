# Examples

Before and after pairs for `SKILL.md`. All names and links are invented.

## Status reply

Before (118 words):

```markdown
Great question! I've spent some time digging into the flaky checkout test. After looking at several possible
causes, including network timeouts and a race in the cart service, it turns out the real culprit is that the
test fixture reuses the same user across parallel runs, which means two runs can empty each other's carts. I've
gone ahead and updated the fixture to create a fresh user per run, and the test has now passed 50 times in a
row locally. It's worth noting that CI still needs to confirm this. Let me know if you'd like me to open a PR!
```

After (41 words):

```markdown
Checkout test flakes because parallel runs share one test user and empty each other's carts.

- Fixed: `make_user()` now creates a fresh user per run.
- Passed 50 runs in a row locally.
- CI not run yet.

Open a PR?
```

Dropped: the greeting, the search history (timeouts, the cart race), "It's worth noting", the closer. Every fact stays.

## Answers to quoted points

Before:

```markdown
Answers in your order. The deploy and the backfill are still running.

> why does the webhook form ask for a URL?

The URL prompt appears because push mode isn't on yet. With push mode on, the form doesn't ask for a URL. You can turn it on at https://example.com/settings/push.

> keep polling as a fallback

Recorded. Once push is live, the 5-minute poll stays on as the backup.
```

After:

```markdown
Still running:
- deploy
- backfill

> why does the form ask for a URL?

Push mode is off. Turn it on at https://example.com/settings/push and the URL prompt goes away.

> keep polling as a fallback

Once push is live, the 5-minute poll stays on as backup.
```

## List run

Before:

```markdown
- Blocked on: acme/api#212, acme/api#215 and the web twin acme/web#88, plus the design review (Figma: Cart, Checkout, Receipt).
```

After:

```markdown
- Blocked on the API changes:
  - acme/api#212
  - acme/api#215
- Blocked on the web twin:
  - acme/web#88
- Design review, Figma:
  - [Cart](https://example.com/cart)
  - [Checkout](https://example.com/checkout)
  - [Receipt](https://example.com/receipt)
```

## Hype

Before:

```markdown
We're thrilled to announce that export is here, and it's a real game changer! It's not just faster: it's a
whole new way of working. Export now leverages streaming, seamlessly handling a 2 GB workspace in 40 seconds
instead of 6 minutes. Exports over 5 GB still time out for now.
```

After:

```markdown
Export now streams: a 2 GB workspace exports in 40 seconds instead of 6 minutes.

Exports over 5 GB still time out.
```

## Stock phrases

| Write | Not |
|---|---|
| the tests pass | we're in good shape |
| a setting | a knob to turn |
| decided | made a decision |
| can | has the ability to |
| The cache is the bottleneck. | The question isn't the database. It's the cache. |
| Not verified. | It seems like this should probably work. |
