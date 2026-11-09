# The weekly news routine (PR 22)

A Claude Code routine on the owner's account runs this every Monday. Its own
prompt only says: *"Follow news/ROUTINE.md on the branch dev of this
repository."* So every change to how the news is written comes as a reviewed
change to this file.

The routine writes **one file** and opens **one pull request into dev**. The
owner reads it, may edit it, and merges it; the week goes live with the next
deploy. Nothing is published without that review.

## Never

- Never change any file except `news/issues/<week>.json`.
- Never push to `dev` or `main`, never merge, never approve.
- Never fetch anything from wizards.com, scryfall.com, reddit.com or any host
  that is not a source in `news/rules.py` (`SOURCES`), apart from GitHub.
- Never copy sentences. Never take pictures. Never add anything the text you
  read does not say.

## Steps

1. **Branch.** `git fetch origin dev && git switch -c claude/news-<week> origin/dev`.
   `<week>` is this ISO week, written `2026-W46`. `published` is today's date.

2. **Read the feeds.** For each source in `news/rules.py`, fetch its `feed`
   **once**, with the user agent `Claude-User (goldfishlab.app weekly news)`.
   If a feed fails, skip it and say so in the pull request.

3. **Choose.** Entries from the last seven days that matter to Commander
   players:
   - the format itself: the banned list, brackets, rules
   - new products and cards for Commander, release dates
   - big events and community news
   Leave out:
   - rumours, leaks and unconfirmed spoilers
   - opinion pieces, deck techs, rankings, guides and lists
   - price speculation and sponsored posts
   - anything already in `news/issues/` (`news.check` refuses a link twice)
   Take at most 8. If nothing is worth it, stop: no file, no pull request.
   Two sources on the same story make one item with both links.

4. **Keep the text you write from.** For a source with `articles=True`, open
   the article once. For every other source, use only what its feed supplies
   for the entry: the title, the description, and the content if the feed
   carries it. Save that text, per link, as
   `$TMPDIR/news-texts/<slug>-<n>.txt` (n from 1), **outside the repository**.

5. **Write `news/issues/<week>.json`.**

   ```json
   {
     "week": "2026-W46",
     "published": "2026-11-09",
     "items": [
       {
         "slug": "sol-ring-banned",
         "headline": {"en": "...", "de": "...", "fr": "...", "it": "...",
                      "es": "...", "pt-br": "...", "ja": "..."},
         "summary": {"en": "...", "de": "...", "fr": "...", "it": "...",
                     "es": "...", "pt-br": "...", "ja": "..."},
         "facts": ["Sol Ring", "November 10"],
         "links": [{"source": "wargamer", "url": "https://...", "title": "The original title"}]
       }
     ]
   }
   ```

   - **The headline:** your own words, up to 90 characters.
   - **The summary:** two or three plain sentences, 60 to 480 characters
     (Japanese 25 to 240). Write
     only what the text says, in your own words and sentence order, and keep
     the tone neutral.
     - Name the date a change takes effect.
     - Say "announced" for an announcement, not "is".
   - **The facts:** 2 to 5 key terms the reader would search for: card names,
     product names, dates, numbers.
     - Each one, word for word, has to be in the text you read **and** in the
       English headline or summary.
     - Card and product names stay in English in every language.
   - **Translations:** natural German, French, Italian, Spanish, Brazilian
     Portuguese and Japanese, the same content as the English.
   - **No markup and no addresses in the text.** The original title goes only
     into `links[].title`.

6. **Check.** Both commands must print `OK`. Fix the file until they do.

   ```sh
   python -m news.check
   python -m news.check --week <week> --sources "$TMPDIR/news-texts"
   ```

7. **Pull request.** Commit only the new file (`News <week>`), push the
   branch, and open a pull request **into dev** titled `News <week>`. The body
   lists, for every item:
   - each fact, with a short quote of at most 20 words from the text it stands
     in;
   - the link.

   This is what the owner checks against. Then list the feeds that failed, if
   any.
