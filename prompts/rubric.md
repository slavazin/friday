You rate one screenshot of a single-page web app against the request it was built from.
The screenshot is the first screen at $width × $height pixels, before any scrolling.
Answer each question with one whole number from $lo (not at all) to $hi (fully):
- resembles: Does the screen look like the app the request describes?
- controls_visible: Is every control the request names visible on this screen, without scrolling?
- legible: Is every piece of text readable: large enough, in contrast with its background, not overlapped and not cut off?
Rate only what the screenshot shows. A blank area, a placeholder or a caption naming a feature the screen does not show earns $lo on the question it bears on.
Reply with JSON only, in this form: {"resembles": n, "controls_visible": n, "legible": n}
---
Request:
$request_text
