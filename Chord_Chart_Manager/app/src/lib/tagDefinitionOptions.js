export function normalizeDefinitionName(value) {
  return String(value ?? '').trim().replace(/\s+/g, ' ');
}

export function definitionKey(value) {
  return normalizeDefinitionName(value).toLocaleLowerCase();
}

export function matchingTagDefinitions(definitions = [], query = '', selected = []) {
  const normalizedQuery = definitionKey(query);
  if (!normalizedQuery) return [];
  const selectedKeys = new Set(selected.map(definitionKey));
  return definitions
    .filter(item => {
      const key = definitionKey(item?.name);
      return key && !selectedKeys.has(key) && key.includes(normalizedQuery);
    })
    .sort((left, right) => {
      const leftName = normalizeDefinitionName(left.name);
      const rightName = normalizeDefinitionName(right.name);
      const leftKey = definitionKey(leftName);
      const rightKey = definitionKey(rightName);
      const leftPrefix = leftKey.startsWith(normalizedQuery) ? 0 : 1;
      const rightPrefix = rightKey.startsWith(normalizedQuery) ? 0 : 1;
      return leftPrefix - rightPrefix
        || leftName.localeCompare(rightName, undefined, { sensitivity: 'base' })
        || Number(left.id || 0) - Number(right.id || 0);
    });
}
