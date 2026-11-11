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

export type SetLog = {
  id: string;
  exercise_id: string;
  exercise_name: string;
  program_exercise_id: string | null;
  set_number: number;
  weight_kg: number | null;
  reps: number | null;
  rpe: number | null;
  done: boolean;
  is_warmup: boolean;
  target_reps_min: number | null;
  target_reps_max: number | null;
  target_weight_kg: number | null;
};
export type SetPatch = Partial<Pick<SetLog, "weight_kg" | "reps" | "rpe" | "done">>;
export type SessionSummary = {
  id: string;
  program_day_id: string | null;
  day_name: string | null;
  performed_on: string;
  started_at: string | null;
  ended_at: string | null;
  sets_done: number;
  sets_planned: number;
  volume_kg: number;
};
export type WorkoutSession = SessionSummary & { notes: string | null; sets: SetLog[] };
export type FinishedSession = WorkoutSession & { new_records: string[] };
export type SessionList = { items: SessionSummary[]; total: number; limit: number; offset: number };

export type ExerciseSummary = {
  id: string;
  name: string;
  muscle_primary: string;
  muscles_secondary: string[];
  equipment: string | null;
  image_url: string | null;
};
export type ExerciseDetail = ExerciseSummary & {
  instructions: string[];
  image_urls: string[]; // signed, valid for five minutes
  attribution: string | null;
};
export type Substitutes = { exercise_id: string; substitutes: ExerciseSummary[] };
export type BestSet = { weight_kg: number; reps: number; date: string; estimated_1rm_kg: number | null };
export type ExerciseLast = {
  exercise_id: string;
  last_date: string | null;
  last_sets: { set_number: number; weight_kg: number | null; reps: number; rpe: number | null }[];
  personal_best: { best_e1rm: BestSet | null; heaviest: BestSet | null; most_reps: number } | null;
};

export type WeightHistory = {
  points: { date: string; weight_kg: number; average_7d: number }[];
  latest_kg: number | null;
  average_7d: number | null;
  weekly_change_kg: number | null;
};
export const CHECKIN_SCORES = ["energy", "motivation", "soreness", "stress"] as const;
export type CheckinScore = (typeof CHECKIN_SCORES)[number];
export type CheckinIn = { sleep_h: number | null } & Record<CheckinScore, number | null>;
export type Checkin = CheckinIn & { id: string; date: string; notes: string | null };

export type Macros = { kcal: number; protein_g: number; carbs_g: number; fat_g: number };
export type NutritionDay = {
  date: string;
  water_ml: number;
  totals: Macros & { meals_counted: number; drafts_excluded: number };
  targets: (Macros & { water_ml: number }) | null; // null when targets are withheld
  targets_withheld: boolean;
};

export type FileKind = "inbody" | "body_photo" | "food" | "chat";
export type StoredFile = { id: string; kind: FileKind; content_type: string; size_bytes: number };

export const MEAL_SLOTS = ["breakfast", "snack", "lunch", "pre_workout", "dinner", "bed"] as const;
export type MealSlot = (typeof MEAL_SLOTS)[number] | "post_workout";
export type FoodItem = {
  name: string;
  grams?: number;
  units?: string;
  kcal_min?: number;
  kcal_max?: number;
  protein_g?: number;
  carbs_g?: number;
  fat_g?: number;
};
export type Meal = {
  id: string;
  date: string;
  slot: MealSlot;
  source: "text" | "photo" | "manual" | "catalog";
  name: string;
  items: FoodItem[];
  kcal: number | null; // an estimate's midpoint
  kcal_min: number | null;
  kcal_max: number | null;
  protein_g: number | null;
  carbs_g: number | null;
  fat_g: number | null;
  confidence: number | null;
  file_id: string | null;
  confirmed: boolean; // drafts do not count until confirmed
  user_edited: boolean;
  questions: string[]; // a draft's questions from the AI
  flags: FoodFlag[]; // a draft's numbers to check
};
export type FoodFlag = { item: number | null; field: string; code: string; message: string };
export type FoodDraft = Meal;
export type FoodLogIn = {
  date: string;
  slot: MealSlot;
  name: string;
  items: FoodItem[];
  kcal: number;
  kcal_min: number | null;
  kcal_max: number | null;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
};
export type PersonalFood = {
  id: string;
  name: string;
  slot: MealSlot | null;
  items: FoodItem[];
  kcal: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
};
export type FoodDay = {
  date: string;
  meals: Meal[];
  water_ml: number;
  totals: Macros & { kcal_min: number; kcal_max: number; meals_counted: number; drafts_excluded: number };
  targets: (Macros & { water_ml: number }) | null;
  targets_withheld: boolean;
};

export const INBODY_FIELDS = [
  "weight_kg",
  "smm_kg",
  "body_fat_mass_kg",
  "pbf_pct",
  "bmi",
  "tbw_l",
  "bmr_kcal",
  "visceral_fat_level",
  "inbody_score",
] as const;
export type InbodyField = (typeof INBODY_FIELDS)[number];
export type Segments = Record<"left_arm" | "right_arm" | "trunk" | "left_leg" | "right_leg", number | null>;
export type ScanFlag = { field: string; code: string; message: string };
export type InbodyScan = Record<InbodyField, number | null> & {
  id: string;
  taken_on: string;
  file_id: string | null;
  confirmed: boolean;
  confirmed_at: string | null;
  segmental_lean: Segments | null;
  segmental_fat: Segments | null;
  confidence: Record<string, number>;
  flags: ScanFlag[];
};
export type InbodyTrend = {
  points: { date: string; weight_kg: number | null; smm_kg: number | null; body_fat_mass_kg: number | null; pbf_pct: number | null }[];
  change: {
    from_date: string;
    to_date: string;
    days: number;
    weight_kg: number | null;
    smm_kg: number | null;
    body_fat_mass_kg: number | null;
    pbf_pct: number | null;
    interpretation_code: string | null;
    interpretation: string | null;
  } | null;
};

export const PHOTO_VIEWS = ["front", "side", "back"] as const;
export type PhotoView = (typeof PHOTO_VIEWS)[number];
export type ProgressPhoto = {
  id: string;
  taken_on: string;
  view: PhotoView;
  notes: string | null;
  file_id: string;
  url: string;
  thumbnail_url: string | null;
};
export type VolumeStats = {
  weeks: { week_start: string; sets_by_muscle: Record<string, number> }[];
  target_min: number | null;
  target_max: number | null;
};
export type PersonalRecord = {
  exercise_id: string;
  name: string;
  date: string;
  best_e1rm: BestSet | null;
  heaviest: BestSet | null;
  most_reps: number;
};
export type RecommendationStatus = "pending" | "approved" | "rejected" | "expired";
export type Recommendation = {
  id: string;
  kind: string;
  payload: Record<string, unknown>;
  reason: string | null;
  source: "coach" | "review";
  status: RecommendationStatus;
  created_at: string;
  expires_at: string | null;
  decided_at: string | null;
  applied_at: string | null;
  decision_note: string | null;
};
export type WeeklyReview = {
  id: string;
  run_on: string;
  summary: string | null;
  written_by: "ai" | "rules";
  recommendations: Recommendation[];
};
