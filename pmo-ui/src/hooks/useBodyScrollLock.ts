import { useEffect } from 'react';

/**
 * Lock body scroll while a modal/overlay is mounted. Restores the previous
 * overflow value on unmount, so nested modals behave correctly.
 */
export function useBodyScrollLock(active = true): void {
  useEffect(() => {
    if (!active) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, [active]);
}
