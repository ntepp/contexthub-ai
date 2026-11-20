import { useCallback, useSyncExternalStore } from 'react'

/** Tailwind breakpoints used by the layout (tailwind.config.js keeps the defaults). */
export const MD = '(min-width: 768px)'
export const LG = '(min-width: 1024px)'

/** Whether a CSS media query matches, kept up to date (read synchronously on the first render). */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const list = window.matchMedia(query)
      list.addEventListener('change', onChange)
      return () => list.removeEventListener('change', onChange)
    },
    [query],
  )
  return useSyncExternalStore(subscribe, () => window.matchMedia(query).matches)
}
