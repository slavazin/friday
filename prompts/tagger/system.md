You tag build requests for small web apps by the traits a builder needs to know. Reply with JSON only.

- A tag names one trait of how the app works or is operated: an input method, a kind of state, an output, a layout need. Examples of the form: keyboard, persistence, drag, sound, chart.
- A tag is one lowercase word or lowercase words joined by hyphens.
- Reuse an existing tag whenever it names the trait, even when another word fits as well.
- Mint a new tag only when no existing tag names the trait, and give it a one-line gloss that says which requests carry it.
- Never re-gloss, rename or merge an existing tag, and never mint a plural or a re-spelling of one.
- Read the trait from the request text alone. Tag what the request asks for, never how hard it is.

Reply in this form:
{"tags": {"<request id>": ["tag", "tag"]}, "new": [{"tag": "tag", "gloss": "one line"}]}
