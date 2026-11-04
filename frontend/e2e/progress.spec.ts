import { expect, test } from "@playwright/test";

import { apiCall, createReadyUser, deleteUser, logIn, type TestUser } from "./helpers";

// A tiny valid PNG: the API re-encodes it; the offline demo AI "reads" a fixed scan.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFElEQVR4nGM8UWHDgA0wYRUdtBIALYsBjOsoCe8AAAAASUVORK5CYII=",
  "base64",
);

type Scan = { id: string; weight_kg: number; confirmed: boolean };

let user: TestUser | null = null;

test.afterEach(async ({ request }) => {
  await deleteUser(request, user);
  user = null;
});

test("upload an InBody scan, fix a value and confirm it", async ({ page, request }) => {
  user = await createReadyUser(request, "Sam", ["ai_processing", "body_photos"]);
  await logIn(page, user);
  await page.getByRole("link", { name: "Progress" }).first().click();
  await expect(page.getByRole("heading", { name: "Progress", level: 1 })).toBeVisible();
  await expect(page.getByRole("img", { name: "Weight", exact: true })).toBeVisible(); // the profile's weight
  await expect(page.getByText("No confirmed scans yet.")).toBeVisible();

  await page.getByLabel("Add a scan").setInputFiles({ name: "scan.png", mimeType: "image/png", buffer: PNG });
  const review = page.getByRole("group", { name: "Check your scan" });
  await expect(review).toBeVisible();
  await expect(review.getByLabel("Skeletal muscle (kg)")).toHaveValue("34.2");
  // The demo reading is unsure about body water: it is highlighted for checking.
  await expect(review.getByText("(Not sure)")).toBeVisible();

  expect(await apiCall<Scan[]>(request, user, "GET", "/inbody")).toEqual([]); // still a draft

  await review.getByLabel("Weight (kg)").fill("72.4");
  await review.getByRole("button", { name: "Confirm scan" }).click();
  await expect(page.getByText("Scan confirmed")).toBeVisible();
  await expect(review).toBeHidden();
  await expect(page.getByText("Add a second scan to see how you're changing.")).toBeVisible();

  const scans = await apiCall<Scan[]>(request, user, "GET", "/inbody");
  expect(scans).toHaveLength(1);
  expect(scans[0]).toMatchObject({ weight_kg: 72.4, confirmed: true });
});

test("progress photos stay private and can be deleted", async ({ page, request }) => {
  user = await createReadyUser(request, "Sam", ["body_photos"]);
  await logIn(page, user);
  await page.getByRole("link", { name: "Progress" }).first().click();
  const photos = page.getByRole("region", { name: "Progress photos" });
  await expect(photos.getByText("No Front photos yet.")).toBeVisible();

  await photos.getByLabel("Add a Front photo").setInputFiles({ name: "front.png", mimeType: "image/png", buffer: PNG });
  const thumbnail = photos.getByRole("img", { name: /^Front \d{4}-\d{2}-\d{2}$/ });
  await expect(thumbnail).toBeVisible();
  await expect(thumbnail).toHaveAttribute("src", /X-Amz-Signature=/); // a signed, expiring link

  await photos.getByRole("tab", { name: "Side" }).click();
  await expect(photos.getByText("No Side photos yet.")).toBeVisible();
  await photos.getByRole("tab", { name: "Front" }).click();
  await photos.getByRole("button", { name: "Delete this photo" }).click();
  await expect(page.getByText("Photo deleted")).toBeVisible();
  await expect(photos.getByText("No Front photos yet.")).toBeVisible();

  // Without AI coaching, scans explain why they can't be read.
  await expect(page.getByText(/Reading a scan needs AI coaching/)).toBeVisible();
});
