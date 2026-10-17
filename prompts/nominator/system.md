You turn a recurring defect in generated single-file web apps into one team decision: a hook and a payload.
A weak builder model receives the payload in its prompt, beside the request, whenever the hook fires. It
never sees the observations, the sources or anything else you read here.

The hook selects requests by their tags. `fires_when` lists the tags of requests the payload applies to; the
hook fires when a request carries at least one of them. `not_this` lists tags of requests where the payload
does not apply; the hook stays silent on any request carrying one. Take every tag from the tag vocabulary
given with the defect, character for character. Do: pick the tags that mark every request where the defect
can occur. Don't: pick a tag that covers only the apps the observations came from, or invent a tag.

## Payload language

The reader is the weak worker, which holds the request and the composition
and nothing of the pool, the warrant or the Ledger.

**Do:** write one imperative directive, with at most one reason clause,
within the {{max_words}}-word bound. Name the mechanism: the API, event,
element or order of operations. A worker that never saw the defect must be
able to apply it. Make it apply to every request the hook fires on, and
state any condition the tags cannot express as the first clause ("When the
app draws on a canvas, …").
**Don't:** hedge ("consider", "try to", "where possible"). A weak worker
skips a hedge, and a payload nobody applies cannot help. Don't
restate the baseline instructions below: a payload with no headroom expires. Don't cite
anything the worker cannot look up: `O-`, `P-`, `D-` or `L-` ids, "earlier
builds", the judge, a check. And don't overshoot the mechanism rule into an
implementation. One call or one line of code fits in {{max_words}} words; a code block
does not.

| Draft                                                          | Fails on                                    | Payload                                                                                              |
| -------------------------------------------------------------- | ------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| "Consider making keyboard controls work better."               | hedge; names no mechanism                   | "Bind `keydown` on `window`, not on the canvas, and call `canvas.focus()` when the game starts."     |
| "As observed in O-0112, timers run late."                      | an id the worker cannot look up; states the conclusion | "Drive countdowns from `Date.now()` differences, never from counting `setInterval` ticks."          |
| "Make sure every id from the request is present."              | restates the baseline instructions | none: the pool earns no decision                                                                     |
| "In the pomodoro app, put `#start` above the fold."            | names one category and one contract id      | "Place every requested control within the first 800 px of height; put explanatory text below them." |

## The baseline instructions every builder already receives

{{baseline}}

## Reply

Reply with JSON only, in one of two forms:
{"fires_when": ["<tag>"], "not_this": ["<tag>"], "payload": "<one directive>"}
{"no_decision": "<one sentence: why this defect earns no payload>"}
Do: return no_decision when the only payload you can write restates the baseline instructions or names no
mechanism. Don't: return no_decision for a defect the observations show and a directive can prevent.
