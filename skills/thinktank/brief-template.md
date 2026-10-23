Answer directly from your own reasoning and from the text below. Do NOT use any tools, shell commands, code execution, web access, or files. Output only the answer.

# Task
<one paragraph: what is being reviewed or decided, for whom, what a good answer looks like. This is a correctness review, not an attack exercise.>

# Questions
1. <numbered, concrete, answerable from the material>

# Material
<diff / code / tables. For every table: how it was produced (script, query, filter, date range).>

# Output contract
Return ONLY a list of findings in this shape, most severe first, nothing else:

- title: <one line>
  evidence: <quote or number from the material above; "none" if you have none>
  confidence: high | medium | low
  falsified_by: <what observation would prove this finding wrong>
  needs_source: yes | no   (yes if you relied on a fact not present in the material: a price, a parameter meaning, an API limit, a standard, a URL)
  fix: <one or two sentences, if applicable>

A finding without evidence from the material must be marked confidence: low. If you believe something is fine, do not list it, and do not add a line saying that everything else is correct: silence is the only way to say that.
