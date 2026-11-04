import { expect, test } from "@playwright/test";

import { apiCall, createReadyUser, deleteUser, logIn, type TestUser } from "./helpers";

// The API runs with the offline demo AI: a koshari (700–850 kcal) and salad (40–70 kcal).

type Day = {
  water_ml: number;
  meals: { name: string; slot: string; confirmed: boolean; user_edited: boolean; kcal: number; kcal_min: number | null; kcal_max: number | null }[];
  totals: { kcal: number; kcal_min: number; kcal_max: number; meals_counted: number };
};

let user: TestUser | null = null;

test.afterEach(async ({ request }) => {
  await deleteUser(request, user);
  user = null;
});

test("log a meal by text, adjust the estimate and save it", async ({ page, request }) => {
  user = await createReadyUser(request, "Sam", ["ai_processing"]);
  await logIn(page, user);
  await page.getByRole("link", { name: "Food" }).first().click();
  await expect(page.getByRole("heading", { name: "Food", level: 1 })).toBeVisible();
  await expect(page.getByRole("figure", { name: "Calories" })).toBeVisible();

  // Describe lunch; the estimate comes back as an unsaved draft.
  const lunch = page.getByRole("region", { name: "Lunch" });
  await lunch.getByRole("button", { name: "Add to Lunch" }).click();
  await lunch.getByRole("tab", { name: "Describe it" }).click();
  await lunch.getByLabel("What did you eat?").fill("a plate of koshari with salad");
  await lunch.getByRole("button", { name: "Estimate" }).click();

  const review = lunch.getByRole("group", { name: "Check the estimate" });
  await expect(review).toBeVisible();
  await expect(review.getByText("Not saved: check it")).toBeVisible();
  await expect(review.getByText("Rough estimate")).toBeVisible();
  await expect(review.getByText("Did you add extra oil or dakka on top?")).toBeVisible();
  await expect(review.getByText(/740–920\W*kcal/)).toBeVisible();

  let day = await apiCall<Day>(request, user, "GET", "/nutrition/day");
  expect(day.totals.meals_counted).toBe(0); // a draft never counts

  // A smaller koshari, a better name, and keep it in My foods.
  await review.getByRole("button", { name: "Smaller portion of Koshari" }).click();
  await expect(review.getByText("338 g")).toBeVisible();
  await expect(review.getByText(/565–708\W*kcal/)).toBeVisible();
  await review.getByLabel("Meal name").fill("Small koshari");
  await review.getByRole("checkbox", { name: "Also keep it in My foods" }).check();
  await review.getByRole("button", { name: "Save meal" }).click();
  await expect(page.getByText("Meal saved")).toBeVisible();
  await expect(lunch.getByText("Small koshari")).toBeVisible();
  await expect(lunch.getByText("565–708")).toBeVisible();

  day = await apiCall<Day>(request, user, "GET", "/nutrition/day");
  expect(day.meals[0]).toMatchObject({
    name: "Small koshari",
    confirmed: true,
    user_edited: true,
    kcal_min: 565,
    kcal_max: 708,
    kcal: 637,
  });
  expect(day.totals).toMatchObject({ kcal: 637, kcal_min: 565, kcal_max: 708, meals_counted: 1 });

  // The saved meal is one tap away at dinner.
  const dinner = page.getByRole("region", { name: "Dinner" });
  await dinner.getByRole("button", { name: "Add to Dinner" }).click();
  await dinner.getByRole("tab", { name: "My foods" }).click();
  await dinner.getByRole("button", { name: "Log Small koshari" }).click();
  await expect(page.getByText("Meal logged")).toBeVisible();
  await expect(dinner.getByText("Small koshari")).toBeVisible();

  // Water: two cups.
  const water = page.getByRole("region", { name: "Water" });
  await water.getByRole("button", { name: "Cup 2", exact: true }).click();
  await expect(water.getByRole("button", { name: "Cup 2", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect.poll(async () => (await apiCall<Day>(request, user!, "GET", "/nutrition/day")).water_ml).toBe(500);

  day = await apiCall<Day>(request, user, "GET", "/nutrition/day");
  expect(day.totals.meals_counted).toBe(2);
});

test("discarding a draft removes it and estimates need AI coaching", async ({ page, request }) => {
  user = await createReadyUser(request);
  await logIn(page, user);
  await page.getByRole("link", { name: "Food" }).first().click();
  const breakfast = page.getByRole("region", { name: "Breakfast" });
  await breakfast.getByRole("button", { name: "Add to Breakfast" }).click();
  // AI coaching is off for this user: the add panel opens on saved foods and explains.
  await expect(breakfast.getByText("No saved foods yet.")).toBeVisible();
  await breakfast.getByRole("tab", { name: "Describe it" }).click();
  await expect(breakfast.getByText(/needs AI coaching, which is turned off/)).toBeVisible();

  // Turn AI coaching on in Settings, then estimate and discard.
  await page.getByRole("link", { name: "Settings" }).first().click();
  await page.getByRole("switch", { name: "AI coaching" }).click();
  await expect(page.getByRole("switch", { name: "AI coaching" })).toHaveAttribute("aria-checked", "true");
  await page.getByRole("link", { name: "Food" }).first().click();
  // The page kept its open panel; with AI coaching on, the description box is there.
  await breakfast.getByLabel("What did you eat?").fill("koshari");
  await breakfast.getByRole("button", { name: "Estimate" }).click();
  const review = breakfast.getByRole("group", { name: "Check the estimate" });
  await review.getByRole("button", { name: "Discard" }).click();
  await expect(review).toBeHidden();
  await expect(breakfast.getByText("Nothing logged")).toBeVisible();
  const day = await apiCall<Day>(request, user, "GET", "/nutrition/day");
  expect(day.meals).toEqual([]);
});
