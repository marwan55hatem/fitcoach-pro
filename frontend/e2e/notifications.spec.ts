import { expect, test } from "@playwright/test";

import { apiCall, createReadyUser, deleteUser, logIn, type TestUser } from "./helpers";

type Settings = {
  consent: boolean;
  reminders: { type: string; enabled: boolean; schedule: Record<string, unknown> }[];
  prefs: { quiet_start: string | null; show_details: boolean };
};

let user: TestUser | null = null;

test.afterEach(async ({ request }) => {
  await deleteUser(request, user);
  user = null;
});

test("turn on reminders and set them up", async ({ page, request }) => {
  user = await createReadyUser(request, "Sam");
  await logIn(page, user);
  await page.goto("/en/settings");
  await page.getByRole("link", { name: "Reminders and notifications" }).click();
  await expect(page.getByRole("heading", { name: "Reminders", level: 1 })).toBeVisible();

  // Without the consent, one button turns reminders on.
  await page.getByRole("button", { name: "Turn on reminders" }).click();
  await expect(page.getByRole("heading", { name: "Where to send" })).toBeVisible();
  // No push here: the dev server has no service worker, and the emulated phone browser
  // reports notifications as blocked. Either way the screen says why, honestly.
  await expect(
    page.getByText(/Notifications work in the installed app\.|Notifications are blocked for this site\./),
  ).toBeVisible();
  await expect(page.getByText("Telegram isn't set up on this server yet.")).toBeVisible();

  // Meals start off; turning them on shows the three meal times.
  await page.getByRole("switch", { name: "Meals", exact: true }).click();
  await expect(page.getByLabel("Lunch")).toBeVisible();
  await page.getByLabel("Lunch").fill("13:15");
  await page.getByLabel("Lunch").blur();

  // Workout days: Wednesday on.
  const days = page.getByRole("group", { name: "Training days" });
  await days.getByRole("button", { name: "Wed" }).click();
  await expect(days.getByRole("button", { name: "Wed" })).toHaveAttribute("aria-pressed", "true");

  await page.getByRole("switch", { name: "Show numbers in notifications" }).click();

  await expect
    .poll(async () => {
      const s = await apiCall<Settings>(request, user!, "GET", "/notifications/settings");
      const meals = s.reminders.find((r) => r.type === "meals")!;
      const workout = s.reminders.find((r) => r.type === "workout")!;
      return [
        s.consent,
        meals.enabled,
        (meals.schedule.times as Record<string, string>).lunch,
        (workout.schedule.days as number[]).includes(2),
        s.prefs.show_details,
      ];
    })
    .toEqual([true, true, "13:15", true, true]);

  // The test button says plainly that there is no device yet.
  await page.getByRole("button", { name: "Send a test" }).click();
  await expect(page.getByText("No device got it. Add a device first.")).toBeVisible();
});

test("the reminders screen in Arabic", async ({ page, request }) => {
  user = await createReadyUser(request, "Sam");
  await apiCall(request, user, "PUT", "/consents", { consents: { notifications: true } });
  await logIn(page, user, "ar");
  await page.goto("/ar/settings/notifications");
  await expect(page.getByRole("heading", { name: "التذكيرات", level: 1 })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("switch", { name: "التمرين", exact: true })).toBeChecked();
  await expect(page.getByRole("switch", { name: "الوجبات", exact: true })).not.toBeChecked();
});
