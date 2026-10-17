You read one single-file web app that a builder wrote for a request, and you answer every lens listed about it.
A lens is an angle and its counterfactual. The angle stipulates a defect: it asks where the defect is, never whether there is one. The counterfactual names the opposite reading; rule it out before you raise p.
For each lens:
- Set "p" to the probability, from 0 to 1, that the angle names a real defect in this artifact.
- When p is {flag_p} or higher, set "quote" to the span of the artifact source where the defect shows, copied character for character: same spacing, same quotes, no ellipsis, no paraphrase.
- When p is below {flag_p}, set "quote" to "".
Read the code itself. Credit a feature only where the code implements it; the how-to section is the builder's own claim.
Answer every lens listed, each once, by its id.
Reply with JSON only, in this form:
{"lenses": [{"id": "L-01", "p": 0.0, "quote": ""}]}
