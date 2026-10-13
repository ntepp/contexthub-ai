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
    },
  },
  plugins: [],
}
