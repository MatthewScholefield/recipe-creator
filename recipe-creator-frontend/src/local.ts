export function load<T>(key: string, fallback: T): T { try { const value = localStorage.getItem(`notebook:${key}`); if (value === null) return fallback; const parsed = JSON.parse(value); if (fallback !== null && (Array.isArray(fallback) ? !Array.isArray(parsed) : typeof parsed !== typeof fallback)) return fallback; return parsed; } catch { return fallback; } }
export function save(key: string, value: unknown): boolean { try { localStorage.setItem(`notebook:${key}`, JSON.stringify(value)); return true; } catch { return false; } }
export function remove(key: string) { try { localStorage.removeItem(`notebook:${key}`); } catch { /* storage unavailable */ } }
export function safeUrl(value: string): string | undefined { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined; } catch { return undefined; } }
export function translateLegacy(hash: string): string | null {
  const match = /^#\/(view|edit|tag)\/(.+)$/.exec(hash);
  if (!match) return null;
  let value: string; try { value = decodeURIComponent(match[2]); } catch { return null; }
  if (match[1] === 'tag') return `/?q=${encodeURIComponent(`tag:${value}`)}`;
  return `/recipes/${encodeURIComponent(value)}${match[1] === 'edit' ? '/edit' : ''}`;
}
export const categories = ['breakfast', 'lunch', 'dinner', 'dessert', 'american', 'asian', 'indian', 'mexican'];
