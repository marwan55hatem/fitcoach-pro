import { expect, type Locator, type Page, test } from "@playwright/test";

import { apiCall, createReadyUser, deleteUser, logIn, type TestUser } from "./helpers";

type Session = {
  sets_done: number;
  sets_planned: number;
  sets: { exercise_id: string; exercise_name: string; done: boolean; reps: number | null; weight_kg: number | null }[];
};

let user: TestUser | null = null;

test.afterEach(async ({ request }) => {
  await deleteUser(request, user);
  user = null;
});

/** Log one set in a card and dismiss the rest timer, which floats over the page. */
async function logSet(page: Page, card: Locator, n: number, kg: string, reps: string) {
  await card.getByLabel(`Weight for set ${n}`, { exact: true }).fill(kg);
  await card.getByLabel(`Reps for set ${n}`, { exact: true }).fill(reps);
  await card.getByRole("button", { name: `Mark set ${n} done`, exact: true }).click();
  await expect(card.getByRole("button", { name: `Set ${n} done. Tap to undo`, exact: true })).toBeVisible();
  const timer = page.getByRole("timer");
  await expect(timer).toBeVisible();
  await page.getByRole("button", { name: "Skip", exact: true }).click();
  await expect(timer).toBeHidden();
}

test("log a full workout with a swap, keeping sets logged while offline", async ({
  page,
  context,
  request,
}) => {
  user = await createReadyUser(request);
  await logIn(page, user);

  // Today points at day 1 of the program.
  const next = page.getByRole("region", { name: "Next workout" });
  await expect(next).toBeVisible();
  await next.getByRole("button", { name: "Start workout" }).click();
  await expect(page).toHaveURL(/\/en\/train\/workout$/);

  const cards = page.getByRole("article");
  await expect(cards.first()).toBeVisible();
  const total = await cards.count();
  expect(total).toBeGreaterThan(2);

  // First exercise: the steps and photos open on demand; "first time" with no history.
  const first = cards.nth(0);
  const planned = (await first.getByRole("heading", { level: 2 }).textContent()) ?? "";
  await expect(first.getByText("First time doing this one.")).toBeVisible();
  await first.getByRole("button", { name: "How to do it" }).click();
  await expect(first.getByText("Muscles worked")).toBeVisible();
  await first.getByRole("button", { name: "Hide steps" }).click();

  // A reps-less set can't be ticked.
  await first.getByRole("button", { name: "Mark set 1 done", exact: true }).click();
  await expect(first.getByText("Enter the reps first.")).toBeVisible();
  await logSet(page, first, 1, "60", "10");

  // Swap the rest of the first exercise for a substitute.
  await first.getByRole("button", { name: "Swap", exact: true }).click();
  const option = first.getByRole("group", { name: "Swap for" }).getByRole("button").first();
  await expect(option).toBeVisible();
  const substitute = ((await option.locator("span").first().textContent()) ?? "").trim();
  await option.click();
  await expect(page.getByText(`Swapped to ${substitute}`)).toBeVisible();
  await expect(first.getByRole("heading", { level: 2 })).toHaveText(substitute);
  expect(substitute).not.toBe(planned);

  // No signal: the set still ticks, waits in the queue, then saves when back online.
  await context.setOffline(true);
  const firstSets = await first.getByRole("button", { name: /^Mark set \d+ done$/ }).count();
  const offlineSet = await first.getByRole("button", { name: /^Mark set \d+ done$/ }).first().getAttribute("aria-label");
  const n = Number(offlineSet?.match(/\d+/)?.[0]);
  await logSet(page, first, n, "22.5", "12");
  await expect(page.getByRole("status").filter({ hasText: "not saved yet" })).toBeVisible();
  await context.setOffline(false);
  await expect(page.getByRole("status").filter({ hasText: "not saved yet" })).toBeHidden();
  let open = await apiCall<Session>(request, user, "GET", "/sessions/open");
  const saved = open.sets.filter((s) => s.exercise_name === substitute && s.done);
  expect(saved.map((s) => [s.weight_kg, s.reps])).toEqual([[22.5, 12]]);

  // Every remaining set of every exercise.
  for (let left = firstSets - 1; left > 0; left--) {
    const label = await first.getByRole("button", { name: /^Mark set \d+ done$/ }).first().getAttribute("aria-label");
    await logSet(page, first, Number(label?.match(/\d+/)?.[0]), "22.5", "10");
  }
  for (let i = 1; i < total; i++) {
    const card = cards.nth(i);
    const sets = await card.getByRole("button", { name: /^Mark set \d+ done$/ }).count();
    for (let s = 1; s <= sets; s++) await logSet(page, card, s, "40", "10");
  }

  open = await apiCall<Session>(request, user, "GET", "/sessions/open");
  expect(open.sets.every((s) => s.done)).toBeTruthy();

  await page.getByRole("button", { name: "Finish workout" }).click();
  await expect(page.getByRole("heading", { name: "Workout done" })).toBeVisible();
  await expect(page.getByText(`${open.sets.length}/${open.sets.length}`)).toBeVisible();
  await expect(page.getByText("No new bests this time.")).toBeVisible(); // a first workout is a baseline

  const history = await apiCall<{ items: Session[] }>(request, user, "GET", "/sessions?limit=1");
  expect(history.items[0].sets_done).toBe(history.items[0].sets_planned);

  // Back on Today, the next day of the program is up.
  await page.getByRole("link", { name: "Done" }).click();
  await expect(page.getByRole("region", { name: "Next workout" })).toBeVisible();
});

test("finishing early asks first, and the train tab shows the week", async ({ page, request }) => {
  user = await createReadyUser(request);
  await logIn(page, user);
  await page.getByRole("link", { name: "Train" }).first().click();
  const week = page.getByRole("list", { name: "Your week" });
  await expect(week.locator(":scope > li")).toHaveCount(4);
  await expect(week.locator(":scope > li").first().getByText("Next", { exact: true })).toBeVisible();

  // Open day 2 to see its exercises, then start it.
  const day2 = week.locator(":scope > li").nth(1);
  await day2.getByRole("button", { expanded: false }).click();
  await expect(day2.getByRole("listitem").first()).toBeVisible();
  await day2.getByRole("button", { name: /^Start: / }).click();
  await expect(page).toHaveURL(/\/en\/train\/workout$/);

  await page.getByRole("button", { name: "Finish workout" }).click();
  await expect(page.getByText(/sets aren't done\. Finish anyway\?/)).toBeVisible();
  await page.getByRole("button", { name: "Keep going" }).click();
  await page.getByRole("button", { name: "Finish workout" }).click();
  await page.getByRole("button", { name: "Finish anyway" }).click();
  await expect(page.getByRole("heading", { name: "Workout done" })).toBeVisible();
});
