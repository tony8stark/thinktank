Answer directly from your own reasoning and from the text below. Do NOT use any tools, shell commands, code execution, web access, or files. Output only the answer.

# Task
A text is about to be fact-checked claim by claim. Another analyst is extracting the claims independently; your list will be merged with theirs, so recall matters more than neatness. List every checkable claim in the text.

Include:
- explicit factual claims: numbers, prices, dates, deadlines, durations, thresholds, penalties, fees, distances, names, rules, eligibility, requirements, history;
- implicit claims a reader would act on ("you can pay at the entrance" implies no advance booking is required);
- claims that are only true with a condition the text does not state (an exception, a threshold, an effective date, a jurisdiction, a category of person). Extract the claim as written and put the suspected missing condition in "omission".

Split compound sentences into atomic claims (one checkable proposition each). Skip opinions and marketing language that no source could confirm or refute.

# Context
{{CONTEXT}}

# Text (paragraph numbers in brackets)
{{SOURCE}}

# Output
Return ONLY one JSON object inside a ```json fence:

```json
{"claims": [
  {"para": "¶n", "span": "exact substring of the text, copied verbatim", "claim": "atomic proposition",
   "kind": "number | date | deadline | price | rule | eligibility | penalty | procedure | history | other",
   "stakes": "money | standard | low", "omission": "suspected missing condition, or empty"}
]}
```
stakes = money when a wrong claim can cost a reader money, a legal right, a filing, a fine, or entry at a border; low for trivia; standard otherwise.
