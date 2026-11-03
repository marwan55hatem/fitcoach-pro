import { expect, test } from "@playwright/test";

test("Arabic pages are right-to-left and switch back to English", async ({ page }) => {
  await page.goto("/ar/login");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.locator("html")).toHaveAttribute("lang", "ar");
  await expect(page.getByRole("heading", { name: "أهلًا بيك تاني" })).toBeVisible();

  await page.getByRole("link", { name: "التحويل للإنجليزي" }).click();
  await expect(page).toHaveURL(/\/en\/login$/);
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
});

test("the chosen language is remembered", async ({ page }) => {
  await page.goto("/ar/register");
  await page.goto("/");
  await expect(page).toHaveURL(/\/ar\/login$/);
});

test("the root follows an Arabic browser", async ({ browser }) => {
  const context = await browser.newContext({ locale: "ar-EG" });
  const page = await context.newPage();
  await page.goto("http://localhost:3001/");
  await expect(page).toHaveURL(/\/ar\/login$/);
  await context.close();
});
