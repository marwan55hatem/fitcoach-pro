// Shapes of the FastAPI responses this app uses (see backend/app/schemas).

export type Sex = "male" | "female";
export type Goal = "lean_bulk" | "maintain" | "cut" | "recomp";
export type Experience = "beginner" | "intermediate" | "advanced";
export type Activity = "sedentary" | "light" | "moderate" | "active" | "very_active";
export type Equipment =
  | "barbell"
  | "dumbbell"
  | "ez_bar"
  | "cable"
  | "machine"
  | "bench"
  | "pull_up_bar"
  | "kettlebell"
  | "bands"
  | "body_weight"
  | "other";
export type BodyArea =
  | "neck"
  | "shoulder"
  | "elbow"
  | "wrist_hand"
  | "upper_back"
  | "lower_back"
  | "hip"
  | "knee"
  | "ankle_foot"
  | "other";
export type Severity = "mild" | "moderate" | "severe";
export type Budget = "low" | "medium" | "high";
export type ConsentType = "health_data" | "body_photos" | "ai_processing" | "notifications";
export type RiskLevel = "ok" | "caution" | "blocked";

export const EQUIPMENT: Equipment[] = [
  "barbell",
  "dumbbell",
  "ez_bar",
  "cable",
  "machine",
  "bench",
  "pull_up_bar",
  "kettlebell",
  "bands",
  "body_weight",
];
export const BODY_AREAS: BodyArea[] = [
  "neck",
  "shoulder",
  "elbow",
  "wrist_hand",
  "upper_back",
  "lower_back",
  "hip",
  "knee",
  "ankle_foot",
  "other",
];
export const CONSENTS: ConsentType[] = [
  "health_data",
  "ai_processing",
  "body_photos",
  "notifications",
];
export const SCREENING_QUESTIONS = [
  "chest_pain",
  "heart_condition",
  "blood_pressure_uncontrolled",
  "fainting_or_dizziness",
  "pregnant",
  "recent_surgery",
  "medication_affecting_exercise",
  "joint_or_bone_problem",
  "eating_concern",
] as const;
export type ScreeningQuestion = (typeof SCREENING_QUESTIONS)[number];

export type User = { id: string; email: string; display_name: string | null };

export type ConsentStatus = {
  type: ConsentType;
  granted: boolean;
  granted_at: string | null;
  revoked_at: string | null;
};
export type Consents = { consents: ConsentStatus[] };

export type ProfileIn = {
  birth_date: string;
  sex: Sex;
  height_cm: number;
  weight_kg: number;
  goal: Goal;
  experience: Experience;
  days_per_week: number;
  session_minutes: number;
  equipment: Equipment[];
  activity_level: Activity;
  timezone: string;
  language: "en" | "ar";
};
export type Profile = { [K in keyof ProfileIn]: ProfileIn[K] | null } & {
  equipment: Equipment[];
  timezone: string;
  language: string;
};

export type LimitationIn = {
  body_area: BodyArea;
  severity: Severity;
  description: string | null;
  active: boolean;
};
export type Limitations = { reviewed: boolean; limitations: (LimitationIn & { id: string })[] };

export type Diet = {
  allergies: string[];
  intolerances: string[];
  dislikes: string[];
  cuisine_hints: string[];
  meals_per_day: number;
  budget_level: Budget | null;
};

export type ScreeningIn = Record<ScreeningQuestion, boolean> & { free_text: string | null };
export type Reason = { code: string; message: string };
export type Screening = {
  id: string;
  risk_level: RiskLevel;
  reasons: Reason[];
  notes: Reason[];
  disordered_eating_flag: boolean;
  clearance_required: boolean;
  clearance_confirmed: boolean;
};

export type OnboardingStatus = {
  steps: { key: string; completed: boolean; required: boolean }[];
  missing: string[];
  onboarding_complete: boolean;
  risk_level: RiskLevel | null;
  clearance_required: boolean;
  clearance_confirmed: boolean;
  disordered_eating_flag: boolean;
  plan_access: { allowed: boolean; numeric_diet_targets: boolean; blocked_reasons: Reason[] };
};

export type Violation = { code: string; message: string; day_index: number | null; muscle: string | null };
export type ProgramExercise = {
  id: string;
  exercise_id: string;
  name: string;
  muscle_primary: string;
  equipment: string | null;
  order: number;
  sets: number;
  rep_min: number;
  rep_max: number;
  rest_s: number | null;
  notes: string | null;
  image_url: string | null;
};
export type ProgramDay = {
  id: string;
  day_index: number;
  name: string;
  focus: string | null;
  rationale: string | null;
  estimated_minutes: number;
  exercises: ProgramExercise[];
};
export type Program = {
  id: string;
  version: number;
  status: "draft" | "active" | "archived";
  generated_by: string;
  rationale: string | null;
  days: ProgramDay[];
  warnings: Violation[];
  errors: Violation[];
};
export type GeneratedProgram = Program & {
  source: "ai" | "template";
  fallback_reason: string | null;
};
