// Frontend AST facts for the 159 inventory, read with the TypeScript compiler API from desktop/node_modules.
// Usage (worktree root): node inventory/scripts/fe_scan.mjs --repo . --out inventory/out
// Output: <out>/fe_facts.json (raw facts). inventory/scripts/merge.py turns them into counts and candidates.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const argv = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 ? argv[i + 1] : dflt;
};
const repo = path.resolve(opt('repo', '.'));
const outDir = path.resolve(repo, opt('out', 'inventory/out'));
const require = createRequire(path.join(repo, 'desktop', 'package.json'));
const ts = require('typescript');

const MATCHERS = new Set([
  'toHaveBeenCalled', 'toHaveBeenCalledTimes', 'toHaveBeenCalledWith',
  'toHaveBeenLastCalledWith', 'toHaveBeenNthCalledWith',
]);

function walk(dir, acc = []) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    if (['node_modules', 'dist', 'target'].includes(ent.name)) continue;
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) walk(p, acc);
    else if (/\.(ts|tsx)$/.test(ent.name) && !ent.name.endsWith('.d.ts')) acc.push(p);
  }
  return acc;
}

function visit(node, fn) {
  fn(node);
  ts.forEachChild(node, (child) => { visit(child, fn); });
}

const files = [
  ...walk(path.join(repo, 'desktop', 'src')),
  ...walk(path.join(repo, 'desktop', 'scripts')),
].sort();

const facts = { files: [], defense: [], exports: [], lockin: [] };

for (const file of files) {
  const rel = path.relative(repo, file).split(path.sep).join('/');
  const text = fs.readFileSync(file, 'utf8');
  const total = text.endsWith('\n') ? text.split('\n').length - 1 : text.split('\n').length;
  facts.files.push({ file: rel, lines: total });

  const kind = file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind);
  const lineOf = (node) => sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
  const endLine = (node) => sf.getLineAndCharacterOfPosition(node.getEnd()).line + 1;
  const stmtName = (st) => {
    if ((ts.isFunctionDeclaration(st) || ts.isClassDeclaration(st) || ts.isInterfaceDeclaration(st)
        || ts.isTypeAliasDeclaration(st) || ts.isEnumDeclaration(st)) && st.name) return st.name.text;
    if (ts.isVariableStatement(st)) {
      return st.declarationList.declarations.map((d) => (ts.isIdentifier(d.name) ? d.name.text : '<解构>')).join(',');
    }
    return '<顶层语句>';
  };
  // Each defense item also records the top-level statement that contains it (symbol-level candidates).
  const addDefense = (node, kindName, detail) => {
    const pos = node.getStart(sf);
    const top = sf.statements.find((s) => s.getStart(sf) <= pos && pos < s.end);
    facts.defense.push({
      file: rel, line: lineOf(node), kind: kindName, detail,
      stmt_start: top ? lineOf(top) : null, stmt_end: top ? endLine(top) : null,
      stmt_name: top ? stmtName(top) : null,
    });
  };

  visit(sf, (node) => {
    if (ts.isCatchClause(node)) {
      const empty = node.block.statements.length === 0;
      let hasThrow = false;
      visit(node.block, (n) => { if (ts.isThrowStatement(n)) hasThrow = true; });
      if (empty) addDefense(node, 'empty_catch', '空 catch');
      if (!hasThrow) addDefense(node, 'catch_no_throw', empty ? '空 catch，不重新抛' : '不重新抛');
    } else if (ts.isAsExpression(node) && node.type.kind === ts.SyntaxKind.AnyKeyword) {
      addDefense(node, 'as_any', 'as any');
    } else if (ts.isTypeAssertionExpression(node) && node.type.kind === ts.SyntaxKind.AnyKeyword) {
      addDefense(node, 'as_any', '<any>');
    } else if ((ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)
        || ts.isCallExpression(node)) && node.questionDotToken) {
      addDefense(node, 'optional_chain', '?.');
    } else if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken) {
      addDefense(node, 'nullish', '??');
    }
  });

  for (const st of sf.statements) {
    const mods = ts.canHaveModifiers(st) ? (ts.getModifiers(st) ?? []) : [];
    const exported = mods.some((m) => m.kind === ts.SyntaxKind.ExportKeyword);
    const range = { start: lineOf(st), end: endLine(st) };
    const push = (name, kindName) => facts.exports.push({ file: rel, name, kind: kindName, ...range });
    if (ts.isFunctionDeclaration(st) && exported && st.name) push(st.name.text, 'function');
    else if (ts.isClassDeclaration(st) && exported && st.name) push(st.name.text, 'class');
    else if (ts.isInterfaceDeclaration(st) && exported) push(st.name.text, 'interface');
    else if (ts.isTypeAliasDeclaration(st) && exported) push(st.name.text, 'type');
    else if (ts.isEnumDeclaration(st) && exported) push(st.name.text, 'enum');
    else if (ts.isVariableStatement(st) && exported) {
      for (const d of st.declarationList.declarations) {
        if (ts.isIdentifier(d.name)) push(d.name.text, 'variable');
      }
    } else if (ts.isExportDeclaration(st) && !st.moduleSpecifier && st.exportClause
        && ts.isNamedExports(st.exportClause)) {
      for (const el of st.exportClause.elements) push(el.name.text, 'named-export');
    } else if (ts.isExportAssignment(st)) {
      push('default', 'default-export');
    }
  }

  const isTest = /\.test\.[cm]?[jt]sx?$/.test(rel) || rel.startsWith('desktop/src/test/');
  if (isTest) {
    const row = { file: rel, lines: total, vi_mock_internal: 0, tohavebeencalled: 0 };
    visit(sf, (node) => {
      if (!ts.isCallExpression(node)) return;
      const callee = node.expression;
      if (ts.isPropertyAccessExpression(callee) && ts.isIdentifier(callee.expression)
          && callee.expression.text === 'vi' && callee.name.text === 'mock') {
        const a0 = node.arguments[0];
        if (a0 && ts.isStringLiteralLike(a0) && a0.text.startsWith('.')) row.vi_mock_internal += 1;
      }
      if (ts.isPropertyAccessExpression(callee) && MATCHERS.has(callee.name.text)) {
        row.tohavebeencalled += 1;
      }
    });
    facts.lockin.push(row);
  }
}

fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(path.join(outDir, 'fe_facts.json'), JSON.stringify(facts, null, 2), 'utf8');
console.log(`files=${facts.files.length} defense=${facts.defense.length} exports=${facts.exports.length} tests=${facts.lockin.length}`);
