# Triggered Failure Modes — Evidence (Milestone 5)

Each failure was deliberately triggered from the terminal. Every one returns a
specific, informative result instead of crashing or returning nothing.

---

## 1. `search_listings` returns zero results

```
$ python -c "from tools import search_listings; print(search_listings('designer ballgown', size='XXS', max_price=5))"
[]
```

Run through the full agent, the planning loop retries with looser constraints,
then terminates early without calling the downstream tools:

```
$ python agent.py   # (no-results path)
Error message: No listings matched 'designer ballgown', size XXS, under $5 --
even after relaxing size and price. Try broader keywords, a higher budget, or a
different category.
fit_card is None: True
suggest_outfit called? False
```

➡️ Specific + actionable: names what was searched and what to change. `suggest_outfit`
and `create_fit_card` are never called on empty input.

---

## 2. `suggest_outfit` with an empty wardrobe

```
$ python -c "
from tools import search_listings, suggest_outfit
from utils.data_loader import get_empty_wardrobe
results = search_listings('vintage graphic tee', size=None, max_price=50)
print(suggest_outfit(results[0], get_empty_wardrobe()))
"
Since you haven't shared your wardrobe with me, I'll provide some general styling
advice for your 'Graphic Tee — 2003 Tour Bootleg Style'. This tee would look great
in a casual, streetwear-inspired outfit, paired with distressed denim jeans...
[full general-styling paragraph]
```

➡️ No crash, no empty string — switches to a general-advice branch.

---

## 3. `create_fit_card` with an empty outfit string

```
$ python -c "
from tools import search_listings, create_fit_card
results = search_listings('vintage graphic tee', size=None, max_price=50)
print(create_fit_card('', results[0]))
"
Can't write a fit card without an outfit suggestion - try generating an outfit first.
```

➡️ Returns a descriptive error **string**, not a Python exception.

---

## 4. (Bonus) LLM call itself fails — bad key / network

```
$ GROQ_API_KEY=bad_key python -c "...suggest_outfit / create_fit_card..."
suggest_outfit fallback: Couldn't generate a custom styling idea right now, but
this Graphic Tee — 2003 Tour Bootleg Style (graphic tee, vintage, ...) would pair
well with neutral basics...
create_fit_card fallback: thrifted this Graphic Tee — 2003 Tour Bootleg Style for
$24 on depop and i'm obsessed - styled it up...
```

➡️ Both LLM tools catch exceptions and return a useful fallback so the pipeline
still completes.
