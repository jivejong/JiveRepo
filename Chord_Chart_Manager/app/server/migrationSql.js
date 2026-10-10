function sqlWords(sql, inspectRoutineBodies = false) {
  const words = [];
  const routineBodies = [];
  let statementStart = 0;
  let i = 0;
  while (i < sql.length) {
    const ch = sql[i];
    if (/\s/.test(ch) || ch === "," || ch === "(" || ch === ")") { i += 1; continue; }
    if (ch === ";") { words.push(";"); statementStart = words.length; i += 1; continue; }
    if (ch === "-" && sql[i + 1] === "-") {
      i += 2;
      while (i < sql.length && sql[i] !== "\n") i += 1;
      continue;
    }
    if (ch === "/" && sql[i + 1] === "*") {
      i += 2;
      let depth = 1;
      while (i < sql.length && depth) {
        if (sql[i] === "/" && sql[i + 1] === "*") { depth += 1; i += 2; }
        else if (sql[i] === "*" && sql[i + 1] === "/") { depth -= 1; i += 2; }
        else i += 1;
      }
      if (depth) throw new Error("Unterminated SQL block comment in migration.");
      continue;
    }
    let escapeString = false;
    if ((ch === "E" || ch === "e") && sql[i + 1] === "'") { escapeString = true; i += 1; }
    if (sql[i] === "'") {
      i += 1;
      let closed = false;
      while (i < sql.length) {
        if (escapeString && sql[i] === "\\") { i += Math.min(2, sql.length - i); continue; }
        if (sql[i] === "'" && sql[i + 1] === "'") { i += 2; continue; }
        if (sql[i] === "'") { i += 1; closed = true; break; }
        i += 1;
      }
      if (!closed) throw new Error("Unterminated SQL string in migration.");
      continue;
    }
    if (ch === '"') {
      i += 1;
      let closed = false;
      while (i < sql.length) {
        if (sql[i] === '"' && sql[i + 1] === '"') { i += 2; continue; }
        if (sql[i] === '"') { i += 1; closed = true; break; }
        i += 1;
      }
      if (!closed) throw new Error("Unterminated quoted identifier in migration.");
      continue;
    }
    if (ch === "$") {
      const delimiter = sql.slice(i).match(/^\$[A-Za-z_][A-Za-z0-9_]*\$|^\$\$/)?.[0];
      if (delimiter) {
        const end = sql.indexOf(delimiter, i + delimiter.length);
        if (end < 0) throw new Error("Unterminated dollar-quoted body in migration.");
        const statement = words.slice(statementStart);
        const isRoutine = statement[0] === "DO" ||
          (statement[0] === "CREATE" && (statement.includes("FUNCTION") || statement.includes("PROCEDURE")));
        if (inspectRoutineBodies && isRoutine) {
          routineBodies.push(sql.slice(i + delimiter.length, end));
        }
        i = end + delimiter.length;
        continue;
      }
    }
    const word = sql.slice(i).match(/^[A-Za-z_][A-Za-z0-9_$]*/)?.[0];
    if (word) { words.push(word.toUpperCase()); i += word.length; continue; }
    i += 1;
  }
  return { words, routineBodies };
}

function assertNoTransactionCommands(words, filename, routineBody = false) {
  let statementStart = true;
  for (let i = 0; i < words.length; i += 1) {
    const word = words[i];
    if (word === ";") { statementStart = true; continue; }
    if (routineBody && ["BEGIN", "THEN", "ELSE", "LOOP"].includes(word)) {
      statementStart = true;
      continue;
    }
    if (!statementStart) continue;
    statementStart = false;
    const next = words[i + 1];
    if (word === "COMMIT" || word === "ROLLBACK" || word === "ABORT" || word === "SAVEPOINT" ||
        word === "RELEASE" || (word === "BEGIN" && !routineBody) || (word === "END" && !routineBody)) {
      throw new Error(`${filename} contains transaction control (${word}); the migration runner owns transaction boundaries.`);
    }
    if (word === "START" && next === "TRANSACTION") {
      throw new Error(`${filename} contains transaction control (START TRANSACTION); the migration runner owns transaction boundaries.`);
    }
    if (word === "PREPARE" && next === "TRANSACTION") {
      throw new Error(`${filename} contains transaction control (PREPARE TRANSACTION); the migration runner owns transaction boundaries.`);
    }
    if (word === "EXECUTE" || word === "CALL") {
      throw new Error(`${filename} uses dynamic SQL or a procedure call; migration files must keep transaction control with the runner.`);
    }
  }
}

function validateMigrationSql(sql, filename = "migration") {
  const { words, routineBodies } = sqlWords(sql, true);
  assertNoTransactionCommands(words, filename);
  for (const body of routineBodies) {
    assertNoTransactionCommands(sqlWords(body).words, filename, true);
  }
}

module.exports = { validateMigrationSql };
