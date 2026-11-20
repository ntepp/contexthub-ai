/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
        serif: ['"Source Serif 4"', 'Georgia', 'serif'],
      },
      colors: {
        paper: '#F5F6F8',
        surface: '#FFFFFF',
        ink: '#16213A',
        slate: '#5A6475',
        faint: '#8A93A3',
        rule: '#D8DDE6',
        wash: '#EEF1F5',
        ledger: { DEFAULT: '#1E5B48', soft: '#E4EFEA', dark: '#164536' },
        refusal: { DEFAULT: '#8A5A00', soft: '#FFF5DE' },
        danger: { DEFAULT: '#A33A2B', soft: '#FBEAE7' },
        cloud: { DEFAULT: '#6B3FA0', soft: '#F1EAF8' },
      },
      fontSize: {
        xs: ['0.75rem', '1.1rem'],
        sm: ['0.875rem', '1.35rem'],
        base: ['1rem', '1.6rem'],
        answer: ['1.0625rem', '1.75rem'],
      },
      boxShadow: {
        panel: '-1px 0 0 #D8DDE6, -12px 0 24px -16px rgba(22, 33, 58, 0.18)',
      },
      // Mobile navigation drawer (Radix keeps it mounted until the closing animation ends).
      keyframes: {
        'drawer-in': { from: { transform: 'translateX(-100%)' }, to: { transform: 'translateX(0)' } },
        'drawer-out': { from: { transform: 'translateX(0)' }, to: { transform: 'translateX(-100%)' } },
        'fade-in': { from: { opacity: '0' }, to: { opacity: '1' } },
        'fade-out': { from: { opacity: '1' }, to: { opacity: '0' } },
      },
      animation: {
        'drawer-in': 'drawer-in 200ms ease-out',
        'drawer-out': 'drawer-out 150ms ease-in',
        'fade-in': 'fade-in 200ms ease-out',
        'fade-out': 'fade-out 150ms ease-in',
      },
    },
  },
  plugins: [],
}
