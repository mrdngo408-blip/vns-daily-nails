# Venetian Nail Spa · Daily Nail Posts

Every day at **9 AM and 6 PM Pacific** this app:
1. Creates a luxury 9:16 nail image (long almond/stiletto nails, gold chrome "stained glass" art, crystals) that changes with the season and upcoming holidays
2. Writes an Instagram caption with exactly 5 hashtags
3. Posts it to Instagram: a 4:5 feed post with the caption, plus the full 9:16 image as a Story
4. Saves everything to the gallery page (copy the caption or save images from your phone)

## Where things are
| What | Where |
|---|---|
| Gallery | `https://<your-github-name>.github.io/vns-daily-nails/` |
| Make a post now / test | GitHub → Actions → "Nail Posts (9 AM + 6 PM)" → Run workflow |
| Secrets | GitHub → Settings → Secrets and variables → Actions |

## Secrets
| Name | What it is |
|---|---|
| `OPENAI_API_KEY` | OpenAI key (images + captions) |
| `GALLERY_PASSCODE` | Passcode for the gallery page (optional) |
| `IG_USER_ID` | Instagram Business account ID |
| `IG_ACCESS_TOKEN` | Long-lived Meta access token (System User token never expires) |

Optional variable: `POST_STORY` = `0` to stop the Story post.

## Files
- `generate.py`: seasons, holidays, design recipes, image + caption
- `publish.py`: posts to Instagram
- `.github/workflows/daily.yml`: the schedule
- `docs/`: gallery page, images and `posts.json` history (used to avoid repeat designs)

Images are AI-generated design inspiration.
