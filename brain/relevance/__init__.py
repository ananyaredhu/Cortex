"""Retrieval relevance at scale (backlog T10): does the right document reach the candidates among thousands of others?

Praew's scale work showed that each persona's filter returns exactly the documents the spec allows, and that queries are fast. That is
access correctness and speed. This package measures the other thing: for a question with known right documents, is one of them
among the candidates, and how high does it rank, as the corpus grows from Company A's 18 documents to 12,000 pages?

`python -m brain.relevance.run` builds Company A plus a prefix of the generated company (simulators/scale.py) in a throwaway database
`brain_relevance...`, runs the 43 labeled questions of `questions.json` for the right persona through the real retrieval code, and
reports hit and recall at 1, 3 and 8 and mean reciprocal rank at each size. The generated text is filler from a 100-word vocabulary,
which makes every question's words common across the distractors: a hard case for keyword search, harsher than real prose.
No real embeddings exist at scale (an overnight job), so the vector leg is off: this measures the keyword leg alone.
"""
