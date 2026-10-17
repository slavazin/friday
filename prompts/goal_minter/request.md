Write one new request for this kind of app: {family}.

Give it {lo} to {hi} checks. Each wait is at most {max_wait_ms} ms. A timed request ends its text with: "With {test_query} in the address, every duration is at most 2 seconds." Its checks reload with {{"goto": "{test_query}"}} first.
{phone_block}{retry_block}
Requests already written for this kind of app (write a different app, not a variant of one of these):
{prior}

Reply in this form:
{{"text": "...", "timed": false, "contract": {{"#id": "kind and role"}}, "checks": [{{"steps": [], "expect": {{}}}}]}}
