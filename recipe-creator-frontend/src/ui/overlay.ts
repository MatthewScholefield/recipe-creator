const VIEWPORT_PADDING = 12;

export function keepInViewportHorizontally(node: HTMLElement) {
  function position() {
    node.style.setProperty('--overlay-shift-x', '0px');
    const { left, right } = node.getBoundingClientRect();
    const viewportWidth = document.documentElement.clientWidth;
    const shift = left < VIEWPORT_PADDING
      ? VIEWPORT_PADDING - left
      : right > viewportWidth - VIEWPORT_PADDING
        ? viewportWidth - VIEWPORT_PADDING - right
        : 0;
    node.style.setProperty('--overlay-shift-x', `${shift}px`);
  }

  const observer = new ResizeObserver(position);
  observer.observe(node);
  window.addEventListener('resize', position);
  window.addEventListener('scroll', position, true);
  position();

  return {
    destroy() {
      observer.disconnect();
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
    },
  };
}
