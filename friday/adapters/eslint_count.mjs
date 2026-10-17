// Lints one JavaScript source under one flat config, both read from stdin as JSON {code, config}.
// Writes {"errors": n, "messages": [...]} to stdout. Uses the Linter API, so no user, project or global
// ESLint config is ever read: the config given is the only one applied.
import { Linter } from "eslint";

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  const { code, config } = JSON.parse(input);
  const linter = new Linter({ configType: "flat" });
  const messages = linter.verify(code, [config], { filename: "inline.js" });
  const errors = messages.filter((m) => m.severity === 2);
  process.stdout.write(JSON.stringify({
    errors: errors.length,
    messages: errors.map((m) => ({ rule: m.ruleId, fatal: !!m.fatal, line: m.line, message: m.message })),
  }));
});
