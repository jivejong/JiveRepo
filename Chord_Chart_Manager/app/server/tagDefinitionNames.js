const CATEGORY_TABLES = Object.freeze({
  Genre: 'public.genres',
  Vibe: 'public.vibes',
});

function definitionError(status, code, category, message) {
  const error = new Error(message);
  error.status = status;
  error.code = code;
  if (category) error.category = category;
  return error;
}

function normalizeDefinitionName(value, category = null) {
  if (typeof value !== 'string') {
    throw definitionError(400, 'INVALID_TAG_INPUT', category, `${category || 'Tag'} name must be text.`);
  }
  const name = value.trim().replace(/\s+/g, ' ');
  if (!name || name.length > 120) {
    throw definitionError(400, 'INVALID_TAG_INPUT', category,
      `${category || 'Tag'} name must contain 1 to 120 characters.`);
  }
  return name;
}

function definitionKey(value) {
  return normalizeDefinitionName(value).toLocaleLowerCase();
}

async function lockTagDefinitionNames(client, category, names) {
  const keys = [...new Set(names.map(name => definitionKey(name)))].sort();
  const tableKey = category;
  for (const key of keys) {
    await client.query(
      'SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))',
      [`ccm-tag-definition:${tableKey}`, key],
    );
  }
}

function categoryTable(category) {
  const table = CATEGORY_TABLES[category];
  if (!table) {
    throw definitionError(400, 'INVALID_TAG_INPUT', category,
      'Only Genre or Vibe assignments are supported.');
  }
  return table;
}

async function findTagDefinition(client, category, rawName) {
  const name = normalizeDefinitionName(rawName, category);
  const table = categoryTable(category);
  await lockTagDefinitionNames(client, category, [name]);
  const { rows } = await client.query(
    `SELECT id, name FROM ${table}
     WHERE lower(regexp_replace(btrim(name), '[[:space:]]+', ' ', 'g')) = lower($1)
     ORDER BY id`, [name],
  );
  if (rows.length === 0) {
    throw definitionError(422, 'UNKNOWN_TAG_DEFINITION', category,
      `${category} definition “${name}” is no longer available. Choose an existing definition or remove the assignment.`);
  }
  if (rows.length > 1) {
    throw definitionError(422, 'AMBIGUOUS_TAG_DEFINITION', category,
      `Multiple ${category} definitions match “${name}”. Resolve the duplicate definitions before assigning it.`);
  }
  const locked = await client.query(`SELECT id,name FROM ${table} WHERE id=$1 FOR SHARE`, [rows[0].id]);
  if (!locked.rowCount) {
    throw definitionError(422, 'UNKNOWN_TAG_DEFINITION', category,
      `${category} definition “${name}” is no longer available. Choose an existing definition or remove the assignment.`);
  }
  return { id: locked.rows[0].id, name: locked.rows[0].name };
}

async function resolveTagDefinitionSet(client, category, values) {
  const byKey = new Map();
  for (const item of values) {
    const name = normalizeDefinitionName(item.name, category);
    const key = definitionKey(name);
    if (!byKey.has(key)) byKey.set(key, { ...item, name });
  }
  await lockTagDefinitionNames(client, category, [...byKey.values()].map(item => item.name));
  const resolved = new Map();
  const table = categoryTable(category);
  const lookedUp = new Map();
  for (const [key, item] of byKey) {
    const name = normalizeDefinitionName(item.name, category);
    const { rows } = await client.query(
      `SELECT id,name FROM ${table}
       WHERE lower(regexp_replace(btrim(name), '[[:space:]]+', ' ', 'g')) = lower($1)
       ORDER BY id`, [name],
    );
    if (!rows.length) throw definitionError(422, 'UNKNOWN_TAG_DEFINITION', category,
      `${category} definition “${name}” is no longer available. Choose an existing definition or remove the assignment.`);
    if (rows.length > 1) throw definitionError(422, 'AMBIGUOUS_TAG_DEFINITION', category,
      `Multiple ${category} definitions match “${name}”. Resolve the duplicate definitions before assigning it.`);
    lookedUp.set(key, { id: rows[0].id, name: rows[0].name });
  }
  const ids = [...lookedUp.values()].map(item => item.id).sort((left, right) => left - right);
  const locked = ids.length ? await client.query(
    `SELECT id,name FROM ${table} WHERE id=ANY($1::int[]) ORDER BY id FOR SHARE`, [ids],
  ) : { rows: [] };
  if (locked.rows.length !== ids.length) {
    throw definitionError(422, 'UNKNOWN_TAG_DEFINITION', category,
      `A ${category} definition is no longer available. Choose existing definitions or remove the assignment.`);
  }
  const lockedById = new Map(locked.rows.map(row => [row.id, row]));
  for (const [key, item] of byKey) {
    const row = lockedById.get(lookedUp.get(key).id);
    resolved.set(key, { id: row.id, name: row.name, is_primary: Boolean(item.is_primary) });
  }
  return values.map(item => {
    const name = normalizeDefinitionName(item.name, category);
    return { ...resolved.get(definitionKey(name)), is_primary: Boolean(item.is_primary) };
  });
}

module.exports = {
  categoryTable,
  definitionError,
  definitionKey,
  findTagDefinition,
  lockTagDefinitionNames,
  normalizeDefinitionName,
  resolveTagDefinitionSet,
};
