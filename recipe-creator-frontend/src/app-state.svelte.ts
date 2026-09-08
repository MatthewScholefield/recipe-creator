import { request, session, message } from './api';
import type { Session, SiteCopy, SiteSettings } from './types';

export const DEFAULT_SITE_COPY: Readonly<SiteCopy> = Object.freeze({
  site_title: 'Recipes', site_tagline: 'Share your food.', home_title: 'Recipes', home_intro: '', footer_text: '',
});
export const appState = $state<{identity: Session | null; copy: SiteCopy; copyError: string}>({
  identity: null, copy: {...DEFAULT_SITE_COPY}, copyError: '',
});
let copyGeneration = 0;
export async function refreshIdentity(): Promise<Session> {
  const value = await session(true);
  appState.identity = value;
  return value;
}
export function setSiteCopy(value: SiteCopy): void {
  copyGeneration++;
  appState.copy = {...value};
  appState.copyError = '';
}
export async function refreshSiteCopy(): Promise<void> {
  const generation = ++copyGeneration;
  try {
    const settings = await request<SiteSettings>('/site-settings');
    if (generation === copyGeneration) setSiteCopy(settings.copy);
  } catch (error) {
    if (generation === copyGeneration) appState.copyError = `Site text could not be loaded. ${message(error)}`;
  }
}
