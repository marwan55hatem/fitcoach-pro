import { WorkoutView } from "@/components/train/workout-view";

// Shown only after the client-side login check, which instant-navigation checks can't see.
export const instant = false;

export default function WorkoutPage() {
  return <WorkoutView />;
}
