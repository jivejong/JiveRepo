import { expect, test } from '@playwright/test';

test('the production app shell loads', async ({ page }) => {
  await page.goto('/');

  await expect(page.getByPlaceholder('Search songs, artists…')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add a new song' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Filter by tags' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Setlists' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Settings' })).toBeVisible();
});
