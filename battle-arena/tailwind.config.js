/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        neko: {
          darker: '#0a0814',
          dark:   '#110d22',
          card:   '#1a1330',
          rim:    '#2a1f45',
        },
      },
      fontFamily: {
        display: ['"Noto Sans SC"', 'sans-serif'],
      },
      keyframes: {
        ticker: {
          '0%':   { transform: 'translateX(0%)' },
          '100%': { transform: 'translateX(-50%)' },
        },
      },
      animation: {
        ticker: 'ticker 60s linear infinite',
      },
    },
  },
  plugins: [],
}
