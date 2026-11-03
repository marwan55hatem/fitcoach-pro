import { expect, test } from "@playwright/test";

import { apiCall, createReadyUser, deleteUser, logIn, type TestUser } from "./helpers";

let user: TestUser | null = null;

test.afterEach(async ({ request }) => {
  await deleteUser(request, user);
  user = null;
});

test("log today's weight and the weekly check-in", async ({ page, request }) => {
  user = await createReadyUser(request);
  await logIn(page, user);

  await expect(page.getByRole("heading", { name: "Hi Sam" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Next workout" })).toBeVisible();
  await expect(page.getByText("No recommendations right now.")).toBeVisible();

  // Food: nothing logged yet, targets from the nutrition rules.
  const food = page.getByRole("region", { name: "Food today" });
  await expect(food.getByText("No meals logged yet.")).toBeVisible();
  await expect(food.getByText(/^0 of \d+$/)).toBeVisible(); // calories

  // Weight: out-of-range is refused, then a real value is logged.
  const weight = page.getByRole("region", { name: "Today's weight" });
  await weight.getByLabel("Weight (kg)").fill("12");
  await weight.getByRole("button", { name: "Log weight" }).click();
  await expect(weight.getByText("Enter a weight between 30 and 300 kg.")).toBeVisible();
  await weight.getByLabel("Weight (kg)").fill("70.5");
  await weight.getByRole("button", { name: "Log weight" }).click();
  await expect(page.getByText("Weight saved")).toBeVisible();
  await expect(weight.getByText("7-day average 70.5 kg")).toBeVisible();

  // Check-in: an empty one is refused; answers are saved and shown as done.
  const checkin = page.getByRole("region", { name: "Weekly check-in" });
  await checkin.getByRole("button", { name: "Save check-in" }).click();
  await expect(checkin.getByText("Answer at least one question.")).toBeVisible();
  await checkin.getByLabel("Sleep per night (hours)").fill("7.5");
  await checkin.getByRole("radiogroup", { name: "Energy" }).getByRole("radio", { name: "4" }).click();
  await checkin.getByRole("radiogroup", { name: "Motivation" }).getByRole("radio", { name: "5, High" }).click();
  await checkin.getByRole("radiogroup", { name: "Muscle soreness" }).getByRole("radio", { name: "2" }).click();
  await checkin.getByRole("radiogroup", { name: "Stress" }).getByRole("radio", { name: "1, Low" }).click();
  await checkin.getByRole("button", { name: "Save check-in" }).click();
  await expect(page.getByText("Check-in saved")).toBeVisible();
  await expect(checkin.getByText(/^Checked in on /)).toBeVisible();

  const stored = await apiCall<{ sleep_h: number; energy: number; motivation: number; soreness: number; stress: number }[]>(
    request,
    user,
    "GET",
    "/checkins?days=7",
  );
  expect(stored).toHaveLength(1);
  expect(stored[0]).toMatchObject({ sleep_h: 7.5, energy: 4, motivation: 5, soreness: 2, stress: 1 });
  const history = await apiCall<{ latest_kg: number }>(request, user, "GET", "/weight");
  expect(history.latest_kg).toBe(70.5);

  // After a reload the answers are still there.
  await page.reload();
  await expect(
    page.getByRole("radiogroup", { name: "Energy" }).getByRole("radio", { name: "4" }),
  ).toHaveAttribute("aria-checked", "true");
});

test("Today in Arabic reads right to left", async ({ page, request }) => {
  user = await createReadyUser(request, "سام");
  await logIn(page, user, "ar");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("heading", { name: "أهلًا يا سام" })).toBeVisible();
  await expect(page.getByRole("region", { name: "التمرين الجاي" })).toBeVisible();
  await expect(page.getByRole("region", { name: "متابعة الأسبوع" })).toBeVisible();
});
