import { expect, type Page, test } from "@playwright/test";

const PASSWORD = "correct-horse-battery";

function adultBirthDate(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 30);
  return d.toISOString().slice(0, 10);
}

let created: string | null = null; // an account to remove if the test stops early

test.afterEach(async ({ request }) => {
  if (!created) return;
  const login = await request.post("http://localhost:8001/auth/login", {
    data: { email: created, password: PASSWORD },
  });
  if (login.ok()) {
    const { access_token } = (await login.json()) as { access_token: string };
    await request.delete("http://localhost:8001/me", {
      headers: { authorization: `Bearer ${access_token}` },
      data: { password: PASSWORD },
    });
  }
  created = null;
});

async function next(page: Page) {
  await page.getByRole("button", { name: "Next", exact: true }).click();
}

test("register, finish onboarding and accept a program, then delete the account", async ({
  page,
}) => {
  const email = `e2e-${Date.now()}@example.com`;
  created = email;

  await page.goto("/");
  await expect(page).toHaveURL(/\/en\/login$/);
  await page.getByRole("link", { name: "Create an account" }).click();

  // Register (adults only).
  await page.getByLabel("First name").fill("Sam");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByText("Confirm that you are 18 or older.")).toBeVisible();
  await page.getByRole("checkbox", { name: "I am 18 or older" }).check();
  await page.getByRole("button", { name: "Create account" }).click();

  // 1 Privacy: health data is required; AI stays off so no provider is called.
  await expect(page.getByRole("heading", { name: "What you share, and why" })).toBeVisible();
  const progress = page.getByRole("progressbar");
  await expect(progress).toHaveAttribute("aria-valuenow", "0");
  await next(page);
  await expect(page.getByText("Health and body data is needed")).toBeVisible();
  await page.getByRole("switch", { name: "Health and body data" }).click();
  await next(page);

  // 2 About you
  await expect(page.getByRole("heading", { name: "About you" })).toBeVisible();
  await expect(progress).toHaveAttribute("aria-valuenow", "1");
  await page.getByLabel("Date of birth").fill(adultBirthDate());
  await page.getByRole("radio", { name: "Male", exact: true }).click();
  await page.getByLabel("Height (cm)").fill("193");
  await page.getByLabel("Weight (kg)").fill("70");
  await next(page);

  // 3 Goal
  await page.getByRole("radio", { name: "Build muscle (lean bulk)" }).click();
  await page.getByRole("radio", { name: "Intermediate (1–3 years)" }).click();
  await page.getByRole("radio", { name: "On my feet a lot" }).click();
  await next(page);

  // 4 Schedule and equipment (saves the profile)
  await page.getByRole("radio", { name: "4", exact: true }).click();
  await page.getByRole("radio", { name: "75", exact: true }).click();
  for (const item of ["Barbell", "Dumbbells", "Cable machine", "Machines", "Bench"]) {
    await page.getByRole("button", { name: item, exact: true }).click();
  }
  await next(page);

  // 5 Injuries
  await expect(page.getByRole("heading", { name: "Any injuries or pain?" })).toBeVisible();
  await page.getByRole("checkbox", { name: "No injuries or pain" }).check();
  await next(page);

  // 6 Food
  await page.getByRole("radio", { name: "4", exact: true }).click();
  await page.getByLabel("Allergies").fill("peanuts");
  await next(page);

  // 7 Health check: answer every question "No".
  await expect(page.getByRole("heading", { name: "Quick health check" })).toBeVisible();
  const groups = page.getByRole("radiogroup");
  const count = await groups.count();
  for (let i = 0; i < count; i++) {
    await groups.nth(i).getByRole("radio", { name: "No" }).click();
  }
  await page.getByRole("button", { name: "Check my answers" }).click();
  await expect(page.getByRole("heading", { name: "You're good to go" })).toBeVisible();
  await next(page);

  // 8 Scans and photos: a skippable placeholder for now.
  await page.getByRole("button", { name: "Skip for now" }).click();

  // 9 Program preview, built from the rules (no AI consent), then accepted.
  await expect(progress).toHaveAttribute("aria-valuenow", "8");
  await page.getByRole("button", { name: "Build my program" }).click();
  await expect(page.getByText("Built from our training rules.")).toBeVisible();
  const days = page.getByRole("list", { name: "Your program" }).locator(":scope > li");
  await expect(days).toHaveCount(4);
  await page.getByRole("button", { name: "Start this program" }).click();

  // Today points at the first day of the accepted program.
  await expect(page).toHaveURL(/\/en\/today$/);
  await expect(page.getByRole("heading", { name: "Hi Sam" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Next workout" })).toBeVisible();

  // A reload keeps the session (refresh cookie) without storing tokens in the page.
  await page.reload();
  await expect(page.getByRole("heading", { name: "Hi Sam" })).toBeVisible();
  const stored = await page.evaluate(() => JSON.stringify(window.localStorage));
  expect(stored).toBe("{}");

  // Delete the account: a wrong password deletes nothing and keeps you logged in.
  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByLabel("Enter your password to confirm").fill("not-my-password");
  await page.getByRole("button", { name: "Delete my account" }).click();
  await expect(page.getByText("That password is wrong. Nothing was deleted.")).toBeVisible();
  await page.getByLabel("Enter your password to confirm").fill(PASSWORD);
  await page.getByRole("button", { name: "Delete my account" }).click();
  await expect(page).toHaveURL(/\/en\/login$/);
  created = null;

  // The account is gone.
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByText("That email and password don't match.")).toBeVisible();
});

test("private pages send visitors to login", async ({ page }) => {
  await page.goto("/en/today");
  await expect(page).toHaveURL(/\/en\/login$/);
});
