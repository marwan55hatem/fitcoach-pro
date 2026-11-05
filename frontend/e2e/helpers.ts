import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const API = "http://localhost:8001";
export const PASSWORD = "correct-horse-battery";

export type TestUser = { email: string; token: string };

function adultBirthDate(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 30);
  return d.toISOString().slice(0, 10);
}

async function call(
  request: APIRequestContext,
  method: "GET" | "POST" | "PUT" | "DELETE",
  path: string,
  token: string | null,
  data?: unknown,
) {
  const res = await request.fetch(`${API}${path}`, {
    method,
    data,
    headers: token ? { authorization: `Bearer ${token}` } : {},
  });
  expect(res.ok(), `${method} ${path}: ${res.status()} ${await res.text()}`).toBeTruthy();
  return res.status() === 204 ? null : res.json();
}

export function apiCall<T>(
  request: APIRequestContext,
  user: TestUser,
  method: "GET" | "POST" | "PUT" | "DELETE",
  path: string,
  data?: unknown,
): Promise<T> {
  return call(request, method, path, user.token, data) as Promise<T>;
}

/** A user who finished onboarding (fake data, rules-only program, no AI) straight via the API. */
export async function createReadyUser(
  request: APIRequestContext,
  name = "Sam",
  extraConsents: ("ai_processing" | "body_photos")[] = [],
): Promise<TestUser> {
  const email = `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
  await call(request, "POST", "/auth/register", null, { email, password: PASSWORD, display_name: name });
  const { access_token: token } = (await call(request, "POST", "/auth/login", null, {
    email,
    password: PASSWORD,
  })) as { access_token: string };
  const user = { email, token };
  try {
    const consents = Object.fromEntries(["health_data", ...extraConsents].map((c) => [c, true]));
    await apiCall(request, user, "PUT", "/consents", { consents });
    await apiCall(request, user, "PUT", "/onboarding/profile", {
      birth_date: adultBirthDate(),
      sex: "male",
      height_cm: 193,
      weight_kg: 70,
      goal: "lean_bulk",
      experience: "intermediate",
      days_per_week: 4,
      session_minutes: 75,
      equipment: ["barbell", "dumbbell", "ez_bar", "cable", "machine", "bench"],
      activity_level: "moderate",
      timezone: "Africa/Cairo",
      language: "en",
    });
    await apiCall(request, user, "PUT", "/onboarding/limitations", { limitations: [] });
    await apiCall(request, user, "PUT", "/onboarding/diet", { meals_per_day: 4 });
    const no = Object.fromEntries(
      [
        "chest_pain",
        "heart_condition",
        "blood_pressure_uncontrolled",
        "fainting_or_dizziness",
        "pregnant",
        "recent_surgery",
        "medication_affecting_exercise",
        "joint_or_bone_problem",
        "eating_concern",
      ].map((q) => [q, false]),
    );
    await apiCall(request, user, "POST", "/onboarding/screening", no);
    const draft = await apiCall<{ id: string }>(request, user, "POST", "/programs/generate", { use_ai: false });
    await apiCall(request, user, "POST", `/programs/${draft.id}/accept`);
  } catch (error) {
    await deleteUser(request, user); // never leave a half-made test account behind
    throw error;
  }
  return user;
}

/** Remove the account and everything in it (the token from creation is still valid). */
export async function deleteUser(request: APIRequestContext, user: TestUser | null): Promise<void> {
  if (!user) return;
  await request.delete(`${API}/me`, {
    headers: { authorization: `Bearer ${user.token}` },
    data: { password: PASSWORD },
  });
}

export async function logIn(page: Page, user: TestUser, lang: "en" | "ar" = "en"): Promise<void> {
  await page.goto(`/${lang}/login`);
  await page.getByLabel(lang === "en" ? "Email" : "الإيميل").fill(user.email);
  await page.getByLabel(lang === "en" ? "Password" : "كلمة السر").fill(PASSWORD);
  await page.getByRole("button", { name: lang === "en" ? "Log in" : "دخول" }).click();
  await expect(page).toHaveURL(new RegExp(`/${lang}/today$`));
}
