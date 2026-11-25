# Package «Customisation» (B-10)

Goal: your own mat and sleeves, so the deck looks like yours on the table. Started 2026-11-25 (owner:
«die storage containers sind oben! du kannst weitermachen!»).

## Storage

The images live on a Shared Storage node in the Jelastic environment, mounted into the web node at
`/data/custom-images` (`CUSTOM_IMAGES_DIR`). Added 2026-11-25 with the add-on `infra/custom-images.jps`
(`type: update`, imported by the owner); `infra/jelastic.jps` has the node and the mount for a fresh
install. Locally the folder is `.data/custom-images` (gitignored, inside the mounted source tree).

Lockdown (ARCHITECTURE-SECURITY §8):

- the server names every file: `<look uuid hex>/<uuid4 hex>.webp`; nothing from the user's file name
- every path is matched against that pattern and resolved against the base folder before use
- no URL under `static/` or a media URL, no listing; one view serves a file to its owner only, with a
  fixed `Content-Type: image/webp`, `nosniff` and `Cache-Control: private`
- the file goes when its row goes (after the commit), and with the account

## Limits (owner, 2026-11-25: my recommendations stand, «weitermachen»)

| What | Limit |
|---|---|
| Upload | 5 MB, 25 megapixels, PNG, JPEG or WebP (read from the content) |
| Stored mat | WebP, at most 1920 px on the long side |
| Stored sleeve | WebP, 744 × 1039 (the card's 63 × 88 mm) |
| Looks (mat + sleeve + options) | free 1 per account; paid 10, at most one per deck |
| Backup of the share | none for now: images can be uploaded again, a missing file falls back to the default |
| Share size | the smallest step |

## PRs

| PR | Content | State |
|---|---|---|
| 1 | Storage, model and upload pipeline: app `looks`, model `Look` (images, inner sleeve and seam with their colours), Pillow (new dependency): type from the content, re-encoded to WebP, EXIF gone, byte and pixel limits; the owner-only view; delete with the row and the account; in the data export | done |
| 2 | The «Customisation» page: button with a new icon on the deck page, upload or pick a template (CSS only), colour suggestions, picker and hex/RGBA input, preview (sleeve in front, mat behind) | done |
| 3 | Everywhere: free play, deck in hand, deck page, for the owner only (shared reports show the default); quotas per tier; one look per deck | open |

Not in: showing a look to others (needs moderation and a report path); custom card fronts; sponsored
templates (B-20).

Per PR: ruff, djlint, full suite, browser check, translations, push to dev.

## PR 1, findings

- New app `looks`: `Look` (owner, name, `is_default` with one per account as a constraint, `mat_image`
  and `sleeve_image` as paths on the share, inner sleeve and seam with colours). Colours are stored
  only as `#rrggbbaa` (`looks/colours.py`): hex or rgb()/rgba() in, anything else refused, because the
  value ends up in a CSS custom property.
- `looks/images.py`: Pillow 13.0.0 opens only PNG, JPEG and WebP (`formats=`), 5 MB and 25 MP before
  decoding (a decompression bomb is refused, also as a warning), EXIF rotation applied, then drawn on
  a fresh image (EXIF, ICC and XMP gone) and saved as WebP quality 82. Sleeve 744 × 1039, mat up to
  1920 px. Written as `.part`, mode 0640, then renamed.
- Files: `<look hex>/<uuid4 hex>.webp` (the look's folder, not the owner's: the user model has no
  UUID, and the folder goes with the look). The old file goes after the commit; a failed save removes
  the new one.
- `looks:image` (`/looks/<uuid>/<mat|sleeve>.webp`): owner only, everyone else 404, signed out to the
  sign-in page; `image/webp`, `nosniff`, `private, max-age=86400`.
- Local stack: named volume `custom-images` at `/data/custom-images`, like the production mount; the
  image creates that folder for `appuser` (0750). The source tree was not writable for the container
  (user namespace), and the share should not sit in the source tree anyway.
- Browser (`land_light`): 12 checks PASS, among them a JPEG with EXIF in, a 744 × 1039 WebP out,
  three traversal URLs 404, signed out to sign-in, the file 0640 in the share; deleting the test
  account emptied the folder.

## PR 2, findings

- `/decks/<uuid>/look/` (`LookView`, owner of the deck only, 30 POST/h): mat and sleeve each a
  template (`looks/presets.py`: 5 mats, 6 sleeves, pure CSS) or «Your own image»; seam on the mat and
  inner sleeve on the sleeve, each with a colour (text field for hex/rgba, picker and seven
  suggestions with JavaScript). Choosing a file selects «own»; «own» without an image asks for one.
- The look is four CSS variables (`looks/css.py`): `--mat`, `--sleeve`, `--sleeve-inner` (inset rim),
  `--mat-seam` (dashed outline). Only our values go in: preset CSS, our image URL, `#rrggbbaa`; a
  stored value that does not match falls back to `transparent`.
- Preview: the mat tilted behind, a sleeve in front; `static/js/look-preview.js` updates it while
  you choose. A file not yet saved is not previewed: `img-src` does not allow `blob:`, and loosening
  the CSP for a preview is not worth it.
- Saving is one transaction: both uploads are processed first; a refused image shows at its field
  and nothing is stored. «Back to the standard» deletes the look and its files.
- Privacy policy: a new row in the retention table (images, purpose, until changed/reset/account
  deleted). `CONSENT_VERSION` not bumped: a new optional feature that only the user fills, no new
  recipient (my call, owner can overrule). Methodology: `#customisation`.
- New symbol `sleeve` (card above its sleeve, the card lifts), in the styleguide.
- Browser (`land_light`, desktop + phone + without JS): 19 checks PASS, among them a JPEG mat and a
  PNG sleeve uploaded through the form and served back as WebP, an SVG-as-PNG refused at its field,
  `red; background:url(x)` refused, no sideways scroll at 390 px, reset back to the standard.
