import { expect, test, type APIRequestContext } from "@playwright/test";

import { API, apiCall, createReadyUser, deleteUser, logIn, type TestUser } from "./helpers";

// The test API runs the offline demo AI: asked about calories, the coach proposes
// +100 kcal; the weekly review gets a fixed summary.

let user: TestUser | null = null;

test.afterEach(async ({ request }) => {
  await deleteUser(request, user);
  user = null;
});

/** One coach chat that leaves a pending +100 kcal suggestion. */
async function suggestCalories(request: APIRequestContext, account: TestUser) {
  const res = await request.post(`${API}/coach/chat`, {
    headers: { authorization: `Bearer ${account.token}` },
    data: { text: "Should I eat more calories?" },
  });
  expect(res.ok()).toBeTruthy();
  await res.body(); // the whole stream, so the suggestion is stored
}

test("approve and reject suggestions on Today, run the weekly review", async ({ page, request }) => {
  user = await createReadyUser(request, "Sam", ["ai_processing"]);
  await suggestCalories(request, user);
  await suggestCalories(request, user);
  await logIn(page, user);

  const review = page.getByRole("region", { name: "Weekly review" });
  await expect(review.getByRole("heading", { name: "Waiting for your approval" })).toBeVisible();
  const cards = review.getByRole("group", { name: "Suggested change" });
  await expect(cards).toHaveCount(2);

  const first = cards.nth(0);
  await first.getByRole("button", { name: "Approve" }).click();
  await expect(first.getByText("Approved and applied")).toBeVisible();

  const second = cards.nth(1);
  await second.getByRole("button", { name: "Reject" }).click();
  await second.getByLabel("Why not? (optional)").fill("I already eat enough");
  await second.getByRole("checkbox", { name: "Let the coach remember this" }).click();
  await second.getByRole("button", { name: "Reject" }).click();
  await expect(second.getByText("Rejected")).toBeVisible();

  const decided = await apiCall<{ status: string; decision_note: string | null }[]>(
    request,
    user,
    "GET",
    "/recommendations",
  );
  expect(decided.map((r) => r.status).sort()).toEqual(["approved", "rejected"]);
  expect(decided.find((r) => r.status === "rejected")?.decision_note).toBe("I already eat enough");
  const notes = await apiCall<{ key: string }[]>(request, user, "GET", "/coach/notes");
  expect(notes.map((n) => n.key)).toContain("declined_calorie_change");

  // The weekly review: a summary now, and only one run a day.
  await review.getByRole("button", { name: "Review my last weeks" }).click();
  await expect(review.getByText("Steady work these weeks.", { exact: false })).toBeVisible();
  await review.getByRole("button", { name: "Review my last weeks" }).click();
  await expect(review.getByText("You already had a review today.", { exact: false })).toBeVisible();

  // History lists what was decided, with the user's reason.
  await review.getByRole("link", { name: "Past suggestions" }).click();
  await expect(page.getByRole("heading", { name: "Past suggestions", level: 1 })).toBeVisible();
  const history = page.getByRole("listitem");
  await expect(history.filter({ hasText: "Your reason: I already eat enough" })).toHaveCount(1);
  await expect(history.filter({ hasText: "Approved" })).toHaveCount(1);
});

test("the review section in Arabic", async ({ page, request }) => {
  user = await createReadyUser(request, "Sam", ["ai_processing"]);
  await suggestCalories(request, user);
  await logIn(page, user, "ar");
  const review = page.getByRole("region", { name: "مراجعة الأسبوع" });
  await expect(review.getByRole("heading", { name: "مستني موافقتك" })).toBeVisible();
  const card = review.getByRole("group", { name: "تغيير مقترح" });
  await expect(card).toHaveCount(1);
  await card.getByRole("button", { name: "وافق" }).click();
  await expect(card.getByText("اتوافق عليه واتطبّق")).toBeVisible();
});
