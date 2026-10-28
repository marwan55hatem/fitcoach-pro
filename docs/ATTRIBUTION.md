# Attribution and licenses

## Exercise library (data and demo images)

- **Dataset:** [free-exercise-db](https://github.com/yuhonas/free-exercise-db) by yuhonas
- **License:** The Unlicense (public domain dedication). The repository's `LICENSE.md` is the Unlicense, and its README describes the data and imagery as an open public-domain dataset. Commercial use, modification and redistribution are allowed without attribution; we credit it anyway.
- **Derived from:** [wrkout/exercises.json](https://github.com/wrkout/exercises.json) by Ollie Jennings, which is also released under the Unlicense.
- **Pinned version:** commit `f00c92c7dcf1216a928a52c3706c7ce8e2f71ed5` (see `DATASET_REF` in `backend/app/seed/exercises.py`), so imports are reproducible.
- **How it is used:** `python -m app.seed.exercises` downloads that commit's `exercises.json` and images, validates every field and image, normalises muscles, equipment and level to our vocabulary, and copies the images into our own private storage bucket under `catalog/exercises/`. Each exercise row records `source`, `license` and `attribution`.

### Datasets considered and not used

- **wger** ([wger.de](https://wger.de)): the application is AGPL-3.0 and its exercise data and images are published under Creative Commons licenses (CC-BY-SA family, with per-image terms). Share-alike obligations and per-image attribution make it a poor fit for a closed product, so it was not imported.

### Custom exercises

Exercises created by a user are that user's own content (`source = "user"`), are private to them, and are deleted with their account.
