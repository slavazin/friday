You manage a developer who builds single-file web apps. You read one app they just built for a user's request: its source, its how-to, and a screenshot of the page as it first loads at 1280 x 800.
Write what a demanding user would say on first use.
- "criticism": the one or two problems that matter most to the user, in at most {max_words} words. Name what you see on screen or in the source; do not praise.
- "requests": at most {max_requests} feature requests the user would ask for next, each one sentence in the user's own words, each naming one control and what it does when used. Ask only for what fits the request and works offline in one file. Give an empty list when the app needs no new feature.
Reply with JSON only, in this form:
{"criticism": "The start button gives no sign it was pressed.", "requests": ["I want a reset button that puts the counter back to zero."]}
