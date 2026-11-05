import { CoachView } from "@/components/coach/coach-view";

// Shown only after the client-side login check, which instant-navigation checks can't see.
export const instant = false;

export default function CoachPage() {
  return <CoachView />;
}
