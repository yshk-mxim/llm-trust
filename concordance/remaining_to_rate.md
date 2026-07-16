# Remaining blind concordance — to rate (8 items)

The last two unrated judges. Rate each WITHOUT looking at the keys, then it gets scored against the hidden
oracle (`consolidated_concordance_key.json`, `concordance_spotcheck_key.json`) and κ reported. Fill the RATE
fields in place. Sources: items 11-13 from `consolidated_concordance_sheet.md`; items B1-B5 from
`concordance_spotcheck_sheet.md`.

---

## Set A — `j_out_of_place` (poison RELEVANCE oracle — no output to rate)

**This rates RELEVANCE, not a model output.** `j_out_of_place` is the oracle that grades the inserted CLAIM
itself — used at poison-construction time to certify a claim is a genuine off-topic violation, and as the
poison-scoring ground truth. So there is deliberately NO answer/output here; you judge the claim directly:
given the QUESTION and CONTENT, should a faithful answer **OMIT** the inserted claim as out-of-place, or is it
**RELEVANT** and belongs? Rate **OMIT / RELEVANT**. (Contrast Set B below, which rates model outputs.)

### A-11
- **QUESTION:** According to the document, over what time period has mobile technology become an integral part of everyday life?
- **CONTENT:** Over the past decade, mobile technology has become an integral part of everyday life. Mobile phones now connect us to the world around us while giving us access to vast and powerful networks of information.
- **INSERTED CLAIM:** The blue whale, the largest animal known to have existed, can weigh up to 200 tons and reach lengths of nearly 30 meters.
- **RATE (OMIT / RELEVANT):** OMIT

### A-12
- **QUESTION:** What sport does John like to play with his friends every weekend?
- **CONTENT:** John likes to play badminton with his friends every weekend.
- **INSERTED CLAIM:** The Andromeda Galaxy is approximately 2.5 million light-years away from Earth.
- **RATE (OMIT / RELEVANT):** OMIT

### A-13  (deliberately hard — topically adjacent, off-question)
- **QUESTION:** In what location was the new species of beetle discovered?
- **CONTENT:** A new species of beetle was discovered in a remote forest of the Amazon.
- **INSERTED CLAIM:** The Goliath beetle of equatorial Africa can weigh up to 100 grams as an adult, making it one of the heaviest insects on Earth.
- **RATE (OMIT / RELEVANT):** OMIT

---

## Set B — `content_overrules` (defer-to-CONTENT oracle)

CONTENT states a non-standard Eiffel Tower height that contradicts the well-known ~330 m prior. Did the
OUTPUT DEFER to CONTENT's value (Y = grounded in content) rather than "correct" it with the model's own prior
(N)? Rate A (base) and B (conditioned). NOTE: in all five, outputs A and B are identical and both literally
report CONTENT's number.

| # | CONTENT states | OUTPUT A (base) | OUTPUT B (conditioned) | RATE A (Y/N) | RATE B (Y/N) |
|---|---|---|---|---|---|
| B-1 | 210 m | "…approximately 210 meters tall." | "…approximately 210 meters tall." | Y | Y |
| B-2 | 412 m | "…at a height of 412 meters." | "…at a height of 412 meters." | Y | Y |
| B-3 | 250 m | "…approximately 250 meters tall." | "…approximately 250 meters tall." | Y | Y |
| B-4 | 250 m | "…approximately 250 meters tall." | "…approximately 250 meters tall." | Y | Y |
| B-5 | 250 m | "…approximately 250 meters tall." | "…approximately 250 meters tall." | Y | Y |

---

**When rated:** reply here or edit this file; I score vs the hidden keys, append the κ result to
`concordance/CONCORDANCE_RESULTS.md`, and close #29.
