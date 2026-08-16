export function flattenKeyValuePairs(value, prefix = '') {
  if (Array.isArray(value)) {
    if (!value.length) return prefix ? [{ key: prefix, value: 'None' }] : [];
    return value.flatMap((item, position) => flattenKeyValuePairs(item, `${prefix}[${position}]`));
  }
  if (value && typeof value === 'object') {
    const entries = Object.entries(value).sort(([left], [right]) => left.localeCompare(right));
    if (!entries.length) return prefix ? [{ key: prefix, value: 'None' }] : [];
    return entries.flatMap(([key, item]) => flattenKeyValuePairs(item, prefix ? `${prefix}.${key}` : key));
  }
  return [{ key: prefix || 'value', value: formatAttributeValue(value) }];
}

export function formatAttributeKey(path) {
  return path
    .replaceAll(/\[(\d+)]/g, '.$1')
    .split('.')
    .map(humanizeSegment)
    .join(' › ');
}

function humanizeSegment(value) {
  const words = value
    .replaceAll('_', ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  return words.map((word) => {
    if (/^(opc|udt|rtac|id|ip|url)$/i.test(word)) return word.toUpperCase();
    return word.charAt(0).toUpperCase() + word.slice(1);
  }).join(' ');
}

function formatAttributeValue(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'True' : 'False';
  return String(value);
}
