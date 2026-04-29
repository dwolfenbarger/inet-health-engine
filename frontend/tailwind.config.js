/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        noc: {
          bg:        '#080b12',
          panel:     '#0d1526',
          border:    '#1a2a44',
          muted:     '#2a4060',
          dim:       '#3a5070',
          text:      '#c8d6e8',
          cyan:      '#00e5ff',
          green:     '#00ff88',
          amber:     '#ffaa00',
          red:       '#ff3b3b',
          orange:    '#ff6b00',
        },
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', '"Fira Code"', '"Courier New"', 'monospace'],
      },
      animation: {
        pulse2: 'pulse 2s ease-in-out infinite',
        flicker: 'flicker 3s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
