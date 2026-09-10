import { mount } from 'svelte';
import App from './App.svelte';
import './style.css';

function isMobileDevice() {
  return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)
    || (navigator.maxTouchPoints > 1 && /Macintosh/i.test(navigator.userAgent));
}

async function initMobileDevtools() {
  if (!isMobileDevice()) return;
  // Keep the desktop bundle free of Eruda; it is only useful on mobile.
  const { default: eruda } = await import('eruda');
  eruda.init();
}

void initMobileDevtools();
mount(App, { target: document.getElementById('app')! });
