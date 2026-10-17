// Lints a page's inline JavaScript under one flat config. Reads JSON {classic, modules, config} from stdin:
// `classic` is the classic-script pieces in document order (inline scripts, then wrapped handler bodies),
// `modules` the module-script bodies. Writes {"errors": n, "messages": [...]} to stdout.
//
// The browser compiles each piece on its own, so a piece that fails to compile breaks only itself: here it
// counts as one error and is left out, and the rest is still linted. Accepted: a broken piece's own lint errors
// are not counted, so breaking a lint-heavy script can lower Q; Q is a guard, and P and C catch the dead page. Classic pieces share one global scope
// and are linted as one source; each module is linted on its own with sourceType "module", seeing the
// classic pieces' top-level names as globals (a classic name a module reads counts as used). Browser
// globals come from the `globals` package (pinned in package.json) under the config's own list. Uses the
// Linter API, so no user, project or global ESLint config is ever read.
import { Linter } from "eslint";
import globals from "globals";

const FILENAME = "inline.js";

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  const { classic, modules, config } = JSON.parse(input);
  const linter = new Linter({ configType: "flat" });
  const base = {
    ...config,
    languageOptions: {
      ...config.languageOptions,
      globals: { ...globals.browser, ...(config.languageOptions?.globals ?? {}) },
    },
  };
  const withSource = (cfg, sourceType, extra) => ({
    ...cfg,
    languageOptions: { ...cfg.languageOptions, sourceType, globals: { ...cfg.languageOptions.globals, ...extra } },
  });
  const fatalIn = (code, cfg) =>
    linter.verify(code, [{ ...cfg, rules: {} }], { filename: FILENAME }).find((m) => m.fatal);

  const errors = [];
  const classicCfg = withSource(base, "script", {});
  const kept = [];
  classic.forEach((piece, index) => {
    const fatal = fatalIn([...kept, piece].join("\n;\n"), classicCfg);
    if (fatal) {
      errors.push({ rule: null, fatal: true, piece: `classic:${index}`, line: fatal.line, message: fatal.message });
    } else {
      kept.push(piece);
    }
  });
  const report = (piece) => (m) => {
    if (m.severity === 2) errors.push({ rule: m.ruleId, fatal: !!m.fatal, piece, line: m.line, message: m.message });
  };
  const source = kept.join("\n;\n");
  linter.verify(source, [{ ...classicCfg, rules: {} }], { filename: FILENAME });
  const declared = {};
  for (const v of linter.getSourceCode().scopeManager.globalScope.variables) {
    if (v.defs.length > 0) declared[v.name] = "writable";
  }
  // Classic names a module reads count as used: the module sees the classic global scope.
  const usedByModules = new Set();
  modules.forEach((code, index) => {
    linter.verify(code, [withSource(base, "module", declared)], { filename: FILENAME }).forEach(report(`module:${index}`));
    for (const v of linter.getSourceCode()?.scopeManager.globalScope.variables ?? []) {
      if (v.name in declared && v.references.length > 0) usedByModules.add(v.name);
    }
  });
  const uses = usedByModules.size ? `\n;\n(function () { void [${[...usedByModules].join(", ")}]; });` : "";
  linter.verify(source + uses, [classicCfg], { filename: FILENAME }).forEach(report("classic"));
  process.stdout.write(JSON.stringify({ errors: errors.length, messages: errors }));
});
