import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    include: ['test/unit/**/*.test.js'],
    coverage: {
      reporter: ['text', 'html'],
      reportsDirectory: 'coverage',
    },
  },
});
