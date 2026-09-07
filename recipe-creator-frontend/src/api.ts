import type { Session } from './types';
let current: Session | null = null;
let loading: Promise<Session> | null = null;
export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }
export async function session(refresh = false): Promise<Session> {
  if (current && !refresh) return current;
  if (loading) return loading;
  loading = request<Session>('/session').then(value => current = value).finally(() => loading = null);
  return loading;
}
export function clearSession() { current = null; }
export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  const method = options.method || 'GET';
  if (!['GET', 'HEAD'].includes(method)) headers.set('X-CSRF-Token', (await session()).csrf_token);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await fetch(`/api${path}`, { ...options, headers, credentials: 'same-origin' });
  if (!response.ok) {
    let message = `Request failed (${response.status}). Please try again.`;
    try { const body = await response.json(); const detail = body.detail ?? body.error?.message ?? body.message; if (typeof detail === 'string') message = detail; } catch {}
    throw new ApiError(response.status, message);
  }
  return response.status === 204 ? undefined as T : response.json();
}
export const mutate = <T>(path: string, body?: unknown, method = 'POST', signal?: AbortSignal, key?: string) => request<T>(path, {method, signal, ...(key ? {headers: {'Idempotency-Key': key}} : {}), ...(body === undefined ? {} : {body: body instanceof FormData ? body : JSON.stringify(body)})});
export const id = encodeURIComponent;
export const message = (error: unknown) => error instanceof Error ? error.message : 'Something went wrong. Please try again.';
export const photoUrl = (photoId: string, thumbnail = false) => `/api/photos/${id(photoId)}/image${thumbnail ? '?thumbnail=true' : ''}`;
